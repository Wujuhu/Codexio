import SwiftUI
import AppKit

struct JourneyBlock: Identifiable {
    var id:String
    var model:Object?
    var steps:[Object]
    static func build(_ events:[Object])->[JourneyBlock] {
        var result:[JourneyBlock]=[],pending:[Object]=[]
        let parents=Set(events.filter {$0.string("kind")=="tool"}.map {$0.string("id")})
        var nested:[String:[Object]]=[:]
        for e in events where parents.contains(e.string("parent_id")) {nested[e.string("parent_id"),default:[]].append(e)}
        func flush(){guard !pending.isEmpty else{return};if !result.isEmpty,result[result.count-1].model != nil{result[result.count-1].steps += pending}else{result.append(JourneyBlock(id:pending[0].string("id"),steps:pending))};pending=[]}
        for raw in events {
            if parents.contains(raw.string("parent_id")){continue}
            var e=raw;if let children=nested[e.string("id")] {e["children"]=children}
            let kind=e.string("kind")
            if e.flag("primary"){continue}
            if kind=="model"{result.append(JourneyBlock(id:e.string("id"),model:e,steps:pending));pending=[]}
            else if ["pause","resume","completed","userMessage","agentInput"].contains(kind){flush();result.append(JourneyBlock(id:e.string("id"),steps:[e]))}
            else{pending.append(e)}
        }
        flush();return result
    }
}

