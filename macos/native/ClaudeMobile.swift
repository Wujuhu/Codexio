import Foundation

struct ClaudeMobileSnapshot {
    var revision=""
    var recent:[UsageRow]=[]
    var days:[DayUsage]=[]
    var periods:[MobilePeriod]=[]
}
extension ClaudeLedger {
    func mobileSnapshot()throws->ClaudeMobileSnapshot {
        let all=try trend(range:UsageRange(period:"all"),granularity:"day")
        let periods=try [7,30,0].map{count->MobilePeriod in
            let value=count==0 ? all : try trend(range:UsageRange(period:count==7 ? "week":"month"),granularity:"day")
            let sum=value.summary
            return MobilePeriod(days:count,total:MobileMetric(tokens:sum.tokens,cost:sum.cost,requests:sum.requests,costComplete:sum.cost != nil,hitRate:sum.cacheRate),models:value.modelRows.first ?? [])
        }
        let recent=try database.query("SELECT q.data,(SELECT group_concat(line_hash) FROM (SELECT line_hash FROM claude_locations WHERE id=q.id ORDER BY offset DESC LIMIT 64)) AS signature FROM claude_requests q WHERE id LIKE ? AND timestamp>=? AND timestamp<=? ORDER BY timestamp DESC,id DESC LIMIT 200",[scope+"%",Date().timeIntervalSince1970-MobileProtocol.detailRetention,Date().timeIntervalSince1970]).map{row in var raw=jsonObject(Data(row.string("data").utf8));raw["message_digest"]=identity(row.string("signature"));return UsageRow(raw:raw)}
        return ClaudeMobileSnapshot(revision:String(revision),recent:recent,days:all.days,periods:periods)
    }
    func mobileDetail(_ id:String)throws->Object? {
        guard id.hasPrefix(scope),let stored=try database.query("SELECT data FROM claude_requests WHERE id=?",[id]).first else{return nil}
        let request=jsonObject(Data(stored.string("data").utf8)),rows=try original(id)
        let user=rows.first{$0.string("uuid")==request.string("main_uuid") && $0.string("type")=="user"}
        let last=try database.query("SELECT id,data FROM claude_calls WHERE request_id=? AND coalesce(json_extract(data,'$.is_subagent'),0)=0 ORDER BY timestamp DESC,id DESC LIMIT 1",[id]).first
        let call=last.map{jsonObject(Data($0.string("data").utf8))} ?? [:]
        let replies=try last.map{try original($0.string("id"))} ?? []
        let final=replies.filter{$0.string("type")=="assistant" && !$0.flag("isSidechain") && $0.string("agentId").isEmpty && $0.object("message").string("id")==call.string("response_id")}
        var texts:[String]=[],seen=Set<String>()
        for row in final {for part in row.object("message").objects("content") where part.string("type")=="text" {let text=part.string("text");if seen.insert(text).inserted{texts.append(text)}}}
        let terminal=final.contains{["end_turn","stop_sequence"].contains($0.object("message").string("stop_reason"))}
        let userText=user.map{JourneyEvent.text($0.object("message")["content"])} ?? ""
        var attachments:[Object]=[]
        for row in ([user].compactMap{$0}+final) {
            for (index,part) in row.object("message").objects("content").enumerated() where ["image","document"].contains(part.string("type")){
                let source=part.object("source"),mime=source.string("media_type",part.string("media_type")),name=part.string("title",part.string("name",L("图片","Image")))
                var item:Object=["id":identity(row.string("uuid")+":"+String(index)),"name":name,"mime":mime,"placement":row.string("type")=="user" ? "user":"final","created_at":parsedDate(row["timestamp"])?.timeIntervalSince1970 ?? parsedDate(request["timestamp"])?.timeIntervalSince1970 ?? 0]
                if source.string("type")=="base64",mime.hasPrefix("image/"),source.string("data").utf8.count<=16_777_216{item["reference"]="data:"+mime+";base64,"+source.string("data")}
                if attachments.count<32{attachments.append(item)}
            }
        }
        let available=user != nil || !final.isEmpty
        return ["id":id,"started_at":parsedDate(request["timestamp"])?.timeIntervalSince1970 ?? 0,"completed_at":parsedDate(request["ended_at"] ?? request["completed_at"])?.timeIntervalSince1970 as Any? ?? NSNull(),"status":request.string("status"),"user":userText,"final":terminal ? texts.joined(separator:"\n") : "","user_complete":user != nil,"final_complete":terminal && replies.count<64,"availability":available ? "available":"unavailable","attachments":attachments]
    }
}
