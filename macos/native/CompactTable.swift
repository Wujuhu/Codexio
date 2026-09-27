import SwiftUI
import AppKit

struct GridColumn {
    let id: String
    let title: String
    let width: CGFloat
    var maximum: CGFloat? = nil
    var alignment: NSTextAlignment = .left
}
struct GridText { var main: String; var secondary = "" }
struct GridRow {
    let id: String
    let text: (String) -> GridText
    var dynamic = false
    var detail: (() -> (UsageRow,[UsageRow]))? = nil
}

private final class GridTextCell: NSView {
    let main = NSTextField(labelWithString:""), secondary = NSTextField(labelWithString:"")
    override init(frame: NSRect) {
        super.init(frame:frame)
        main.font = .systemFont(ofSize:12); main.lineBreakMode = .byTruncatingTail
        main.isSelectable = true; main.maximumNumberOfLines = 2
        secondary.font = .systemFont(ofSize:10); secondary.textColor = .secondaryLabelColor
        secondary.lineBreakMode = .byTruncatingTail
        addSubview(main); addSubview(secondary)
    }
    required init?(coder: NSCoder) { fatalError() }
    func apply(_ value: GridText,alignment: NSTextAlignment) {
        main.stringValue = value.main; secondary.stringValue = value.secondary
        main.alignment = alignment; secondary.alignment = alignment
        secondary.isHidden = value.secondary.isEmpty; needsLayout = true
    }
    override func layout() {
        super.layout()
        let width = max(0,bounds.width-16)
        if secondary.isHidden { main.frame = NSRect(x:8,y:max(0,(bounds.height-18)/2),width:width,height:18) }
        else {
            main.frame = NSRect(x:8,y:20,width:width,height:max(18,bounds.height-24))
            secondary.frame = NSRect(x:8,y:4,width:width,height:14)
        }
    }
}
private final class GridDetailCell: NSView {
    let link = HoverLinkView(title:L("详情", "Details"),target:nil,action:nil)
    var coordinator: DetailsLink.Coordinator?
    override init(frame: NSRect) {
        super.init(frame:frame)
        link.isBordered = false; link.bezelStyle = .inline
        link.font = .systemFont(ofSize:12); link.contentTintColor = .controlAccentColor
        addSubview(link)
    }
    required init?(coder: NSCoder) { fatalError() }
    func apply(_ row: GridRow) {
        coordinator?.popover.close()
        guard let source = row.detail else { return }
        let payload = source()
        let coordinator = DetailsLink.Coordinator(row:payload.0,members:[])
        coordinator.loadMembers = { source().1 }
        self.coordinator = coordinator; coordinator.anchor = link
        link.target = coordinator; link.action = #selector(DetailsLink.Coordinator.clicked)
        link.entered = { [weak coordinator] in coordinator?.anchorInside = true; coordinator?.show() }
        link.exited = { [weak coordinator] in coordinator?.anchorInside = false; coordinator?.scheduleClose() }
    }
    override func layout() { super.layout(); link.frame = NSRect(x:3,y:(bounds.height-24)/2,width:max(0,bounds.width-6),height:24) }
    deinit { coordinator?.popover.close() }
}

