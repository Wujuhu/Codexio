import Foundation

/// Claude prices are API reference prices. Speed rules come from the Code docs,
/// and never become a claimed multiplier for subscription allowance percentages.
final class ClaudePricingCatalog {
    let directory: URL
    private let seed: Object
    private var cache: Object
    private var overrides: Object
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
        cache = readObject(self.directory.appendingPathComponent("cache.json"))
        overrides = readObject(self.directory.appendingPathComponent("overrides.json"))
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
        let cached = cache.objects("rows")
        rows = seed.objects("rows").map { original in
            var row = original
            if let saved = cached.first(where:{$0.string("model") == original.string("model") && $0.integer("threshold") == original.integer("threshold")}) {
                for field in ["input","cache_read","cache_write","cache_write_1h","output"] {row[field] = saved[field] ?? original[field]}
            }
            if row.integer("threshold") == 0, let manual = overrides[row.string("model")] as? Object {
                for field in ["input","cache_read","cache_write","cache_write_1h","output"] {row[field] = manual[field]}
                row["locked"] = true
            }
            return PriceRow(raw:row)
        }
        ids = [:]; labels = [:]
        for row in rows {
            ids[Self.id(row.model)] = row.model
            for alias in row.raw["aliases"] as? [String] ?? [] {ids[Self.id(alias)] = row.model}
            let name = row.raw.string("display_name")
            labels[Self.label(name)] = row.model
            labels[Self.label(name.replacingOccurrences(of:"Claude ",with:""))] = row.model
        }
        let fields = ["model","aliases","threshold","input","cache_read","cache_write","cache_write_1h","output","fast_multiplier"]
        let content = rows.map {row in Dictionary(uniqueKeysWithValues:fields.map {($0,row.raw[$0] ?? NSNull())})}
        version = identity(["rule":seed.string("rule_version"),"rows":content,"aliases":aliases] as Object)
        updated = parsedDate(cache["updated_at"])
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
            current[scope+":"+raw] = labels[Self.label(option.string("label"))] ?? ""
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
        if let model = ids[Self.id(returned)] {return (model,false)}
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
        var raw = input
        raw["cost_usd"] = NSNull(); raw["pricing_status"] = "unpriced"; raw["price_version"] = version
        raw["pricing_basis"] = "official_api_reference"
        for field in ["price_rates","pricing_mode"] {raw.removeValue(forKey:field)}
        guard !raw.flag("usage_conflict"), let model = resolve(raw,root:root),
              let ordinary = raw.integer("raw_input_tokens"), let read = raw.integer("cached_input_tokens"),
              let write = raw.integer("cache_write_input_tokens"), let output = raw.integer("output_tokens"),
              [ordinary,read,write,output].allSatisfy({$0 >= 0}), ordinary <= Int.max-read, ordinary+read <= Int.max-write else {return raw}
        let totalInput = ordinary+read+write
        guard var rates = rows.filter({$0.model == model.0 && (($0.raw.integer("threshold") ?? 0) == 0 || totalInput > ($0.raw.integer("threshold") ?? 0))}).max(by:{($0.raw.integer("threshold") ?? 0)<($1.raw.integer("threshold") ?? 0)})?.raw else {return raw}
        let tier = raw.string("service_tier").lowercased(), fast = ["priority","fast"].contains(tier)
        var estimated = model.1 || tier.isEmpty
        if !tier.isEmpty && !["priority","fast","default","standard"].contains(tier) {return raw}
        if fast {
            guard let multiplier = rates.number("fast_multiplier") else {return raw}
            for field in ["input","cache_read","cache_write","cache_write_1h","output"] {rates[field] = rates.number(field).map {$0*multiplier}}
            raw["pricing_mode"] = "credits"
        }
        var five = raw.integer("cache_write_5m_tokens"), hour = raw.integer("cache_write_1h_tokens")
        if write == 0 {five = 0; hour = 0}
        if raw["cache_write_5m_tokens"] is NSNumber && five == nil || raw["cache_write_1h_tokens"] is NSNumber && hour == nil {return raw}
        if five == nil || hour == nil {five = write; hour = 0; estimated = true}
        guard let five, let hour, five >= 0, hour >= 0, five <= Int.max-hour, five+hour == write else {return raw}
        let counts = [ordinary,read,five,hour,output]
        let prices = ["input","cache_read","cache_write","cache_write_1h","output"].map {rates.number($0)}
        guard zip(counts,prices).allSatisfy({$0.0 == 0 || $0.1 != nil}) else {return raw}
        let cost = zip(counts,prices).reduce(0.0) {$0+Double($1.0)*($1.1 ?? 0)/1e6}
        guard cost.isFinite, cost >= 0 else {return raw}
        raw["cost_usd"] = cost
        raw["pricing_status"] = estimated ? "estimated" : "priced"; raw["price_rates"] = rates
        return raw
    }
    func setOverride(model: String,rates: Object?) throws {
        if let rates {
            guard rows.contains(where:{$0.model == model}), ["input","cache_read","cache_write","cache_write_1h","output"].allSatisfy({key in rates.number(key).map {$0.isFinite && $0 >= 0} ?? false}) else {throw AppFailure(L("请输入有限的非负单价", "Enter finite, nonnegative prices"))}
            overrides[model] = rates
        } else {overrides.removeValue(forKey:model)}
        try atomicJSON(overrides,to:directory.appendingPathComponent("overrides.json")); rebuild(); try archive()
    }
    func sync(force: Bool = false) throws {
        if !force, let checked = parsedDate(cache["checked_at"]), Date().timeIntervalSince(checked) < (cache.flag("failed") ? 3600 : 86400) {return}
        do {
            let data = try HTTP.read(URL(string:Self.source+".md")!,maximum:2_000_000)
            guard let text = String(data:data,encoding:.utf8), let table = text.components(separatedBy:"\n\n").first(where:{$0.contains("| Model") && $0.contains("5m cache writes")}) else {throw AppFailure(L("价格目录无有效内容", "No valid prices in the catalog"))}
            let regex = try NSRegularExpression(pattern:#"\$([0-9]+(?:\.[0-9]+)?)"#)
            var fetched = seed.objects("rows")
            for index in fetched.indices {
                let row = fetched[index], name = row.string("display_name")
                guard let line = table.components(separatedBy:"\n").first(where:{line in
                    guard line.hasPrefix("| "+name), line.dropFirst(name.count+2).first.map({$0 == " " || $0 == "|"}) == true else {return false}
                    return row.string("model") != "claude-haiku-5-5" || line.contains((row.integer("threshold") ?? 0) > 0 ? "over 100,000" : "up to 100,000")
                }) else {throw AppFailure(L("价格目录缺少已有型号，保留已有价格", "The price catalog is missing a model; saved prices were retained"))}
                let values = regex.matches(in:line,range:NSRange(line.startIndex...,in:line)).compactMap {Range($0.range(at:1),in:line).flatMap {Double(line[$0])}}
                guard values.count == 5, values.allSatisfy({$0.isFinite && $0 >= 0}) else {throw AppFailure(L("价格目录无有效内容", "No valid prices in the catalog"))}
                for (key,value) in zip(["input","cache_write","cache_write_1h","cache_read","output"],values) {fetched[index][key] = value}
            }
            cache = ["rows":fetched,"updated_at":iso(),"checked_at":iso(),"failed":false]; warning = nil
            try atomicJSON(cache,to:directory.appendingPathComponent("cache.json")); rebuild(); try archive()
        } catch {
            cache["checked_at"] = iso(); cache["failed"] = true; warning = L("价格同步失败，保留已有价格", "Price sync failed; saved prices are retained")
            try? atomicJSON(cache,to:directory.appendingPathComponent("cache.json")); throw error
        }
    }
    private func archive() throws {
        let file = directory.appendingPathComponent("versions/"+version+".json")
        if !FileManager.default.fileExists(atPath:file.path) {try atomicJSON(["rows":rows.map(\.raw),"aliases":aliases,"rule_version":seed.string("rule_version")],to:file)}
    }
}
