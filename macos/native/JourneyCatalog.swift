import Foundation

// Source discovery mirrors UsageIndexer title discovery. Project ownership is
// explicit Codex metadata; distinct projectless working directories stay Chats.
final class JourneyCatalog {
    private let objects = ObjectFileCache()
    private var cached: [String:([FileStamp],Object)] = [:]
    static func database(_ root: URL,prefix: String) -> URL? {
        (try? FileManager.default.contentsOfDirectory(at:root,includingPropertiesForKeys:nil))?
            .filter {$0.lastPathComponent.hasPrefix(prefix) && $0.pathExtension == "sqlite"}
            .sorted {$0.lastPathComponent.compare($1.lastPathComponent,options:.numeric) == .orderedDescending}.first
    }
    func load(roots: [URL],project: String,before: String = "") throws -> Object {
        let databases = roots.compactMap { root -> (URL,URL)? in Self.database(root,prefix:"state_").map {(root,$0)} }
        let inputs = databases.flatMap { [FileStamp($0.1),FileStamp(URL(fileURLWithPath:$0.1.path+"-wal")),FileStamp($0.0.appendingPathComponent(".codex-global-state.json"))] }
        let key = roots.map(\.path).joined(separator:"|")+"|"+project+"|"+before
        if let entry = cached[key],entry.0 == inputs { return entry.1 }
        let cursor = jsonObject(Data(before.utf8)), stamp = cursor.integer("stamp") ?? Int.max, last = cursor.string("id","~")
        var projects: [String:String] = ["chats":L("聊天", "Chats")], threads: [Object] = []
        for (root,file) in databases {
            let global = objects.read(root.appendingPathComponent(".codex-global-state.json"))
            for (id,value) in global.object("local-projects") { if let row = value as? Object { projects[id] = row.string("name","—") } }
            let assignments = global.object("thread-project-assignments"), projectless = Set(global["projectless-thread-ids"] as? [String] ?? [])
            let db = try Database(file,readOnly:true)
            let columns = Set(try db.query("PRAGMA table_info(threads)").map {$0.string("name")})
            let time = columns.contains("recency_at_ms") ? "coalesce(recency_at_ms,updated_at_ms,updated_at*1000)" : columns.contains("recency_at") ? "coalesce(recency_at,updated_at)*1000" : "updated_at*1000"
            let name = columns.contains("name") ? "coalesce(nullif(name,''),title,'')" : "coalesce(title,'')"
            var visibleProjects:Object=[:]
            for id in global.object("local-projects").keys {visibleProjects[id]=id}
            for value in global.object("app-server-project-id-by-legacy-project-id-by-host").values {
                for (legacy,server) in value as? Object ?? [:] where visibleProjects[legacy] != nil {if let server=server as? String {visibleProjects[server]=legacy}}
            }
            let stored = columns.contains("project_id") ? "json_extract(?,'$.\"'||project_id||'\"')" : "NULL"
            var mapped: Object = [:]
            for (id,value) in assignments { if let row=value as? Object,let known=visibleProjects[row.string("projectId")] {mapped[id] = known} }
            for id in projectless where mapped[id]==nil { mapped[id] = "chats" }
            let owner = "coalesce(json_extract(?,'$.\"'||id||'\"'),\(stored),'chats')"
            var args: [Any] = [jsonString(mapped)]
            if columns.contains("project_id") {args.append(jsonString(visibleProjects))}
            args += [stamp,stamp,last]
            let filter = project.isEmpty ? "" : " AND project_key=?"
            if !project.isEmpty {args.append(project)}
            let sql = "SELECT * FROM (SELECT id,\(name) AS title,\(time) AS stamp,rollout_path,\(owner) AS project_key FROM threads WHERE archived=0 AND instr(source,'subagent')=0) WHERE (stamp<? OR (stamp=? AND id<?))\(filter) ORDER BY stamp DESC,id DESC LIMIT 51"
            for row in try db.query(sql,args) {
                var row=row;row["root"]=root.path;row["project_id"]=row["project_key"];row["title"]=requestValueText(requestPreview(row.string("title")))
                threads.append(row)
            }
        }
        threads.sort { ($0.integer("stamp") ?? 0,$0.string("id")) > ($1.integer("stamp") ?? 0,$1.string("id")) }
        var seen=Set<String>();threads=threads.filter {seen.insert($0.string("id")).inserted}
        let more=threads.count>50;threads=Array(threads.prefix(50))
        let next=more ? threads.last.map {jsonString(["stamp":$0["stamp"] ?? 0,"id":$0.string("id")])} ?? "" : ""
        let result: Object=["projects":projects.sorted {$0.value.localizedStandardCompare($1.value) == .orderedAscending}.map {["id":$0.key,"name":$0.value]},"threads":threads,"next":next]
        if cached.count>=6 {cached.removeAll(keepingCapacity:true)};cached[key]=(inputs,result)
        return result
    }
}
