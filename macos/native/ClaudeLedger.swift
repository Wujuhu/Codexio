import Foundation

// Client identity belongs to the source, never to the returned model/provider.
// Legacy Codex rows stay in their existing ledger and consumers.
enum DesktopClient: String, CaseIterable {
    case codex, claudeCode = "claude_code"
    var title: String { self == .codex ? "ChatGPT / Codex" : "Claude Code" }
    var logo: String { self == .codex ? "chatgpt" : "claude-code" }
}

/// Read-only source adapter, owned by AppState.dataQueue. Reuses FileStamp,
/// Database transactions, UsageIndexer checkpoints and directory invalidation.
/// Full transcript bodies never enter the durable normalized ledger.
final class ClaudeLedger {
    let database: Database
    let pricing: ClaudePricingCatalog
    let root: URL
    private let cancellationLock = NSLock()
    private var cancelled = false
    private var scanned: [String:FileStamp] = [:]
    private var directories: [URL:FileStamp] = [:]
    private var files: [URL] = []
    private var discoveredAt: TimeInterval = -.infinity
    private(set) var warning: String?
    let scope: String
    private var affected = Set<String>()
    private var affectedSessions = Set<String>()
    private var changedFiles = false
    private var ownershipNeedsRepair = false
    private var sourceGeneration = 0
    private var sourceStamp: FileStamp?
    private var sourceLineHash = ""
    private var contextIndexReady = false
    private(set) var surfaceRevision = 0
    private var surfacePrepared = false
    init(paths: AppPaths, pricing: ClaudePricingCatalog) throws {
        self.pricing = pricing
        // Mock paths never consult CLAUDE_CONFIG_DIR or the real home directory.
        root = paths.mock ? paths.data.appendingPathComponent("claude-mock") : URL(fileURLWithPath:NSString(string:ProcessInfo.processInfo.environment["CLAUDE_CONFIG_DIR"] ?? FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".claude").path).expandingTildeInPath).standardizedFileURL
        scope = "claude_code:"+String(identity(root.path).prefix(16))+":"
        database = try Database(paths.data.appendingPathComponent("claude-code.sqlite"))
        try database.script("""
        CREATE TABLE IF NOT EXISTS claude_context_windows(session_id TEXT NOT NULL,message_uuid TEXT NOT NULL,prompt_id TEXT NOT NULL,observed_at REAL NOT NULL,window INTEGER NOT NULL,PRIMARY KEY(session_id,message_uuid,prompt_id));
        CREATE TABLE IF NOT EXISTS claude_sources(path TEXT PRIMARY KEY,present INTEGER NOT NULL,data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS claude_nodes(id TEXT PRIMARY KEY,request_id TEXT NOT NULL,session_id TEXT NOT NULL,parent TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS claude_calls(id TEXT PRIMARY KEY,request_id TEXT NOT NULL,session_id TEXT NOT NULL,timestamp REAL,model TEXT NOT NULL,input INTEGER,cache_read INTEGER,cache_write INTEGER,output INTEGER,total INTEGER,data TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS claude_calls_time ON claude_calls(timestamp DESC);
        CREATE INDEX IF NOT EXISTS claude_calls_request ON claude_calls(request_id);
        CREATE INDEX IF NOT EXISTS claude_calls_session ON claude_calls(session_id);
        CREATE INDEX IF NOT EXISTS claude_timing_pending ON claude_calls(id) WHERE COALESCE(json_extract(data,'$.call_timing_checked'),0)=0 AND COALESCE(json_extract(data,'$.is_subagent'),0)=0;
        CREATE TABLE IF NOT EXISTS claude_requests(id TEXT PRIMARY KEY,session_id TEXT NOT NULL,timestamp REAL,model TEXT NOT NULL,status TEXT NOT NULL,input INTEGER,cache_read INTEGER,cache_write INTEGER,output INTEGER,total INTEGER,data TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS claude_requests_time ON claude_requests(timestamp DESC);
        CREATE INDEX IF NOT EXISTS claude_requests_session ON claude_requests(session_id);
        CREATE TABLE IF NOT EXISTS claude_sessions(id TEXT PRIMARY KEY,title TEXT NOT NULL,data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS claude_locations(id TEXT NOT NULL,path TEXT NOT NULL,offset INTEGER NOT NULL,length INTEGER NOT NULL,uuid TEXT NOT NULL,PRIMARY KEY(id,path,offset));
        CREATE INDEX IF NOT EXISTS claude_locations_id ON claude_locations(id);
        CREATE TABLE IF NOT EXISTS claude_ranking(id TEXT PRIMARY KEY,tokens INTEGER,data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS claude_search(id TEXT NOT NULL,path TEXT NOT NULL,record_id TEXT NOT NULL,request_id TEXT NOT NULL,text TEXT NOT NULL,PRIMARY KEY(id,path));
        CREATE INDEX IF NOT EXISTS claude_search_record ON claude_search(record_id);
        CREATE INDEX IF NOT EXISTS claude_search_request ON claude_search(request_id);
        CREATE INDEX IF NOT EXISTS claude_nodes_parent ON claude_nodes(parent);
        CREATE TABLE IF NOT EXISTS claude_request_hints(id TEXT PRIMARY KEY,request_id TEXT NOT NULL,session_id TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS claude_events(id TEXT PRIMARY KEY,request_id TEXT NOT NULL,session_id TEXT NOT NULL,data TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS claude_events_request ON claude_events(request_id);
        INSERT OR IGNORE INTO usage_revisions VALUES('claude',0);
        """)
        let locationColumns = Set(try database.query("PRAGMA table_info(claude_locations)").map {$0.string("name")})
        for (column,type) in [("generation","INTEGER NOT NULL DEFAULT 0"),("line_hash","TEXT NOT NULL DEFAULT ''"),("device","TEXT NOT NULL DEFAULT ''"),("inode","TEXT NOT NULL DEFAULT ''")] where !locationColumns.contains(column) {
            try database.script("ALTER TABLE claude_locations ADD COLUMN \(column) \(type)")
        }
        let contextColumns = Set(try database.query("PRAGMA table_info(claude_context_windows)").map {$0.string("name")})
        for column in ["model","reasoning_effort"] where !contextColumns.contains(column) {
            try database.script("ALTER TABLE claude_context_windows ADD COLUMN \(column) TEXT NOT NULL DEFAULT ''")
        }
        for table in ["claude_calls","claude_requests","claude_sessions","claude_locations","claude_search"] {
            for action in ["INSERT","UPDATE","DELETE"] {
                try database.script("CREATE TRIGGER IF NOT EXISTS revision_\(table)_\(action.lowercased()) AFTER \(action) ON \(table) BEGIN UPDATE usage_revisions SET revision=revision+1 WHERE kind='claude'; END;")
            }
        }
        try database.script("""
        DROP TRIGGER IF EXISTS revision_claude_sources_update;
        CREATE TRIGGER IF NOT EXISTS revision_claude_sources_insert AFTER INSERT ON claude_sources BEGIN UPDATE usage_revisions SET revision=revision+1 WHERE kind='claude'; END;
        CREATE TRIGGER revision_claude_sources_update AFTER UPDATE ON claude_sources WHEN NEW.present<>OLD.present OR json_extract(NEW.data,'$.inode')<>json_extract(OLD.data,'$.inode') OR COALESCE(json_extract(NEW.data,'$.device'),'')<>COALESCE(json_extract(OLD.data,'$.device'),'') OR COALESCE(json_extract(NEW.data,'$.generation'),0)<>COALESCE(json_extract(OLD.data,'$.generation'),0) BEGIN UPDATE usage_revisions SET revision=revision+1 WHERE kind='claude'; END;
        """)
    }
    func cancel() { cancellationLock.lock(); cancelled = true; cancellationLock.unlock() }
    func resume() { cancellationLock.lock(); cancelled = false; cancellationLock.unlock() }
    private var isCancelled: Bool { cancellationLock.lock(); defer { cancellationLock.unlock() }; return cancelled }
    private func namespaced(_ id: String) -> String { id.isEmpty ? "" : scope+id }
    var revision: Int { (try? database.query("SELECT revision FROM usage_revisions WHERE kind='claude'").first?.integer("revision")) ?? 0 }
    private func discover() -> [URL] {
        let projects = root.appendingPathComponent("projects"), now = ProcessInfo.processInfo.systemUptime
        if now-discoveredAt < 60, !directories.isEmpty, directories.allSatisfy({FileStamp($0.key) == $0.value}) { return files }
        var dirs = Set([projects]), found: [URL] = []
        if let iterator = FileManager.default.enumerator(at:projects,includingPropertiesForKeys:[.isDirectoryKey,.isSymbolicLinkKey],options:[.skipsHiddenFiles],errorHandler:{[weak self] _,_ in
            self?.warning = L("部分 Claude 记录暂不可读，已保存的历史保留", "Some Claude records are temporarily unreadable; saved history is retained")
            return true
        }) {
            for case let file as URL in iterator {
                if isCancelled { break }
                let info = try? file.resourceValues(forKeys:[.isDirectoryKey,.isSymbolicLinkKey])
                if info?.isSymbolicLink == true { iterator.skipDescendants(); continue }
                if info?.isDirectory == true { dirs.insert(file) }
                else if file.pathExtension == "jsonl" { found.append(file) }
            }
        }
        files = found.sorted {$0.path < $1.path}; directories = Dictionary(uniqueKeysWithValues:dirs.map {($0,FileStamp($0))}); discoveredAt = now
        return files
    }
    func scan() throws {
        guard !isCancelled else {return}
        changedFiles = false
        try pricing.observeLabels(root:root)
        do {
            _ = try FileManager.default.attributesOfItem(atPath:root.appendingPathComponent("projects").path); warning = nil
        } catch let error as NSError {
            warning = error.domain == NSCocoaErrorDomain && [NSFileNoSuchFileError,NSFileReadNoSuchFileError].contains(error.code)
                ? L("未找到本机 Claude Code 来源", "No local Claude Code source found")
                : L("Claude 来源暂不可读，已保存的历史保留", "The Claude source is temporarily unreadable; saved history is retained")
        }
        let available = discover()
        for file in available {
            if isCancelled { return }
            do { try scanFile(file) }
            catch { warning = L("部分 Claude 数据导入失败，已保存的历史保留", "Some Claude data could not be imported; saved history is retained")+"："+error.localizedDescription }
        }
        // A failed read is not deletion. Confirm ENOENT before marking absent.
        for row in try database.query("SELECT path FROM claude_sources WHERE present=1") where !FileStamp(URL(fileURLWithPath:row.string("path"))).exists {
            let path = row.string("path")
            do { _ = try FileManager.default.attributesOfItem(atPath:path) }
            catch let error as NSError {
                guard error.domain == NSCocoaErrorDomain && [NSFileNoSuchFileError,NSFileReadNoSuchFileError].contains(error.code) else { continue }
                try database.transaction {
                    try database.run("UPDATE claude_sources SET present=0 WHERE path=? AND present<>0",[path])
                    try database.run("DELETE FROM claude_search WHERE path=?",[path])
                }; scanned.removeValue(forKey:path)
            }
        }
        if changedFiles || ownershipNeedsRepair {ownershipNeedsRepair = try repairOwnership()}
        try recoverCallTiming()
        try reprice()
        try materializeAffected()
    }
    func refreshPricing() throws {try reprice(); try materializeAffected()}
    private func materializeAffected() throws {
        if !affected.isEmpty || !affectedSessions.isEmpty {
            try database.transaction {
                for id in affected { try materializeRequest(id) }
                for id in affectedSessions { try materializeRanking(id) }
            }
            affected.removeAll(keepingCapacity:true); affectedSessions.removeAll(keepingCapacity:true)
        }
    }
    private func scanFile(_ file: URL) throws {
        let stamp = FileStamp(file)
        guard stamp.exists, scanned[file.path] != stamp else { return }
        changedFiles = true
        let key = "claude-v1:"+file.path, cursor = database.object("usage_cursors",key:key)
        var offset = cursor.integer("offset") ?? 0
        var state = cursor.object("state")
        let handle = try FileHandle(forReadingFrom:file); defer {try? handle.close()}
        let source = try database.query("SELECT data FROM claude_sources WHERE path=?",[file.path]).first.map {jsonObject(Data($0.string("data").utf8))} ?? [:]
        sourceGeneration = source.integer("generation") ?? 0; sourceStamp = stamp
        if !source.isEmpty, let previous = try database.query("SELECT present FROM claude_sources WHERE path=?",[file.path]).first, previous.integer("present") != 1 {
            try database.run("UPDATE claude_calls SET data=json_set(data,'$.call_timing_checked',json('false')) WHERE json_extract(data,'$.call_timing_incomplete')=1 AND id IN(SELECT id FROM claude_locations WHERE path=?)",[file.path])
        }
        let priorSize = cursor.integer("file_size") ?? source.integer("size")
        let priorModified = cursor.number("modified") ?? source.number("modified")
        let inPlaceRewrite = priorSize == Int(stamp.size) && priorModified != stamp.modified?.timeIntervalSince1970
        let priorDevice = cursor.string("device",source.string("device"))
        var valid = (priorDevice.isEmpty || priorDevice == String(stamp.device)) && !inPlaceRewrite && cursor.integer("parser") == 1 && cursor.number("inode") == Double(stamp.inode) && offset <= Int(stamp.size)
        if valid && offset > 0 {
            try handle.seek(toOffset:UInt64(max(0,offset-4096)))
            valid = digest(try handle.read(upToCount:min(4096,offset)) ?? Data()) == cursor.string("tail_hash")
        }
        if !valid {
            offset = 0; state = [:]; sourceGeneration += 1
            try database.run("DELETE FROM claude_search WHERE path=?",[file.path])
        }
        // Publish generation before parsing so a canceled/failed source repair
        // still invalidates open originals; append growth retains generation.
        try publishSource(file,stamp:stamp)
        try handle.seek(toOffset:UInt64(offset))
        var pending = Data(), dropping = false
        while !isCancelled {
            let chunk = try autoreleasepool {try handle.read(upToCount:1_048_576)} ?? Data()
            if chunk.isEmpty { break }; pending.append(chunk)
            try database.transaction {
                while let newline = pending.firstIndex(of:10) {
                    let length = newline+1
                    if !dropping {
                        try autoreleasepool {
                            let line = pending.subdata(in:0..<length)
                            sourceLineHash = digest(line)
                            let row = jsonObject(line)
                            if !row.isEmpty { try ingest(row,file:file,offset:offset,length:length,state:&state) }
                        }
                    }
                    dropping = false; pending.removeSubrange(0...newline); offset += length
                }
                // Do not retain arbitrarily large partial records or decode them.
                if pending.count > 16_000_000 { offset += pending.count; pending.removeAll(); dropping = true }
                if !dropping { try checkpoint(handle,file:file,key:key,offset:offset,state:state,stamp:stamp) }
            }
        }
        if !dropping { try database.transaction {try checkpoint(handle,file:file,key:key,offset:offset,state:state,stamp:stamp)} }
        scanned[file.path] = stamp
    }
    private func checkpoint(_ handle: FileHandle,file: URL,key: String,offset: Int,state: Object,stamp: FileStamp) throws {
        let position = try handle.offset()
        try handle.seek(toOffset:UInt64(max(0,offset-4096)))
        let tail = digest(try handle.read(upToCount:min(4096,offset)) ?? Data())
        try handle.seek(toOffset:position)
        try database.put("usage_cursors",key:key,value:["parser":1,"offset":offset,"inode":Double(stamp.inode),"device":String(stamp.device),"tail_hash":tail,"state":state,"file_size":Double(stamp.size),"modified":stamp.modified?.timeIntervalSince1970 as Any? ?? NSNull()])
        try publishSource(file,stamp:stamp)
    }
    private func publishSource(_ file: URL,stamp: FileStamp) throws {
        let payload = jsonString(["inode":String(stamp.inode),"device":String(stamp.device),"size":Double(stamp.size),"modified":stamp.modified?.timeIntervalSince1970 as Any? ?? NSNull(),"generation":sourceGeneration])
        try database.run("INSERT INTO claude_sources VALUES(?,1,?) ON CONFLICT(path) DO UPDATE SET present=1,data=excluded.data WHERE present<>1 OR data<>excluded.data",[file.path,payload])
    }
    private func canonicalContext(_ id: String) throws -> (session: String,request: String)? {
        guard !id.isEmpty else {return nil}
        var cursor = id, session = "", seen = Set<String>()
        for _ in 0..<64 {
            guard seen.insert(cursor).inserted, let node = try database.query("SELECT session_id,request_id,parent FROM claude_nodes WHERE id=?",[cursor]).first else {break}
            if session.isEmpty {session = node.string("session_id")}
            if !node.string("request_id").isEmpty {
                let owner = try database.query("SELECT session_id FROM claude_requests WHERE id=?",[node.string("request_id")]).first
                return (owner?.string("session_id") ?? node.string("session_id"),node.string("request_id"))
            }
            cursor = node.string("parent"); if cursor.isEmpty {break}
        }
        return session.isEmpty ? nil : (session,"")
    }
    private func inheritedRequest(_ row: Object,state: Object) throws -> String {
        try canonicalContext(namespaced(row.string("parentUuid")))?.request ?? ""
    }
    private func text(_ content: Any?) -> String {
        if let value = content as? String { return value }
        return (content as? [Object] ?? []).filter {$0.string("type") == "text"}.map {$0.string("text")}.joined(separator:"\n")
    }
    private func location(_ id: String,_ uuid: String,file: URL,offset: Int,length: Int) throws {
        guard !id.isEmpty, let stamp = sourceStamp else { return }
        try database.run("INSERT INTO claude_locations(id,path,offset,length,uuid,generation,line_hash,device,inode) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id,path,offset) DO UPDATE SET length=excluded.length,uuid=excluded.uuid,generation=excluded.generation,line_hash=excluded.line_hash,device=excluded.device,inode=excluded.inode WHERE generation<>excluded.generation OR line_hash<>excluded.line_hash",[id,file.path,offset,length,uuid,sourceGeneration,sourceLineHash,String(stamp.device),String(stamp.inode)])
    }
    private func ingest(_ row: Object,file: URL,offset: Int,length: Int,state: inout Object) throws {
        let sessionRaw = row.string("sessionId",state.string("session"))
        var session = namespaced(sessionRaw)
        guard !session.isEmpty else { return }
        state["session"] = sessionRaw
        let entry = row.string("entrypoint",state.string("entrypoint")); if !entry.isEmpty {state["entrypoint"] = entry}
        let uuid = row.string("uuid"), nodeID = namespaced(uuid), type = row.string("type"), message = row.object("message")
        let child = file.pathComponents.contains("subagents") || row.flag("isSidechain") || !row.string("agentId").isEmpty
        let ownContext = try canonicalContext(nodeID), parentContext = try canonicalContext(namespaced(row.string("parentUuid")))
        let mainHuman = type == "user" && row.object("origin").string("kind") == "human" && !child && !row.flag("isMeta") && !row.flag("turnCompanion") && !(row["turnCompanion"] is Object) && row.string("turnCompanion").isEmpty && row.string("continuationOfPromptId").isEmpty
        if let ownContext {session = ownContext.session}
        else if !mainHuman, let parentContext {session = parentContext.session}
        let existingSession = try database.query("SELECT data,title FROM claude_sessions WHERE id=?",[session]).first
        var sessionInfo = jsonObject(Data((existingSession?.string("data") ?? "{}").utf8))
        if !entry.isEmpty { sessionInfo["client_origin"] = ["cli","claude-desktop-3p"].contains(entry) ? entry : NSNull() }
        if !row.string("version").isEmpty {sessionInfo["version"] = row.string("version")}
        if !row.string("cwd").isEmpty {sessionInfo["cwd"] = row.string("cwd")}
        for key in ["projectId","projectName"] where !row.string(key).isEmpty {sessionInfo[key] = row.string(key)}
        var title = existingSession?.string("title") ?? ""
        if type == "custom-title", !row.string("customTitle").isEmpty {title = String(row.string("customTitle").prefix(400))}
        try database.run("INSERT INTO claude_sessions VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET title=excluded.title,data=excluded.data WHERE title<>excluded.title OR data<>excluded.data",[session,title,jsonString(sessionInfo)])
        var searchRecord = ""
        var request = ownContext?.request ?? parentContext?.request ?? ""
        if request.isEmpty, !row.string("promptId").isEmpty {
            let candidate = namespaced("request:"+row.string("promptId"))
            if let owner = try database.query("SELECT session_id FROM claude_requests WHERE id=?",[candidate]).first, owner.string("session_id") == session { request = candidate }
        }
        if type == "custom-title" { affectedSessions.insert(session) }
        if type == "user" {
            let contents = message["content"], origin = row.object("origin").string("kind")
            let tool = (contents as? [Object] ?? []).contains {$0.string("type") == "tool_result"}
            let companionInfo = row.object("turnCompanion")
            let companion = row.flag("turnCompanion") || !row.string("turnCompanion").isEmpty || row["turnCompanion"] is Object || !row.string("continuationOfPromptId").isEmpty
            let companionPrompt = companionInfo.string("parentPromptId",companionInfo.string("promptId",row.string("continuationOfPromptId")))
            if companion && request.isEmpty && !companionPrompt.isEmpty {
                let candidate = namespaced("request:"+companionPrompt)
                if let owner = try database.query("SELECT session_id FROM claude_requests WHERE id=?",[candidate]).first, owner.string("session_id") == session {request = candidate}
            }
            let prompt = row.string("promptId")
            // Supplements share prompt identity; independent human messages have
            // their own UUID. Tool results/subagent launch inputs are not requests.
            let human = !child && !tool && !row.flag("isMeta") && origin == "human"
            if human && !companion, !uuid.isEmpty {
                let id = namespaced("request:"+(prompt.isEmpty ? uuid : prompt))
                request = id
                var raw = (try database.query("SELECT data FROM claude_requests WHERE id=?",[id]).first).map {jsonObject(Data($0.string("data").utf8))} ?? [:]
                if raw.isEmpty {
                    raw = ["id":id,"client_kind":"claude_code","session_id":session,"timestamp":row["timestamp"] ?? NSNull(),"source_id":"local","local_origin":true,"record_kind":"user_request","prompt_preview":String(requestPreview(text(contents)).prefix(400)),"main_uuid":uuid,"model":row.string("requestedModel"),"reasoning_effort":row.string("effort"),"service_tier":"","status":"","pricing_status":"unpriced"]
                }
                if raw.string("model").isEmpty {raw["model"] = row.string("requestedModel")}
                if raw.string("reasoning_effort").isEmpty {raw["reasoning_effort"] = row.string("effort")}
                try saveRequest(raw)
                searchRecord = id
                try location(id,uuid,file:file,offset:offset,length:length)
                affected.insert(id)
                state["last_request"] = id
            } else if human && companion && !request.isEmpty {
                try location(request,uuid,file:file,offset:offset,length:length); searchRecord = request
            } else if human && companion && request.isEmpty && !prompt.isEmpty {
                let candidate = namespaced("request:"+prompt)
                if !(try database.query("SELECT id FROM claude_requests WHERE id=?",[candidate])).isEmpty {request = candidate}
            }
        }
        if type == "assistant" {
            let synthetic = row.flag("isApiErrorMessage") || message.string("model") == "<synthetic>"
            if synthetic {
                if !child && row.string("agentId").isEmpty {
                    try recordEvent(row,kind:"failed",request:request,session:session,file:file,offset:offset,length:length)
                    if !request.isEmpty {try location(request,uuid,file:file,offset:offset,length:length); searchRecord = request}
                }
            } else if !message.object("usage").isEmpty, !message.string("id").isEmpty {
                let usage = message.object("usage")
                var raw: Object = ["client_kind":"claude_code","client_origin":sessionInfo["client_origin"] ?? NSNull(),"session_id":session,"source_id":"local","local_origin":true,"response_id":message.string("id"),"uuid":uuid,"parent_uuid":row.string("parentUuid"),"agent_id":row.string("agentId"),"request_id":request,"record_kind":request.isEmpty ? "unassigned" : "model_call","timestamp":row["timestamp"] ?? NSNull(),"model":row.string("requestedModel"),"upstream_model":message.string("model"),"upstream_mismatched":false,"reasoning_effort":row.string("effort"),"service_tier":"","is_subagent":child,"quality":"response","pricing_status":"unpriced","status":"","prompt_preview":""]
                let input = usage.integer("input_tokens"), read = usage.integer("cache_read_input_tokens"), write = usage.integer("cache_creation_input_tokens"), output = usage.integer("output_tokens")
                let creation = usage.object("cache_creation")
                raw["cache_write_5m_tokens"] = creation.integer("ephemeral_5m_input_tokens") as Any? ?? NSNull()
                raw["cache_write_1h_tokens"] = creation.integer("ephemeral_1h_input_tokens") as Any? ?? NSNull()
                let speed = usage.string("speed",row.string("speed")).lowercased()
                if speed == "fast" {raw["service_tier"] = "priority"}
                else if speed == "standard" {raw["service_tier"] = "default"}
                else if let fast = row["fastMode"] as? Bool ?? row["fast_mode"] as? Bool {raw["service_tier"] = fast ? "priority" : "default"}
                for key in ["auth_mode","billing_source"] where row[key] != nil {raw[key] = row[key]}
                raw["raw_input_tokens"] = input as Any? ?? NSNull(); raw["cached_input_tokens"] = read as Any? ?? NSNull(); raw["cache_write_input_tokens"] = write as Any? ?? NSNull(); raw["output_tokens"] = output as Any? ?? NSNull()
                if let input, let read, let write, input <= Int.max-read, input+read <= Int.max-write {
                    let completeInput = input+read+write; raw["input_tokens"] = completeInput
                    if let output, completeInput <= Int.max-output {raw["total_tokens"] = completeInput+output}
                }
                raw["reasoning_output_tokens"] = usage.integer("thinking_tokens") as Any? ?? usage.object("output_tokens_details").integer("thinking_tokens") as Any? ?? NSNull()
                let terminal = !message.string("stop_reason").isEmpty
                raw["call_timing"] = ClaudeTiming.observation(row)
                raw["call_timing_checked"] = true
                raw["terminal_usage"] = terminal
                raw["usage_evidence_terminal"] = terminal && raw.integer("total_tokens") != nil
                raw["usage_read_state"] = raw.integer("total_tokens") == nil ? "partial" : terminal ? "terminal" : "provisional"
                raw["status"] = terminal ? "completed" : ""
                raw["usage_evidence_timestamp"] = row["timestamp"] ?? NSNull()
                // Reply identity plus UUID chain protects gateways reusing IDs;
                // copied histories retain their UUID/request ancestry.
                let anchor = request.isEmpty ? session : request
                let id = namespaced("response:"+String(identity([message.string("id"),anchor,row.string("agentId")]).prefix(40)))
                raw["id"] = id
                if let previous = try database.query("SELECT data FROM claude_calls WHERE id=?",[id]).first {
                    let old = jsonObject(Data(previous.string("data").utf8))
                    affectedSessions.insert(old.string("session_id")); raw = mergeUsage(old,raw)
                }
                raw["id"] = id; raw["session_id"] = session; raw["request_id"] = request
                if !request.isEmpty {
                    let parent = try database.query("SELECT data FROM claude_requests WHERE id=?",[request]).first.map {jsonObject(Data($0.string("data").utf8))} ?? [:]
                    raw["prompt_preview"] = parent["prompt_preview"] ?? ""
                    // Main configuration can be supplied on its first assistant
                    // evidence, but a child never rewrites captured main effort.
                    if !child {
                        var fill: Object = [:]
                        for key in ["model","reasoning_effort"] where parent.string(key).isEmpty && !raw.string(key).isEmpty {fill[key] = raw[key]}
                        if !fill.isEmpty {try updateRequest(request,fields:fill)}
                    }
                }
                try saveCall(raw); searchRecord = id; try location(id,uuid,file:file,offset:offset,length:length)
                if !request.isEmpty {affected.insert(request)}
                affectedSessions.insert(session)
            }
        }
        // The verified CLI event is user-turn timing, not API-call timing. A
        // parent-chain match is mandatory; never copy it to individual calls.
        if type == "system", row.string("subtype") == "turn_duration", let ms = row.number("durationMs"), ms >= 0, !child, row.string("agentId").isEmpty {
            try recordEvent(row,kind:"duration",request:request,session:session,file:file,offset:offset,length:length)
        }
        if type == "attachment", !child, !request.isEmpty, row.object("attachment").string("type") == "model" {
            let model = row.object("attachment").object("identity").string("modelId")
            if !model.isEmpty, let current = try database.query("SELECT model FROM claude_requests WHERE id=?",[request]).first, current.string("model").isEmpty {
                try updateRequest(request,fields:["model":model]); affected.insert(request)
            }
        }
        if !searchRecord.isEmpty, !uuid.isEmpty {
            let searchable = text(message["content"])
            if !searchable.isEmpty {
                try database.run("INSERT INTO claude_search VALUES(?,?,?,?,?) ON CONFLICT(id,path) DO UPDATE SET record_id=excluded.record_id,request_id=excluded.request_id,text=excluded.text WHERE text<>excluded.text OR record_id<>excluded.record_id OR request_id<>excluded.request_id",[nodeID,file.path,searchRecord,request,searchable])
            }
        }
        if !nodeID.isEmpty {
            if request.isEmpty, !row.string("promptId").isEmpty {
                try database.run("INSERT INTO claude_request_hints VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET request_id=excluded.request_id,session_id=excluded.session_id",[nodeID,namespaced("request:"+row.string("promptId")),session])
            }
            try database.run("INSERT INTO claude_nodes VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET request_id=excluded.request_id WHERE request_id='' AND excluded.request_id<>''",[nodeID,request,session,namespaced(row.string("parentUuid"))])
        }
        if !request.isEmpty {
            affectedSessions.insert(session)
            if !child, let date = parsedDate(row["timestamp"]), Date().timeIntervalSince(date) < 900, date <= Date() {
                var status = ""
                if type == "assistant", row.flag("isApiErrorMessage") || message.string("model") == "<synthetic>" {status = "failed"}
                else if type == "assistant", ["end_turn","stop_sequence"].contains(message.string("stop_reason")) {status = "completed"}
                else if type == "system", row.string("subtype") == "turn_duration" {status = "completed"}
                else if ["assistant","progress"].contains(type) || type == "user" {status = "running"}
                if !status.isEmpty {try observeSurfaceRequest(request,at:date.timeIntervalSince1970,status:status)}
            }
        }
    }
    private func prepareSurface() throws {
        guard !surfacePrepared else {return}
        try database.script("""
        CREATE TABLE IF NOT EXISTS claude_live_requests(id TEXT PRIMARY KEY,observed_at REAL NOT NULL,status TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS claude_live_time ON claude_live_requests(status,observed_at DESC);
        CREATE INDEX IF NOT EXISTS claude_completed_time ON claude_requests(COALESCE((julianday(json_extract(data,'$.ended_at'))-2440587.5)*86400,timestamp) DESC,id) WHERE status='completed';
        """)
        surfacePrepared = true
        // Recover only fresh, unfinished indexed requests on first upgrade. No
        // transcript replay or historical usage/pricing rebuild is needed.
        let now = Date().timeIntervalSince1970
        for row in try database.query("SELECT id,timestamp FROM claude_requests r WHERE id LIKE ? AND status='' AND timestamp>? AND timestamp<=? AND NOT EXISTS(SELECT 1 FROM claude_live_requests l WHERE l.id=r.id) ORDER BY timestamp DESC LIMIT 64",[scope+"%",now-900,now]) {
            let call = try database.query("SELECT json_extract(data,'$.call_timing.last') AS observed,json_extract(data,'$.call_timing.stop') AS stop FROM claude_calls WHERE request_id=? AND COALESCE(json_extract(data,'$.is_subagent'),0)=0 ORDER BY timestamp DESC LIMIT 1",[row.string("id")]).first ?? [:]
            if !call.isEmpty && call.string("stop") != "tool_use" {continue}
            let observed = parsedDate(call["observed"])?.timeIntervalSince1970 ?? row.number("timestamp") ?? now-900
            if observed <= now {try observeSurfaceRequest(row.string("id"),at:observed,status:"running")}
        }
    }
    private func observeSurfaceRequest(_ id: String,at: Double,status: String) throws {
        try prepareSurface()
        let prior = try database.query("SELECT observed_at,status FROM claude_live_requests WHERE id=?",[id]).first
        if let prior, let previous = prior.number("observed_at"), previous > at || previous == at && (prior.string("status") == status || prior.string("status") != "running") {return}
        try database.run("INSERT INTO claude_live_requests VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET observed_at=excluded.observed_at,status=excluded.status",[id,at,status])
        try database.run("DELETE FROM claude_live_requests WHERE rowid NOT IN(SELECT rowid FROM claude_live_requests ORDER BY observed_at DESC LIMIT 256)")
        surfaceRevision += 1
    }

    // Reuse the existing SQL aggregation and day buckets, retaining only one
    // request plus seven days. Live observations have no ledger-revision trigger.
    func surface(now: Date = Date(),includeUsage: Bool = true) throws -> AgentSurfaceData {
        try prepareSurface()
        var result = AgentSurfaceData(); result.ready = true
        let stamp = now.timeIntervalSince1970
        let running = try database.query("SELECT r.data,l.observed_at FROM claude_live_requests l JOIN claude_requests r ON r.id=l.id WHERE r.id LIKE ? AND l.status='running' AND l.observed_at>? AND l.observed_at<=? AND r.timestamp<=? AND r.status NOT IN('failed','aborted') AND NOT(r.status='completed' AND (julianday(json_extract(r.data,'$.ended_at'))-2440587.5)*86400>=l.observed_at) ORDER BY r.timestamp DESC,r.id LIMIT 1",[scope+"%",stamp-900,stamp,stamp]).first
        var completed = running == nil ? try database.query("SELECT data FROM claude_requests WHERE id LIKE ? AND status='completed' AND timestamp<=? ORDER BY COALESCE((julianday(json_extract(data,'$.ended_at'))-2440587.5)*86400,timestamp) DESC,id LIMIT 1",[scope+"%",stamp]).first : nil
        if running == nil, let observed = try database.query("SELECT r.data,l.observed_at,1 AS observed_completion FROM claude_live_requests l JOIN claude_requests r ON r.id=l.id WHERE r.id LIKE ? AND l.status='completed' AND l.observed_at<=? AND r.timestamp<=? AND r.status NOT IN('failed','aborted') ORDER BY l.observed_at DESC,r.id LIMIT 1",[scope+"%",stamp,stamp]).first {
            let prior = completed.map {jsonObject(Data($0.string("data").utf8))} ?? [:]
            let end = parsedDate(prior["ended_at"]) ?? parsedDate(prior["timestamp"]) ?? .distantPast
            if (observed.number("observed_at") ?? 0) > end.timeIntervalSince1970 {completed = observed}
        }
        if let row = running ?? completed {
            var raw = jsonObject(Data(row.string("data").utf8))
            let models = Array(Set((raw["upstream_models"] as? [String] ?? [])+[raw.string("upstream_model")])).filter {!$0.isEmpty}.sorted()
            raw["model"] = (models.isEmpty ? [raw.string("model")] : models).filter {!$0.isEmpty}.map {pricing.compactModelName($0)}.joined(separator:" + ")
            raw["cache_hit_rate"] = raw.number("input_tokens").flatMap {input in input > 0 ? raw.number("cached_input_tokens").map {$0/input} : nil} as Any? ?? NSNull()
            raw["duration_running"] = running != nil
            if running != nil {raw["status"] = "running"; raw["duration_started_at"] = raw["timestamp"]; raw["duration_ms"] = NSNull()}
            else if row.integer("observed_completion") == 1 {
                raw["status"] = "completed"
                // A stop event confirms completion, but never invents missing
                // duration or reuses timing from an earlier continuation.
                if (parsedDate(raw["ended_at"])?.timeIntervalSince1970 ?? 0)+0.01 < (row.number("observed_at") ?? 0) {raw["duration_ms"] = NSNull()}
            }
            result.request = UsageRow(raw:raw); result.hasRunningTask = running != nil
        }
        if let expiry = try database.query("SELECT min(observed_at+900) AS expiry FROM claude_live_requests WHERE id LIKE ? AND status='running' AND observed_at>? AND observed_at<=?",[scope+"%",stamp-900,stamp]).first?.number("expiry") {result.nextBoundary = Date(timeIntervalSince1970:expiry)}
        if includeUsage {
            let range = UsageRange(period:"today"), (sql,args) = filtered("claude_calls",range:range)
            let sums = try sumColumns("claude_calls",where:sql,args:args)
            let (requestSQL,requestArgs) = filtered("claude_requests",range:range)
            var summary = UsageSummary(); summary.calls = sums.integer("calls") ?? 0
            summary.requests = try database.query("SELECT count(*) AS n FROM claude_requests WHERE \(requestSQL)",requestArgs).first?.integer("n") ?? 0
            summary.tokens = summary.calls == 0 ? 0 : sums.integer("total"); summary.cost = summary.calls == 0 ? 0 : sums.number("cost")
            summary.input = sums.integer("input") ?? 0; summary.cached = sums.integer("cache_read") ?? 0; summary.output = sums.integer("output") ?? 0
            summary.unknownCosts = summary.calls > 0 && summary.cost == nil ? 1 : 0
            result.today = summary; result.days = try days(range:UsageRange(period:"week"),model:"all",granularity:"day")
        }
        return result
    }
    private let usageKeys = ["cache_write_5m_tokens","cache_write_1h_tokens","raw_input_tokens","cached_input_tokens","cache_write_input_tokens","input_tokens","output_tokens","total_tokens","reasoning_output_tokens"]
    private func mergeUsage(_ old: Object,_ incoming: Object) -> Object {
        let retained = old.object("usage_evidence").isEmpty ? old : old.object("usage_evidence")
        let newFacts = incoming.object("usage_evidence").isEmpty ? incoming : incoming.object("usage_evidence")
        let oldTotal = retained.integer("total_tokens"), newTotal = newFacts.integer("total_tokens")
        let oldFinal = old.flag("usage_evidence_terminal",old.flag("terminal_usage")) && oldTotal != nil
        let newFinal = incoming.flag("usage_evidence_terminal",incoming.flag("terminal_usage")) && newTotal != nil
        let staleZero = (oldTotal ?? 0) > 0 && newTotal == 0
        let overlapDiffers = usageKeys.contains {key in
            guard let a = retained.integer(key), let b = newFacts.integer(key) else {return false}; return a != b
        }
        let terminalConflict = !staleZero && overlapDiffers && (oldTotal ?? 0) > 0 && oldFinal && incoming.flag("terminal_usage") && ((newTotal ?? 0) > 0 || newTotal == nil)
        let oldTime = parsedDate(old["usage_evidence_timestamp"]) ?? parsedDate(old["timestamp"]) ?? .distantPast
        let newTime = parsedDate(incoming["usage_evidence_timestamp"]) ?? parsedDate(incoming["timestamp"]) ?? .distantPast
        let newHasPositiveComponent = usageKeys.contains {(newFacts.integer($0) ?? 0) > 0}
        let preferIncoming = !staleZero && (oldTotal == nil && newTotal != nil || oldTotal == 0 && ((newTotal ?? 0) > 0 || !oldFinal && newHasPositiveComponent) || newTotal != nil && (newFinal && !oldFinal || oldFinal == newFinal && newTime > oldTime))
        var result = preferIncoming ? incoming : old
        var facts: Object = [:]
        for key in usageKeys {
            facts[key] = (preferIncoming ? newFacts[key] : retained[key]) ?? NSNull()
            // Fill compatible missing fields from a fragment; never replace a
            // complete observation merely because an incomplete one is terminal.
            if facts[key] is NSNull, !overlapDiffers {facts[key] = newFacts[key] ?? retained[key] ?? NSNull()}
            result[key] = facts[key]
        }
        if let input = facts.integer("raw_input_tokens"), let read = facts.integer("cached_input_tokens"), let write = facts.integer("cache_write_input_tokens"), input <= Int.max-read, input+read <= Int.max-write {
            facts["input_tokens"] = input+read+write; result["input_tokens"] = facts["input_tokens"]
            if let output = facts.integer("output_tokens"), input+read+write <= Int.max-output {facts["total_tokens"] = input+read+write+output; result["total_tokens"] = facts["total_tokens"]}
        }
        result["terminal_usage"] = old.flag("terminal_usage") || incoming.flag("terminal_usage")
        result["usage_evidence_terminal"] = preferIncoming ? newFinal : oldFinal
        result["status"] = result.flag("terminal_usage") ? "completed" : ""
        result["timestamp"] = old["timestamp"] ?? incoming["timestamp"]
        result["call_timing"] = ClaudeTiming.merge(old.object("call_timing"),incoming.object("call_timing"))
        result["call_timing_checked"] = old.flag("call_timing_checked") && incoming.flag("call_timing_checked") && !old.flag("call_timing_incomplete")
        result["call_timing_incomplete"] = old.flag("call_timing_incomplete") || incoming.flag("call_timing_incomplete")
        for key in ["model","reasoning_effort","upstream_model","service_tier","auth_mode","billing_source"] where result.string(key).isEmpty {result[key] = old[key] ?? incoming[key]}
        let conflict = old.flag("usage_conflict") || incoming.flag("usage_conflict") || terminalConflict
        result["usage_conflict"] = conflict
        if conflict {
            result["usage_evidence"] = facts
            if terminalConflict {result["usage_conflicting_evidence"] = Dictionary(uniqueKeysWithValues:usageKeys.map {($0,(preferIncoming ? retained[$0] : newFacts[$0]) ?? NSNull())})}
            else {result["usage_conflicting_evidence"] = old["usage_conflicting_evidence"] ?? incoming["usage_conflicting_evidence"] ?? NSNull()}
            for key in usageKeys {result[key] = NSNull()}
            result["quality"] = "invalid:usage_conflict"
        }
        if newTotal == nil {result["usage_partial_evidence"] = Dictionary(uniqueKeysWithValues:usageKeys.map {($0,newFacts[$0] ?? NSNull())})}
        else if let partial = old["usage_partial_evidence"] {result["usage_partial_evidence"] = partial}
        result["usage_read_state"] = conflict ? "conflict" : result.integer("total_tokens") == nil ? "partial" : result.flag("usage_evidence_terminal") ? "terminal" : "provisional"
        return result
    }
    private func recordEvent(_ row: Object,kind: String,request: String,session: String,file: URL,offset: Int,length: Int) throws {
        let id = namespaced(row.string("uuid")); guard !id.isEmpty, let stamp = sourceStamp else {return}
        let evidence: Object = ["kind":kind,"timestamp":row["timestamp"] ?? NSNull(),"duration_ms":row.number("durationMs") as Any? ?? NSNull(),"path":file.path,"offset":offset,"length":length,"uuid":row.string("uuid"),"generation":sourceGeneration,"line_hash":sourceLineHash,"device":String(stamp.device),"inode":String(stamp.inode)]
        try database.run("INSERT INTO claude_events VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET request_id=excluded.request_id,session_id=excluded.session_id WHERE request_id='' AND excluded.request_id<>''",[id,request,session,jsonString(evidence)])
        try location(namespaced("event:"+row.string("uuid")),row.string("uuid"),file:file,offset:offset,length:length)
        let retained = try database.query("SELECT data FROM claude_events WHERE id=?",[id]).first.map {jsonObject(Data($0.string("data").utf8))} ?? evidence
        if !request.isEmpty {try applyEvent(retained,request:request,session:session)}
    }
    private func applyEvent(_ event: Object,request: String,session: String) throws {
        guard let parent = try database.query("SELECT session_id,status FROM claude_requests WHERE id=?",[request]).first, parent.string("session_id") == session else {return}
        if event.string("kind") == "failed" {
            try updateRequest(request,fields:["status":"failed"])
            try database.run("INSERT OR IGNORE INTO claude_locations(id,path,offset,length,uuid,generation,line_hash,device,inode) SELECT ?,path,offset,length,uuid,generation,line_hash,device,inode FROM claude_locations WHERE id=?",[request,namespaced("event:"+event.string("uuid"))])
            try database.run("INSERT INTO claude_locations(id,path,offset,length,uuid,generation,line_hash,device,inode) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id,path,offset) DO UPDATE SET generation=excluded.generation,line_hash=excluded.line_hash,device=excluded.device,inode=excluded.inode",[request,event.string("path"),event.integer("offset") ?? 0,event.integer("length") ?? 0,event.string("uuid"),event.integer("generation") ?? 0,event.string("line_hash"),event.string("device"),event.string("inode")])
        } else if let ms = event.number("duration_ms"), ms >= 0 {
            var fields: Object = ["duration_ms":ms,"ended_at":event["timestamp"] ?? NSNull(),"duration_source":"turn_duration","explicit_duration_ms":ms,"explicit_ended_at":event["timestamp"] ?? NSNull()]
            if !["failed","aborted"].contains(parent.string("status")) {fields["status"] = "completed"}
            try updateRequest(request,fields:fields)
        }
        affected.insert(request); affectedSessions.insert(session)
    }
    private func repairOwnership() throws -> Bool {
        // Late files and copied histories may supply an ancestor after a child.
        // Join explicit identities without time proximity or transcript rescans.
        return try database.transaction {
            var pending = false
            let hints = try database.query("SELECT h.id,h.request_id FROM claude_request_hints h JOIN claude_requests r ON r.id=h.request_id AND r.session_id=h.session_id LIMIT 2048")
            pending = hints.count == 2048
            for hint in hints {
                try database.run("UPDATE claude_nodes SET request_id=? WHERE id=? AND request_id=''",[hint.string("request_id"),hint.string("id")])
                try database.run("DELETE FROM claude_request_hints WHERE id=?",[hint.string("id")])
            }
            for step in 0..<64 {
                if isCancelled {pending = true; break}
                let nodes = try database.query("SELECT c.id,p.request_id,p.session_id FROM claude_nodes c JOIN claude_nodes p ON c.parent=p.id WHERE c.request_id='' AND p.request_id<>'' LIMIT 2048")
                if nodes.isEmpty {break}
                if step == 63 {pending = true}
                for node in nodes {try database.run("UPDATE claude_nodes SET request_id=?,session_id=? WHERE id=? AND request_id=''",[node.string("request_id"),node.string("session_id"),node.string("id")])}
            }
            let unresolved = try database.query("SELECT c.id,c.data,n.request_id,n.session_id AS canonical_session FROM claude_calls c JOIN claude_nodes n ON n.id=?||json_extract(c.data,'$.uuid') WHERE c.request_id='' AND n.request_id<>'' LIMIT 2048",[scope])
            pending = pending || unresolved.count == 2048
            for row in unresolved {
                var raw = jsonObject(Data(row.string("data").utf8)), request = row.string("request_id")
                let oldSession = raw.string("session_id")
                let oldID = raw.string("id"), newID = namespaced("response:"+String(identity([raw.string("response_id"),request,raw.string("agent_id")]).prefix(40)))
                raw["id"] = newID; raw["request_id"] = request; raw["record_kind"] = "model_call"; raw["session_id"] = row.string("canonical_session")
                let parent = try database.query("SELECT data FROM claude_requests WHERE id=?",[request]).first.map {jsonObject(Data($0.string("data").utf8))} ?? [:]
                raw["prompt_preview"] = parent["prompt_preview"] ?? ""
                if let existing = try database.query("SELECT data FROM claude_calls WHERE id=?",[newID]).first {
                    let other = jsonObject(Data(existing.string("data").utf8))
                    affectedSessions.insert(other.string("session_id"))
                    raw = mergeUsage(other,raw)
                }
                raw["session_id"] = parent.string("session_id",row.string("canonical_session"))
                raw["request_id"] = request; raw["id"] = newID
                try saveCall(raw)
                try database.run("INSERT OR IGNORE INTO claude_locations(id,path,offset,length,uuid,generation,line_hash,device,inode) SELECT ?,path,offset,length,uuid,generation,line_hash,device,inode FROM claude_locations WHERE id=?",[newID,oldID])
                try database.run("DELETE FROM claude_locations WHERE id=?",[oldID])
                try database.run("UPDATE claude_search SET record_id=?,request_id=? WHERE record_id=?",[newID,request,oldID])
                try database.run("DELETE FROM claude_calls WHERE id=?",[oldID])
                affected.insert(request); affectedSessions.insert(oldSession); affectedSessions.insert(raw.string("session_id"))
            }
            let events = try database.query("SELECT e.id,e.data,n.request_id,n.session_id FROM claude_events e JOIN claude_nodes n ON n.id=e.id WHERE e.request_id='' AND n.request_id<>'' LIMIT 2048")
            pending = pending || events.count == 2048
            for event in events {
                try applyEvent(jsonObject(Data(event.string("data").utf8)),request:event.string("request_id"),session:event.string("session_id"))
                try database.run("UPDATE claude_events SET request_id=?,session_id=? WHERE id=?",[event.string("request_id"),event.string("session_id"),event.string("id")])
            }
            return pending
        }
    }
    func subscriptionConsumption(start: Double,end: Double) -> (Int,Double,Bool) {
        let rows = try? database.query("SELECT count(*) AS n,count(json_extract(data,'$.cost_usd')) AS priced,sum(total) AS tokens,sum(json_extract(data,'$.cost_usd')) AS cost FROM claude_calls WHERE timestamp>? AND timestamp<=? AND COALESCE(json_extract(data,'$.service_tier'),'') NOT IN ('priority','fast') AND COALESCE(json_extract(data,'$.auth_mode'),'') NOT IN ('api','api_key','apikey') AND COALESCE(json_extract(data,'$.billing_source'),'') NOT IN ('credits','api','api_key','usage_credits')",[start,end])
        guard let row = rows?.first else {return (0,0,true)}
        return (row.integer("tokens") ?? 0,row.number("cost") ?? 0,row.integer("n") != row.integer("priced"))
    }
    func reprice() throws {
        let key = "claude-prices:"+scope
        guard database.object("usage_meta",key:key).string("version") != pricing.version else {return}
        var last = ""
        while !isCancelled {
            let page = try database.query("SELECT id,data FROM claude_calls WHERE id LIKE ? AND id>? ORDER BY id LIMIT 256",[scope+"%",last])
            if page.isEmpty {break}
            try database.transaction {
                for row in page {
                    let raw = jsonObject(Data(row.string("data").utf8)); try saveCall(raw)
                    if !raw.string("request_id").isEmpty {affected.insert(raw.string("request_id"))}
                    affectedSessions.insert(raw.string("session_id")); last = row.string("id")
                }
            }
        }
        if !isCancelled {try database.put("usage_meta",key:key,value:["version":pricing.version])}
    }
    private func saveCall(_ input: Object) throws {
        var input = input
        let observed = contextFields(session:input.string("session_id"),uuid:input.string("uuid"),prompt:"")
        for (key,value) in observed where input[key] == nil || input[key] is NSNull || (input[key] as? String) == "" {input[key] = value}
        let raw = pricing.price(input,root:root)
        try database.run("INSERT INTO claude_calls VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET request_id=excluded.request_id,session_id=excluded.session_id,input=excluded.input,cache_read=excluded.cache_read,cache_write=excluded.cache_write,output=excluded.output,total=excluded.total,model=excluded.model,data=excluded.data WHERE data<>excluded.data",[raw.string("id"),raw.string("request_id"),raw.string("session_id"),parsedDate(raw["timestamp"])?.timeIntervalSince1970 as Any? ?? NSNull(),raw.string("model"),raw["input_tokens"] ?? NSNull(),raw["cached_input_tokens"] ?? NSNull(),raw["cache_write_input_tokens"] ?? NSNull(),raw["output_tokens"] ?? NSNull(),raw["total_tokens"] ?? NSNull(),jsonString(raw)])
    }
    private func saveRequest(_ value: Object) throws {
        var raw = value
        let observed = contextFields(session:raw.string("session_id"),uuid:raw.string("main_uuid"),prompt:String(raw.string("id").components(separatedBy:"request:").last ?? ""))
        for (key,value) in observed where raw[key] == nil || raw[key] is NSNull || (raw[key] as? String) == "" {raw[key] = value}
        try database.run("INSERT INTO claude_requests(id,session_id,timestamp,model,status,input,cache_read,cache_write,output,total,data) VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET model=excluded.model,status=excluded.status,input=excluded.input,cache_read=excluded.cache_read,cache_write=excluded.cache_write,output=excluded.output,total=excluded.total,data=excluded.data WHERE data<>excluded.data",[raw.string("id"),raw.string("session_id"),parsedDate(raw["timestamp"])?.timeIntervalSince1970 as Any? ?? NSNull(),raw.string("model"),raw.string("status"),raw["input_tokens"] ?? NSNull(),raw["cached_input_tokens"] ?? NSNull(),raw["cache_write_input_tokens"] ?? NSNull(),raw["output_tokens"] ?? NSNull(),raw["total_tokens"] ?? NSNull(),jsonString(raw)])
    }
    private func updateRequest(_ id: String,fields: Object) throws {
        guard let row = try database.query("SELECT data FROM claude_requests WHERE id=?",[id]).first else {return}
        var raw = jsonObject(Data(row.string("data").utf8)); for (key,value) in fields {raw[key] = value}; try saveRequest(raw)
    }
    private func sumColumns(_ table: String,where predicate: String,args: [Any]) throws -> Object {
        let sums = ["input","cache_read","cache_write","output","total"].map {"CASE WHEN count(*)>0 AND count(\($0))=count(*) THEN sum(\($0)) END AS \($0)"}.joined(separator:",")
        return try database.query("SELECT count(*) AS calls,\(sums),CASE WHEN count(*)>0 AND count(json_extract(data,'$.cost_usd'))=count(*) THEN sum(json_extract(data,'$.cost_usd')) END AS cost FROM \(table) WHERE \(predicate)",args).first ?? [:]
    }
    private func materializeRequest(_ id: String) throws {
        let sums = try sumColumns("claude_calls",where:"request_id=?",args:[id])
        let extra = try database.query("SELECT CASE WHEN count(*)>0 AND count(json_extract(data,'$.raw_input_tokens'))=count(*) THEN sum(json_extract(data,'$.raw_input_tokens')) END AS raw_input,CASE WHEN count(*)>0 AND count(json_extract(data,'$.reasoning_output_tokens'))=count(*) THEN sum(json_extract(data,'$.reasoning_output_tokens')) END AS reasoning,max(COALESCE(json_extract(data,'$.usage_conflict'),0)) AS usage_conflict FROM claude_calls WHERE request_id=?",[id]).first ?? [:]
        var fields: Object = [:]
        for (column,key) in [("input","input_tokens"),("cache_read","cached_input_tokens"),("cache_write","cache_write_input_tokens"),("output","output_tokens"),("total","total_tokens")] {fields[key] = sums[column] ?? NSNull()}
        fields["usage_conflict"] = (extra.integer("usage_conflict") ?? 0) > 0
        fields["raw_input_tokens"] = extra["raw_input"] ?? NSNull(); fields["reasoning_output_tokens"] = extra["reasoning"] ?? NSNull()
        let context = try database.query("SELECT input,json_extract(data,'$.model_context_window') AS capacity FROM claude_calls WHERE request_id=? AND COALESCE(json_extract(data,'$.is_subagent'),0)=0 ORDER BY timestamp DESC,id DESC LIMIT 1",[id]).first
        fields["context_input_tokens"] = context?["input"] ?? NSNull()
        fields["call_count"] = sums["calls"] ?? 0
        fields["cost_usd"] = sums["cost"] ?? NSNull()
        let priced = try database.query("SELECT max(json_extract(data,'$.pricing_status')='estimated') AS estimated FROM claude_calls WHERE request_id=?",[id]).first
        fields["pricing_status"] = sums.number("cost") == nil ? "unpriced" : priced?.integer("estimated") == 1 ? "estimated" : "priced"
        let speeds = try database.query("SELECT DISTINCT json_extract(data,'$.service_tier') AS tier FROM claude_calls WHERE request_id=?",[id]).map {$0.string("tier")}
        if !speeds.isEmpty {fields["service_tier"] = speeds.contains("") ? "" : speeds.count == 1 ? speeds[0] : "mixed"}
        fields["upstream_models"] = try database.query("SELECT DISTINCT json_extract(data,'$.upstream_model') AS model FROM claude_calls WHERE request_id=? AND COALESCE(json_extract(data,'$.upstream_model'),'')<>'' ORDER BY model",[id]).map {$0.string("model")}
        if let stored = try database.query("SELECT data FROM claude_requests WHERE id=?",[id]).first {
            let request = jsonObject(Data(stored.string("data").utf8))
            if request.integer("model_context_window") == nil, let capacity = context?.integer("capacity") {fields["model_context_window"] = capacity}
            var main = try database.query("SELECT data FROM claude_calls WHERE request_id=? AND COALESCE(json_extract(data,'$.is_subagent'),0)=0 AND COALESCE(json_extract(data,'$.agent_id'),'')='' ORDER BY COALESCE(json_extract(data,'$.call_timing.first'),json_extract(data,'$.timestamp')) DESC,id DESC LIMIT 2",[id]).map {jsonObject(Data($0.string("data").utf8))}
            let pending = try database.query("SELECT EXISTS(SELECT 1 FROM claude_calls WHERE request_id=? AND COALESCE(json_extract(data,'$.is_subagent'),0)=0 AND (COALESCE(json_extract(data,'$.call_timing_checked'),0)=0 OR COALESCE(json_extract(data,'$.call_timing_incomplete'),0)=1)) AS pending",[id]).first?.integer("pending") == 1
            if pending, !main.isEmpty {main[0]["call_timing_incomplete"] = true}
            for (key,value) in ClaudeTiming.requestFields(request,main:main) {fields[key] = value}
        }
        try updateRequest(id,fields:fields)
    }
    private func materializeRanking(_ session: String) throws {
        let sums = try sumColumns("claude_calls",where:"session_id=?",args:[session])
        if sums.integer("calls") == 0, (try database.query("SELECT count(*) AS n FROM claude_requests WHERE session_id=?",[session]).first?.integer("n") ?? 0) == 0 {try database.run("DELETE FROM claude_ranking WHERE id=?",[session]); return}
        let title = try database.query("SELECT title FROM claude_sessions WHERE id=?",[session]).first?.string("title") ?? ""
        let count = try database.query("SELECT count(*) AS n FROM claude_requests WHERE session_id=?",[session]).first?.integer("n") ?? 0
        let raw: Object = ["thread_id":session,"title":title,"tokens":sums["total"] ?? NSNull(),"calls":sums["calls"] ?? 0,"requests":count,"cost_usd":sums["cost"] ?? NSNull()]
        try database.run("INSERT INTO claude_ranking VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET tokens=excluded.tokens,data=excluded.data WHERE data<>excluded.data",[session,sums["total"] ?? NSNull(),jsonString(raw)])
    }
    private func filtered(_ table: String,range: UsageRange,model: String = "all",status: String = "all",query: String = "") -> (String,[Any]) {
        var sql = "id LIKE ? AND timestamp>=? AND timestamp<=?", args: [Any] = [scope+"%",range.start.timeIntervalSince1970,range.end.timeIntervalSince1970]
        if model != "all" {
            if table == "claude_requests" {sql += " AND (model=? OR EXISTS(SELECT 1 FROM claude_calls c WHERE c.request_id=claude_requests.id AND (c.model=? OR json_extract(c.data,'$.upstream_model')=?)))"; args += [model,model,model]}
            else {sql += " AND (model=? OR json_extract(data,'$.upstream_model')=?)"; args += [model,model]}
        }
        if status != "all" {sql += " AND json_extract(data,'$.status')=?"; args.append(status)}
        if !query.isEmpty {
            let searchColumn = table == "claude_requests" ? "request_id" : "record_id"
            sql += " AND (instr(lower(json_extract(data,'$.prompt_preview')),lower(?))>0 OR instr(lower(session_id),lower(?))>0 OR EXISTS(SELECT 1 FROM claude_search x JOIN claude_sources s ON s.path=x.path WHERE x.\(searchColumn)=\(table).id AND s.present=1 AND instr(lower(x.text),lower(?))>0))"
            args += [query,query,query]
        }
        return (sql,args)
    }
    func models() throws -> [String] {
        try database.query("SELECT model FROM claude_calls WHERE id LIKE ? AND model<>'' UNION SELECT model FROM claude_requests WHERE id LIKE ? AND model<>'' UNION SELECT json_extract(data,'$.upstream_model') AS model FROM claude_calls WHERE id LIKE ? AND json_extract(data,'$.upstream_model')<>'' ORDER BY model",[scope+"%",scope+"%",scope+"%"]).map {$0.string("model")}
    }
    func logPage(range: UsageRange,mode: String,model: String,status: String,query: String,page: Int) throws -> LogProjection {
        let table = mode == "calls" ? "claude_calls" : "claude_requests", (sql,args) = filtered(table,range:range,model:model,status:status,query:query)
        let (orphanSQL,orphanArgs) = filtered("claude_calls",range:range,model:model,status:status,query:query)
        let selectedRows = "SELECT id,timestamp,data,0 AS orphan FROM \(table) WHERE \(sql)"+(mode == "requests" ? " UNION ALL SELECT id,timestamp,data,1 AS orphan FROM claude_calls WHERE request_id='' AND \(orphanSQL)" : "")
        let allArgs = args+(mode == "requests" ? orphanArgs : [])
        let count = try database.query("SELECT count(*) AS n,min(timestamp) AS first,sum(orphan) AS unassigned FROM (\(selectedRows))",allArgs).first ?? [:]
        let total = count.integer("n") ?? 0, selected = max(0,min(page,max(0,(total-1)/60)))
        let rows = try database.query("SELECT data FROM (\(selectedRows)) ORDER BY timestamp DESC,id LIMIT 60 OFFSET ?",allArgs+[selected*60]).map {UsageRow(raw:jsonObject(Data($0.string("data").utf8)))}
        let orphans = mode == "requests" ? count.integer("unassigned") ?? 0 : (try database.query("SELECT count(*) AS n FROM claude_calls WHERE request_id='' AND \(orphanSQL)",orphanArgs).first?.integer("n") ?? 0)
        let year = Calendar.current.component(.year,from:Date()), first = count.number("first").map {Date(timeIntervalSince1970:$0)}
        let showYear = range.start == .distantPast ? first.map {Calendar.current.component(.year,from:$0) != year} ?? false : Calendar.current.component(.year,from:range.start) != year || Calendar.current.component(.year,from:range.end) != year
        return LogProjection(rows:rows,count:total,unassigned:orphans,showYear:showYear,page:selected)
    }
    // The same closed periods and completeness rules as UsageReportStore,
    // reduced in SQLite instead of loading historical calls or transcript text.
    func reportAggregate(range: DateInterval) throws -> UsageReportStore.Aggregate {
        let predicate = "id LIKE ? AND timestamp>=? AND timestamp<?"
        let args: [Any] = [scope+"%",range.start.timeIntervalSince1970,range.end.timeIntervalSince1970]
        let cost = "json_extract(data,'$.cost_usd')"
        let inputKnown = "input IS NOT NULL AND cache_read IS NOT NULL AND cache_read<=input"
        let row = try database.query("SELECT count(*) AS calls,COALESCE(sum(total),0) AS total,COALESCE(sum(output),0) AS output,COALESCE(sum(total IS NULL),0) AS unknown_tokens,COALESCE(sum(CASE WHEN \(inputKnown) THEN input ELSE 0 END),0) AS input,COALESCE(sum(CASE WHEN \(inputKnown) THEN cache_read ELSE 0 END),0) AS cached,COALESCE(sum(\(cost)),0) AS cost,count(\(cost)) AS priced,COALESCE(sum(\(cost) IS NULL),0) AS unknown_costs FROM claude_calls WHERE \(predicate)",args).first ?? [:]
        var value = UsageReportStore.Aggregate()
        value.modelCalls = row.integer("calls") ?? 0; value.total = row.integer("total") ?? 0
        value.output = row.integer("output") ?? 0; value.input = row.integer("input") ?? 0; value.cached = row.integer("cached") ?? 0
        value.unknownTokens = row.integer("unknown_tokens") ?? 0; value.unknownCosts = row.integer("unknown_costs") ?? 0
        value.cost = row.number("cost") ?? 0; value.priced = row.integer("priced") ?? 0
        let model = "COALESCE(NULLIF(json_extract(data,'$.upstream_model'),''),NULLIF(model,''),'—')"
        for group in try database.query("SELECT \(model) AS name,count(*) AS calls,COALESCE(sum(total),0) AS tokens,sum(\(cost)) AS cost,COALESCE(sum(\(cost) IS NULL),0) AS unknown_costs FROM claude_calls WHERE \(predicate) GROUP BY \(model)",args) {
            let name = group.string("name")
            value.models[name] = UsageReportModel(name:name,calls:group.integer("calls") ?? 0,tokens:group.integer("tokens") ?? 0,costUSD:group.number("cost"),costComplete:group.integer("unknown_costs") == 0)
        }
        for group in try database.query("SELECT CAST(strftime('%H',timestamp,'unixepoch','localtime') AS INTEGER)/6 AS bucket,count(*) AS n FROM claude_requests WHERE \(predicate) GROUP BY bucket",args) {
            let bucket = group.integer("bucket") ?? 0, count = group.integer("n") ?? 0
            if value.times.indices.contains(bucket) {value.times[bucket] = count; value.requests += count}
        }
        let clock = "CAST(strftime('%H',timestamp,'unixepoch','localtime') AS INTEGER)*3600+CAST(strftime('%M',timestamp,'unixepoch','localtime') AS INTEGER)*60+CAST(strftime('%S',timestamp,'unixepoch','localtime') AS INTEGER)"
        value.first = try database.query("SELECT timestamp FROM claude_requests WHERE \(predicate) ORDER BY (\(clock)),timestamp LIMIT 1",args).first?.number("timestamp").map {Date(timeIntervalSince1970:$0)}
        value.last = try database.query("SELECT timestamp FROM claude_requests WHERE \(predicate) ORDER BY (\(clock)) DESC,timestamp LIMIT 1",args).first?.number("timestamp").map {Date(timeIntervalSince1970:$0)}
        return value
    }
    func trend(range: UsageRange,model: String = "all",granularity: String) throws -> TrendProjection {
        let (sql,args) = filtered("claude_calls",range:range,model:model)
        let sum = try sumColumns("claude_calls",where:sql,args:args)
        let (requestSQL,requestArgs) = filtered("claude_requests",range:range,model:model)
        let requests = try database.query("SELECT count(*) AS n FROM claude_requests WHERE \(requestSQL)",requestArgs).first?.integer("n") ?? 0
        var summary = UsageSummary(); summary.tokens = sum.integer("total"); summary.cost = sum.number("cost"); summary.calls = sum.integer("calls") ?? 0; summary.requests = requests
        if let input = sum.integer("input"), let read = sum.integer("cache_read") {summary.input = input; summary.cached = read}
        summary.output = sum.integer("output") ?? 0
        let rows = try database.query("SELECT model,count(*) AS calls,CASE WHEN count(total)=count(*) THEN sum(total) END AS tokens,CASE WHEN count(json_extract(data,'$.cost_usd'))=count(*) THEN sum(json_extract(data,'$.cost_usd')) END AS cost FROM claude_calls WHERE \(sql) GROUP BY model ORDER BY tokens DESC",args)
        let requestRows = try database.query("SELECT model,count(*) AS n FROM claude_requests WHERE \(requestSQL) GROUP BY model ORDER BY n DESC",requestArgs)
        let requestCounts = Dictionary(uniqueKeysWithValues:requestRows.map {($0.string("model"),$0.integer("n") ?? 0)})
        let costs = Dictionary(uniqueKeysWithValues:rows.map {($0.string("model"),$0.number("cost"))})
        let tokenCounts = Dictionary(uniqueKeysWithValues:rows.map {($0.string("model"),$0.integer("tokens"))})
        let groups = rows.map {row -> MobileModel in MobileModel(id:row.string("model"),name:requestValueText(modelName(row.string("model"))),metric:MobileMetric(tokens:row.integer("tokens"),cost:row.number("cost"),requests:requestCounts[row.string("model")] ?? 0,costComplete:row.number("cost") != nil,hitRate:nil))}
        let requestGroups = requestRows.map {row in MobileModel(id:row.string("model"),name:requestValueText(modelName(row.string("model"))),metric:MobileMetric(tokens:tokenCounts[row.string("model")] ?? nil,cost:costs[row.string("model")] ?? nil,requests:row.integer("n") ?? 0,costComplete:(costs[row.string("model")] ?? nil) != nil,hitRate:nil))}
        let recent = try database.query("SELECT data FROM claude_requests WHERE \(requestSQL) ORDER BY timestamp DESC LIMIT 3",requestArgs).map {UsageRow(raw:jsonObject(Data($0.string("data").utf8)))}
        return TrendProjection(summary:summary,days:try days(range:range,model:model,granularity:granularity),recent:recent,modelRows:[groups,groups,requestGroups])
    }
    private func days(range: UsageRange,model: String,granularity: String) throws -> [DayUsage] {
        // SQL reduces historical calls to hourly/day aggregates. Swift performs
        // timezone/calendar bucket mapping on these small values, never bodies.
        let (sql,args) = filtered("claude_calls",range:range,model:model)
        let grouping = granularity == "hour" ? "strftime('%Y-%m-%d %H',timestamp,'unixepoch','localtime')" : granularity == "week" ? "strftime('%Y-%W',timestamp,'unixepoch','localtime')" : "strftime('%Y-%m-%d',timestamp,'unixepoch','localtime')"
        let calls = try database.query("SELECT min(timestamp) AS t,count(*) AS n,CASE WHEN count(total)=count(*) THEN sum(total) END AS tokens,CASE WHEN count(json_extract(data,'$.cost_usd'))=count(*) THEN sum(json_extract(data,'$.cost_usd')) END AS cost FROM claude_calls WHERE \(sql) GROUP BY \(grouping)",args)
        let (requestSQL,requestArgs) = filtered("claude_requests",range:range,model:model)
        let requests = try database.query("SELECT min(timestamp) AS t,count(*) AS n FROM claude_requests WHERE \(requestSQL) GROUP BY \(grouping)",requestArgs)
        var calendar = Calendar.current; calendar.firstWeekday = 2
        let component: Calendar.Component = granularity == "hour" ? .hour : granularity == "week" ? .weekOfYear : .day
        var groups: [Date:DayUsage] = [:], missing = Set<Date>(), missingCosts = Set<Date>()
        for row in calls {
            guard let t = row.number("t"), let start = calendar.dateInterval(of:component,for:Date(timeIntervalSince1970:t))?.start else {continue}
            var value = groups[start] ?? DayUsage(date:start,tokens:0,cost:0,calls:0)
            if let tokens = row.integer("tokens") {value.tokens = (value.tokens ?? 0)+tokens} else {missing.insert(start)}
            if let cost = row.number("cost") {value.cost = (value.cost ?? 0)+cost} else {missingCosts.insert(start)}
            value.calls += row.integer("n") ?? 0; groups[start] = value
        }
        for row in requests {
            guard let t = row.number("t"), let start = calendar.dateInterval(of:component,for:Date(timeIntervalSince1970:t))?.start else {continue}
            var value = groups[start] ?? DayUsage(date:start,tokens:0,cost:0,calls:0); value.requests += row.integer("n") ?? 0; groups[start] = value
        }
        var cursor = calendar.dateInterval(of:component,for:range.start == .distantPast ? groups.keys.min() ?? range.end : range.start)!.start
        var result: [DayUsage] = []
        while cursor <= range.end && result.count < 20_000 {
            var value = groups[cursor] ?? DayUsage(date:cursor,tokens:0,cost:0,calls:0)
            if missing.contains(cursor) {value.tokens = nil}; if missingCosts.contains(cursor) {value.cost = nil}; result.append(value)
            guard let next = calendar.date(byAdding:component,value:1,to:cursor), next > cursor else {break}; cursor = next
        }
        return result
    }
    func activity() throws -> ActivityStats {
        let projection = try trend(range:UsageRange(period:"all"),granularity:"day")
        let calendar = Calendar.current, today = calendar.startOfDay(for:Date())
        let byDay = Dictionary(projection.days.map {($0.date,$0)},uniquingKeysWith:{$1})
        var value = ActivityStats(); value.days = (-364...0).compactMap { offset in
            calendar.date(byAdding:.day,value:offset,to:today).map {byDay[$0] ?? DayUsage(date:$0,tokens:0,cost:0,calls:0)}
        }; value.total = projection.summary.tokens; value.peak = projection.days.contains {$0.calls > 0 && $0.tokens == nil} ? nil : projection.days.compactMap(\.tokens).max(); value.calls = projection.summary.calls
        var longest = 0, current = 0, previous: Date?
        for day in projection.days where day.calls > 0 || day.requests > 0 {
            current = previous.flatMap {Calendar.current.date(byAdding:.day,value:1,to:$0)} == day.date ? current+1 : 1
            longest = max(longest,current); previous = day.date
        }
        value.longestStreak = longest
        if let previous, Calendar.current.isDateInToday(previous) || Calendar.current.isDateInYesterday(previous) {value.currentStreak = current}
        let efforts = try database.query("SELECT json_extract(data,'$.reasoning_effort') AS effort,count(*) AS n FROM claude_calls WHERE id LIKE ? AND json_extract(data,'$.reasoning_effort')<>'' GROUP BY effort ORDER BY n DESC LIMIT 1",[scope+"%"]).first
        value.effort = efforts?.string("effort") ?? ""
        if value.calls > 0, let n = efforts?.integer("n") {value.effortPercent = Double(n)/Double(value.calls)*100}
        // Chat duration requires complete explicit timing for the whole chat.
        let duration = try database.query("SELECT CASE WHEN sum(missing)=0 THEN max(ms) END AS ms FROM (SELECT session_id,sum(json_extract(data,'$.duration_ms')) AS ms,sum(CASE WHEN status='completed' AND json_extract(data,'$.duration_ms')>=0 THEN 0 ELSE 1 END) AS missing FROM claude_requests WHERE id LIKE ? GROUP BY session_id)",[scope+"%"]).first?.number("ms")
        value.longestChat = duration.map {$0/1000}
        let speeds = try database.query("SELECT count(*) AS total,sum(json_extract(data,'$.service_tier') IN ('default','priority')) AS known,sum(json_extract(data,'$.service_tier')='priority') AS fast FROM claude_calls WHERE id LIKE ?",[scope+"%"]).first ?? [:]
        if let total = speeds.integer("total"), total>0, speeds.integer("known") == total {value.fastPercent = Double(speeds.integer("fast") ?? 0)/Double(total)*100}
        return value
    }
    func ranking(page: Int,metric: String = "cost") throws -> Object {
        let count = try database.query("SELECT count(*) AS n FROM claude_ranking WHERE id LIKE ?",[scope+"%"]).first?.integer("n") ?? 0
        let pages = max(1,(count+24)/25), selected = min(max(0,page),pages-1)
        let rows = try database.query("SELECT data FROM claude_ranking WHERE id LIKE ? ORDER BY \(metric == "tokens" ? "tokens IS NULL,tokens DESC" : "json_extract(data,'$.cost_usd') IS NULL,json_extract(data,'$.cost_usd') DESC"),id LIMIT 25 OFFSET ?",[scope+"%",selected*25]).map {jsonObject(Data($0.string("data").utf8))}
        return ["threads":rows,"page":selected,"pages":pages,"total":count]
    }
    func rankingDetails(_ id: String,page: Int) throws -> [Object] {
        guard id.hasPrefix(scope) else {return []}
        return try database.query("SELECT model,json_extract(data,'$.reasoning_effort') AS effort,json_extract(data,'$.service_tier') AS service_tier,count(*) AS calls,CASE WHEN count(total)=count(*) THEN sum(total) END AS tokens,CASE WHEN count(json_extract(data,'$.cost_usd'))=count(*) THEN sum(json_extract(data,'$.cost_usd')) END AS cost_usd FROM claude_calls WHERE session_id=? GROUP BY model,effort,service_tier ORDER BY tokens DESC LIMIT 25 OFFSET ?",[id,max(0,page)*25])
    }
    func members(_ id: String,page: Int) throws -> [UsageRow] {
        guard id.hasPrefix(scope) else {return []}
        return try database.query("SELECT data FROM claude_calls WHERE request_id=? ORDER BY timestamp,id LIMIT 60 OFFSET ?",[id,max(0,page)*60]).map {UsageRow(raw:jsonObject(Data($0.string("data").utf8)))}
    }
    private func recoverCallTiming() throws {
        guard !isCancelled else {return}
        let calls = try database.query("SELECT id,data FROM claude_calls WHERE id LIKE ? AND COALESCE(json_extract(data,'$.call_timing_checked'),0)=0 AND COALESCE(json_extract(data,'$.is_subagent'),0)=0 ORDER BY id LIMIT 32",[scope+"%"])
        var remainingBytes = 32_000_000
        for call in calls {
            if remainingBytes <= 0 {break}
            if isCancelled {return}
            let id = call.string("id"); var raw = jsonObject(Data(call.string("data").utf8))
            if !raw.flag("call_timing_checked") {
                let locations = try database.query("SELECT l.*,s.present,COALESCE(json_extract(s.data,'$.generation'),0) AS source_generation FROM claude_locations l LEFT JOIN claude_sources s ON s.path=l.path WHERE l.id=? ORDER BY l.path,l.offset LIMIT 65",[id])
                var timing: Object = [:], complete = !locations.isEmpty && locations.count <= 64, bytes = 0
                for location in locations.prefix(64) {
                    guard location.integer("present") == 1, location.integer("generation") == location.integer("source_generation"), let length = location.integer("length"), length <= 8_000_000, bytes+length <= 32_000_000 else {complete = false; continue}
                    bytes += length; remainingBytes -= length
                    let file = URL(fileURLWithPath:location.string("path")), stamp = FileStamp(file)
                    guard stamp.exists, String(stamp.device) == location.string("device"), String(stamp.inode) == location.string("inode"), let handle = try? FileHandle(forReadingFrom:file) else {complete = false; continue}
                    defer {try? handle.close()}
                    do {
                        try handle.seek(toOffset:UInt64(location.integer("offset") ?? 0))
                        let line = try handle.read(upToCount:length) ?? Data(), row = jsonObject(line)
                        guard digest(line) == location.string("line_hash"), row.string("uuid") == location.string("uuid"), row.string("type") == "assistant", row.object("message").string("id") == raw.string("response_id"), !row.flag("isSidechain"), row.string("agentId").isEmpty else {complete = false; continue}
                        timing = ClaudeTiming.merge(timing,ClaudeTiming.observation(row))
                    } catch {complete = false}
                }
                raw["call_timing"] = timing; raw["call_timing_checked"] = true; raw["call_timing_incomplete"] = !complete
                try saveCall(raw)
            }
            if !raw.string("request_id").isEmpty {affected.insert(raw.string("request_id"))}
            affectedSessions.insert(raw.string("session_id"))
        }
    }

    func original(_ id: String) throws -> [Object] {
        guard id.hasPrefix(scope) else {return []}
        // Never retain body/media caches. Each opening rechecks the live source.
        // A deleted source can retain summary/metrics but cannot impersonate text.
        let locations = try database.query("SELECT l.* FROM claude_locations l JOIN claude_sources s ON s.path=l.path WHERE l.id=? AND s.present=1 AND l.generation=COALESCE(json_extract(s.data,'$.generation'),0) ORDER BY l.offset LIMIT 64",[id])
        var rows: [Object] = [], seen = Set<String>(), bytes = 0
        for location in locations {
            let file = URL(fileURLWithPath:location.string("path")), stamp = FileStamp(file); guard stamp.exists, String(stamp.device) == location.string("device"), String(stamp.inode) == location.string("inode"), let offset = location.integer("offset"), let length = location.integer("length"), length <= 16_000_000 else {continue}
            guard bytes+length <= 32_000_000 else {break}; bytes += length
            guard let handle = try? FileHandle(forReadingFrom:file) else {continue}; defer {try? handle.close()}
            try handle.seek(toOffset:UInt64(offset))
            let line = try handle.read(upToCount:length) ?? Data()
            guard !location.string("line_hash").isEmpty, digest(line) == location.string("line_hash") else {continue}
            let row = jsonObject(line)
            guard row.string("uuid") == location.string("uuid"), !row.isEmpty else {continue}
            let key = row.string("uuid")
            if seen.insert(key).inserted {rows.append(row)}
        }
        return rows
    }
}