final class JourneyModel: ObservableObject {
    @Published var projects:[Object]=[]
    @Published var threads:[Object]=[]
    @Published var requests:[Object]=[]
    @Published var project=""
    @Published var thread:Object=[:]
    @Published var request:Object=[:]
    @Published var summary:Object=[:]
    @Published var selected:Object=[:]
    @Published var blocks:[JourneyBlock]=[]
    @Published var loading=false
    @Published var error:String?
    @Published var threadNext=""
    @Published var requestPage=1
    @Published var requestPages=1
    @Published var next=""
    @Published var scope=""
    var cursors=[""]
    private var catalogTicket=0,threadTicket=0,flowTicket=0
    private var catalogSignature=""
    private var flowSignature=""
    func cancel(){catalogTicket+=1;threadTicket+=1;flowTicket+=1}
    func catalog(_ state:AppState,project nextProject:String?=nil,before:String="",preferred:Object?=nil){
        let target=nextProject ?? project;catalogTicket+=1;let ticket=catalogTicket,roots=state.preferences.roots,snapshot=state.usage
        state.dataQueue.async{[weak self] in
            do {
                let data:Object
                if state.paths.mock {data=["projects":[["id":"chats","name":L("聊天","Chats")]],"threads":snapshot.chatCandidates.map {["id":$0.string("thread_id"),"title":snapshot.chatTitles[$0.string("thread_id")] ?? "—","project_id":"chats"]},"next":""]}
                else{data=try state.journeyCatalog.load(roots:roots,project:target,before:before)}
                DispatchQueue.main.async{
                    guard let self,self.catalogTicket==ticket else{return}
                    if self.project.isEmpty,let latest=data.objects("threads").first {self.project=latest.string("project_id","chats");self.catalog(state,project:self.project,preferred:latest);return}
                    self.projects=data.objects("projects");self.threadNext=data.string("next")
                    let signature=identity(data.objects("threads").map {[$0.string("id"),$0.string("title"),$0.string("project_id")]})
                    if self.catalogSignature != signature{self.catalogSignature=signature;self.threads=data.objects("threads")}
                    if let preferred {self.chooseThread(state,preferred)}else if self.thread.isEmpty,let first=self.threads.first{self.chooseThread(state,first)}
                }
            } catch {let message=error.localizedDescription;DispatchQueue.main.async{if self?.catalogTicket==ticket{self?.error=message}}}
        }
    }
    func chooseProject(_ state:AppState,_ id:String){cancel();error=nil;project=id;thread=[:];request=[:];selected=[:];blocks=[];flowSignature="";requests=[];threads=[];catalogSignature="";catalog(state,project:id)}
    func chooseThread(_ state:AppState,_ value:Object){error=nil;thread=value;request=[:];scope="";cursors=[""];flowTicket+=1;blocks=[];flowSignature="";selected=[:];loadThread(state,page:1)}
    func loadThread(_ state:AppState,page:Int?=nil){
        guard !thread.isEmpty else{return};let id=thread.string("id"),index=page ?? requestPage;threadTicket+=1;let ticket=threadTicket,snapshot=state.usage
        state.dataQueue.async{[weak self] in
            do {
                if state.paths.mock{try LocalChatRankingStore.update(snapshot,database:state.database)}
                let total=try state.database.query("SELECT count(*) AS n FROM usage_journey_requests WHERE session_id=?",[id]).first?.integer("n") ?? 0
                let pages=max(1,(total+24)/25),current=min(max(1,index),pages)
                let rows=try state.database.query("SELECT json_remove(data,'$.member_ids','$.member_turn_keys') AS data FROM usage_journey_requests WHERE session_id=? ORDER BY timestamp DESC,id DESC LIMIT 25 OFFSET ?",[id,(current-1)*25]).map {jsonObject(Data($0.string("data").utf8))}
                let raw=try state.database.query("SELECT data FROM usage_local_chat_rank WHERE thread_id=?",[id]).first?.string("data") ?? "{}"
                var sum=jsonObject(Data(raw.utf8));sum["requests"]=total
                DispatchQueue.main.async{
                    guard let self,self.threadTicket==ticket,self.thread.string("id")==id else{return}
                    self.requests=rows;self.requestPages=pages;self.requestPage=current;self.summary=sum
                    if self.request.isEmpty,let first=rows.first {self.chooseRequest(state,first)}
                    else if let refreshed=rows.first(where:{$0.string("id")==self.request.string("id")}){self.request=refreshed;self.flow(state,cursor:self.cursors.last ?? "")}
                }
            }catch{let message=error.localizedDescription;DispatchQueue.main.async{if self?.threadTicket==ticket{self?.error=message}}}
        }
    }
    func chooseRequest(_ state:AppState,_ row:Object){error=nil;request=row;scope="";cursors=[""];selected=[:];blocks=[];flowSignature="";flow(state)}
    func agent(_ state:AppState,_ id:String){scope=id;cursors=[""];selected=[:];blocks=[];flowSignature="";flow(state)}
    func flow(_ state:AppState,cursor:String=""){
        let id=request.string("id"),scope=scope,thread=thread,root=thread.string("root"),snapshot=state.usage
        guard !id.isEmpty else{return};flowTicket+=1;let ticket=flowTicket;loading=blocks.isEmpty
        state.dataQueue.async{[weak self] in
            do{
                let raw=try state.database.query("SELECT data FROM usage_journey_requests WHERE id=?",[id]).first?.string("data") ?? "{}",record=jsonObject(Data(raw.utf8))
                let session=scope.isEmpty ? thread.string("id") : scope
                var turns=Set((record["member_turn_keys"] as? [String] ?? []).compactMap{ key->String? in let parts=key.split(separator:":",maxSplits:2).map(String.init);return parts.count==3 && parts[1]==session ? parts[2] : nil})
                if scope.isEmpty{turns.insert(record.string("turn_id"))};turns.remove("")
                let calls=(record["member_ids"] as? [String] ?? []).compactMap{snapshot.callsByID[$0]}
                let metered=Dictionary(calls.filter {$0.raw.string("session_id")==session}.map{($0.raw.string("response_id"),$0.raw)},uniquingKeysWith:{$1})
                var data:Object
                if state.paths.mock {data=["events":calls.map{["id":$0.id,"kind":"model","model":$0.raw.string("model"),"cost_usd":$0.cost as Any? ?? NSNull(),"tokens":$0.tokens as Any? ?? NSNull(),"usage":$0.raw]},"next":""]}
                else{data=try state.journeyReader.page(root:URL(fileURLWithPath:root),thread:session,turns:turns,meters:metered,revision:snapshot.revision.uuidString,cursor:cursor,child:!scope.isEmpty,resumes:Set(record["resume_turn_ids"] as? [String] ?? []))}
                var events=data.objects("events")
                for index in events.indices where !events[index].string("agent_id").isEmpty {
                    let child=events[index].string("agent_id"),total=UsageSummary(rows:calls.filter {$0.raw.string("session_id")==child})
                    events[index]["cost_usd"]=total.cost;events[index]["tokens"]=total.tokens;events[index]["calls"]=total.calls
                }
                data["events"]=events;let blocks=JourneyBlock.build(events),signature=id+"|"+scope+"|"+cursor+"|"+identity(data)
                DispatchQueue.main.async{
                    guard let self,self.flowTicket==ticket,self.request.string("id")==id,self.scope==scope else{return}
                    if self.flowSignature != signature{self.flowSignature=signature;self.blocks=blocks;if let refreshed=events.first(where:{$0.string("id")==self.selected.string("id")}){self.selected=refreshed}}
                    if self.next != data.string("next"){self.next=data.string("next")};if self.loading{self.loading=false};if self.error != nil{self.error=nil}
                    if self.selected.isEmpty{self.selected=events.first(where:{$0.string("kind")=="model"}) ?? events.first ?? [:]}
                }
            }catch{let message=error.localizedDescription;DispatchQueue.main.async{if self?.flowTicket==ticket{self?.error=message;self?.loading=false}}}
        }
    }
}

