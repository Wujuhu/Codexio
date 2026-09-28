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
    static let update: DateFormatter = {let f=DateFormatter(); f.dateFormat="M.d HH:mm"; return f}()
    static func date(_ value: Double) -> String { Date(timeIntervalSince1970:value).formatted(.dateTime.month().day()) }
}
struct MobileCard<Content: View>: View {
    @ViewBuilder var content: Content
    var body: some View { VStack(alignment:.leading,spacing:16) {content}.frame(maxWidth:.infinity,alignment:.leading).padding(20).background(Color(uiColor:.secondarySystemGroupedBackground),in:RoundedRectangle(cornerRadius:24)) }
}
struct MobileRoot: View {
    @EnvironmentObject var store: MobileStore
    @State private var scanner = false
    var body: some View {
        TabView {
            NavigationStack { OverviewPage(scan:{scanner=true}).navigationTitle("").toolbar {header} }.tabItem {Label("概览",systemImage:"square.grid.2x2")}
            NavigationStack { UsagePage().navigationTitle("用量").navigationBarTitleDisplayMode(.large) }.tabItem {Label("用量",systemImage:"chart.xyaxis.line")}
            NavigationStack { RecordsPage().navigationTitle("记录") }.tabItem {Label("记录",systemImage:"list.bullet")}
            NavigationStack { MobileSettings(scan:{scanner=true}).navigationTitle("设置") }.tabItem {Label("设置",systemImage:"slider.horizontal.3")}
        }
        .sheet(isPresented:$scanner) { NavigationStack { QRScanner { value in scanner=false; store.beginPair(value) }.ignoresSafeArea(edges:.bottom).navigationTitle("扫描 Mac 配对二维码").navigationBarTitleDisplayMode(.inline).toolbar {ToolbarItem(placement:.cancellationAction) { Button("取消") {scanner=false} }} } }
        .alert("同步提示",isPresented:Binding(get:{store.error != nil},set:{if !$0 {store.error=nil}})) { Button("知道了") {store.error=nil} } message: {Text(store.error ?? "")}
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
                Button("添加 Mac",systemImage:"qrcode.viewfinder") {scanner=true}
            } label: {Label(store.live?.name ?? "Mac",systemImage:"laptopcomputer").font(.subheadline)}
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
                if store.devices.isEmpty && !store.pairing { Button(action:scan) {Label("连接我的 Mac",systemImage:"qrcode.viewfinder").frame(maxWidth:.infinity)}.buttonStyle(.glassProminent) }
                if store.pairing { MobileCard {Label("正在配对",systemImage:"link").font(.headline); Text(store.status); Button("取消配对") {store.cancelPair()} } }
                MobileCard {
                    HStack {Text("当前任务").font(.headline); Spacer(); if store.live?.runningCount ?? 0 > 0 {Image(systemName:"circle.dotted").symbolEffect(.rotate)} }
                    if let task = store.live?.task {
                        if Date().timeIntervalSince1970-(store.live?.observed ?? 0) > 900 { Text("任务状态待更新").font(.caption).foregroundStyle(.orange) }
                        Text(task.preview ?? "请求预览未同步").font(.title3.weight(.semibold)).lineLimit(3)
                        Text([task.model,task.effort ?? ""].filter {!$0.isEmpty}.joined(separator:" · ")).font(.caption).foregroundStyle(.secondary)
                        HStack {Text(MobileFormat.tokens(task.tokens)+" Token"); Spacer();Text(MobileFormat.money(task.cost))}.monospacedDigit()
                    } else { Label("当前未有任务正在进行",systemImage:"checkmark.circle").foregroundStyle(.secondary).padding(.vertical,10) }
                }
                MobileCard { quota("5 小时额度",store.live?.five); Divider();quota("周额度",store.live?.week) }
                MobileCard { Text("今天").font(.headline); metricRow(store.live?.today ?? MobileMetric()) }
                if !store.recent.isEmpty {
                    Text("最近使用").font(.title2.bold())
                    MobileCard { ForEach(Array(store.recent.prefix(3))) { item in NavigationLink { RequestDetails(item:item) } label: {RequestSummary(item:item)}.buttonStyle(.plain); if item.id != store.recent.prefix(3).last?.id {Divider()} } }
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
                            HStack {Text(model.name).font(.headline);Spacer();Text(metric == 0 ? MobileFormat.money(model.metric.cost) : metric == 1 ? MobileFormat.tokens(model.metric.tokens) : "\(model.metric.requests) 次").font(.subheadline).monospacedDigit()}
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
    var body: some View {
        VStack(alignment:.leading,spacing:7) {
            Text(item.preview ?? "请求预览未同步").font(.subheadline.weight(.medium)).lineLimit(2).foregroundStyle(.primary)
            HStack {Text(item.model);Spacer();Text(MobileFormat.money(item.cost))}.font(.caption).foregroundStyle(.secondary)
            HStack {Text(Date(timeIntervalSince1970:item.started),style:.time);Text("·");Text(item.effort ?? "");Spacer();Text(MobileFormat.tokens(item.tokens)+" Token")}.font(.caption2).foregroundStyle(.secondary)
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
            ForEach(Array(rows.prefix(count))) {item in NavigationLink {RequestDetails(item:item)} label:{RequestSummary(item:item)}}
            if count < rows.count {Button("显示更多") {count+=20}.frame(maxWidth:.infinity)}
        }.searchable(text:$query,prompt:"搜索请求或模型").overlay {if rows.isEmpty {ContentUnavailableView("暂无记录",systemImage:"list.bullet")}}.onChange(of:query) {_,_ in count=20}.refreshable {store.refresh()}
    }
}
struct RequestDetails: View {
    let item: MobileRequest
    var body: some View {
        List {
            Section {Text(item.preview ?? "请求预览未同步")}
            LabeledContent("模型",value:item.model)
            LabeledContent("状态",value:["running":"进行中","completed":"已完成","aborted":"已中断"][item.status] ?? item.status)
            LabeledContent("思考强度",value:item.effort ?? "—")
            LabeledContent("费用",value:MobileFormat.money(item.cost))
            LabeledContent("Token",value:MobileFormat.tokens(item.tokens))
            LabeledContent("耗时",value:item.duration.map {String(format:"%.0f 秒",$0)} ?? "—")
        }.navigationTitle("请求").navigationBarTitleDisplayMode(.inline)
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
            Section("我的 Mac") {ForEach(store.devices) {device in Button {store.select(device.id)} label:{HStack {Label(device.code.name,systemImage:"laptopcomputer");Spacer();if device.id==store.selected {Image(systemName:"checkmark")}}}}; Button(action:scan) {Label("添加 Mac",systemImage:"qrcode.viewfinder")}}
            Section("外观") {Picker("主题",selection:$theme) {Text("跟随系统").tag("system");Text("浅色").tag("light");Text("深色").tag("dark")}}
            Section("此 iPhone") {TextField("设备名称",text:$phoneName).onSubmit {store.renamePhone(phoneName)}}
            Section("连接") {LabeledContent("当前连接",value:store.connectionLabel);Button("立即刷新") {store.refresh()}.disabled(store.device == nil)}
            if store.device != nil {Section {Button("移除此 Mac",role:.destructive) {remove=true}}}
        }.onAppear {phoneName=store.phoneName}.onDisappear {store.renamePhone(phoneName)}.confirmationDialog("移除此 Mac 并清除手机缓存？",isPresented:$remove,titleVisibility:.visible) {Button("移除",role:.destructive) {store.remove()}}
    }
}
