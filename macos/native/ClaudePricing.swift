import Foundation

/// Claude prices are API reference prices. Speed rules come from the Code docs,
/// and never become a claimed multiplier for subscription allowance percentages.
final class ClaudePricingCatalog {
    let directory: URL
    private let seed: Object
    private var aliases: Object
    private var settingsStamp: FileStamp?
    private(set) var rows: [PriceRow] = []
    private(set) var version = ""
    private(set) var updated: Date?
    private(set) var warning: String?
    private var ids: [String:String] = [:]
    private var labels: [String:String] = [:]
    static let source = "https://platform.claude.com/docs/en/about-claude/pricing"
    init(_ directory: URL) {
        self.directory = directory.appendingPathComponent("claude")
        seed = Bundle.main.url(forResource:"claude_pricing_seed",withExtension:"json").map(readObject) ?? [:]
        aliases = readObject(self.directory.appendingPathComponent("aliases.json"))
        rebuild()
    }
    private static func id(_ value: String) -> String { value.trimmingCharacters(in:.whitespacesAndNewlines).lowercased().replacingOccurrences(of:"[1m]",with:"") }
    private static func label(_ value: String) -> String { value.lowercased().filter {$0.isASCII && ($0.isLetter || $0.isNumber)} }
    func compactModelName(_ model: String) -> String {
        let canonical = ids[Self.id(model)]
        let name = canonical.flatMap {id in rows.first(where:{$0.model == id})?.raw.string("display_name")} ?? model
        return name.replacingOccurrences(of:"^claude[-\\s]+",with:"",options:[.regularExpression,.caseInsensitive])
    }
    private func rebuild() {
        rows = ModelPricing.shared.catalogRows.filter {$0.raw.string("group") == "claude"}
        ids = [:]; labels = [:]
        for row in ModelPricing.shared.catalogRows {
            ids[Self.id(row.model)] = row.model
            for alias in row.raw["aliases"] as? [String] ?? [] {ids[Self.id(alias)] = row.model}
            let name = row.raw.string("display_name")
            labels[Self.label(name)] = row.model
            labels[Self.label(name.replacingOccurrences(of:"Claude ",with:""))] = row.model
        }
        version = identity(["catalog":ModelPricing.shared.version,"aliases":aliases] as Object)
        updated = ModelPricing.shared.updated
    }
    /// Learn bindings only for future records. A current label never rewrites old history.
    func observeLabels(root: URL) throws {
        let path = root.appendingPathComponent("settings.json"), stamp = FileStamp(path)
        guard settingsStamp != stamp else { return }; settingsStamp = stamp
        let settings = readObject(path), now = Date().timeIntervalSince1970, scope = identity(root.path)
        var changed = false, current: [String:String] = [:]
        for option in settings.object("modelPicker").objects("options") {
            let raw = option.string("model")
            guard !raw.isEmpty, raw.count <= 256 else {continue}
            current[scope+":"+raw] = ModelPricing.shared.model(option.string("label")) ?? labels[Self.label(option.string("label"))] ?? ""
        }
        // A label change starts a new binding; older calls retain the old one.
        for key in Set(current.keys).union(aliases.keys.filter {$0.hasPrefix(scope+":")}) {
            let saved = aliases.object(key)
            var history = saved.objects("history")
            if history.isEmpty, saved.number("since") != nil {history = [saved]}
            let model = current[key] ?? "", previous = history.last?.string("model") ?? ""
            guard model != previous, now > (history.last?.number("since") ?? 0), aliases[key] != nil || aliases.count < 2000 else {continue}
            // Bound pathological config churn without repricing old calls or
            // keeping an obsolete binding active forever.
            let next = history.count < 128 ? model : ""
            guard next != previous else {continue}
            history.append(["model":next,"since":now])
            aliases[key] = ["history":history]; changed = true
        }
        if changed {try atomicJSON(aliases,to:directory.appendingPathComponent("aliases.json")); rebuild()}
    }
    private func resolve(_ raw: Object,root: URL) -> (String,Bool)? {
        let returned = raw.string("upstream_model")
        guard !returned.isEmpty else {return nil}
        if let model = ModelPricing.shared.model(returned) {return (model,false)}
        // An alias must identify this returned model, never a different request.
        let saved = aliases.object(identity(root.path)+":"+returned)
        let history = saved.objects("history").isEmpty ? [saved] : saved.objects("history")
        if let time = parsedDate(raw["timestamp"])?.timeIntervalSince1970,
           let binding = history.last(where:{($0.number("since") ?? .infinity) <= time}), ids[binding.string("model")] != nil {
            return (binding.string("model"),true)
        }
        return nil
    }
    func price(_ input: Object,root: URL) -> Object {
        var raw = ModelPricing.shared.price(input,resolved:resolve(input,root:root),returnedOnly:true)
        raw["price_version"] = version
        return raw
    }
    func setOverride(model: String,rates: Object?) throws {
        throw AppFailure(L("官方价格目录为只读", "The official price catalog is read-only"))
    }
    func sync(force: Bool = false) throws {try archive()}
    private func archive() throws {
        let file = directory.appendingPathComponent("versions/"+version+".json")
        if !FileManager.default.fileExists(atPath:file.path) {try atomicJSON(["rows":rows.map(\.raw),"aliases":aliases,"rule_version":seed.string("rule_version")],to:file)}
    }
}
