import SwiftUI

struct LogFilterMemory {
    var mode = "requests", period = "today", model = "all", status = "all", query = ""
    var page = 0
    var from = Calendar.current.date(byAdding:.day,value:-6,to:Date())!
    var through = Date()
}

struct LogFilterBar: View {
    @Binding var period: String
    @Binding var mode: String
    @Binding var model: String
    @Binding var status: String
    @Binding var query: String
    @Binding var from: Date
    @Binding var through: Date
    let models: [String]
    let fields: [String]
    let saveFields: ([String]) -> Void
    @State private var datesOpen = false
    @FocusState private var searchFocused: Bool
    var body: some View {
        VStack(spacing:14) {
            HStack(spacing:10) {
                PeriodPicker(selection:$period,custom:true,condensed:true)
                if period == "custom" { Button {datesOpen = true} label:{Image(systemName:"calendar")}.popover(isPresented:$datesOpen) {DateRangeControls(from:$from,through:$through).padding(18).frame(width:360)} }
                Spacer()
                CompactFilterPicker(title:mode == "requests" ? L("用户请求", "User requests") : L("模型调用", "Model calls"),label:L("日志类型", "Log type"),selection:mode,items:[.init(id:"requests",title:L("用户请求", "User requests")),.init(id:"calls",title:L("模型调用", "Model calls"))],choose:{mode = $0}).frame(width:140)
            }
            HStack(spacing:10) {
                CompactFilterPicker(title:model == "all" ? L("全部模型", "All models") : model,label:L("模型", "Model"),selection:model,items:[.init(id:"all",title:L("全部模型", "All models"))]+models.map {.init(id:$0,title:$0)},choose:{model = $0}).frame(width:170)
                CompactFilterPicker(title:statusTitle(status),label:L("状态", "Status"),selection:status,items:["all","running","completed","failed","aborted","unknown"].map {.init(id:$0,title:statusTitle($0))},choose:{status = $0}).frame(width:140)
                TextField(L("搜索正文或摘要", "Search prompt or summary"),text:$query).textFieldStyle(.roundedBorder).focused($searchFocused).frame(maxWidth:.infinity).frame(height:30)
                Menu {
                    ForEach(LogFields.all.filter {$0 != "content" && $0 != "details"},id:\.self) {field in
                        Toggle(LogFields.title(field),isOn:Binding(get:{fields.contains(field)},set:{enabled in
                            var selected = Set(fields); if enabled {selected.insert(field)} else {selected.remove(field)}
                            saveFields(LogFields.all.filter {selected.contains($0)})
                        }))
                    }
                    Divider(); Button(L("恢复默认字段", "Reset columns")) {saveFields(LogFields.defaults)}
                } label:{Image(systemName:"slider.horizontal.3")}.menuStyle(.borderlessButton).fixedSize().frame(width:30).help(L("显示字段", "Columns"))
            }
        }.onChange(of:period) {_,value in if value == "custom" {datesOpen = true}}
        .onReceive(NotificationCenter.default.publisher(for:.init("CodexioSearch"))) {_ in searchFocused = true}
    }
    private func statusTitle(_ value: String) -> String {
        switch value {
        case "all": return L("全部状态", "All statuses")
        case "running": return L("进行中", "In progress")
        case "completed": return L("已完成", "Completed")
        case "failed": return L("失败", "Failed")
        case "aborted": return L("已中断", "Aborted")
        default: return "—"
        }
    }
}