struct JourneyView:View{
    @ObservedObject var state:AppState
    @StateObject private var model=JourneyModel()
    @State private var collapsed=Set<String>()
    var body:some View{
        VStack(alignment:.leading,spacing:16){
            SectionHeading(title:L("执行行程","Execution journey"))
            HStack(spacing:12){
                JourneyPicker(title:model.projects.first(where:{$0.string("id")==model.project})?.string("name") ?? L("项目","Project"),label:L("项目","Project"),selection:model.project,items:model.projects.map{JourneyPicker.Item(id:$0.string("id"),title:$0.string("name"))}){model.chooseProject(state,$0)}.frame(width:180)
                JourneyPicker(title:model.thread.string("title",L("线程","Thread")),label:L("线程","Thread"),selection:model.thread.string("id"),items:threadOptions){id in
                    if id=="__more"{model.catalog(state,before:model.threadNext)}else if let row=model.threads.first(where:{$0.string("id")==id}){model.chooseThread(state,row)}
                }.frame(maxWidth:320)
                Spacer(minLength:0)
            }
            if let error=model.error{StatusNote(text:error)}
            if model.thread.isEmpty{EmptyState(title:L("暂无本机聊天记录","No local chat records"),detail:"")}
            else{
                GeometryReader{geometry in
                    HSplitView{
                        VStack(spacing:0){
                            JourneyPicker(title:requestValueText(model.request.string("prompt_preview")),label:L("选择用户请求","Select request"),selection:model.request.string("id"),items:requestOptions){id in
                                if id=="__newer"{model.loadThread(state,page:model.requestPage-1)}
                                else if id=="__older"{model.loadThread(state,page:model.requestPage+1)}
                                else if let row=model.requests.first(where:{$0.string("id")==id}){collapsed=[];model.chooseRequest(state,row)}
                            }.padding(14)
                            Divider()
                            if !model.scope.isEmpty{Button(L("返回主行程","Back to main journey")){model.agent(state,"")}.buttonStyle(.plain).padding(10)}
                            ScrollView{
                                LazyVStack(alignment:.leading,spacing:12){
                                    if model.loading{ProgressView().controlSize(.small)}
                                    if model.blocks.isEmpty && !model.loading{Text("—").foregroundStyle(.secondary).padding(20)}
                                    ForEach(model.blocks){block in
                                        VStack(alignment:.leading,spacing:0){
                                            if let call=block.model{
                                                Button{model.selected=call;if !collapsed.insert(block.id).inserted{collapsed.remove(block.id)}}label:{HStack(spacing:8){JourneyIcon(kind:"model");Text(L("调用了 ","Called ")+modelName(call.string("model"))).lineLimit(1);Spacer(minLength:8);Text(money(call.number("cost_usd"))+" · "+compact(call.number("tokens"))+(call.number("tokens")==nil ? "" : " Token")).font(.system(size:12)).monospacedDigit();JourneyIcon(kind:collapsed.contains(block.id) ? "chevronRight":"chevronDown",size:12)}.font(.system(size:13,weight:.medium)).padding(12).frame(maxWidth:.infinity).contentShape(Rectangle())}.buttonStyle(.plain).background(Color.indigo.opacity(0.09))
                                            }
                                            if block.model==nil || !collapsed.contains(block.id){
                                                if block.model != nil{Divider()}
                                                ForEach(block.steps,id:\.journeyID){e in JourneyEventRow(event:e,selected:model.selected.string("id"),choose:{model.selected=$0},agent:{model.agent(state,$0)})}.padding(.horizontal,12).padding(.vertical,4)
                                            }
                                        }.clipShape(RoundedRectangle(cornerRadius:10)).overlay(RoundedRectangle(cornerRadius:10).stroke(block.model == nil ? Color.secondary.opacity(0.17) : Color.indigo.opacity(0.22)))
                                    }
                                    HStack{Button(L("上一页","Previous")){if model.cursors.count>1{model.cursors.removeLast();model.flow(state,cursor:model.cursors.last ?? "")}}.disabled(model.cursors.count<=1);Spacer();Button(L("下一页","Next")){model.cursors.append(model.next);model.flow(state,cursor:model.next)}.disabled(model.next.isEmpty)}.controlSize(.small)
                                }.padding(14).padding(.bottom,10)
                            }.frame(minHeight:0,maxHeight:.infinity)
                            Divider();HStack{Spacer();Button(L("在 Codex 中继续 ↗","Continue in Codex ↗")){open()}.buttonStyle(.bordered)}.padding(12)
                        }.frame(minWidth:300,idealWidth:geometry.size.width*48/77,maxWidth:.infinity).frame(height:geometry.size.height)
                        JourneyInspector(thread:model.thread,request:model.request,summary:model.summary,event:model.selected).frame(minWidth:230,idealWidth:geometry.size.width*29/77,maxWidth:.infinity).frame(height:geometry.size.height)
                    }.frame(width:geometry.size.width,height:geometry.size.height)
                }.containerRelativeFrame(.vertical){height,_ in max(320,min(650,height-140))}.clipShape(RoundedRectangle(cornerRadius:14)).overlay(RoundedRectangle(cornerRadius:14).stroke(.secondary.opacity(0.18)))
            }
        }.onAppear{model.catalog(state)}.onDisappear{model.cancel()}
        .onChange(of:state.usage.revision){_,_ in guard state.mainWindowVisible else{return};model.catalog(state);model.loadThread(state)}
    }
    private var threadOptions:[JourneyPicker.Item]{
        model.threads.map{JourneyPicker.Item(id:$0.string("id"),title:$0.string("title"))} + (model.threadNext.isEmpty ? [] : [.init(id:"__more",title:L("显示更多","Show more"))])
    }
    private var requestOptions:[JourneyPicker.Item]{
        model.requests.map{JourneyPicker.Item(id:$0.string("id"),title:requestValueText($0.string("prompt_preview")))} + (model.requestPage>1 ? [.init(id:"__newer",title:L("较新的请求","Newer requests"))] : []) + (model.requestPage<model.requestPages ? [.init(id:"__older",title:L("较早的请求","Earlier requests"))] : [])
    }
    private func open(){let id=model.thread.string("id");guard UUID(uuidString:id) != nil,let url=URL(string:"codex://threads/"+id),NSWorkspace.shared.urlForApplication(toOpen:url) != nil else{state.errorMessage=L("无法在本机定位此聊天","This chat cannot be opened locally");return};NSWorkspace.shared.open(url)}
}

