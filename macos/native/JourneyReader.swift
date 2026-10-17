import Foundation

// Reads only the selected turn ranges. History-base byte/ordinal cuts are the
// same verified chain used by the prototype; no whole-history UI snapshot.
final class JourneyReader {
    struct Range {var file: URL;var segment: String;var user: String;var start: UInt64;var end: UInt64;var turn: String;var stamp: String}
    private let database: Database
    private let sources = ReadOnlySource()
    private var cache: [String:([FileStamp],Object)] = [:]
    private var headers: [URL:(FileStamp,Object)] = [:]
    init(_ database: Database) {self.database=database}
    func closeSources() { sources.clear() }
    private func segment(_ file: URL) -> String {
        let expression=try! NSRegularExpression(pattern:"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}")
        let name=file.lastPathComponent as NSString
        return expression.matches(in:name as String,range:NSRange(location:0,length:name.length)).last.map {name.substring(with:$0.range)} ?? ""
    }
    private func allowed(_ file: URL,root: URL) -> Bool {
        ["sessions","archived_sessions"].contains {file.standardizedFileURL.path.hasPrefix(root.appendingPathComponent($0).standardizedFileURL.path+"/")} && file.pathExtension=="jsonl" && file.resolvingSymlinksInPath()==file.standardizedFileURL
    }
    func ranges(root: URL,thread: String,turns: Set<String>) throws -> [Range] {
        guard !turns.isEmpty,let stateFile=JourneyCatalog.database(root,prefix:"state_"),let historyFile=JourneyCatalog.database(root,prefix:"thread_history_") else {throw AppFailure(L("本地过程记录暂不可用", "Local process history is unavailable"))}
        guard let head=try sources.read(stateFile,{db in try db.query("SELECT rollout_path FROM threads WHERE id=?",[thread]).first?.string("rollout_path")}),!head.isEmpty else {return []}
        return try sources.read(historyFile) { history in
            var file=URL(fileURLWithPath:head).standardizedFileURL,cut=UInt64.max,ordinal=Int.max,seen=Set<String>(),result:[Range]=[]
            for _ in 0..<128 {
                let id=segment(file);guard allowed(file,root:root),seen.insert(id).inserted else {throw AppFailure(L("历史片段关联不完整", "Incomplete history segment chain"))}
                let end=min(cut,FileStamp(file).size)
                let slots=Array(repeating:"?",count:turns.count).joined(separator:",")
                let sql="SELECT turn_id,first_user_item_id,rollout_byte_offset,rollout_end_byte_offset,started_at FROM thread_turns WHERE thread_id=? AND turn_id IN (\(slots)) AND rollout_ordinal<? ORDER BY rollout_ordinal"
                var segmentRanges:[Range]=[]
                for row in try history.query(sql,[id]+turns.sorted().map {$0 as Any}+[ordinal]) {
                    let begin=UInt64(max(0,row.integer("rollout_byte_offset") ?? 0)),finish=min(end,UInt64(max(0,row.integer("rollout_end_byte_offset") ?? Int(end))))
                    if finish>begin {segmentRanges.append(Range(file:file,segment:id,user:row.string("first_user_item_id"),start:begin,end:finish,turn:row.string("turn_id"),stamp:String(row.integer("started_at") ?? 0)))}
                }
                result.insert(contentsOf:segmentRanges,at:0)
                let stamp=FileStamp(file),base:Object
                if let saved=headers[file],saved.0==stamp {base=saved.1}
                else {
                    let reader=try JourneyLines(file,offset:0,end:min(end,2_097_152))
                    base=reader.next().map {jsonObject($0.data).object("payload").object("history_base")} ?? [:]
                    if headers.count>=128{headers.removeAll(keepingCapacity:true)};headers[file]=(stamp,base)
                }
                if base.isEmpty {break}
                let parent=base.string("thread_id");cut=UInt64(max(0,base.integer("end_byte_offset") ?? 0));ordinal=base.integer("end_ordinal_exclusive") ?? 0
                let candidates=try database.query("SELECT path FROM usage_session_files WHERE root=? AND path LIKE ? ORDER BY path LIMIT 256",[root.path,"%"+parent+"%.jsonl"])
                guard let previous=candidates.map({URL(fileURLWithPath:$0.string("path"))}).first(where:{segment($0)==parent}) else {throw AppFailure(L("历史片段关联不完整", "Incomplete history segment chain"))}
                file=previous.standardizedFileURL
        }
        return result
        }
    }
    func page(root: URL,thread: String,turns: Set<String>,meters: [String:Object],revision: String,cursor: String,child: Bool = false,resumes: Set<String> = []) throws -> Object {
        let spans=try ranges(root:root,thread:thread,turns:turns),inputs=spans.map {FileStamp($0.file)}
        let key=thread+"|"+turns.sorted().joined(separator:",")+"|"+spans.map {"\($0.segment):\($0.start):\($0.end)"}.joined(separator:",")+"|"+cursor
        if let old=cache[key],old.0==inputs {return metered(old.1,meters:meters,resumes:resumes)}
        let position=jsonObject(Data(cursor.utf8));var index=max(0,position.integer("range") ?? 0),offset=UInt64(max(0,position.integer("offset") ?? 0)),events:[Object]=[],seen=Set<String>(),readBytes=0
        var active=position.string("turn"),parent=position.string("parent"),next=""
        while index<spans.count && events.count<120 && readBytes<8_388_608 {
            let span=spans[index],reader=try JourneyLines(span.file,offset:min(span.end,max(span.start,offset)),end:span.end)
            if active.isEmpty {active=span.turn}
            while let line=reader.next() {
                readBytes+=line.bytes;offset=line.end
                if let row=try? JSONSerialization.jsonObject(with:line.data) as? Object {
                    let p=row.object("payload"),kind=row.string("type"),type=p.string("type")
                    if kind=="turn_context" || (kind=="event_msg" && type=="task_started") {active=p.string("turn_id")}
                    let owner=p.string("turn_id",active)
                    if turns.contains(owner),var event=JourneyEvent.decode(row,meters:[:],parent:&parent) {
                        event["turn_id"]=owner
                        if event.string("kind")=="userMessage" {event["primary"] = !child && event.string("id")==spans.first?.user;if child{event["kind"]="agentInput"}}
                        if seen.insert(event.string("id")).inserted {events.append(event)}
                    }
                }
                if events.count>=120 || readBytes>=8_388_608 {break}
            }
            if offset>=span.end {index+=1;offset=0;active="";parent=""}
            if events.count>=120 || readBytes>=8_388_608 {break}
        }
        if index<spans.count {next=jsonString(["range":index,"offset":offset,"turn":active,"parent":parent])}
        let result:Object=["events":events,"next":next]
        if cache.count>=3 {cache.removeAll(keepingCapacity:true)}
        if ((try? jsonData(result).count) ?? Int.max) <= 2_097_152 {cache[key]=(inputs,result)}
        return metered(result,meters:meters,resumes:resumes)
    }
    private func metered(_ page:Object,meters:[String:Object],resumes:Set<String>)->Object{
        var page=page
        page["events"]=page.objects("events").compactMap {raw->Object? in
            if raw.string("kind")=="turnStart"{guard resumes.contains(raw.string("turn_id")) else{return nil};var e=raw;e["kind"]="resume";return e}
            guard raw.string("kind")=="model" else{return raw};var row=raw
            let meter=meters[row.string("id")] ?? [:]
            row["model"]=meter["model"];row["effort"]=meter["reasoning_effort"];row["speed"]=meter["service_tier"];row["cost_usd"]=meter["cost_usd"]
            return row
        }
        return page
    }
}

