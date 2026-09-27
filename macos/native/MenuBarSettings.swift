import AppKit
import QuartzCore
import SwiftUI

enum MenuBarField {
    static let all = ["week","task","today_cost","today_tokens","task_cost","task_tokens"]
    static func normalize(_ values: [String]) -> [String] {
        var seen = Set<String>(); return values.filter {all.contains($0) && seen.insert($0).inserted}
    }
    static func title(_ field: String) -> String {
        switch field {
        case "week": return L("周额度", "Weekly allowance")
        case "task": return L("当前任务状态", "Current task status")
        case "today_cost": return L("今日总费用", "Today's total cost")
        case "today_tokens": return L("今日总 Token", "Today's total tokens")
        case "task_cost": return L("当前任务费用", "Current task cost")
        default: return L("当前任务 Token", "Current task tokens")
        }
    }
    static func value(_ field: String,state: AppState) -> String {
        let today = state.usage.summaries["today"]
        let recent = state.usage.widgetRequest
        let ready = state.taskRunning != nil
        let current = !ready || (state.taskRunning == true && recent?.raw.string("status") != "running") ? nil : recent
        let todayCost = ready ? today.flatMap {$0.cost ?? ($0.unknownCosts == 0 ? 0 : nil)} : nil
        let todayTokens = ready ? today.flatMap {$0.tokens ?? ($0.unknownCosts == 0 ? 0 : nil)} : nil
        switch field {
        case "week": return percent(state.quota.fresh ? state.quota.week?.remaining : nil)
        case "task": return state.taskRunning.map {$0 ? L("运行中", "Running") : L("已完成", "Completed")} ?? "—"
        case "today_cost": return L("今日 ", "Today ")+money(todayCost)
        case "today_tokens": return L("今日 ", "Today ")+compact(todayTokens.map(Double.init))
        case "task_cost": return L("任务 ", "Task ")+money(current?.cost)
        default: return L("任务 ", "Task ")+compact(current?.tokens.map(Double.init))
        }
    }
    static func text(_ state: AppState) -> String { state.menuFields.map {value($0,state:state)}.joined(separator:"  ") }
}

final class TaskStatusImageView: NSView {
    private let glyph = CALayer()
    private static let ring: NSImage = {
        let image = Bundle.main.url(forResource:"c-dot-ring-static",withExtension:"png").flatMap {NSImage(contentsOf:$0)} ?? Branding.menuIcon()
        image.size = NSSize(width:23,height:23); image.isTemplate = true; return image
    }()
    private static let completed: NSImage = {
        let image = Bundle.main.url(forResource:"completed",withExtension:"png").flatMap {NSImage(contentsOf:$0)} ?? Branding.menuIcon()
        image.size = NSSize(width:23,height:23); image.isTemplate = true; return image
    }()
    private var running = false
    private var configured = false
    private var observer: NSObjectProtocol?
    override var isFlipped: Bool { false }
    override var intrinsicContentSize: NSSize { NSSize(width:23,height:23) }
    override init(frame: NSRect) {
        super.init(frame:frame); wantsLayer = true
        glyph.anchorPoint = CGPoint(x:0.5,y:0.5); layer?.addSublayer(glyph)
        observer = NSWorkspace.shared.notificationCenter.addObserver(forName:NSWorkspace.accessibilityDisplayOptionsDidChangeNotification,object:nil,queue:.main) { [weak self] _ in self?.updateAnimation() }
    }
    required init?(coder: NSCoder) { fatalError() }
    override func hitTest(_ point: NSPoint) -> NSView? { nil }
    func setRunning(_ value: Bool) {
        guard !configured || running != value else { return }
        configured = true; running = value; updateGlyph(); updateAnimation()
    }
    private func updateAnimation() {
        let layer = glyph
        if running && !NSWorkspace.shared.accessibilityDisplayShouldReduceMotion {
            guard layer.animation(forKey:"task-ring") == nil else { return }
            let animation = CAKeyframeAnimation(keyPath:"transform.rotation.z")
            animation.values = (0...8).map {-Double($0)*Double.pi/4}
            animation.keyTimes = (0...8).map {NSNumber(value:Double($0)/8)}
            animation.calculationMode = .discrete; animation.duration = 1.6; animation.repeatCount = .infinity
            layer.add(animation,forKey:"task-ring")
        } else { layer.removeAnimation(forKey:"task-ring") }
    }
    override func layout() {
        super.layout()
        CATransaction.begin(); CATransaction.setDisableActions(true)
        glyph.bounds = CGRect(x:0,y:0,width:23,height:23)
        glyph.position = CGPoint(x:bounds.midX,y:bounds.midY)
        CATransaction.commit()
    }
    override func viewDidChangeEffectiveAppearance() { super.viewDidChangeEffectiveAppearance(); updateGlyph() }
    private func updateGlyph() {
        effectiveAppearance.performAsCurrentDrawingAppearance {
            let source = running ? Self.ring : Self.completed
            let tinted = NSImage(size:NSSize(width:23,height:23),flipped:false) { rect in
                source.draw(in:rect)
                NSColor.labelColor.setFill(); rect.fill(using:.sourceIn)
                return true
            }
            CATransaction.begin(); CATransaction.setDisableActions(true)
            glyph.contents = tinted.cgImage(forProposedRect:nil,context:nil,hints:nil)
            glyph.contentsScale = window?.backingScaleFactor ?? 2
            CATransaction.commit()
        }
    }
    func stopAnimation() { glyph.removeAnimation(forKey:"task-ring") }
    deinit { if let observer { NSWorkspace.shared.notificationCenter.removeObserver(observer) } }
}