// The native bordered control matches other filters; the anchored popover keeps
// long prompts and paged options inside a ten-row scrolling viewport.
private struct JourneyPicker:View{
    struct Item:Identifiable{let id:String;let title:String}
    let title:String,label:String,selection:String,items:[Item],choose:(String)->Void
    @State private var open=false
    private func preview(_ value:String)->String{
        let text=value.split(whereSeparator:{$0.isWhitespace}).joined(separator:" ")
        return text.count>80 ? String(text.prefix(80))+"…" : requestValueText(text)
    }
    var body:some View{
        GeometryReader{geometry in
            Button{open.toggle()}label:{
                HStack(spacing:8){Text(preview(title)).lineLimit(1).truncationMode(.tail).frame(maxWidth:.infinity,alignment:.leading);JourneyIcon(kind:"chevronDown",size:12).foregroundStyle(.secondary)}.font(.system(size:13)).frame(maxWidth:.infinity).contentShape(Rectangle())
            }.buttonStyle(.bordered).controlSize(.regular).frame(maxWidth:.infinity,maxHeight:.infinity).disabled(items.isEmpty).accessibilityLabel(label)
                .popover(isPresented:$open,arrowEdge:.bottom){
                    ScrollViewReader{reader in
                        ScrollView{
                            LazyVStack(spacing:0){
                                ForEach(items){item in
                                    Button{open=false;choose(item.id)}label:{
                                        HStack(spacing:8){Image(systemName:"checkmark").font(.system(size:10)).opacity(item.id==selection ? 1:0).frame(width:12);Text(preview(item.title)).lineLimit(1).truncationMode(.tail).frame(maxWidth:.infinity,alignment:.leading)}.font(.system(size:13)).padding(.horizontal,8).frame(height:30).contentShape(Rectangle())
                                    }.buttonStyle(.plain).background(item.id==selection ? Color.primary.opacity(0.07):.clear,in:RoundedRectangle(cornerRadius:5)).id(item.id)
                                }
                            }
                        }.frame(width:min(520,max(160,geometry.size.width)),height:CGFloat(min(10,items.count))*30).padding(4)
                            .onAppear{if items.contains(where:{$0.id==selection}){reader.scrollTo(selection,anchor:.center)}}
                    }
                }
        }.frame(height:30)
    }
}