final class JourneyLines {
    let handle: FileHandle;let end: UInt64
    private var offset:UInt64;private var buffer=Data();private var discarded=0
    private var prefix=Data(),tail=Data()
    init(_ url:URL,offset:UInt64,end:UInt64)throws {handle=try FileHandle(forReadingFrom:url);self.offset=offset;self.end=end;try handle.seek(toOffset:offset)}
    deinit {try? handle.close()}
    func next() -> (data:Data,end:UInt64,bytes:Int)? {
        while true {
            if let newline=buffer.firstIndex(of:10) {
                let count=buffer.distance(from:buffer.startIndex,to:newline)+1
                let line=discarded==0 ? Data(buffer.prefix(count-1)) : largeRecord(ending:Data(buffer.prefix(count-1)))
                buffer.removeFirst(count);let used=count+discarded;discarded=0;offset+=UInt64(count)
                prefix=Data();tail=Data()
                return (line,offset,used)
            }
            let available=end-offset-UInt64(buffer.count)
            if available==0 {
                guard !buffer.isEmpty || discarded>0 else{return nil}
                let line=discarded==0 ? buffer : largeRecord(ending:buffer),used=buffer.count+discarded;offset+=UInt64(buffer.count);buffer=Data();discarded=0;prefix=Data();tail=Data()
                return (line,offset,used)
            }
            guard let data=try? handle.read(upToCount:Int(min(65_536,available))),!data.isEmpty else{return nil}
            buffer.append(data)
            if buffer.count>2_097_152 && !buffer.contains(10) {if prefix.isEmpty{prefix=Data(buffer.prefix(8192))};tail=Data(buffer.suffix(8192));discarded+=buffer.count;offset+=UInt64(buffer.count);buffer.removeAll(keepingCapacity:true)}
        }
    }
    private func largeRecord(ending:Data)->Data{
        let expression=try! NSRegularExpression(pattern:#"[,\{]\s*"([A-Za-z_]+)"\s*:\s*("(?:[^"\\]|\\.)*")"#)
        var fields:Object=[:],types:[String]=[]
        for data in [prefix,Data((tail+ending).suffix(8192))] {
            let content=String(decoding:data,as:UTF8.self) as NSString
            for match in expression.matches(in:content as String,range:NSRange(location:0,length:content.length)) {
                let key=content.substring(with:match.range(at:1)),raw=content.substring(with:match.range(at:2))
                guard let value=(try? JSONSerialization.jsonObject(with:Data(raw.utf8),options:.fragmentsAllowed)) as? String else{continue}
                if key=="type"{types.append(value)};if fields[key]==nil{fields[key]=value}
            }
        }
        if types.count>=2,types[0]=="response_item",["function_call_output","custom_tool_call_output"].contains(types[1]) {return (try? jsonData(["timestamp":fields.string("timestamp"),"type":"response_item","payload":["type":types[1],"call_id":fields.string("call_id")]])) ?? Data()}
        guard types.count>=3,types[0]=="event_msg",types[1]=="item_completed",!fields.string("id").isEmpty else{return Data()}
        var item:Object=["type":types[2],"id":fields.string("id")]
        for key in ["kind","path","savedPath","revisedPrompt","tool","server","phase","status"] {item[key]=fields[key]}
        return (try? jsonData(["timestamp":fields.string("timestamp"),"type":"event_msg","payload":["type":"item_completed","turn_id":fields.string("turn_id"),"item":item]])) ?? Data()
    }
}

enum JourneyEvent {
    static func pending(_ value:String)->[String]{guard !value.isEmpty else{return []};return (try? JSONSerialization.jsonObject(with:Data(value.utf8))) as? [String] ?? [value]}
    static func start(_ value:inout String,id:String){var ids=pending(value);if !ids.isEmpty && !ids.contains("__ambiguous__"){ids.append("__ambiguous__")};if ids.count>64{ids=["__ambiguous__"]};ids.append(id);value=jsonString(ids)}
    static func finish(_ value:inout String,id:String){let ids=pending(value).filter {$0 != id};value=ids.isEmpty || ids==["__ambiguous__"] ? "" : jsonString(ids)}
    static func text(_ value:Any?) -> String {
        if let value=value as? String{return value}
        if let values=value as? [Any] {return values.map {text($0)}.filter {!$0.isEmpty}.joined(separator:"\n")}
        if let value=value as? Object {return value.string("text",text(value["content"]))}
        return ""
    }
    static func answer(_ content:String)->String {
        guard let start=content.range(of:"<send_user_message_question_reply>"),let end=content.range(of:"</send_user_message_question_reply>"),start.upperBound<=end.lowerBound,let values=(try? JSONSerialization.jsonObject(with:Data(content[start.upperBound..<end.lowerBound].utf8))) as? [Object] else{return clip(requestPreview(content))}
        return clip(values.map {$0.string("question")+"\n"+text($0["answer"])}.joined(separator:"\n"))
    }
    static func clip(_ value:String,_ limit:Int=900)->String {String(value.prefix(limit))+(value.count>limit ? "…" : "")}
    static func decode(_ row:Object,meters:[String:Object],parent:inout String)->Object? {
        let p=row.object("payload"),type=p.string("type"),kind=row.string("type"),time=row.string("timestamp")
        var e:Object=["time":time,"status":"","files":[] as [Object]]
        if kind=="token_usage_record" {
            let id=p.string("response_id"),usage=p.object("usage"),meter=meters[id] ?? [:]
            e.merge(["id":id,"kind":"model","model":meter["model"] ?? NSNull(),"effort":meter["reasoning_effort"] ?? NSNull(),"speed":meter["service_tier"] ?? NSNull(),"cost_usd":meter["cost_usd"] ?? NSNull(),"tokens":usage["total_tokens"] ?? NSNull(),"usage":usage,"status":"completed"],uniquingKeysWith:{$1});return e
        }
        if kind=="response_item" && ["function_call","custom_tool_call"].contains(type) {
            let name=p.string("name"),id=p.string("call_id",p.string("id"));start(&parent,id:id)
            e["id"]="call:"+id;e["kind"]="tool";e["tool"]=name;e["command"]=clip(p.string("input",p.string("arguments")),65_536)
            if name.contains("request_user_input") {e["kind"]="userInput";let args=jsonObject(Data(p.string("arguments").utf8));e["preview"]=clip(args.objects("questions").map {$0.string("title",$0.string("question"))}.joined(separator:"\n"));e["command"]=nil}
            return e
        }
        if kind=="response_item" && ["function_call_output","custom_tool_call_output"].contains(type) {finish(&parent,id:p.string("call_id"));return nil}
        if kind=="event_msg",type=="task_started"{e["id"]="start:"+p.string("turn_id");e["kind"]="turnStart";return e}
        if kind=="event_msg" && ["task_complete","turn_aborted"].contains(type) {e["id"]=type+":"+p.string("turn_id")+":"+time;e["kind"]=type=="task_complete" ? "completed" : "pause";e["status"]=type=="task_complete" ? "completed" : "interrupted";return e}
        if kind=="compacted" {e["id"]="compact:"+time;e["kind"]="contextCompaction";return e}
        guard kind=="event_msg",type=="item_completed" else{return nil}
        var item=p.object("item");let raw=item.string("type"),normalized=raw.prefix(1).lowercased()+raw.dropFirst()
        if raw=="Extension" {item["type"]=item.string("kind","extension")}
        let eventKind=raw=="Extension" ? item.string("type") : normalized
        e["id"]=item.string("id");e["kind"]=eventKind;e["status"]=item.string("status","completed")
        if let stamp=p.number("started_at_ms") {e["time"]=iso(Date(timeIntervalSince1970:stamp/1000))}
        let duration=item.object("duration")
        if !duration.isEmpty {e["duration_ms"]=(duration.number("secs") ?? 0)*1000+(duration.number("nanos") ?? 0)/1_000_000}
        else {e["duration_ms"]=item["durationMs"]}
        switch eventKind {
        case "reasoning":e["preview"]=clip(text(item["summary_text"] ?? item["summary"]),2000)
        case "agentMessage":
            let message=text(item["content"] ?? item["text"]),pattern=try! NSRegularExpression(pattern:"visualize(.*?)",options:.dotMatchesLineSeparators),value=message as NSString
            let artifacts=pattern.matches(in:message,range:NSRange(location:0,length:value.length)).compactMap {match->Object? in let data=jsonObject(Data(value.substring(with:match.range(at:1)).utf8));return data.string("path").isEmpty ? nil : ["path":data.string("path"),"action":"visualization"]}
            e["preview"]=clip(pattern.stringByReplacingMatches(in:message,range:NSRange(location:0,length:value.length),withTemplate:""));e["phase"]=item["phase"];e["files"]=artifacts
        case "userMessage":
            let content=text(item["content"]);e["preview"]=clip(requestPreview(content));if content.contains("send_user_message_question_reply") {e["kind"]="userInputReply";e["preview"]=answer(content)}
        case "commandExecution":
            let parts=item["command"] as? [String] ?? [];e["command"]=clip(parts.count==3 && ["-lc","-c"].contains(parts[1]) ? parts[2] : parts.isEmpty ? item.string("command") : parts.joined(separator:" "),65_536)
            e["tool"]="exec_command";e["cwd"]=item["cwd"];e["exit_code"]=item["exit_code"] ?? item["exitCode"]
            e["files"]=item.objects("parsed_cmd").compactMap { value->Object? in let path=value.string("path",value.string("name"));return path.isEmpty ? nil : ["path":path,"action":value.string("type")] }
        case "fileChange":
            var changes=item.objects("changes")
            if changes.isEmpty {changes=item.object("changes").map {key,value in var value=value as? Object ?? [:];value["path"]=key;return value}}
            e["tool"]="apply_patch";e["files"]=changes.map {value in ["path":value.string("path"),"action":value.object("kind").string("type",value.string("type","update")),"destination":value["move_path"] ?? value.object("kind")["move_path"] ?? NSNull()] as Object}
        case "webSearch":e["tool"]="web.run";let action=item.object("action");e["preview"]=clip(action.string("query",text(action["queries"]).isEmpty ? action.string("url",action.string("pattern",item.string("query"))) : text(action["queries"])));e["action"]=action["type"]
        case "mcpToolCall":e["tool"]=item["tool"];e["server"]=item["server"];e["preview"]=clip(jsonString(item["arguments"] ?? [:]),400)
        case "subAgentActivity":e["agent_id"]=item["agent_thread_id"];e["preview"]=item["agent_path"];e["activity"]=item["kind"]
        case "collabAgentToolCall":e["tool"]=item["tool"];e["preview"]=clip(text(item["receiver_thread_ids"]));e["agent_ids"]=item["receiver_thread_ids"]
        case "imageView":e["tool"]="view_image";e["preview"]=item["path"]
        case "imageGeneration":e["tool"]="imagegen";e["preview"]=clip(item.string("revisedPrompt"));e["files"]=[["path":item.string("savedPath"),"action":"image"]]
        case "sleep":e["duration_ms"]=item["duration_ms"] ?? item["durationMs"]
        default:e["preview"]=clip(item.string("text"));e["tool"]=item["tool"]
        }
        let calls=pending(parent)
        if calls.count==1,calls[0] != "__ambiguous__",!["userMessage","reasoning","agentMessage"].contains(eventKind) {e["parent_id"]="call:"+calls[0]}
        return e
    }
}