struct CompactTable: NSViewRepresentable {
    var columns: [GridColumn]
    var rows: [GridRow]
    var revision: String
    var rowHeight: CGFloat = 52
    var selection: Binding<String?>? = nil
    func makeCoordinator() -> Coordinator { Coordinator(self) }
    func makeNSView(context: Context) -> NSScrollView {
        let scroll = NSScrollView(), table = NSTableView()
        scroll.hasVerticalScroller = true; scroll.hasHorizontalScroller = true
        scroll.autohidesScrollers = false; scroll.scrollerStyle = .legacy
        scroll.drawsBackground = true; scroll.backgroundColor = .textBackgroundColor
        table.headerView = NSTableHeaderView()
        table.columnAutoresizingStyle = .noColumnAutoresizing
        table.intercellSpacing = NSSize(width:0,height:1)
        table.gridStyleMask = [.solidHorizontalGridLineMask]
        table.usesAlternatingRowBackgroundColors = false
        table.allowsEmptySelection = true; table.allowsMultipleSelection = false
        table.allowsColumnReordering = false; table.allowsColumnResizing = false
        table.allowsExpansionToolTips = false
        table.delegate = context.coordinator; table.dataSource = context.coordinator
        scroll.documentView = table; context.coordinator.table = table; context.coordinator.scroll = scroll
        scroll.contentView.postsBoundsChangedNotifications = true
        context.coordinator.observer = NotificationCenter.default.addObserver(forName:NSView.boundsDidChangeNotification,object:scroll.contentView,queue:.main) { [weak coordinator = context.coordinator] _ in coordinator?.fit() }
        return scroll
    }
    func updateNSView(_ scroll: NSScrollView,context: Context) {
        let coordinator = context.coordinator
        coordinator.parent = self
        guard let table = coordinator.table else { return }
        let columnKey = columns.map {$0.id+":"+$0.title}.joined(separator:"|")
        if coordinator.columnsKey != columnKey {
            for column in table.tableColumns { table.removeTableColumn(column) }
            for definition in columns {
                let column = NSTableColumn(identifier:.init(definition.id))
                column.title = definition.title; column.width = definition.width; column.minWidth = definition.width
                column.maxWidth = definition.maximum ?? definition.width
                column.headerCell.alignment = definition.alignment; column.headerCell.font = .systemFont(ofSize:11,weight:.medium)
                table.addTableColumn(column)
            }
            coordinator.columnsKey = columnKey; coordinator.lastWidth = -1
            scroll.contentView.scroll(to:.zero)
        }
        let key = revision+"|"+columnKey
        if coordinator.dataKey != key {
            coordinator.dataKey = key; coordinator.lastWidth = -1; table.rowHeight = rowHeight; table.reloadData()
            if let selected = selection?.wrappedValue, let row = rows.firstIndex(where:{$0.id == selected}) { table.selectRowIndexes(IndexSet(integer:row),byExtendingSelection:false) } else if selection != nil { table.deselectAll(nil) }
            coordinator.configureTimer()
        }
        coordinator.fit()
    }
    static func dismantleNSView(_ scroll: NSScrollView,coordinator: Coordinator) {
        coordinator.timer?.invalidate()
        if let observer = coordinator.observer { NotificationCenter.default.removeObserver(observer) }
    }
    final class Coordinator: NSObject,NSTableViewDataSource,NSTableViewDelegate {
        var parent: CompactTable
        weak var table: NSTableView?
        weak var scroll: NSScrollView?
        var columnsKey = "", dataKey = ""
        var lastWidth: CGFloat = -1
        var observer: NSObjectProtocol?
        var timer: Timer?
        init(_ parent: CompactTable) { self.parent = parent }
        func configureTimer() {
            timer?.invalidate(); timer = nil
            guard parent.rows.contains(where:{$0.dynamic}), parent.columns.contains(where:{$0.id == "duration"}) else { return }
            timer = Timer.scheduledTimer(withTimeInterval:1,repeats:true) { [weak self] _ in
                guard let self, let table = self.table, let column = self.parent.columns.firstIndex(where:{$0.id == "duration"}) else { return }
                guard table.window?.isVisible == true, table.window?.occlusionState.contains(.visible) == true else { return }
                let visible = table.rows(in:table.visibleRect)
                let indices = self.parent.rows.indices.filter {$0 >= visible.location && $0 < NSMaxRange(visible) && self.parent.rows[$0].dynamic}
                table.reloadData(forRowIndexes:IndexSet(indices),columnIndexes:IndexSet(integer:column))
            }
            timer?.tolerance = 0.2
        }
        func numberOfRows(in tableView: NSTableView) -> Int { parent.rows.count }
        func tableView(_ tableView: NSTableView,viewFor tableColumn: NSTableColumn?,row: Int) -> NSView? {
            guard let tableColumn, parent.rows.indices.contains(row), let definition = parent.columns.first(where:{$0.id == tableColumn.identifier.rawValue}) else { return nil }
            let value = parent.rows[row], id = NSUserInterfaceItemIdentifier(definition.id)
            if definition.id == "details" {
                let cell = tableView.makeView(withIdentifier:id,owner:nil) as? GridDetailCell ?? GridDetailCell(frame:.zero)
                cell.identifier = id; cell.apply(value); return cell
            }
            let cell = tableView.makeView(withIdentifier:id,owner:nil) as? GridTextCell ?? GridTextCell(frame:.zero)
            cell.identifier = id; cell.apply(value.text(definition.id),alignment:definition.alignment); return cell
        }
        func tableViewSelectionDidChange(_ notification: Notification) {
            guard let table, let binding = parent.selection else { return }
            let value = parent.rows.indices.contains(table.selectedRow) ? parent.rows[table.selectedRow].id : nil
            if binding.wrappedValue != value { DispatchQueue.main.async { binding.wrappedValue = value } }
        }
        func fit() {
            guard let table, let scroll else { return }
            let available = scroll.contentSize.width
            guard abs(lastWidth-available) > 0.5 else { return }; lastWidth = available
            let base = parent.columns.reduce(CGFloat(0)) {$0+$1.width}
            var spare = max(0,available-base)
            let flexible = parent.columns.filter {($0.maximum ?? $0.width) > $0.width}
            for (index,definition) in parent.columns.enumerated() where table.tableColumns.indices.contains(index) {
                let addition = flexible.isEmpty ? 0 : min(max(0,(definition.maximum ?? definition.width)-definition.width),spare/CGFloat(flexible.count))
                table.tableColumns[index].width = definition.width+addition
            }
            spare = table.tableColumns.reduce(CGFloat(0)) {$0+$1.width}
            table.setFrameSize(NSSize(width:max(available,spare),height:max(scroll.contentSize.height,CGFloat(parent.rows.count)*(parent.rowHeight+1))))
        }
    }
}