struct JourneyIcon:View{
    let kind:String
    var size:CGFloat=16
    var body:some View{Image(nsImage:Branding.journeyIcon(kind)).resizable().renderingMode(.template).frame(width:size,height:size).accessibilityHidden(true)}
}

struct JourneyCommandButton:View{
    let command:String,tool:String
    @State private var open=false
    var body:some View{
        Button{open.toggle()}label:{JourneyIcon(kind:"command",size:14).frame(width:24,height:22).contentShape(Rectangle())}
            .buttonStyle(.plain).foregroundStyle(.secondary).background(.primary.opacity(0.045),in:RoundedRectangle(cornerRadius:5))
            .help(L("打开命令","Open command")).accessibilityLabel(L("打开命令","Open command"))
            .popover(isPresented:$open,arrowEdge:.bottom){JourneyCommandContent(command:command,tool:tool,close:{open=false})}
    }
}

struct JourneyCommandContent:View{
    let command:String,tool:String,close:()->Void
    @State private var commandHeight:CGFloat=18
    var body:some View{
        VStack(spacing:8){
            HStack{Text(tool).font(.system(size:12,weight:.medium));Spacer();Button(action:close){JourneyIcon(kind:"close",size:14)}.buttonStyle(.plain).accessibilityLabel(L("关闭","Close"))}
            Divider()
            ScrollView(.vertical){
                Text(command).font(.system(size:12,design:.monospaced)).textSelection(.enabled)
                    .frame(maxWidth:.infinity,alignment:.leading).fixedSize(horizontal:false,vertical:true)
                    .onGeometryChange(for:CGFloat.self){$0.size.height}action:{height in if abs(commandHeight-height)>0.5{commandHeight=height}}
            }.frame(height:min(360,max(18,commandHeight)))
        }.padding(12).frame(width:520)
    }
}