// Bounded request-start observations, correlated only by transcript UUID/prompt
// ID. Current settings are never backfilled into unrelated historical turns.
extension ClaudeLedger {
    private func contextFields(session: String,uuid: String,prompt: String) -> Object {
        guard !session.isEmpty, !uuid.isEmpty || !prompt.isEmpty,
              let point = try? database.query("SELECT window,model,reasoning_effort FROM claude_context_windows WHERE session_id=? AND ((message_uuid<>'' AND message_uuid=?) OR (prompt_id<>'' AND prompt_id=?)) ORDER BY observed_at DESC LIMIT 1",[session,uuid,prompt]).first else {return [:]}
        return requestFields(point)
    }
    private func requestFields(_ point: Object) -> Object {
        var fields: Object = [:]
        if let window = point.integer("window"), window > 0 {fields["model_context_window"] = window}
        for key in ["model","reasoning_effort"] where !point.string(key).isEmpty {fields[key] = point.string(key)}
        return fields
    }
    func observeContexts(_ points: [Object]) throws -> Bool {
        guard !points.isEmpty else {return false}
        var changed = false
        if !contextIndexReady {
            try database.script("CREATE INDEX IF NOT EXISTS claude_calls_context_uuid ON claude_calls(session_id,json_extract(data,'$.uuid'))")
            contextIndexReady = true
        }
        try database.transaction {
            for point in points.prefix(32) {
                guard let at = point.number("observed_at"), !point.string("session_id").isEmpty else {continue}
                let session = namespaced(point.string("session_id")), uuid = point.string("message_uuid"), prompt = point.string("prompt_id")
                guard !uuid.isEmpty || !prompt.isEmpty else {continue}
                let prior = try database.query("SELECT window,model,reasoning_effort,observed_at FROM claude_context_windows WHERE session_id=? AND message_uuid=? AND prompt_id=?",[session,uuid,prompt]).first ?? [:]
                if (prior.number("observed_at") ?? 0) > at {continue}
                var fields = requestFields(prior)
                for (key,value) in requestFields(point) {fields[key] = value}
                guard !fields.isEmpty, identity(fields) != identity(requestFields(prior)) else {continue}
                try database.run("INSERT INTO claude_context_windows(session_id,message_uuid,prompt_id,observed_at,window,model,reasoning_effort) VALUES(?,?,?,?,?,?,?) ON CONFLICT(session_id,message_uuid,prompt_id) DO UPDATE SET observed_at=excluded.observed_at,window=excluded.window,model=excluded.model,reasoning_effort=excluded.reasoning_effort",[session,uuid,prompt,at,fields.integer("model_context_window") ?? 0,fields.string("model"),fields.string("reasoning_effort")])
                changed = true
                if !uuid.isEmpty {
                    for row in try database.query("SELECT data FROM claude_calls WHERE session_id=? AND json_extract(data,'$.uuid')=?",[session,uuid]) {
                        var raw = jsonObject(Data(row.string("data").utf8))
                        for (key,value) in fields {raw[key] = value}
                        try saveCall(raw)
                        if !raw.string("request_id").isEmpty {affected.insert(raw.string("request_id"))}
                    }
                }
                let ids = try database.query("SELECT id FROM claude_requests WHERE session_id=? AND ((?<>'' AND json_extract(data,'$.main_uuid')=?) OR (?<>'' AND id=?))",[session,uuid,uuid,prompt,namespaced("request:"+prompt)])
                for row in ids {try updateRequest(row.string("id"),fields:fields); affected.insert(row.string("id"))}
            }
            try database.run("DELETE FROM claude_context_windows WHERE rowid NOT IN(SELECT rowid FROM claude_context_windows ORDER BY observed_at DESC LIMIT 256)")
        }
        try materializeAffected()
        return changed
    }
}
