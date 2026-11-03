import Foundation

/// Request names are evidence, separate from transport IDs and price aliases.
/// Reuses FileStamp and atomicJSON: unchanged catalogs are never decoded again.
/// A newly observed picker cannot name requests from before that observation.
final class RequestModelNames {
    private let file: URL
    private var sources: Object
    private var stamps: [String:FileStamp] = [:]
    private var confirmed: [String:Double] = [:]
    init(directory: URL,client: String) {
        file = directory.appendingPathComponent("request-model-names-"+client+".json")
        sources = readObject(file)
    }
    static func modelID(_ value: String) -> String {
        value.trimmingCharacters(in:.whitespacesAndNewlines).replacingOccurrences(of:"[1m]",with:"",options:.caseInsensitive)
    }
    static func explicit(_ source: Object,to target: inout Object) {
        let name = source.string("model_display_name",source.string("modelDisplayName")).trimmingCharacters(in:.whitespacesAndNewlines)
        guard !name.isEmpty,name.count <= 512 else {return}
        target["model_display_name"] = name;target["model_display_id"] = target["model"]
        target["model_display_source"] = "request"
    }
    static func copy(_ source: Object,to target: inout Object,matchingModel: Bool = true) {
        guard !source.string("model_display_name").isEmpty,
              (!matchingModel || modelID(source.string("model_display_id",source.string("model"))) == modelID(target.string("model"))) else {return}
        for key in ["model_display_name","model_display_id","model_display_source"] {if let value = source[key] {target[key] = value}}
    }
    func observe(root: URL,client: String) throws {
        let path = root.appendingPathComponent(client == "claude_code" ? "settings.json":"models_cache.json")
        let key = identity(root.standardizedFileURL.path), stamp = FileStamp(path), now = Date().timeIntervalSince1970
        if stamps[key] == stamp {confirmed[key] = now;return}
        var labels: [String:String] = [:], ambiguous = Set<String>()
        if stamp.exists {
            guard stamp.size <= 4_194_304, let data = try? Data(contentsOf:path),
                  let raw = (try? JSONSerialization.jsonObject(with:data)) as? Object else {return}
            let options = client == "claude_code" ? raw.object("modelPicker").objects("options"):raw.objects("models")
            for option in options.prefix(2000) {
                let id = Self.modelID(option.string(client == "claude_code" ? "model":"slug"))
                let name = option.string(client == "claude_code" ? "label":"display_name").trimmingCharacters(in:.whitespacesAndNewlines)
                guard !id.isEmpty,id.count <= 512,!name.isEmpty,name.count <= 512,!ambiguous.contains(id) else {continue}
                if let previous = labels[id],previous != name {labels.removeValue(forKey:id);ambiguous.insert(id)}
                else {labels[id] = name}
            }
        }
        let signature = identity(labels)
        var saved = sources.object(key), history = saved.objects("history")
        if saved.string("signature") != signature {
            if !history.isEmpty {history[history.count-1]["until"] = confirmed[key] ?? saved.number("observed_at") ?? now}
            history.append(["since":now,"labels":labels])
            saved = ["signature":signature,"observed_at":now,"history":Array(history.suffix(128))]
            guard sources[key] != nil || sources.count < 64 else {return}
            sources[key] = saved
            try atomicJSON(sources,to:file)
        }
        stamps[key] = stamp;confirmed[key] = now
    }
    func capture(_ row: inout Object,root: URL,client: String,at: Any? = nil) {
        guard row.string("model_display_name").isEmpty,
              let time = parsedDate(at ?? row["started_at"] ?? row["timestamp"])?.timeIntervalSince1970 else {return}
        let key = identity(root.standardizedFileURL.path), id = Self.modelID(row.string("model"))
        guard !id.isEmpty,time <= (confirmed[key] ?? 0),
              let point = sources.object(key).objects("history").last(where:{($0.number("since") ?? .infinity) <= time && time <= ($0.number("until") ?? .infinity)}) else {return}
        let name = point.object("labels").string(id)
        if !name.isEmpty {
            row["model_display_name"] = name;row["model_display_id"] = row["model"]
            row["model_display_source"] = client == "claude_code" ? "model_picker":"codex_catalog"
        }
    }
}