struct JourneyEventRow:View{
    let event:Object,selected:String,choose:(Object)->Void,agent:(String)->Void
    private var guidance:Bool{event.string("kind")=="userMessage"}
    private var fill:Color{guidance ? Color.blue.opacity(event.string("id")==selected ? 0.14:0.075) : event.string("id")==selected ? Color.primary.opacity(0.06):.clear}
    var body:some View{
        VStack(alignment:.leading,spacing:7){
            VStack(alignment:.leading,spacing:5){
                HStack(spacing:8){
                    JourneyIcon(kind:event.string("kind")).foregroundStyle(guidance ? Color.blue:Color.primary)
                    Button{choose(event)}label:{Text(journeyTitle(event)).font(.system(size:12,weight:guidance ? .medium:.regular)).lineLimit(1)}.buttonStyle(.plain).help(journeyTitle(event))
                    if !event.string("command").isEmpty{JourneyCommandButton(command:event.string("command"),tool:journeyTitle(event))}
                    Spacer(minLength:4)
                    Text(event["duration_ms"] != nil ? journeyDuration(event.number("duration_ms")) : logDateText(parsedDate(event["time"]),timeOnly:true)).font(.system(size:10)).foregroundStyle(.secondary)
                }.frame(minHeight:22)
                if !event.string("preview").isEmpty || event.string("kind")=="reasoning"{Text(requestValueText(event.string("preview"))).font(.system(size:11)).foregroundStyle(.secondary).lineLimit(4).padding(.leading,24)}
                JourneyFiles(files:event.objects("files")).padding(.leading,24)
            }.padding(7).frame(maxWidth:.infinity,alignment:.leading).contentShape(Rectangle()).onTapGesture{choose(event)}
                .background(fill,in:RoundedRectangle(cornerRadius:7))
                .overlay(RoundedRectangle(cornerRadius:7).stroke(guidance ? Color.blue.opacity(0.16):.clear).allowsHitTesting(false))
            if !event.string("agent_id").isEmpty{Button(L("查看子代理行程","View subagent journey")){agent(event.string("agent_id"))}.buttonStyle(.plain).font(.system(size:11)).padding(.leading,30)}
            ForEach(event.objects("children"),id:\.journeyID){child in AnyView(JourneyEventRow(event:child,selected:selected,choose:choose,agent:agent)).padding(.leading,15)}
        }.padding(.vertical,5)
    }
}
struct JourneyFiles:View{let files:[Object];var body:some View{ForEach(files.indices,id:\.self){i in HStack(alignment:.top,spacing:8){Text(journeyFileAction(files[i].string("action"))).foregroundStyle(.secondary);Text(files[i].string("path","—")).textSelection(.enabled)}.font(.system(size:11))}}}
struct JourneyInspector:View{
    let thread:Object,request:Object,summary:Object,event:Object
    var body:some View{
        ScrollView{
            VStack(alignment:.leading,spacing:18){
                Text(thread.string("title","—")).font(.system(size:14,weight:.medium))
                LazyVGrid(columns:[GridItem(.flexible()),GridItem(.flexible())],spacing:8){metric(L("费用","Cost"),money(summary.number("cost_usd")));metric("Token",compact(summary.number("tokens")));metric(L("请求","Requests"),summary.integer("requests").map(String.init) ?? "—");metric(L("模型调用","Model calls"),summary.integer("calls").map(String.init) ?? "—")}
                Divider();Text(requestValueText(request.string("prompt_preview"))).font(.system(size:13,weight:.medium)).lineLimit(4)
                fields([(L("费用","Cost"),money(request.number("cost_usd"))),("Token",compact(request.number("total_tokens"))),(L("模型调用","Model calls"),request.integer("call_count").map(String.init) ?? "—"),(L("耗时","Duration"),journeyDuration(request.number("duration_ms"))),(L("模型","Model"),modelName(request.string("model"))),(L("推理强度","Reasoning"),logEffortName(request.string("reasoning_effort"))),(L("速度","Speed"),journeySpeed(request.string("service_tier"))),(L("状态","Status"),journeyStatus(request.string("status"))),(L("开始","Start"),logDateText(parsedDate(request["started_at"] ?? request["timestamp"]))),(L("结束","End"),logDateText(parsedDate(request["ended_at"])))])
                if !event.isEmpty{Divider();HStack(spacing:8){JourneyIcon(kind:event.string("kind"));Text(journeyTitle(event)).font(.system(size:13,weight:.medium));if !event.string("command").isEmpty{JourneyCommandButton(command:event.string("command"),tool:journeyTitle(event))};Spacer(minLength:0)};if !event.string("preview").isEmpty{Text(event.string("preview")).font(.system(size:12)).textSelection(.enabled)}
                    if event.string("kind")=="model"{fields([(L("模型","Model"),modelName(event.string("model"))),(L("费用","Cost"),money(event.number("cost_usd"))),("Token",compact(event.number("tokens"))),(L("推理强度","Reasoning"),logEffortName(event.string("effort"))),(L("速度","Speed"),journeySpeed(event.string("speed"))),(L("耗时","Duration"),journeyDuration(event.number("duration_ms")))]+[("input_tokens",L("输入","Input")),("cached_input_tokens",L("缓存读取","Cache read")),("cache_write_input_tokens",L("缓存写入","Cache write")),("output_tokens",L("输出","Output")),("reasoning_output_tokens",L("推理输出","Reasoning output"))].map {($0.1,compact(event.object("usage").number($0.0)))})}
                    else{fields([(L("工具","Tool"),requestValueText(event.string("tool"))),(L("状态","Status"),journeyStatus(event.string("status"))),(L("耗时","Duration"),journeyDuration(event.number("duration_ms"))),(L("时间","Time"),logDateText(parsedDate(event["time"])))])}
                    if !event.string("agent_id").isEmpty{fields([(L("费用","Cost"),money(event.number("cost_usd"))),("Token",compact(event.number("tokens"))),(L("模型调用","Model calls"),event.integer("calls").map(String.init) ?? "—")])}
                    JourneyFiles(files:event.objects("files"))
                }
            }.padding(16)
        }
    }
    private func metric(_ title:String,_ value:String)->some View{VStack(alignment:.leading,spacing:5){Text(title).font(.system(size:11)).foregroundStyle(.secondary);Text(value).font(.system(size:16,weight:.medium)).monospacedDigit()}.frame(maxWidth:.infinity,alignment:.leading).padding(9).background(.primary.opacity(0.025),in:RoundedRectangle(cornerRadius:9))}
    private func fields(_ values:[(String,String)])->some View{VStack(spacing:10){ForEach(values.indices,id:\.self){i in HStack(alignment:.top){Text(values[i].0).foregroundStyle(.secondary);Spacer(minLength:12);Text(requestValueText(values[i].1)).multilineTextAlignment(.trailing).textSelection(.enabled)}}}.font(.system(size:12))}
}
extension Dictionary where Key==String,Value==Any{var journeyID:String{string("id")}}
func journeyDuration(_ ms:Double?)->String{guard let ms,ms>=0 else{return "—"};if ms==0{return "0 ms"};if ms<1{return "<1 ms"};if ms<1000{return String(format:"%.2f ms",ms)};return durationText(ms/1000)}
func journeySpeed(_ value:String)->String{switch normalizedTier(value){case "priority":return "Fast";case "default":return "Standard";case "ultrafast":return "Ultra Fast";case "mixed":return "Mixed";default:return "—"}}
func journeyStatus(_ value:String)->String{switch value{case "completed":return L("已完成","Completed");case "running","inProgress":return L("进行中","Running");case "failed":return L("失败","Failed");case "aborted","interrupted":return L("已中断","Interrupted");case "declined":return L("已拒绝","Declined");default:return "—"}}
func journeyFileAction(_ value:String)->String{switch value.lowercased(){case "add":return L("新增","Added");case "delete":return L("删除","Deleted");case "read":return L("读取","Read");case "search":return L("搜索","Search");case "listfiles":return L("列出","List");case "visualization":return L("可视化","Visualization");case "image":return L("生成","Generated");default:return L("修改","Modified")}}
func journeyTitle(_ e:Object)->String{switch e.string("kind"){case "model":return L("调用了 ","Called ")+modelName(e.string("model"));case "commandExecution","tool":return requestValueText(e.string("tool"));case "reasoning":return L("思考摘要","Reasoning summary");case "agentMessage":return e.string("phase")=="final_answer" ? L("返回回答","Answer") : L("进度说明","Progress");case "userMessage":return L("用户引导","User guidance");case "agentInput":return L("代理指令","Agent input");case "userInput":return L("AI 提问","AI question");case "userInputReply":return L("用户回答","User answer");case "fileChange":return L("文件改动","File changes");case "webSearch":return L("网页工具","Web tool");case "mcpToolCall":return "MCP · "+e.string("tool");case "subAgentActivity":return e.string("activity")=="started" ? L("启动子代理","Start subagent") : e.string("activity")=="completed" ? L("子代理完成","Subagent completed") : L("子代理","Subagent");case "collabAgentToolCall":return L("子代理 · ","Subagent · ")+e.string("tool");case "imageView":return L("查看图片","View image");case "imageGeneration":return L("生成图片","Generate image");case "contextCompaction":return L("上下文压缩","Context compaction");case "resume":return L("继续执行","Resumed");case "pause":return L("执行中断","Interrupted");case "completed":return L("本轮完成","Completed");case "sleep":return L("等待","Waiting");case "plan":return L("更新计划","Update plan");default:return requestValueText(e.string("kind"))}}
