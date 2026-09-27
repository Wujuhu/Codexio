import SwiftUI
import AppKit

struct NavigationGlyph: Shape {
    let name: String
    func path(in rect: CGRect) -> Path {
        var p = Path()
        func line(_ points: [CGPoint]) { guard let first = points.first else { return }; p.move(to:first); for point in points.dropFirst() { p.addLine(to:point) } }
        switch name {
        case "overview": for x in [3.0,14.0] { for y in [3.0,14.0] { p.addRoundedRect(in:CGRect(x:x,y:y,width:7,height:7),cornerSize:CGSize(width:1,height:1)) } }
        case "logs": for y in [5.0,12.0,19.0] { line([CGPoint(x:7,y:y),CGPoint(x:21,y:y)]); line([CGPoint(x:3,y:y),CGPoint(x:3.1,y:y)]) }
        case "trends": line([CGPoint(x:4,y:3),CGPoint(x:4,y:20),CGPoint(x:21,y:20)]); line([CGPoint(x:8,y:15),CGPoint(x:12,y:9),CGPoint(x:16,y:12),CGPoint(x:21,y:4)])
        case "subscription": p.addRoundedRect(in:CGRect(x:3,y:4,width:18,height:16),cornerSize:CGSize(width:3,height:3)); line([CGPoint(x:3,y:9),CGPoint(x:21,y:9)]); line([CGPoint(x:7,y:15),CGPoint(x:11,y:15)]); line([CGPoint(x:16,y:15),CGPoint(x:17,y:15)])
        case "pricing": line([CGPoint(x:3,y:3),CGPoint(x:11,y:3),CGPoint(x:21,y:13),CGPoint(x:13,y:21),CGPoint(x:3,y:11),CGPoint(x:3,y:3)]); p.addEllipse(in:CGRect(x:6.5,y:6.5,width:2,height:2))
        default: line([CGPoint(x:3,y:7),CGPoint(x:6,y:7)]); line([CGPoint(x:12,y:7),CGPoint(x:21,y:7)]); p.addEllipse(in:CGRect(x:6,y:4,width:6,height:6)); line([CGPoint(x:3,y:17),CGPoint(x:13,y:17)]); line([CGPoint(x:19,y:17),CGPoint(x:21,y:17)]); p.addEllipse(in:CGRect(x:13,y:14,width:6,height:6))
        }
        return p.applying(CGAffineTransform(scaleX:rect.width/24,y:rect.height/24).translatedBy(x:rect.minX,y:rect.minY))
    }
}

struct PageHeading: View {
    let title: String
    var body: some View { Text(title).font(.system(size:28,weight:.semibold)).frame(maxWidth:.infinity,alignment:.leading).padding(.bottom,12) }
}
struct SectionHeading: View {
    let title: String
    var body: some View { Text(title).font(.system(size:16,weight:.semibold)).frame(maxWidth:.infinity,alignment:.leading) }
}
struct StatusNote: View {
    let text: String
    var body: some View { Text(text).font(.system(size:12)).foregroundStyle(.secondary).textSelection(.enabled).frame(maxWidth:.infinity,alignment:.leading) }
}

struct SegmentedMeter: View {
    var value: Double?
    var color: Color = .blue
    var count = 45
    var body: some View {
        GeometryReader { geometry in
            Canvas { context,size in
                let gap = 2.5, width = max(1,(size.width-gap*Double(count-1))/Double(count))
                for index in 0..<count {
                    let filled = value.map { Double(index)/Double(count) < max(0,min(100,$0))/100 } ?? false
                    context.fill(Path(roundedRect:CGRect(x:Double(index)*(width+gap),y:0,width:width,height:size.height),cornerRadius:1),with:.color(filled ? color : Color.secondary.opacity(0.16)))
                }
            }.frame(width:geometry.size.width)
        }.frame(height:12).accessibilityLabel(L("剩余额度", "Remaining allowance")+" "+percent(value))
    }
}