enum LogFields {
    static let defaults = ["content","model","input","output","cache_rate","cost","duration","details"]
    static let all = ["content","time","model","input","output","total","cached","cache_write","cache_rate","cost","duration","effort","speed","context","status","details"]
    static func title(_ key: String) -> String {
        switch key {
        case "content": return L("请求 / 时间", "Request / time")
        case "time": return L("时间", "Time")
        case "model": return L("模型", "Model")
        case "input": return L("输入", "Input")
        case "output": return L("输出", "Output")
        case "total": return L("总 Token", "Total tokens")
        case "cached": return L("缓存读取", "Cached input")
        case "cache_write": return L("缓存写入", "Cache write")
        case "cache_rate": return L("缓存命中率", "Cache hit")
        case "cost": return L("费用", "Cost")
        case "duration": return L("耗时", "Duration")
        case "effort": return L("推理强度", "Reasoning")
        case "speed": return L("速度", "Speed")
        case "context": return L("上下文", "Context")
        case "status": return L("状态", "Status")
        default: return L("详情", "Details")
        }
    }
    static func columns(_ keys: [String]) -> [GridColumn] {
        keys.map { key in
            let width: CGFloat = key == "content" ? 190 : key == "model" ? 130 : key == "time" ? 118 : key == "details" ? 50 : key == "cache_rate" ? 84 : key == "duration" ? 86 : key == "cost" ? 76 : 70
            let left = ["content","model","time"].contains(key)
            return GridColumn(id:key,title:title(key),width:width,maximum:key == "content" ? 285 : key == "model" ? 180 : nil,alignment:left ? .left : key == "details" ? .center : .right)
        }
    }
    static func row(_ row: UsageRow,timeOnly: Bool,details: @escaping () -> [UsageRow]) -> GridRow {
        GridRow(id:row.id,text: { key in
            let raw = row.raw
            switch key {
            case "content": return GridText(main:raw.string("prompt_preview").isEmpty ? row.title : raw.string("prompt_preview"),secondary:dateText(row.date,timeOnly:timeOnly)+(raw.flag("is_subagent") ? " · "+L("子代理", "Subagent") : raw.string("record_kind") == "unassigned" ? " · "+L("未归属调用", "Unassigned call") : ""))
            case "time": return GridText(main:dateText(row.date,timeOnly:timeOnly))
            case "model":
                let detail = [raw.string("reasoning_effort").isEmpty ? "" : effortName(raw.string("reasoning_effort")),normalizedTier(raw.string("service_tier")) == "priority" ? "Fast" : "",raw.number("model_context_window").map {compact($0)} ?? ""].filter {!$0.isEmpty}.joined(separator:" · ")
                return GridText(main:row.modelLabel,secondary:detail)
            case "input": return GridText(main:compact(raw.number("input_tokens")))
            case "output": return GridText(main:compact(raw.number("output_tokens")))
            case "total": return GridText(main:compact(row.tokens.map(Double.init)))
            case "cached": return GridText(main:compact(raw.number("cached_input_tokens")))
            case "cache_write": return GridText(main:compact(raw.number("cache_write_input_tokens")))
            case "cache_rate":
                let rate = raw.number("cache_hit_rate") ?? (raw.number("input_tokens").flatMap {$0 > 0 ? (raw.number("cached_input_tokens") ?? 0)/$0 : nil})
                return GridText(main:percent(rate.map {$0*100},digits:1))
            case "cost": return GridText(main:money(row.cost))
            case "duration":
                guard let seconds = row.duration, seconds >= 0, seconds < Double(Int.max) else { return GridText(main:"—") }
                let value = Int(seconds)
                return GridText(main:String(format:"%02d:%02d:%02d",value/3600,value%3600/60,value%60))
            case "effort": return GridText(main:effortName(raw.string("reasoning_effort")))
            case "speed": return GridText(main:normalizedTier(raw.string("service_tier")) == "priority" ? "Fast" : normalizedTier(raw.string("service_tier")) == "default" ? L("标准", "Standard") : L("未知", "Unknown"))
            case "context": return GridText(main:compact(raw.number("model_context_window")))
            case "status": return GridText(main:raw.string("status") == "running" ? L("进行中", "In progress") : raw.string("status") == "completed" ? L("已完成", "Completed") : L("未知", "Unknown"))
            default: return GridText(main:"—")
            }
        },dynamic:row.raw.flag("duration_running"),detail:{(row,details())})
    }
}
