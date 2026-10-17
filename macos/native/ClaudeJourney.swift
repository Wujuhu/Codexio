import Foundation

// Uses the existing ownership and byte locators. Only the selected request's
// spans are read; source bodies never become part of the normalized ledger.
final class ClaudeJourney {
    private let ledger:ClaudeLedger
    private var cache:[String:([FileStamp],Object)]=[:]
    init(_ ledger:ClaudeLedger){self.ledger=ledger}
    private var db:Database{ledger.database}
    func catalog(project:String,before:String)throws->Object{
        let owner="coalesce(nullif(json_extract(s.data,'$.projectId'),''),'chats')"
        var projects=try db.query("SELECT \(owner) AS id,coalesce(nullif(json_extract(s.data,'$.projectName'),''),\(owner)) AS name FROM claude_sessions s WHERE s.id LIKE ? GROUP BY 1",[ledger.scope+"%"])
        if !projects.contains(where:{$0.string("id")=="chats"}){projects.insert(["id":"chats","name":L("聊天","Chats")],at:0)}
        let cursor=jsonObject(Data(before.utf8)),stamp=cursor.number("stamp") ?? Double.greatestFiniteMagnitude,last=cursor.string("id","~")
        let filter=project.isEmpty ? "" : " AND project_id=?"
        var args:[Any]=[ledger.scope+"%",stamp,stamp,last];if !project.isEmpty{args.append(project)}
        var rows=try db.query("SELECT * FROM (SELECT s.id,coalesce(nullif(s.title,''),(SELECT json_extract(data,'$.prompt_preview') FROM claude_requests WHERE session_id=s.id ORDER BY timestamp,id LIMIT 1),'—') AS title,\(owner) AS project_id,(SELECT max(timestamp) FROM claude_requests WHERE session_id=s.id) AS stamp FROM claude_sessions s WHERE s.id LIKE ? AND EXISTS(SELECT 1 FROM claude_requests WHERE session_id=s.id)) WHERE (stamp<? OR (stamp=? AND id<?))\(filter) ORDER BY stamp DESC,id DESC LIMIT 51",args)
        let more=rows.count>50;rows=Array(rows.prefix(50));for i in rows.indices{
            let path=try db.query("SELECT l.path FROM claude_locations l JOIN claude_requests q ON q.id=l.id WHERE q.session_id=? ORDER BY l.offset LIMIT 1",[rows[i].string("id")]).first?.string("path") ?? ledger.root.path
            rows[i]["root"]=path
        }
        return ["projects":projects.map{row in var row=row;if row.string("id")=="chats"{row["name"]=L("聊天","Chats")};return row},"threads":rows,"next":more ? rows.last.map{jsonString(["stamp":$0["stamp"] ?? 0,"id":$0.string("id")])} ?? "" : ""]
    }
    func thread(_ id:String,page:Int)throws->Object{
        guard id.hasPrefix(ledger.scope) else{return [:]}
        let count=try db.query("SELECT count(*) AS n FROM claude_requests WHERE session_id=?",[id]).first?.integer("n") ?? 0,pages=max(1,(count+24)/25),current=min(max(1,page),pages)
        let requests=try db.query("SELECT data FROM claude_requests WHERE session_id=? ORDER BY timestamp DESC,id DESC LIMIT 25 OFFSET ?",[id,(current-1)*25]).map{jsonObject(Data($0.string("data").utf8))}
        let summary=try db.query("SELECT CASE WHEN count(total)=count(*) THEN sum(total) END AS tokens,CASE WHEN count(json_extract(data,'$.cost_usd'))=count(*) THEN sum(json_extract(data,'$.cost_usd')) END AS cost_usd,count(*) AS calls FROM claude_calls WHERE session_id=?",[id]).first ?? [:]
        return ["requests":requests,"summary":summary.merging(["requests":count]){$1},"page":current,"pages":pages]
    }
    func page(_ request:String,child:String,cursor:String)throws->Object{
        guard request.hasPrefix(ledger.scope) else{return ["events":[],"next":""]}
        // The first/last indexed messages bound old and new transcripts alike;
        // UUID ownership below excludes every intervening unrelated message.
        let spans=try db.query("SELECT l.path,min(l.offset) AS begin,max(l.offset+l.length) AS finish,l.device,l.inode FROM claude_locations l JOIN claude_sources s ON s.path=l.path WHERE s.present=1 AND l.generation=coalesce(json_extract(s.data,'$.generation'),0) AND l.id IN(SELECT id FROM claude_calls WHERE request_id=? UNION SELECT ?) GROUP BY l.path,l.device,l.inode ORDER BY min(l.offset),l.path LIMIT 128",[request,request])
        let inputs=spans.map{FileStamp(URL(fileURLWithPath:$0.string("path")))},key=request+"|"+child+"|"+cursor+"|"+String(ledger.revision)
        if let old=cache[key],old.0==inputs{return old.1}
        let position=jsonObject(Data(cursor.utf8));var index=max(0,position.integer("range") ?? 0),offset=max(0,position.integer("offset") ?? 0),events:[Object]=[],read=0,lastModel=position.string("model"),seen=Set<String>()
        while index<spans.count && read<8_388_608 && events.count<120{
            let span=spans[index],file=URL(fileURLWithPath:span.string("path")),stamp=inputs[index]
            guard stamp.exists,String(stamp.device)==span.string("device"),String(stamp.inode)==span.string("inode"),let begin=span.integer("begin"),let finish=span.integer("finish"),finish<=stamp.size else{index+=1;offset=0;continue}
            let anchor=try db.query("SELECT * FROM claude_locations WHERE path=? AND offset=? LIMIT 1",[file.path,begin]).first ?? [:]
            guard let handle=try? FileHandle(forReadingFrom:file) else{index+=1;offset=0;continue}
            try handle.seek(toOffset:UInt64(begin));let bytes=try handle.read(upToCount:anchor.integer("length") ?? 0) ?? Data();try handle.close()
            guard digest(bytes)==anchor.string("line_hash") else{index+=1;offset=0;continue}
            let next=try db.query("SELECT min(l.offset) AS offset FROM claude_locations l JOIN claude_requests q ON q.id=l.id WHERE l.path=? AND l.offset>=? AND q.id<>? AND l.uuid=json_extract(q.data,'$.main_uuid')",[file.path,finish,request]).first?.integer("offset")
            let end=min(Int(stamp.size),next ?? Int(stamp.size))
            let lines=try JourneyLines(file,offset:UInt64(max(begin,offset)),end:UInt64(end))
            while let line=lines.next(){
                offset=Int(line.end);read+=line.bytes;let row=jsonObject(line.data),uuid=row.string("uuid"),agent=row.string("agentId"),side=row.flag("isSidechain") || file.pathComponents.contains("subagents") || !agent.isEmpty
                let owner=try db.query("SELECT request_id FROM claude_nodes WHERE id=?",[ledger.scope+uuid]).first?.string("request_id")
                if owner==request, (child.isEmpty ? !side : agent==child || file.deletingPathExtension().lastPathComponent=="agent-"+child){
                    let response=row.object("message").string("id")
                    let meter=try db.query("SELECT data FROM claude_calls WHERE request_id=? AND json_extract(data,'$.response_id')=? AND coalesce(json_extract(data,'$.agent_id'),'')=? LIMIT 1",[request,response,agent]).first.map{jsonObject(Data($0.string("data").utf8))} ?? [:]
                    for event in ClaudeJourneyEvent.decode(row,meter:meter,lastModel:&lastModel) where seen.insert(event.string("id")).inserted{events.append(event)}
                }
                if read>=8_388_608 || events.count>=120{break}
            }
            if offset>=end{index+=1;offset=0;lastModel=""}
        }
        let result:Object=["events":events,"next":index<spans.count ? jsonString(["range":index,"offset":offset,"model":lastModel]) : ""]
        if cache.count>=3{cache.removeAll(keepingCapacity:true)};cache[key]=(inputs,result);return result
    }
}

