import Foundation

final class UsageIndexer {
    let database: Database
    private var cancelled = false
    private struct Signature: Equatable { let size: Int; let modified: Double }
    private var scannedFiles: [String:Signature] = [:]
    private struct FileList { let files: [URL]; let directories: [URL:FileStamp]; let checked: TimeInterval }
    private var fileLists: [URL:FileList] = [:]
    private var titleInputs: [URL:[URL:FileStamp]] = [:]
    private let rolloutPattern = try! NSRegularExpression(pattern:#"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"#)
    private let counters = ["input_tokens","cached_input_tokens","cache_write_input_tokens","output_tokens","reasoning_output_tokens","total_tokens"]
    init(_ database: Database) { self.database = database }
    func cancel() { cancelled = true }
    func rescan() throws {
        scannedFiles.removeAll(); titleInputs.removeAll(); fileLists.removeAll()
        try database.transaction {
            try database.run("DELETE FROM usage_cursors WHERE key LIKE 'native-v1:%' OR key LIKE 'usage:local:%' OR key LIKE 'usage:local:%:%'")
        }
    }

    private func rolloutID(_ url: URL) -> String {
        let name = url.lastPathComponent
        let matches = rolloutPattern.matches(in:name,range:NSRange(name.startIndex...,in:name))
        if let range = matches.last.flatMap({Range($0.range,in:name)}) { return String(name[range]).lowercased() }
        return String(identity(name).prefix(32))
    }
    func scan(_ roots: [URL], progress: (Int,Int)->Void = {_,_ in}) throws {
        cancelled = false
        for root in roots {
            if cancelled { return }
            try titles(root)
            let files = sessionFiles(root)
            for (index,file) in files.enumerated() {
                if cancelled { return }
                try scanFile(file,root:root); progress(index+1,files.count)
            }
        }
        let active = Set(roots)
        fileLists = fileLists.filter {active.contains($0.key)}; titleInputs = titleInputs.filter {active.contains($0.key)}
        let paths = Set(fileLists.values.flatMap {$0.files.map(\.path)})
        scannedFiles = scannedFiles.filter {paths.contains($0.key)}
    }
    private func sessionFiles(_ root: URL) -> [URL] {
        let now = ProcessInfo.processInfo.systemUptime
        if let cached = fileLists[root], now-cached.checked < 60,
           cached.directories.allSatisfy({FileStamp($0.key) == $0.value}) { return cached.files }
        var directories = Set([root]), selected: [String:(URL,Int)] = [:]
        for folder in ["sessions","archived_sessions"] {
            let directory = root.appendingPathComponent(folder); directories.insert(directory)
            guard let iterator = FileManager.default.enumerator(at:directory,includingPropertiesForKeys:[.isSymbolicLinkKey,.isDirectoryKey,.fileSizeKey],options:[.skipsHiddenFiles]) else { continue }
            for case let file as URL in iterator {
                if cancelled { return [] }
                let info = try? file.resourceValues(forKeys:[.isSymbolicLinkKey,.isDirectoryKey,.fileSizeKey])
                if info?.isSymbolicLink == true { iterator.skipDescendants(); continue }
                if info?.isDirectory == true { directories.insert(file); continue }
                guard file.pathExtension == "jsonl" else { continue }
                let key = rolloutID(file), size = info?.fileSize ?? 0
                if selected[key] == nil || selected[key]!.1 < size { selected[key] = (file,size) }
            }
        }
        let files = selected.values.map(\.0).sorted {$0.path < $1.path}
        fileLists[root] = FileList(files:files,directories:Dictionary(uniqueKeysWithValues:directories.map {($0,FileStamp($0))}),checked:now)
        return files
    }
    private func titles(_ root: URL) throws {
        let index = root.appendingPathComponent("session_index.jsonl")
        let states = (try? FileManager.default.contentsOfDirectory(at:root,includingPropertiesForKeys:nil))?.filter { $0.lastPathComponent.hasPrefix("state") && $0.pathExtension == "sqlite" } ?? []
        let inputs = [index]+states.flatMap {[$0,$0.deletingLastPathComponent().appendingPathComponent($0.lastPathComponent+"-wal")]}
        let signatures = Dictionary(uniqueKeysWithValues:inputs.map {($0,FileStamp($0))})
        if titleInputs[root] == signatures { return }
        var values: [String:String] = [:], complete = true
        if signatures[index]?.exists == true && (signatures[index]?.size ?? 0) < 32_000_000 {
            guard let data = try? Data(contentsOf:index), let text = String(data:data,encoding:.utf8) else { return }
            for line in text.split(separator:"\n") {
                let row = jsonObject(Data(line.utf8)), id = row.string("id",row.string("thread_id")), title = row.string("thread_name",row.string("title"))
                if !id.isEmpty && !title.hasPrefix("# AGENTS.md") { values[id] = String(title.prefix(400)) }
            }
        }
        for file in states.sorted(by:{$0.lastPathComponent < $1.lastPathComponent}) {
            if let db = try? Database(file,readOnly:true), let rows = try? db.query("SELECT id,title FROM threads") {
                for row in rows where !row.string("id").isEmpty { values[row.string("id")] = String(row.string("title").prefix(400)) }
            } else { complete = false }
        }
        try database.updateTitles(values)
        if complete { titleInputs[root] = signatures }
    }
    private func scanFile(_ file: URL, root: URL) throws {
        let info = try FileManager.default.attributesOfItem(atPath:file.path)
        let size = (info[.size] as? NSNumber)?.intValue ?? 0, modified = (info[.modificationDate] as? Date)?.timeIntervalSince1970 ?? 0
        let signature = Signature(size:size,modified:modified)
        if scannedFiles[file.path] == signature { return }
        let rollout = rolloutID(file), key = "native-v1:" + String(identity(root.path).prefix(16)) + ":" + rollout
        var cursor = database.object("usage_cursors",key:key)
        if cursor.isEmpty {
            let rootHash = String(identity(root.path).prefix(16))
            let sourceIDs = ["local","local:"+String(digest(Data(root.path.lowercased().utf8)).prefix(16))]
            for source in sourceIDs {
                let old = database.object("usage_cursors",key:"usage:"+source+":"+rootHash+":"+rollout)
                guard old.integer("version") == 11, old.object("state").string("pending_parent").isEmpty, let oldOffset = old.integer("offset"), oldOffset <= size else { continue }
                let reader = try FileHandle(forReadingFrom:file); defer { try? reader.close() }
                let prefix = try reader.read(upToCount:old.integer("prefix_len") ?? 0) ?? Data()
                try reader.seek(toOffset:UInt64(max(0,oldOffset-1024)))
                let tail = try reader.read(upToCount:min(1024,oldOffset)) ?? Data()
                guard digest(prefix) == old.string("prefix_hash"), digest(tail) == old.string("tail_hash") else { continue }
                if oldOffset == size, let stamp = old.number("mtime_ns"), abs(stamp/1e9-modified) > 0.000001 { continue }
                var migrated = old.object("state")
                migrated["turns"] = migrated.object("request_turns")
                for (field,value) in migrated.object("request_source") { migrated[field] = value }
                var candidate = migrated.object("modern_candidate")
                if !candidate.isEmpty { candidate["turn"] = candidate["turn_id"]; migrated["modern_candidate"] = candidate; migrated["last_record"] = candidate["record"] }
                try reader.seek(toOffset:UInt64(max(0,oldOffset-4096)))
                let newTail = digest(try reader.read(upToCount:min(4096,oldOffset)) ?? Data())
                cursor = ["parser":1,"offset":oldOffset,"modified":modified,"tail_hash":newTail,"state":migrated]
                try database.put("usage_cursors",key:key,value:cursor)
                break
            }
        }
        var offset = cursor.integer("offset") ?? 0
        if offset == size && cursor.number("modified") == modified { scannedFiles[file.path] = signature; return }
        let handle = try FileHandle(forReadingFrom:file); defer { try? handle.close() }
        var valid = offset <= size && cursor.integer("parser") == 1
        if valid && offset > 0 {
            let length = min(offset,4096)
            try handle.seek(toOffset:UInt64(offset-length))
            valid = digest(try handle.read(upToCount:length) ?? Data()) == cursor.string("tail_hash")
        }
        if !valid { offset = 0 }
        var state: Object = offset > 0 ? cursor.object("state") : ["rollout_id":rollout,"session_id":rollout,"model":"unknown","provider":"unknown","turn_id":"","prompt_preview":"","turns":Object(),"last_by_source":Object(),"last_totals":Object()]
        try handle.seek(toOffset:UInt64(offset))
        var pending = Data(), oversized = false
        while !cancelled {
            let chunk = try handle.read(upToCount:1_048_576) ?? Data()
            if chunk.isEmpty { break }
            pending.append(chunk)
            try database.transaction {
                while let newline = pending.firstIndex(of:10) {
                    let length = newline+1
                    if !oversized {
                        let record = jsonObject(pending.subdata(in:0..<newline))
                        if !record.isEmpty { try process(record,state:&state) }
                    }
                    oversized = false; pending.removeSubrange(0...newline); offset += length
                }
                if pending.count > 16_000_000 { offset += pending.count; pending.removeAll(); oversized = true }
            }
            if !oversized { try checkpoint(handle:handle,key:key,state:state,offset:offset,modified:modified) }
        }
        if !oversized { try checkpoint(handle:handle,key:key,state:state,offset:offset,modified:modified) }
        if !cancelled { scannedFiles[file.path] = signature }
    }
    private func checkpoint(handle: FileHandle,key: String,state: Object,offset: Int,modified: Double) throws {
        let current = try handle.offset(), length = min(offset,4096)
        try handle.seek(toOffset:UInt64(offset-length))
        let hash = digest(try handle.read(upToCount:length) ?? Data())
        try handle.seek(toOffset:current)
        try database.put("usage_cursors",key:key,value:["parser":1,"offset":offset,"modified":modified,"tail_hash":hash,"state":state])
    }
    private func usage(_ raw: Any?) -> [Int]? {
        guard let row = raw as? Object, counters.contains(where:{row[$0] != nil}) else { return nil }
        var values: [Int] = []
        for key in counters {
            let value = row[key] ?? (key == "cached_input_tokens" ? row["cache_read_input_tokens"] : nil) ?? 0
            guard let n = finiteNumber(value), n >= 0, n < Double(Int.max), n.rounded(.towardZero) == n else { return nil }
            values.append(Int(n))
        }
        return values
    }
    private func plain(_ content: Any?, user: Bool) -> String {
        var text = content as? String ?? (content as? [Object] ?? []).filter { ["text","input_text","output_text"].contains($0.string("type")) }.map { $0.string("text") }.joined(separator:"\n")
        if user {
            if text.contains("<send_user_message_question_reply>") { return "" }
            let tags = "recommended_plugins|environment_context|permissions(?: instructions)?|INSTRUCTIONS|user_instructions|developer_instructions|skills_instructions|skill_instructions|system|developer|system-reminder|app-context|collaboration_mode|multi_agent_role|multi_agent_mode"
            text = text.replacingOccurrences(of:"(?is)<("+tags+")(?:\\s[^>]*)?>.*?</\\1\\s*>",with:" ",options:.regularExpression)
            text = requestPreview(text)
            if ["# AGENTS.md instructions","<environment_context>","<INSTRUCTIONS>"].contains(where:{text.trimmingCharacters(in:.whitespacesAndNewlines).hasPrefix($0)}) { return "" }
            if text.trimmingCharacters(in:.whitespacesAndNewlines).isEmpty {
                let images = (content as? [Object] ?? []).filter {["image","input_image","image_url","local_image"].contains($0.string("type"))}.count
                if images > 0 { text = L("图片", "Image")+" ×\(images)" }
            }
        }
        return String(text.split(whereSeparator:{$0.isWhitespace}).joined(separator:" ").prefix(600))
    }
    private func saveTurn(_ state: inout Object, stamp: String, mutate: (inout Object)->Void) throws {
        let turn = state.string("turn_id"); guard !turn.isEmpty else { return }
        let id = "turn:"+state.string("session_id")+":"+turn
        var recent = state.object("turns"), row = recent.object(turn)
        if row.isEmpty {
            row = ["id":id,"session_id":state.string("session_id"),"turn_id":turn,"started_at":stamp,"started_inferred":true,"status":"unknown","prompt_preview":"","output_preview":"","verified":true,"is_subagent":state.flag("is_subagent"),"parent_session_id":state.string("parent_session_id"),"parent_turn_id":state.string("parent_turn_id"),"agent_path":state.string("agent_path"),"source_ids":["local"]]
        }
        for key in ["model","reasoning_effort","service_tier","model_context_window","provider"] {
            if let value = state[key], !(value is NSNull) { row[key] = value }
        }
        mutate(&row); row["observed_at"] = stamp
        recent[turn] = row
        if recent.count > 24 { for key in recent.keys.sorted() where key != turn { recent.removeValue(forKey:key); if recent.count <= 24 { break } } }
        state["turns"] = recent; try database.writeTurn(row)
    }
    private func remember(_ record: Object,state: inout Object) {
        state["last_record"] = record
        let response = record.string("response_id"); guard !response.isEmpty else { return }
        var recent = state.object("preview_recent"); recent[response] = record
        if recent.count > 16 { for key in recent.keys.sorted() where key != response { recent.removeValue(forKey:key); if recent.count <= 16 { break } } }
        state["preview_recent"] = recent
    }
    private func takeOutput(_ state: inout Object,response: String,turn: String) -> String {
        var kept: [Object] = [], text: [String] = []
        for item in state.objects("preview_pending") {
            if (item.string("response_id").isEmpty || item.string("response_id") == response) && (item.string("turn_id").isEmpty || item.string("turn_id") == turn) { if !text.contains(item.string("text")) { text.append(item.string("text")) } }
            else { kept.append(item) }
        }
        state["preview_pending"] = kept
        return String(text.joined(separator:" ").prefix(600))
    }
    private func process(_ entry: Object, state: inout Object) throws {
        let kind = entry.string("type"), payload = entry.object("payload"), subtype = payload.string("type")
        let stamp = parsedDate(entry["timestamp"]).map(iso) ?? ""
        if kind == "session_meta" && !state.flag("meta_seen") {
            let spawn = payload.object("source").object("subagent").object("thread_spawn")
            state["session_id"] = payload.string("id",payload.string("thread_id",state.string("session_id")))
            state["provider"] = payload.string("model_provider","unknown")
            state["history_start"] = payload["subagent_history_start_ordinal"]
            state["history_base"] = payload["history_base"]
            state["parent_id"] = payload.string("forked_from_id",spawn.string("parent_thread_id"))
            state["fork_timestamp"] = stamp; state["meta_seen"] = true
            state["is_subagent"] = !spawn.isEmpty
            state["parent_session_id"] = spawn.string("parent_thread_id")
            state["parent_turn_id"] = spawn.string("parent_turn_id",payload.string("parent_turn_id"))
            state["agent_path"] = spawn.string("agent_path")
            return
        }
        let inherited: Bool
        if let boundary = state.integer("history_start"), let ordinal = entry.integer("ordinal") { inherited = ordinal < boundary }
        else { inherited = !state.string("parent_id").isEmpty && !stamp.isEmpty && stamp < state.string("fork_timestamp") }
        if !inherited { try agentEvent(entry,state:&state,stamp:stamp) }
        if kind == "event_msg" && subtype == "thread_settings_applied" {
            let settings = payload.object("thread_settings").isEmpty ? payload.object("settings") : payload.object("thread_settings")
            for key in ["model","reasoning_effort","service_tier"] { if let value = settings[key] { state[key] = value } }
            if let provider = settings["model_provider_id"] ?? settings["model_provider"] { state["provider"] = provider }
        }
        if kind == "turn_context" {
            if let model = payload["model"] ?? payload.object("info")["model"] { state["model"] = model }
            state["reasoning_effort"] = payload["effort"] ?? payload["reasoning_effort"]
            if let tier = payload["service_tier"] { state["service_tier"] = tier }
            state["model_context_window"] = payload["model_context_window"] ?? payload.object("info")["model_context_window"]
            let id = payload.string("turn_id",payload.string("id"))
            if !id.isEmpty && id != state.string("turn_id") { state["previous_turn_id"] = state["turn_id"]; state["prompt_preview"] = ""; state["modern_candidate"] = nil; state["active_response_id"] = nil; state["preview_pending"] = [Object]() }
            if !id.isEmpty { state["turn_id"] = id }
            if !inherited { try saveTurn(&state,stamp:stamp) { _ in } }
        }
        if kind == "event_msg" && ["task_started","turn_started"].contains(subtype) {
            let settings = payload.object("thread_settings").isEmpty ? payload.object("settings") : payload.object("thread_settings")
            for key in ["model","reasoning_effort","service_tier","model_context_window"] { if let value = settings[key] ?? payload[key] { state[key] = value } }
            let id = payload.string("turn_id",payload.string("id"))
            if !id.isEmpty { state["previous_turn_id"] = state["turn_id"]; state["turn_id"] = id; state["modern_candidate"] = nil; state["active_response_id"] = nil; state["preview_pending"] = [Object]() }
            state["prompt_preview"] = ""; state["output_preview"] = ""
            if !inherited { try saveTurn(&state,stamp:stamp) { $0["started_at"] = payload["started_at"].flatMap(parsedDate).map(iso) ?? stamp; $0["started_inferred"] = false; $0["status"] = "running" } }
        }
        let item = payload.object("item")
        let userContent: Any? = kind == "event_msg" && subtype == "user_message" ? payload["message"] ?? payload["content"] : kind == "response_item" && payload.string("role") == "user" ? payload["content"] : kind == "event_msg" && ["user_message","userMessage","UserMessage"].contains(item.string("type")) ? item["content"] ?? item["message"] : nil
        if let content = userContent, !inherited {
            if state.string("turn_id").isEmpty || ["completed","aborted"].contains(state.object("turns").object(state.string("turn_id")).string("status")) { state["previous_turn_id"] = state["turn_id"]; state["turn_id"] = "legacy-user:"+identity([stamp,content]) }
            let text = plain(content,user:true)
            if !text.isEmpty { state["prompt_preview"] = text }
            let previous = state.string("previous_turn_id"), reply = (content as? String)?.contains("<send_user_message_question_reply>") == true
            try saveTurn(&state,stamp:stamp) { row in
                if !text.isEmpty { row["prompt_preview"] = text }
                if reply && !previous.isEmpty { row["continuation_of"] = previous }
            }
        }
        if !inherited && ((kind == "response_item" && payload.string("role") == "assistant") || (kind == "event_msg" && subtype == "agent_message")) {
            guard ["","commentary","final","final_answer"].contains(payload.string("phase")), ["","commentary","final"].contains(payload.string("channel")), ["","all","user"].contains(payload.string("recipient")), kind != "response_item" || payload.string("type","message") == "message" else { return }
            let metadata = payload.object("internal_chat_message_metadata_passthrough")
            let owner = payload.string("thread_id",metadata.string("thread_id")), explicitTurn = payload.string("turn_id",metadata.string("turn_id"))
            guard owner.isEmpty || owner == state.string("session_id"), explicitTurn.isEmpty || explicitTurn == state.string("turn_id") else { return }
            let output = plain(payload["content"] ?? payload["message"],user:false)
            if !output.isEmpty {
                state["output_preview"] = output
                try saveTurn(&state,stamp:stamp) { $0["output_preview"] = output; $0["latest_output_preview"] = output }
                let response = payload.string("response_id",entry.string("response_id"))
                if !response.isEmpty, var record = state.object("preview_recent")[response] as? Object, record.flag("context_owner_verified") {
                    record["output_preview"] = String([record.string("output_preview"),output].filter {!$0.isEmpty}.joined(separator:" ").prefix(600))
                    try database.writeRecord(record); remember(record,state:&state)
                } else {
                    var pending = state.objects("preview_pending")
                    let key = payload.string("id",identity([response,state.string("turn_id"),output]))
                    if !pending.contains(where:{$0.string("key") == key}) { pending.append(["key":key,"response_id":response,"turn_id":state.string("turn_id"),"text":output]) }
                    state["preview_pending"] = Array(pending.suffix(16))
                }
            }
        }
        if kind == "event_msg" && ["task_complete","turn_complete","turn_aborted"].contains(subtype), !inherited {
            try saveTurn(&state,stamp:stamp) { row in
                row["ended_at"] = parsedDate(payload["completed_at"]).map(iso) ?? stamp
                row["status"] = subtype == "turn_aborted" ? "aborted" : "completed"
                let final = plain(payload["last_agent_message"],user:false)
                if !final.isEmpty { row["output_preview"] = final; row["latest_output_preview"] = final }
                if let duration = payload.number("duration_ms"), duration >= 0 { row["duration_ms"] = duration }
            }
            state["preview_pending"] = [Object]()
        }
        if kind == "event_msg" && subtype == "raw_response_completed", !payload.string("response_id").isEmpty {
            let response = payload.string("response_id")
            if response != state.string("active_response_id") { state["modern_candidate"] = nil }
            state["active_response_id"] = response
            state["preview_pending"] = state.objects("preview_pending").map { item in var item = item; if item.string("response_id").isEmpty { item["response_id"] = response }; return item }
            if var record = state.object("preview_recent")[response] as? Object, record.flag("context_owner_verified") {
                let output = takeOutput(&state,response:response,turn:record.string("turn_id"))
                if !output.isEmpty { record["output_preview"] = output; try database.writeRecord(record); remember(record,state:&state) }
            }
        }
        if kind == "token_usage_record" {
            guard let usage = usage(payload["usage"]), usage[0]+usage[3] > 0, !stamp.isEmpty else { return }
            let response = payload.string("response_id"), owner = payload.string("thread_id",state.string("session_id")), turn = payload.string("turn_id",state.string("turn_id"))
            state["modern_candidate"] = ["usage":usage,"turn":turn,"matched":false]
            state["active_response_id"] = response
            guard !inherited else { return }
            let id = response.isEmpty ? "modern:"+identity([state.string("rollout_id"),stamp,usage,turn]) : "response:"+owner+":"+response
            var record = makeRecord(state,usage:usage,id:id,stamp:stamp,quality:"response",owner:owner,turn:turn)
            record["response_id"] = response.isEmpty ? NSNull() : response as Any
            let preview = takeOutput(&state,response:response,turn:turn)
            if record.flag("context_owner_verified") { record["output_preview"] = preview }
            if let duration = payload.number("duration_ms"), duration >= 0 { record["duration_ms"] = duration }
            for (key,source) in [("call_started_at","started_at"),("call_ended_at","completed_at")] { record[key] = parsedDate(payload[source]).map(iso) }
            if record["duration_ms"] == nil, let start = parsedDate(record["call_started_at"]), let end = parsedDate(record["call_ended_at"]), end >= start { record["duration_ms"] = end.timeIntervalSince(start)*1000 }
            try database.writeRecord(record); remember(record,state:&state)
            return
        }
        guard kind == "event_msg", subtype == "token_count", !payload.object("info").isEmpty else { return }
        let info = payload.object("info"), source = payload.object("rate_limits").string("limit_id","unknown")
        let total = usage(info["total_token_usage"]), last = usage(info["last_token_usage"])
        let signature = identity([total as Any? ?? NSNull(),last as Any? ?? NSNull()])
        var signatures = state.object("last_by_source"), totals = state.object("last_totals")
        let duplicate = total != nil && (signatures.string(source) == signature || state.string("previous_signature") == signature)
        let old = totals[source] as? [Int], high = state["high_water"] as? [Int]
        signatures[source] = signature; state["last_by_source"] = signatures; state["previous_signature"] = signature
        if let total { totals[source] = total; state["last_totals"] = totals }
        let synthetic = last.map { $0.prefix(5).reduce(0,+) == 0 && $0[5] > 0 } ?? false
        let reset = total != nil && total == last && old != nil && zip(total!.prefix(5),old!.prefix(5)).allSatisfy({$0 <= $1}) && total != old
        if let total { state["high_water"] = reset || (synthetic && total.prefix(5).reduce(0,+) == 0) ? total : high.map { zip($0,total).map(max) } ?? total }
        guard !duplicate, !synthetic, !inherited else { return }
        if let context = info.integer("model_context_window"), context > 0 { state["model_context_window"] = context }
        var candidate = state.object("modern_candidate")
        if !candidate.isEmpty, !candidate.flag("matched"), candidate.string("turn") == state.string("turn_id"), (last == candidate["usage"] as? [Int] || last == nil) {
            candidate["matched"] = true; state["modern_candidate"] = candidate
            if var record = state["last_record"] as? Object, record.flag("context_owner_verified") {
                if source != "unknown" { record["limit_id"] = source }
                record["model_context_window"] = state["model_context_window"]
                try database.writeRecord(record); state["last_record"] = record
            }
            return
        }
        var amount: [Int]?, quality = "legacy_last"
        if let last, last[0]+last[3] > 0 { amount = last }
        else if let total {
            if high == nil && state.string("parent_id").isEmpty && (state["history_base"] == nil || state["history_base"] is NSNull) { amount = total; quality = "cumulative_observation" }
            else if let high, zip(total.prefix(5),high.prefix(5)).allSatisfy({$0 >= $1}), !(old.map { zip($0.prefix(5),high.prefix(5)).contains(where:{$0 > $1}) } ?? false) {
                amount = zip(total,high).map { max(0,$0-$1) }; quality = "cumulative_delta"
            }
        }
        guard let amount, amount[0]+amount[3] > 0, !stamp.isEmpty else { return }
        if let model = info["model"] ?? info["model_name"] ?? payload["model"] { state["model"] = model }
        let id = "legacy:"+identity([state.string("rollout_id"),stamp,signature])
        var record = makeRecord(state,usage:amount,id:id,stamp:stamp,quality:quality,owner:state.string("session_id"),turn:state.string("turn_id"))
        let response = state.object("modern_candidate").isEmpty ? state.string("active_response_id") : ""
        record["response_id"] = response.isEmpty ? NSNull() : response as Any
        let output = takeOutput(&state,response:response,turn:state.string("turn_id"))
        record["output_preview"] = quality.hasPrefix("cumulative") ? "" : output
        if !response.isEmpty { state["active_response_id"] = nil }
        record["limit_id"] = source == "unknown" ? NSNull() : source as Any
        try database.writeRecord(record); remember(record,state:&state)
    }
    private func makeRecord(_ state: Object,usage: [Int],id: String,stamp: String,quality: String,owner: String,turn: String) -> Object {
        var record: Object = ["id":id,"timestamp":stamp,"session_id":owner,"turn_id":turn,"source_id":"local","source_name":L("本机", "Local"),"provider":state.string("provider","unknown"),"quality":quality,"total_tokens":usage[0]+usage[3],"reported_total_tokens":usage[5]]
        for (index,key) in counters.prefix(5).enumerated() { record[key] = usage[index] }
        let owns = owner == state.string("session_id") && (state.string("turn_id").isEmpty || turn == state.string("turn_id"))
        record["context_owner_verified"] = owns
        record["model"] = owns ? state.string("model","unknown") : "unknown"
        if owns {
            for key in ["reasoning_effort","service_tier","model_context_window","prompt_preview"] { record[key] = state[key] }
        }
        if usage[1]+usage[2] > usage[0] || usage[4] > usage[3] { record["quality"] = quality+":invalid_subsets" }
        return record
    }
    private func agentEvent(_ entry: Object,state: inout Object,stamp: String) throws {
        guard entry.string("type") == "response_item" else { return }
        let payload = entry.object("payload"), kind = payload.string("type"), call = payload.string("call_id")
        guard !call.isEmpty else { return }
        var calls = state.object("request_calls")
        if ["function_call","custom_tool_call"].contains(kind) {
            let name = String(payload.string("name").split(separator:".").last ?? "")
            guard ["spawn_agent","followup_task","send_input"].contains(name), !state.string("turn_id").isEmpty else { return }
            let raw = payload["arguments"] ?? payload["input"]
            let args = raw as? Object ?? (raw as? String).map {jsonObject(Data($0.utf8))} ?? [:]
            var target = args.string(name == "spawn_agent" ? "task_name" : "target",args.string("id"))
            if !target.hasPrefix("/"), UUID(uuidString:target) == nil {
                target = (state.string("agent_path").isEmpty ? "/root" : state.string("agent_path"))+"/"+target
            }
            calls[call] = ["id":"agent-link:"+identity([state.string("session_id"),call]),"parent_session_id":state.string("session_id"),"parent_turn_id":state.string("turn_id"),"kind":name == "spawn_agent" ? "spawn" : "followup","target":target,"timestamp":stamp,"call_id":call]
            if calls.count > 64 { for key in calls.keys.sorted() where key != call { calls.removeValue(forKey:key); if calls.count <= 64 { break } } }
        } else if ["function_call_output","custom_tool_call_output"].contains(kind), var link = calls.removeValue(forKey:call) as? Object {
            let output = payload["output"]
            let result = output as? Object ?? (output as? String).map {jsonObject(Data($0.utf8))} ?? [:]
            guard !result.flag("isError"), result["error"] == nil, !["error","failed"].contains(result.string("status")), !result.isEmpty || link.string("kind") != "spawn" else { state["request_calls"] = calls; return }
            let child = result.string("agent_id",result.string("thread_id"))
            if !child.isEmpty { link["child_session_id"] = child }
            if !result.string("turn_id").isEmpty { link["child_turn_id"] = result["turn_id"] }
            try database.run("INSERT INTO usage_agent_links VALUES(?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data WHERE data<>excluded.data",[link.string("id"),jsonString(link)])
        }
        state["request_calls"] = calls
    }
}