private struct TaskStatusImage: NSViewRepresentable {
    let running: Bool
    func makeNSView(context: Context) -> TaskStatusImageView { TaskStatusImageView(frame:.zero) }
    func updateNSView(_ view: TaskStatusImageView,context: Context) { view.setRunning(running) }
    static func dismantleNSView(_ view: TaskStatusImageView,coordinator: ()) { view.stopAnimation() }
}

struct MenuBarReadout: View {
    let fields: [String]
    let values: [String:String]
    let running: Bool?
    init(state: AppState) {
        fields = state.menuFields; running = state.taskRunning
        values = Dictionary(uniqueKeysWithValues:fields.map {($0,MenuBarField.value($0,state:state))})
    }
    var body: some View {
        HStack(spacing:7) {
            Image(nsImage:Branding.menuIcon()).resizable().frame(width:18,height:18)
            ForEach(fields,id:\.self) { field in
                if field == "task", let running {
                    TaskStatusImage(running:running).frame(width:23,height:23)
                } else { Text(values[field] ?? "—").font(.system(size:field == "week" ? 16 : 12,weight:.medium)) }
            }
        }.font(.system(size:12,weight:.medium)).monospacedDigit().fixedSize()
    }
}

final class MenuBarReadoutHost: NSHostingView<MenuBarReadout> {
    override func hitTest(_ point: NSPoint) -> NSView? { nil }
}

struct MenuBarSettings: View {
    @ObservedObject var state: AppState
    private var fields: [String] { state.menuFields+MenuBarField.all.filter {!state.menuFields.contains($0)} }
    private func save(_ fields: [String]) { state.setPreference("menu_bar_fields",MenuBarField.normalize(fields)) }
    private func move(_ field: String,by offset: Int) {
        var fields = state.menuFields
        guard let index = fields.firstIndex(of:field), fields.indices.contains(index+offset) else { return }
        fields.swapAt(index,index+offset); save(fields)
    }
    var body: some View {
        VStack(alignment:.leading,spacing:12) {
            Text(L("显示内容", "Display content")).font(.system(size:13,weight:.medium))
            VStack(spacing:2) {
                ForEach(fields,id:\.self) { field in
                    HStack(spacing:12) {
                        Toggle(MenuBarField.title(field),isOn:Binding(get:{state.menuFields.contains(field)},set:{ enabled in
                            save(enabled ? state.menuFields+[field] : state.menuFields.filter {$0 != field})
                        })).toggleStyle(.checkbox).frame(maxWidth:.infinity,alignment:.leading)
                        if state.menuFields.contains(field) {
                            Image(systemName:"line.3.horizontal").foregroundStyle(.secondary).font(.system(size:11))
                                .frame(width:26,height:28).contentShape(Rectangle())
                                .draggable(field)
                                .accessibilityLabel(L("调整显示顺序", "Reorder display"))
                                .contextMenu {
                                    Button(L("上移", "Move up")) { move(field,by:-1) }.disabled(state.menuFields.first == field)
                                    Button(L("下移", "Move down")) { move(field,by:1) }.disabled(state.menuFields.last == field)
                                }
                        }
                    }.font(.system(size:12)).padding(.horizontal,10).frame(height:32)
                        .dropDestination(for:String.self) { items,_ in
                            guard let from = items.first, from != field,
                                  let source = state.menuFields.firstIndex(of:from), let target = state.menuFields.firstIndex(of:field) else { return false }
                            var order = state.menuFields; order.remove(at:source); order.insert(from,at:target); save(order); return true
                        }
                }
            }.padding(.vertical,6).background(.primary.opacity(0.035),in:RoundedRectangle(cornerRadius:12))
            HStack {
                Text(L("实时预览", "Live preview")).font(.system(size:11)).foregroundStyle(.secondary)
                Spacer()
                Button(L("恢复默认", "Reset to default")) { save(["week","task"]) }.buttonStyle(.plain).font(.system(size:11)).foregroundStyle(.secondary)
            }
            ScrollView(.horizontal) {
                MenuBarReadout(state:state).padding(.horizontal,12).frame(height:36)
            }.scrollIndicators(.hidden).background(.primary.opacity(0.045),in:RoundedRectangle(cornerRadius:10))
        }
    }
}