enum ClaudeJourneyEvent {
    static func decode(_ row:Object,meter:Object,lastModel:inout String)->[Object]{
        let message=row.object("message"),type=row.string("type"),uuid=row.string("uuid"),time=row["timestamp"] ?? NSNull()
        var events:[Object]=[]
        func event(_ id:String,_ kind:String,_ extra:Object=[:])->Object{["id":id,"kind":kind,"time":time,"status":"","files":[] as [Object]].merging(extra){$1}}
        if type=="assistant"{
            let response=message.string("id")
            if !response.isEmpty,response != lastModel{
                lastModel=response
                events.append(event(response,"model",["model":meter["upstream_model"] ?? message["model"] ?? NSNull(),"effort":meter["reasoning_effort"] ?? NSNull(),"speed":meter["service_tier"] ?? NSNull(),"cost_usd":meter["cost_usd"] ?? NSNull(),"tokens":meter["total_tokens"] ?? NSNull(),"usage":meter,"duration_ms":meter["duration_ms"] ?? NSNull()]))
            }
            for (index,part) in message.objects("content").enumerated(){
                let id=uuid+":"+String(index)
                switch part.string("type"){
                case "thinking":events.append(event(id,"reasoning",["preview":JourneyEvent.clip(part.string("thinking"))]))
                case "text":events.append(event(id,"agentMessage",["preview":JourneyEvent.clip(part.string("text")),"phase":["end_turn","stop_sequence"].contains(message.string("stop_reason")) ? "final_answer":"commentary"]))
                case "tool_use":
                    let tool=part.string("name"),input=part.object("input"),path=input.string("file_path",input.string("path"))
                    let kind=tool=="Bash" ? "commandExecution" : ["Agent","Task"].contains(tool) ? "collabAgentToolCall" : tool.hasPrefix("mcp__") ? "mcpToolCall" : ["WebSearch","WebFetch"].contains(tool) ? "webSearch":"tool"
                    var extra:Object=["tool":tool,"command":input.string("command"),"preview":JourneyEvent.clip(input.string("description",input.string("prompt",input.string("query",input.string("url"))))) ]
                    if !path.isEmpty{extra["files"]=[["path":path,"action":tool=="Read" ? "read":tool=="Write" ? "add":"update"]]}
                    events.append(event(part.string("id",id),kind,extra))
                default:break
                }
            }
        }else if type=="user"{
            for (index,part) in message.objects("content").enumerated() where part.string("type")=="tool_result"{
                let result=row.object("toolUseResult"),agent=result.string("agentId")
                events.append(event(uuid+":"+String(index),agent.isEmpty ? "tool":"subAgentActivity",["parent_id":part.string("tool_use_id"),"tool":L("工具结果","Tool result"),"preview":JourneyEvent.clip(JourneyEvent.text(part["content"])),"status":part.flag("is_error") ? "failed":"completed","agent_id":agent,"activity":"completed","duration_ms":result["durationMs"] ?? NSNull()]))
            }
            if !message.objects("content").contains(where:{$0.string("type")=="tool_result"}), row.flag("turnCompanion") || !row.string("continuationOfPromptId").isEmpty{events.append(event(uuid,"userMessage",["preview":JourneyEvent.clip(requestPreview(JourneyEvent.text(message["content"])))]))}
        }else if type=="system",row.string("subtype")=="turn_duration"{events.append(event(uuid,"completed",["status":"completed","duration_ms":row["durationMs"] ?? NSNull()]))}
        else if type=="system",row.string("subtype")=="compact_boundary"{events.append(event(uuid,"contextCompaction"))}
        return events
    }
}