struct QuotaCard: View {
    let window: QuotaWindow?
    let title: String
    let fresh: Bool
    var size: CGFloat = 34
    var stacked = false
    var body: some View {
        VStack(alignment:.leading,spacing:13) {
            if stacked { Text(title).font(.system(size:14,weight:.medium)) }
            HStack(alignment:.firstTextBaseline) {
                if !stacked { Text(title).font(.system(size:14,weight:.medium)); Spacer(minLength:16) }
                Text(percent(fresh ? window?.remaining : nil)).font(.system(size:size,weight:.semibold)).monospacedDigit()
                Text(L("剩余", "remaining")).font(.system(size:12)).foregroundStyle(.secondary)
            }
            SegmentedMeter(value:fresh ? window?.remaining : nil)
            HStack {
                Text(L("已用", "Used")+" "+percent(fresh ? window?.used : nil))
                Spacer(minLength:12)
                Text(L("重置", "Resets")+" "+dateText(window?.reset,timeOnly:window?.minutes == 300))
            }.font(.system(size:11)).foregroundStyle(.secondary).lineLimit(1)
        }.frame(maxWidth:.infinity,alignment:.leading).modifier(BubbleCard())
    }
}

struct BubbleCard: ViewModifier {
    func body(content: Content) -> some View {
        content.padding(18).background(Color.primary.opacity(0.025),in:RoundedRectangle(cornerRadius:18))
            .overlay(RoundedRectangle(cornerRadius:18).stroke(.secondary.opacity(0.16),lineWidth:1))
    }
}

struct SummaryMetrics: View {
    let summary: UsageSummary
    var body: some View {
        HStack(spacing:12) {
            metric(L("费用", "Cost"),money(summary.cost))
            metric(L("总 Token", "Total tokens"),compact(summary.tokens.map(Double.init)))
            metric(L("用户请求", "User requests"),String(summary.requests))
            metric(L("命中率", "Hit rate"),percent(summary.cacheRate.map {$0*100},digits:1))
        }
    }
    private func metric(_ label: String,_ value: String) -> some View {
        VStack(alignment:.leading,spacing:8) { Text(label).font(.system(size:12)).foregroundStyle(.secondary); Text(value).font(.system(size:27,weight:.medium)).monospacedDigit().lineLimit(1).minimumScaleFactor(0.8) }.frame(maxWidth:.infinity,alignment:.leading).modifier(BubbleCard())
    }
}

struct TrendChart: View {
    let days: [DayUsage]
    var compactStyle = false
    var body: some View {
        let maxToken = max(1,Double(days.compactMap(\.tokens).max() ?? 0))
        let maxCost = max(0.01,days.compactMap(\.cost).max() ?? 0)
        VStack(spacing:10) {
            HStack { Text(compact(maxToken)+" Token").foregroundStyle(.blue); Spacer(); Text(money(maxCost)).foregroundStyle(.green) }.font(.system(size:11))
            Canvas { context,size in
                for i in 0...3 {
                    let y = Double(i)/3*size.height
                    var line = Path(); line.move(to:CGPoint(x:0,y:y)); line.addLine(to:CGPoint(x:size.width,y:y))
                    context.stroke(line,with:.color(.secondary.opacity(0.13)),lineWidth:0.5)
                }
                for series in 0..<2 {
                    var path = Path(), connected = false
                    for (index,day) in days.enumerated() {
                        let amount = series == 0 ? day.tokens.map(Double.init) : day.cost
                        guard let amount else { connected = false; continue }
                        let point = CGPoint(x:Double(index)/Double(max(1,days.count-1))*size.width,y:size.height-max(0,amount/(series == 0 ? maxToken : maxCost))*size.height)
                        if connected { path.addLine(to:point) } else { path.move(to:point); connected = true }
                    }
                    context.stroke(path,with:.color(series == 0 ? .blue : .green),style:StrokeStyle(lineWidth:compactStyle ? 1.8 : 2.2,lineCap:.round,lineJoin:.round))
                }
            }.frame(height:compactStyle ? 85 : 170).overlay { UsageHoverSurface(days:days) }
            HStack { Text(axisLabel(days.first?.date)); Spacer(); Text(axisLabel(days.last?.date)) }.font(.system(size:11)).foregroundStyle(.secondary)
        }.accessibilityElement(children:.combine).accessibilityLabel(L("Token 和费用趋势", "Token and cost trends"))
    }
    private func axisLabel(_ date: Date?) -> String {
        guard let date else { return "—" }
        if let first = days.first?.date, let last = days.last?.date, Calendar.current.isDate(first,inSameDayAs:last) { return date.formatted(.dateTime.hour().minute()) }
        return date.formatted(.dateTime.month().day())
    }
}

