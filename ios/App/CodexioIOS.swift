import SwiftUI
import Charts
import UIKit

private enum MobileBrand {
    static let wordmark: UIImage? = UIImage(named:"CodexioWordmark") ?? Bundle.main.url(forResource:"wordmark",withExtension:"png").flatMap {UIImage(contentsOfFile:$0.path)}
}

@main struct CodexioIOSApp: App {
    @StateObject private var store = MobileStore()
    @Environment(\.scenePhase) private var phase
    @AppStorage("theme") private var theme = "system"
    var body: some Scene {
        WindowGroup {
            MobileRoot().environmentObject(store).preferredColorScheme(theme == "light" ? .light : theme == "dark" ? .dark : nil)
                .onChange(of:phase,initial:true) { _,value in if value == .active { store.activate() } else { store.deactivate() } }
        }
    }
}
enum MobileFormat {
    static func tokens(_ value: Int?) -> String {
        guard let value else { return "—" }
        if value >= 1_000_000 { return String(format:"%.2fM",Double(value)/1_000_000) }
        if value >= 1_000 { return String(format:"%.1fK",Double(value)/1_000) }
        return String(value)
    }
    static func money(_ value: Double?) -> String { value.map {String(format:"$%.2f",$0)} ?? "—" }
    static func speed(_ value: String?) -> String {
        switch value?.trimmingCharacters(in:.whitespacesAndNewlines).lowercased() {
        case "default", "standard": return "standard"
        case "priority", "fast": return "fast"
        case "ultrafast": return "ultrafast"
        case "mixed": return "mixed"
        default: return "—"
        }
    }
    static func duration(_ seconds: Double?) -> String {
        guard let seconds, seconds.isFinite, seconds >= 0, seconds < Double(Int.max) else { return "—" }
        let value = Int(seconds)
        if value >= 3600 { return "\(value/3600)小时\(value%3600/60)分\(value%60)秒" }
        if value >= 60 { return "\(value/60)分\(value%60)秒" }
        return "\(value)秒"
    }
    static let update: DateFormatter = {let f=DateFormatter(); f.dateFormat="M.d HH:mm"; f.timeZone = .autoupdatingCurrent; return f}()
    static let requestDate: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier:"zh_CN"); f.calendar = Calendar(identifier:.gregorian)
        f.dateFormat = "yyyy年M月d日"; f.timeZone = .autoupdatingCurrent
        return f
    }()
    static func date(_ value: Double) -> String { Date(timeIntervalSince1970:value).formatted(.dateTime.month().day()) }
}
struct RequestDuration: View {
    @EnvironmentObject private var store: MobileStore
    let item: MobileRequest
    let active: Bool
    @Environment(\.scenePhase) private var phase
    @State private var visible = false
    var body: some View {
        Group {
            if item.status == "running", let start = store.runningStarts[item.id] {
                TimelineView(.animation(minimumInterval:1,paused:!active || !visible || phase != .active)) { context in
                    Text(MobileFormat.duration(max(0,context.date.timeIntervalSince(start))))
                }
            } else {
                Text(MobileFormat.duration(item.duration))
            }
        }.monospacedDigit().lineLimit(1)
            .onAppear { visible = true }.onDisappear { visible = false }
    }
}
struct MobileCard<Content: View>: View {
    @ViewBuilder var content: Content
    var body: some View { VStack(alignment:.leading,spacing:16) {content}.frame(maxWidth:.infinity,alignment:.leading).padding(20).background(Color(uiColor:.secondarySystemGroupedBackground),in:RoundedRectangle(cornerRadius:24)) }
}
struct MobileRoot: View {
    @EnvironmentObject var store: MobileStore
    @State private var scanner = false
    var body: some View {
        TabView(selection:$store.tab) {
            NavigationStack { OverviewPage(scan:{scanner=true}).navigationTitle("").toolbar {header} }.tabItem {Label("概览",systemImage:"square.grid.2x2")}.tag(0)
            NavigationStack(path:$store.recordPath) { RecordsPage().navigationTitle("记录").navigationDestination(for:String.self) { RequestDetails(id:$0) } }.tabItem {Label("记录",systemImage:"list.bullet")}.tag(2)
            NavigationStack { UsagePage().navigationTitle("用量").navigationBarTitleDisplayMode(.large) }.tabItem {Label("用量",systemImage:"chart.xyaxis.line")}.tag(1)
            NavigationStack { MobileSettings(scan:{scanner=true}).navigationTitle("设置") }.tabItem {Label("设置",systemImage:"slider.horizontal.3")}.tag(3)
        }
        .sheet(isPresented:$scanner) { NavigationStack { QRScanner { value in scanner=false; store.beginPair(value) }.ignoresSafeArea(edges:.bottom).navigationTitle("扫描电脑配对二维码").navigationBarTitleDisplayMode(.inline).toolbar {ToolbarItem(placement:.cancellationAction) { Button("取消") {scanner=false} }} } }
        .alert(store.revokedPrompt != nil ? "电脑配对已失效" : "同步提示",isPresented:Binding(get:{store.revokedPrompt != nil || store.error != nil},set:{if !$0 {store.error=nil;store.revokedPrompt=nil}})) {
            if let id = store.revokedPrompt {
                Button("稍后",role:.cancel) {store.revokedPrompt=nil}
                Button("删除",role:.destructive) {store.remove(id)}
            } else {Button("知道了") {store.error=nil}}
        } message: {Text(store.revokedPrompt != nil ? "电脑已撤销这部 iPhone 的配对。是否删除这台电脑及其手机缓存？" : store.error ?? "")}
    }
    @ToolbarContentBuilder private var header: some ToolbarContent {
        ToolbarItem(placement:.topBarLeading) {
            Group {
                if let image = MobileBrand.wordmark { Image(uiImage:image).renderingMode(.template).resizable().scaledToFit() }
                else { Text("Codexio").font(.title2.bold()) }
            }.foregroundStyle(.primary).frame(width:118,height:28).accessibilityLabel("Codexio")
        }.sharedBackgroundVisibility(.hidden)
        ToolbarItem(placement:.topBarTrailing) {
            Menu {
                ForEach(store.devices) { device in Button(device.code.name) {store.select(device.id)} }
                Button("添加电脑",systemImage:"qrcode.viewfinder") {scanner=true}
            } label: {Label(store.live?.name ?? "电脑",systemImage:"laptopcomputer").font(.subheadline)}
        }
    }
}
struct OverviewPage: View {
    @EnvironmentObject var store: MobileStore
    let scan: () -> Void
    var body: some View {
        ScrollView {
            VStack(alignment:.leading,spacing:18) {
                VStack(spacing:8) {
                    HStack(alignment:.center,spacing:10) {
                        Text("概览").font(.largeTitle.bold())
                        if store.refreshing { ProgressView().controlSize(.small).frame(width:16,height:16).accessibilityLabel("正在同步") }
                        Spacer()
                        Button(action:{store.refresh()}) {Label("刷新",systemImage:"arrow.clockwise")}.buttonStyle(.glass).disabled(store.refreshing || store.device == nil)
                    }
                    HStack(alignment:.firstTextBaseline) {
                        Text(store.connectionLabel)
                        Spacer()
                        Text(store.updated.map {"上次更新："+MobileFormat.update.string(from:$0)} ?? "尚未同步").monospacedDigit()
                    }.font(.caption).foregroundStyle(.secondary)
                }
                if store.devices.isEmpty && !store.pairing { Button(action:scan) {Label("连接我的电脑",systemImage:"qrcode.viewfinder").frame(maxWidth:.infinity)}.buttonStyle(.glassProminent) }
                if store.pairing { MobileCard {Label("正在配对",systemImage:"link").font(.headline); Text(store.status); Button("取消配对") {store.cancelPair()} } }
                Button { if let task = store.overviewTask { store.showRequest(task) } } label: {
                    MobileCard {
                        HStack {
                            Text(store.overviewTask?.status == "completed" ? "最近完成的任务" : "当前任务").font(.headline)
                            Spacer()
                            if store.overviewTask?.status == "running" { Image(systemName:"circle.dotted").symbolEffect(.rotate) }
                            else if store.overviewTask?.status == "completed" { Image(systemName:"checkmark.circle.fill").foregroundStyle(.green).accessibilityLabel("已完成") }
                            if store.overviewTask != nil { Image(systemName:"chevron.right").font(.caption).foregroundStyle(.secondary) }
                        }
                        if let task = store.overviewTask {
                            if task.status == "running", Date().timeIntervalSince1970-(store.live?.observed ?? 0) > 900 { Text("任务状态待更新").font(.caption).foregroundStyle(.orange) }
                            Text(task.preview ?? "请求预览未同步").font(.title3.weight(.semibold)).lineLimit(3)
                            HStack(alignment:.firstTextBaseline) {
                                Text([task.model.capitalized,task.effort ?? ""].filter {!$0.isEmpty}.joined(separator:" · ")).lineLimit(1)
                                Spacer(minLength:8)
                                RequestDuration(item:task,active:store.tab == 0).fixedSize()
                            }.font(.caption).foregroundStyle(.secondary)
                            HStack {Text(MobileFormat.tokens(task.tokens)+" Token"); Spacer();Text(MobileFormat.money(task.cost))}.monospacedDigit()
                        } else { Text("暂无运行或已完成的任务").foregroundStyle(.secondary).padding(.vertical,10) }
                    }
                }.buttonStyle(.plain).disabled(store.overviewTask == nil)
                MobileCard { quota("5 小时额度",store.live?.five); Divider();quota("周额度",store.live?.week) }
                MobileCard { Text("今天").font(.headline); metricRow(store.live?.today ?? MobileMetric()) }
                if !store.recent.isEmpty {
                    Text("最近使用").font(.title2.bold())
                    MobileCard { ForEach(Array(store.recent.prefix(3))) { item in Button { store.showRequest(item) } label: {RequestSummary(item:store.request(item.id) ?? item,active:store.tab == 0)}.buttonStyle(.plain); if item.id != store.recent.prefix(3).last?.id {Divider()} } }
                }
            }.padding(20)
        }.background(Color(uiColor:.systemGroupedBackground)).refreshable {store.refresh()}
    }
    private func quota(_ title: String,_ value: MobileQuota?) -> some View {
        VStack(alignment:.leading,spacing:10) {
            HStack {Text(title).font(.headline);Spacer();Text(value?.remaining.map {String(format:"%.0f%%",$0)} ?? "—").font(.title2.bold()).monospacedDigit()}
            GeometryReader { g in ZStack(alignment:.leading) {Capsule().fill(.blue.opacity(0.12));Capsule().fill(.blue).frame(width:g.size.width*max(0,min(1,(value?.remaining ?? 0)/100)))} }.frame(height:9)
            HStack {Text("重置时间");Spacer();Text(value?.reset.map {MobileFormat.update.string(from:Date(timeIntervalSince1970:$0))} ?? "—")}.font(.caption).foregroundStyle(.secondary)
            if value?.retained == true {Text("上次成功获取的额度").font(.caption2).foregroundStyle(.secondary)}
        }
    }
}
@ViewBuilder private func metricRow(_ value: MobileMetric) -> some View {
    HStack(spacing:8) {
        MetricCell(title:"费用",value:MobileFormat.money(value.cost))
        Divider()
        MetricCell(title:"Token",value:MobileFormat.tokens(value.tokens))
        Divider()
        MetricCell(title:"请求",value:String(value.requests))
    }.fixedSize(horizontal:false,vertical:true)
}
struct MetricCell: View {
    let title: String
    let value: String
    var body: some View { VStack(spacing:8) {Text(title).font(.caption).foregroundStyle(.secondary);Text(value).font(.title3.weight(.semibold)).monospacedDigit().minimumScaleFactor(0.65).lineLimit(1)}.frame(maxWidth:.infinity) }
}
struct UsagePage: View {
    @EnvironmentObject var store: MobileStore
    @State private var range = 7
    @State private var metric = 0
    @State private var selected: Date?
    private var period: MobilePeriod { store.trends.periods.first {$0.days == range} ?? MobilePeriod(days:range,total:MobileMetric(),models:[]) }
    private var days: [MobileDay] { let zone=TimeZone(identifier:store.live?.timeZone ?? "") ?? .current; var calendar=Calendar.current; calendar.timeZone=zone; let floor=calendar.date(byAdding:.day,value:1-range,to:calendar.startOfDay(for:Date()))!; return store.trends.daily.filter {$0.start >= floor.timeIntervalSince1970}.sorted {$0.start < $1.start} }
    private var focus: MobileDay? { guard let selected else {return days.last}; return days.min {abs($0.start-selected.timeIntervalSince1970)<abs($1.start-selected.timeIntervalSince1970)} }
    private func value(_ m: MobileMetric,_ i: Int) -> Double { i == 0 ? m.cost ?? 0 : i == 1 ? Double(m.tokens ?? 0) : Double(m.requests) }
    private func share(_ m: MobileMetric,_ i: Int) -> Double {let total=value(period.total,i);return total>0 ? max(0,min(1,value(m,i)/total)) : 0}
    var body: some View {
        ScrollView {
            VStack(alignment:.leading,spacing:20) {
                Picker("时间范围",selection:$range) {Text("近 7 天").tag(7);Text("近 30 天").tag(30);Text("近 90 天").tag(90)}.pickerStyle(.segmented)
                MobileCard {
                    metricRow(period.total)
                    HStack(spacing:16) { legend("费用",.blue);legend("Token",.teal);legend("请求",.purple) }.font(.caption)
                    if days.isEmpty {ContentUnavailableView("暂无趋势",systemImage:"chart.xyaxis.line").frame(height:190)} else {chart}
                    if let focus {Text("\(MobileFormat.date(focus.start))  ·  \(MobileFormat.money(focus.metric.cost))  /  \(MobileFormat.tokens(focus.metric.tokens)) Token  /  \(focus.metric.requests) 次").font(.caption).foregroundStyle(.secondary).monospacedDigit()}
                }
                Text("模型占比").font(.title2.bold())
                Picker("模型排序",selection:$metric) {Text("费用").tag(0);Text("Token").tag(1);Text("请求").tag(2)}.pickerStyle(.segmented)
                LazyVStack(spacing:12) {
                    ForEach(period.models.sorted {value($0.metric,metric)>value($1.metric,metric)}) { model in
                        MobileCard {
                            HStack {Text(model.name.capitalized).font(.headline);Spacer();Text(metric == 0 ? MobileFormat.money(model.metric.cost) : metric == 1 ? MobileFormat.tokens(model.metric.tokens) : "\(model.metric.requests) 次").font(.subheadline).monospacedDigit()}
                            GeometryReader { g in ZStack(alignment:.leading) {Capsule().fill(Color.accentColor.opacity(0.10));Capsule().fill(Color.accentColor.gradient).frame(width:g.size.width*share(model.metric,metric))} }.frame(height:8)
                            HStack {ForEach(0..<3) {i in Text(["费用","Token","请求"][i]+" "+String(format:"%.0f%%",share(model.metric,i)*100)).font(.caption).foregroundStyle(i==metric ? .primary : .secondary).frame(maxWidth:.infinity,alignment:.leading)}}
                        }
                    }
                }
            }.padding(20)
        }.background(Color(uiColor:.systemGroupedBackground)).onChange(of:range) {_,_ in selected=nil}
    }
    private func legend(_ label: String,_ color: Color) -> some View { HStack(spacing:5) {Circle().fill(color).frame(width:6,height:6);Text(label).foregroundStyle(.secondary)} }
    private var chart: some View {
        Chart {
            ForEach(0..<3) { index in
                ForEach(days) { day in
                    let maximum = max(1,days.map {value($0.metric,index)}.max() ?? 1)
                    LineMark(x:.value("日期",Date(timeIntervalSince1970:day.start)),y:.value("趋势",value(day.metric,index)/maximum),series:.value("指标",index))
                        .foregroundStyle([Color.blue,.teal,.purple][index])
                        .lineStyle(StrokeStyle(lineWidth:2.5,lineCap:.round,lineJoin:.round,dash:index==0 ? [] : index==1 ? [7,4] : [2,4]))
                }
            }
            if let focus {RuleMark(x:.value("日期",Date(timeIntervalSince1970:focus.start))).foregroundStyle(.secondary.opacity(0.25))}
        }.chartYScale(domain:0...1.2).chartYAxis(.hidden).chartXSelection(value:$selected).chartXAxis {AxisMarks(values:.automatic(desiredCount:3)) {AxisValueLabel(format:.dateTime.month().day())}}.frame(height:210).padding(.vertical,6)
    }
}
struct RequestSummary: View {
    let item: MobileRequest
    let active: Bool
    var body: some View {
        VStack(alignment:.leading,spacing:7) {
            Text(item.isApproval ? "自动审批审查" : item.preview ?? "请求预览未同步").font(.subheadline.weight(.medium)).lineLimit(2).foregroundStyle(.primary)
            HStack {Text([item.model.capitalized,item.effort ?? ""].filter {!$0.isEmpty}.joined(separator:" · ")).lineLimit(1);Spacer();Text(MobileFormat.money(item.cost)).fixedSize()}.font(.caption).foregroundStyle(.secondary)
            HStack {Text(MobileFormat.update.string(from:Date(timeIntervalSince1970:item.started)));Text("·");RequestDuration(item:item,active:active);Spacer();Text(MobileFormat.tokens(item.tokens)+" Token").fixedSize()}.font(.caption2).foregroundStyle(.secondary)
        }.padding(.vertical,3)
    }
}
struct RecordsPage: View {
    @EnvironmentObject var store: MobileStore
    @State private var query = ""
    @State private var count = 20
    private var rows: [MobileRequest] {store.recent.filter {query.isEmpty || ($0.preview ?? "").localizedCaseInsensitiveContains(query) || $0.model.localizedCaseInsensitiveContains(query)}}
    var body: some View {
        List {
            ForEach(Array(rows.prefix(count))) {item in NavigationLink(value:item.id) {RequestSummary(item:store.request(item.id) ?? item,active:store.tab == 2 && store.recordPath.isEmpty)}}
            if count < rows.count {Button("显示更多") {count+=20}.frame(maxWidth:.infinity)}
        }.searchable(text:$query,prompt:"搜索请求或模型").overlay {if rows.isEmpty {ContentUnavailableView("暂无记录",systemImage:"list.bullet")}}.onChange(of:query) {_,_ in count=20}.refreshable {store.refresh()}
    }
}
struct RequestDetails: View {
    @EnvironmentObject var store: MobileStore
    let id: String
    @State private var expandUser = false
    @State private var expandFinal = false
    private var item: MobileRequest? { store.request(id) }
    private var detail: MobileRequestDetail? { store.detailValues[id] }
    private var userTitle: String { item?.isApproval == true ? "审查输入" : "用户原文" }
    private var finalTitle: String { item?.isApproval == true ? "审查结果" : "最终回复" }
    private var detailNotice: String? { store.detailErrors[id] ?? (detail?.availability == "unavailable" ? "源记录中暂无正文。" : nil) }
    private var loadKey: String { store.selected+id+String(store.supportsDetails)+String(store.detailVersions[id] ?? 0) }
    private var userImages: [MobileImageReference] { (detail?.images ?? []).filter {$0.placement == "user"} }
    private var finalImages: [MobileImageReference] { (detail?.images ?? []).filter {$0.placement == "final"} }
    private var legacyImages: [MobileAttachment] {
        guard let detail, (detail.images ?? []).isEmpty else { return [] }
        var result: [MobileAttachment] = []
        for attachment in detail.attachments.filter(isImage).sorted(by:{($0.thumbnail != nil) && ($1.thumbnail == nil)}) {
            if result.contains(where:{ prior in
                if prior.id == attachment.id { return true }
                if let left = prior.thumbnail, let right = attachment.thumbnail { return left == right }
                return prior.name == attachment.name
            }) { continue }
            result.append(attachment)
        }
        return result
    }
    private var legacyImageExpiry: Double { detail.map {$0.started+3*86400} ?? 0 }
    private var fileAttachments: [MobileAttachment] { (detail?.attachments ?? []).filter {!isImage($0)} }
    private func isImage(_ attachment: MobileAttachment) -> Bool { (attachment.mime ?? "").lowercased().hasPrefix("image/") || attachment.thumbnail != nil }
    var body: some View {
        List {
            if detail?.user.isEmpty == false || !userImages.isEmpty || store.detailLoading.contains(id) || (item?.preview?.isEmpty == false && item?.isApproval != true) || detailNotice == nil {
            Section(userTitle) {
                if let detail, !detail.user.isEmpty || !userImages.isEmpty {
                    message(detail.user,images:userImages,lines:5,expanded:expandUser)
                    if expandUser, detail.full, !detail.userComplete { Text("原文不完整").font(.caption).foregroundStyle(.secondary) }
                    if !detail.user.isEmpty { expandButton(userTitle,expanded:$expandUser) }
                } else if store.detailLoading.contains(id) { ProgressView("正在读取原文") }
                else {
                    if let preview = item?.preview, !preview.isEmpty, item?.isApproval != true {
                        Text("请求摘要").font(.caption).foregroundStyle(.secondary)
                        Text(preview)
                    } else if detailNotice == nil { Text("暂无原文").foregroundStyle(.secondary) }
                }
            }
            }
            if detail?.final.isEmpty == false || !finalImages.isEmpty || !legacyImages.isEmpty || store.detailLoading.contains(id) || detailNotice == nil {
            Section(finalTitle) {
                if let detail, !detail.final.isEmpty || !finalImages.isEmpty {
                    message(detail.final,images:finalImages,lines:20,expanded:expandFinal)
                    if expandFinal, detail.full, !detail.finalComplete { Text("回复不完整").font(.caption).foregroundStyle(.secondary) }
                    if !detail.final.isEmpty { expandButton(finalTitle,expanded:$expandFinal) }
                } else if store.detailLoading.contains(id) { ProgressView("正在读取回复") }
                else if detailNotice == nil, legacyImages.isEmpty { Text(item?.status == "running" ? "任务进行中" : "暂无最终回复").foregroundStyle(.secondary) }
                ForEach(legacyImages) { attachment in
                    MobileLegacyImageView(attachment:attachment,expires:legacyImageExpiry) { if detail?.full != true { store.loadDetail(id,full:true) } }
                }
            }
            }
            if let error = detailNotice {
                Section { Text(error).font(.caption).foregroundStyle(.secondary); Button("重试详情") { store.loadDetail(id,full:expandUser || expandFinal,force:true) } }
            }
            if detail?.availability == "capacity", store.detailErrors[id] == nil {
                Section { Text("内容超出同步容量，仅显示预览。").font(.caption).foregroundStyle(.secondary) }
            }
            if !fileAttachments.isEmpty {
                Section("附件") {
                    ForEach(fileAttachments) { Label($0.name,systemImage:"doc").font(.subheadline) }
                }
            }
            if let item {
                Section("请求信息") {
                    LabeledContent("模型",value:item.model.capitalized)
                    LabeledContent("思考强度",value:item.effort ?? "—")
                    LabeledContent("速度",value:MobileFormat.speed(item.speed))
                    LabeledContent("状态",value:["running":"进行中","completed":"已完成","aborted":"已中断"][item.status] ?? item.status)
                    LabeledContent("费用",value:MobileFormat.money(item.cost))
                    LabeledContent("Token",value:MobileFormat.tokens(item.tokens))
                    LabeledContent("耗时") { RequestDuration(item:item,active:store.tab == 2) }
                    LabeledContent("时间",value:MobileFormat.requestDate.string(from:Date(timeIntervalSince1970:item.started)))
                }
            }
            Section { Text("云端文字最多保留 7 天，图片最多保留 3 天；容量不足时，部分记录可能尚未同步或已被移除。").font(.caption).foregroundStyle(.secondary) }
        }.navigationTitle("请求").navigationBarTitleDisplayMode(.inline)
            .task(id:loadKey) { store.loadDetail(id,full:expandUser || expandFinal) }
            .onDisappear { store.cancelDetail(id) }
    }
    private func message(_ text: String, images: [MobileImageReference], lines: Int, expanded: Bool) -> some View {
        MobileMarkdownView(text:text,images:images)
            .fixedSize(horizontal:false,vertical:true)
            .frame(maxHeight:(expanded && detail?.full == true) || !images.isEmpty ? nil : UIFont.preferredFont(forTextStyle:.body).lineHeight*CGFloat(lines),alignment:.top)
            .clipped()
    }
    private func expandButton(_ label: String, expanded: Binding<Bool>) -> some View {
        Button {
            expanded.wrappedValue.toggle()
            if expanded.wrappedValue { store.loadDetail(id,full:true) }
        } label: {
            HStack { Text(expanded.wrappedValue ? "收起" : "展开全文"); if expanded.wrappedValue && store.detailLoading.contains(id) { ProgressView().controlSize(.small) } }
        }.accessibilityLabel((expanded.wrappedValue ? "收起" : "展开")+label)
    }
}
struct MobileSettings: View {
    @EnvironmentObject var store: MobileStore
    @AppStorage("theme") private var theme = "system"
    @State private var remove = false
    @State private var phoneName = ""
    let scan: () -> Void
    var body: some View {
        Form {
            Section("我的电脑") {
                ForEach(store.devices) {device in
                    Button { if device.invalid == true {store.revokedPrompt=device.id} else {store.select(device.id)} } label: {
                        HStack {VStack(alignment:.leading,spacing:4) {Label(device.code.name,systemImage:"laptopcomputer"); if device.invalid == true {Text("该电脑配对已失效").font(.caption)}}; Spacer(); if device.id==store.selected {Image(systemName:"checkmark")}}
                            .foregroundStyle(device.invalid == true ? Color.red : Color.primary)
                    }
                }
                Button(action:scan) {Label("添加电脑",systemImage:"qrcode.viewfinder")}
            }
            Section("外观") {Picker("主题",selection:$theme) {Text("跟随系统").tag("system");Text("浅色").tag("light");Text("深色").tag("dark")}}
            Section("此 iPhone") {TextField("设备名称",text:$phoneName).onSubmit {store.renamePhone(phoneName)}}
            Section("连接") {LabeledContent("当前连接",value:store.connectionLabel);Button("立即刷新") {store.refresh()}.disabled(store.device == nil)}
            if store.device != nil {Section {Button("移除此电脑",role:.destructive) {remove=true}}}
        }.onAppear {phoneName=store.phoneName}.onDisappear {store.renamePhone(phoneName)}.confirmationDialog("移除此电脑并清除手机缓存？",isPresented:$remove,titleVisibility:.visible) {Button("移除",role:.destructive) {store.remove()}}
    }
}