struct EmptyState: View {
    let title: String
    var detail = ""
    var body: some View {
        VStack(spacing:12) { Text(title).font(.system(size:17,weight:.medium)); if !detail.isEmpty { Text(detail).font(.system(size:13)).foregroundStyle(.secondary).multilineTextAlignment(.center) } }.frame(maxWidth:.infinity).padding(.vertical,50)
    }
}

final class HoverLinkView: NSButton {
    var entered: (() -> Void)?
    var exited: (() -> Void)?
    private var tracking: NSTrackingArea?
    override func updateTrackingAreas() {
        if let tracking { removeTrackingArea(tracking) }
        tracking = NSTrackingArea(rect:bounds,options:[.activeInKeyWindow,.mouseEnteredAndExited,.inVisibleRect],owner:self,userInfo:nil)
        addTrackingArea(tracking!); super.updateTrackingAreas()
    }
    override func becomeFirstResponder() -> Bool { let accepted = super.becomeFirstResponder(); if accepted { entered?() }; return accepted }
    override func resignFirstResponder() -> Bool { exited?(); return super.resignFirstResponder() }
    override func mouseEntered(with event: NSEvent) { entered?() }
    override func mouseExited(with event: NSEvent) { exited?() }
}

struct DetailsLink: NSViewRepresentable {
    let row: UsageRow
    var members: [UsageRow] = []
    func makeCoordinator() -> Coordinator { Coordinator(row:row,members:members) }
    func makeNSView(context: Context) -> HoverLinkView {
        let view = HoverLinkView(title:L("详情", "Details"),target:context.coordinator,action:#selector(Coordinator.clicked))
        view.isBordered = false; view.bezelStyle = .inline; view.contentTintColor = .controlAccentColor; view.font = .systemFont(ofSize:12)
        context.coordinator.anchor = view
        view.entered = { [weak coordinator = context.coordinator] in coordinator?.anchorInside = true; coordinator?.show() }
        view.exited = { [weak coordinator = context.coordinator] in coordinator?.anchorInside = false; coordinator?.scheduleClose() }
        return view
    }
    func updateNSView(_ view: HoverLinkView, context: Context) { context.coordinator.row = row; context.coordinator.members = members }
    static func dismantleNSView(_ view: HoverLinkView, coordinator: Coordinator) { coordinator.popover.close() }
    final class Coordinator: NSObject {
        var row: UsageRow
        var members: [UsageRow]
        var loadMembers: (() -> [UsageRow])?
        weak var anchor: HoverLinkView?
        let popover = NSPopover()
        var anchorInside = false
        var popupInside = false
        var work: DispatchWorkItem?
        init(row: UsageRow,members: [UsageRow]) { self.row = row; self.members = members; super.init(); popover.behavior = .transient; popover.animates = false }
        @objc func clicked() { if popover.isShown { popover.close() } else { show() } }
        func show() {
            work?.cancel(); guard let anchor, !popover.isShown else { return }
            let content = LogDetail(row:row,members:loadMembers?() ?? members).onHover { [weak self] inside in self?.popupInside = inside; if !inside { self?.scheduleClose() } }
            popover.contentViewController = NSHostingController(rootView:content)
            popover.contentSize = NSSize(width:360,height:500)
            popover.show(relativeTo:anchor.bounds,of:anchor,preferredEdge:.minX)
        }
        func scheduleClose() {
            work?.cancel()
            let next = DispatchWorkItem { [weak self] in if let self, !self.anchorInside, !self.popupInside { self.popover.close() } }
            work = next; DispatchQueue.main.asyncAfter(deadline:.now()+0.25,execute:next)
        }
    }
}

struct LogDetail: View {
    let row: UsageRow
    var members: [UsageRow] = []
    var body: some View {
        ScrollView {
            VStack(alignment:.leading,spacing:15) {
                Text(L("请求详情", "Request details")).font(.system(size:16,weight:.semibold))
                Text(row.title).font(.system(size:14,weight:.medium))
                Text(dateText(row.date)).font(.caption).foregroundStyle(.secondary)
                Divider()
                field(L("模型", "Model"),modelName(row.raw.string("model")))
                if !row.raw.string("upstream_model").isEmpty { field(L("响应返回模型", "Response model"),modelName(row.raw.string("upstream_model"))) }
                field(L("推理强度", "Reasoning"),effortName(row.raw.string("reasoning_effort")))
                field(L("速度", "Speed"),normalizedTier(row.raw.string("service_tier")) == "priority" ? L("快速模式", "Fast mode") : normalizedTier(row.raw.string("service_tier")) == "default" ? L("标准", "Standard") : L("未知", "Unknown"))
                field(L("费用", "Cost"),money(row.cost))
                field(L("耗时", "Duration"),durationText(row.duration))
                field(L("输入 Token", "Input tokens"),compact(row.raw.number("input_tokens")))
                field(L("缓存读取", "Cached input"),compact(row.raw.number("cached_input_tokens")))
                field(L("输出 Token", "Output tokens"),compact(row.raw.number("output_tokens")))
                field(L("推理输出", "Reasoning output"),compact(row.raw.number("reasoning_output_tokens")))
                Divider()
                Text(L("输入预览", "Prompt preview")).foregroundStyle(.secondary)
                Text(row.raw.string("prompt_preview","—"))
                Divider()
                Text(L("回复预览", "Reply preview")).foregroundStyle(.secondary)
                Text(row.raw.string("output_preview","—"))
                if !members.isEmpty {
                    Divider()
                    DisclosureGroup(L("模型调用", "Model calls")+" (\(members.count))") {
                        ForEach(members) { member in
                            VStack(alignment:.leading,spacing:7) {
                                Text(member.modelLabel).fontWeight(.medium)
                                if member.raw.string("session_id") != row.raw.string("session_id") { Text(L("子代理", "Subagent")).foregroundStyle(.secondary) }
                                Text(effortName(member.raw.string("reasoning_effort"))+" · "+(normalizedTier(member.raw.string("service_tier")) == "priority" ? "Fast" : normalizedTier(member.raw.string("service_tier")) == "default" ? L("标准", "Standard") : L("未知", "Unknown")))
                                Text(compact(member.tokens.map(Double.init))+" Token · "+money(member.cost))
                                if !member.raw.string("output_preview").isEmpty { Text(member.raw.string("output_preview")).foregroundStyle(.secondary) }
                                Divider()
                            }.padding(.vertical,6)
                        }
                    }
                }
                Divider(); Text(row.raw.string("session_id")).font(.caption).foregroundStyle(.secondary)
            }.font(.system(size:12)).padding(20).textSelection(.enabled).frame(maxWidth:.infinity,alignment:.leading)
        }.frame(width:360,height:500)
    }
    private func field(_ label: String,_ value: String) -> some View { HStack(alignment:.firstTextBaseline) { Text(label).foregroundStyle(.secondary); Spacer(minLength:12); Text(value).multilineTextAlignment(.trailing) } }
}

struct DateRangeControls: View {
    @Binding var from: Date
    @Binding var through: Date
    var body: some View {
        HStack(spacing:14) {
            DatePicker(L("从", "From"),selection:$from,in:...Date(),displayedComponents:.date)
            DatePicker(L("至", "To"),selection:$through,in:...Date(),displayedComponents:.date)
            Spacer()
        }.datePickerStyle(.field).font(.system(size:12)).fixedSize(horizontal:false,vertical:true)
    }
}

struct UsageHoverSurface: NSViewRepresentable {
    let days: [DayUsage]
    var rows = 0
    var offset = 0
    var cellSize: CGFloat = 16
    func makeNSView(context: Context) -> UsageHoverView { UsageHoverView(frame:.zero) }
    func updateNSView(_ view: UsageHoverView,context: Context) {
        view.update(days:days,rows:rows,offset:offset,cellSize:cellSize)
    }
    static func dismantleNSView(_ view: UsageHoverView,coordinator: ()) { view.close() }
}

final class UsageHoverView: NSView {
    private var days: [DayUsage] = []
    private var rows = 0, offset = 0
    private var cellSize: CGFloat = 16
    private var tracking: NSTrackingArea?
    private var selected: Int?
    private var current: DayUsage?
    private let popover = NSPopover()
    override var isFlipped: Bool { true }
    override init(frame: NSRect) {
        super.init(frame:frame); popover.behavior = .transient; popover.animates = false
    }
    required init?(coder: NSCoder) { fatalError() }
    func update(days: [DayUsage],rows: Int,offset: Int,cellSize: CGFloat) {
        self.days = days; self.rows = rows; self.offset = offset; self.cellSize = cellSize
        if let selected, !days.indices.contains(selected) || days[selected] != current { close() }
    }
    override func updateTrackingAreas() {
        if let tracking { removeTrackingArea(tracking) }
        tracking = NSTrackingArea(rect:bounds,options:[.activeInActiveApp,.mouseMoved,.mouseEnteredAndExited,.inVisibleRect],owner:self,userInfo:nil)
        addTrackingArea(tracking!); super.updateTrackingAreas()
    }
    override func mouseEntered(with event: NSEvent) { mouseMoved(with:event) }
    override func mouseExited(with event: NSEvent) { close() }
    override func viewWillMove(toWindow newWindow: NSWindow?) { if newWindow == nil { close() }; super.viewWillMove(toWindow:newWindow) }
    override func mouseMoved(with event: NSEvent) {
        guard !days.isEmpty, bounds.width > 0 else { return }
        let point = convert(event.locationInWindow,from:nil)
        let index: Int, anchor: NSRect
        if rows > 0 {
            let column = Int(max(0,point.x)/(cellSize+4)), rowHeight: CGFloat = rows == 1 ? 44 : cellSize
            let row = Int(max(0,point.y)/(rowHeight+4))
            index = column*rows+row-offset
            guard row < rows, point.x.truncatingRemainder(dividingBy:cellSize+4) <= cellSize,
                  point.y.truncatingRemainder(dividingBy:rowHeight+4) <= rowHeight else { close(); return }
            anchor = NSRect(x:CGFloat(column)*(cellSize+4),y:CGFloat(row)*(rowHeight+4),width:cellSize,height:rowHeight)
        } else {
            index = min(days.count-1,max(0,Int((point.x/bounds.width*CGFloat(max(1,days.count-1))).rounded())))
            anchor = NSRect(x:CGFloat(index)/CGFloat(max(1,days.count-1))*bounds.width,y:0,width:1,height:bounds.height)
        }
        guard days.indices.contains(index) else { close(); return }
        guard selected != index || !popover.isShown else { return }
        selected = index; current = days[index]; needsDisplay = true
        popover.contentViewController = NSHostingController(rootView:UsagePointDetail(day:days[index],hourly:rows == 0))
        popover.contentSize = NSSize(width:230,height:152)
        if popover.isShown { popover.positioningRect = anchor }
        else { popover.show(relativeTo:anchor,of:self,preferredEdge:.minY) }
    }
    override func draw(_ dirtyRect: NSRect) {
        guard rows == 0, let selected else { return }
        NSColor.secondaryLabelColor.withAlphaComponent(0.3).setStroke()
        let x = CGFloat(selected)/CGFloat(max(1,days.count-1))*bounds.width
        let path = NSBezierPath(); path.move(to:NSPoint(x:x,y:0)); path.line(to:NSPoint(x:x,y:bounds.height)); path.lineWidth = 1; path.stroke()
    }
    func close() { popover.close(); selected = nil; current = nil; needsDisplay = true }
}

private struct UsagePointDetail: View {
    let day: DayUsage
    let hourly: Bool
    var body: some View {
        VStack(alignment:.leading,spacing:12) {
            Text(hourly ? day.date.formatted(.dateTime.month().day().hour().minute()) : day.date.formatted(date:.abbreviated,time:.omitted))
                .font(.system(size:12,weight:.semibold))
            detail("Token",day.tokens.map { $0.formatted() } ?? "—")
            detail(L("费用", "Cost"),money(day.cost))
            detail(L("请求数", "Requests"),String(day.requests))
        }.font(.system(size:12)).padding(18).frame(width:230,height:152)
    }
    private func detail(_ title: String,_ value: String) -> some View {
        HStack { Text(title).foregroundStyle(.secondary); Spacer(); Text(value).monospacedDigit() }
    }
}
