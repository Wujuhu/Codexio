import SwiftUI
import AppKit

struct GridColumn {
    let id: String
    let title: String
    let width: CGFloat
    var maximum: CGFloat? = nil
    var alignment: NSTextAlignment = .left
}
struct GridText { var main: String; var secondary = ""; var upstream = ""; var upstreamMismatch = false }
struct GridRow {
    let id: String
    let text: (String) -> GridText
    var dynamic = false
    var detail: (() -> (UsageRow,[UsageRow]))? = nil
}

enum TableColumnWidths {
    static func load(_ preferences: Preferences?,key: String) -> [String:CGFloat] {
        let values = preferences?.analytics.object("native_table_widths").object(key) ?? [:]
        return values.compactMapValues { value in
            guard let number = value as? NSNumber, number.doubleValue.isFinite else { return nil }
            return CGFloat(max(48,min(1200,number.doubleValue)))
        }
    }
    static func save(_ widths: [String:CGFloat],preferences: Preferences?,key: String) {
        guard let preferences, !key.isEmpty else { return }
        var tables = preferences.analytics.object("native_table_widths")
        tables[key] = widths.mapValues {Double($0)}
        preferences.analytics["native_table_widths"] = tables
        try? preferences.save()
    }
}

private final class GridTextCell: NSView {
    let main = NSTextField(labelWithString:""), secondary = NSTextField(labelWithString:"")
    private var showsUpstream = false
    private var upstreamColor = NSColor.secondaryLabelColor
    override init(frame: NSRect) {
        super.init(frame:frame)
        main.font = .systemFont(ofSize:12); main.lineBreakMode = .byTruncatingTail
        main.isSelectable = true; main.maximumNumberOfLines = 1
        secondary.font = .systemFont(ofSize:10); secondary.textColor = .secondaryLabelColor
        secondary.lineBreakMode = .byTruncatingTail
        addSubview(main); addSubview(secondary)
    }
    required init?(coder: NSCoder) { fatalError() }
    func apply(_ value: GridText,alignment: NSTextAlignment) {
        showsUpstream = !value.upstream.isEmpty
        upstreamColor = value.upstreamMismatch ? .systemGreen : .secondaryLabelColor
        main.stringValue = value.main; secondary.stringValue = showsUpstream ? value.upstream : value.secondary
        secondary.textColor = showsUpstream ? upstreamColor : .secondaryLabelColor
        secondary.toolTip = secondary.stringValue
        main.alignment = alignment; secondary.alignment = alignment
        main.toolTip = value.main
        secondary.isHidden = secondary.stringValue.isEmpty; needsLayout = true; needsDisplay = true
    }
    override func layout() {
        super.layout()
        let width = max(0,bounds.width-16)
        if showsUpstream {
            let bottom = max(0,(bounds.height-34)/2), textWidth = max(0,width-14)
            main.frame = NSRect(x:8,y:bottom,width:textWidth,height:18)
            secondary.frame = NSRect(x:8,y:bottom+20,width:textWidth,height:14)
        } else if secondary.isHidden { main.frame = NSRect(x:8,y:max(0,(bounds.height-18)/2),width:width,height:18) }
        else {
            let bottom = max(0,(bounds.height-34)/2)
            main.frame = NSRect(x:8,y:bottom+16,width:width,height:18)
            secondary.frame = NSRect(x:8,y:bottom,width:width,height:14)
        }
    }
    override func draw(_ dirtyRect: NSRect) {
        super.draw(dirtyRect)
        guard showsUpstream else { return }
        func textRight(_ label: NSTextField) -> CGFloat {
            let measured = (label.stringValue as NSString).size(withAttributes:[.font:label.font!]).width
            return label.frame.midX+min(label.frame.width,measured)/2
        }
        let lower = textRight(main), upper = textRight(secondary)
        let x = min(bounds.maxX-5,max(lower,upper)+8), tip = min(x,upper+3)
        let path = NSBezierPath(); path.lineWidth = 1.1; path.lineCapStyle = .round; path.lineJoinStyle = .round
        path.move(to:NSPoint(x:lower+2,y:main.frame.midY))
        path.line(to:NSPoint(x:x,y:main.frame.midY)); path.line(to:NSPoint(x:x,y:secondary.frame.midY))
        path.line(to:NSPoint(x:tip,y:secondary.frame.midY))
        path.move(to:NSPoint(x:tip+2.5,y:secondary.frame.midY-2)); path.line(to:NSPoint(x:tip,y:secondary.frame.midY)); path.line(to:NSPoint(x:tip+2.5,y:secondary.frame.midY+2))
        upstreamColor.setStroke(); path.stroke()
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

private final class CompactScrollView: NSScrollView {
    var needsScrollToTop = true
    override func layout() {
        super.layout()
        guard needsScrollToTop, window != nil, contentSize.height > 0,
              let table = documentView as? NSTableView, table.numberOfRows > 0 else { return }
        needsScrollToTop = false
        let insets = contentView.contentInsets
        contentView.scroll(to:NSPoint(x:contentView.bounds.minX,y:-insets.top))
        reflectScrolledClipView(contentView)
    }
    override func scrollWheel(with event: NSEvent) {
        if !hasVerticalScroller && abs(event.scrollingDeltaY) >= abs(event.scrollingDeltaX) { nextResponder?.scrollWheel(with:event) }
        else { super.scrollWheel(with:event) }
    }
}

struct CompactTable: NSViewRepresentable {
    var columns: [GridColumn]
    var rows: [GridRow]
    var revision: String
    var rowHeight: CGFloat = 44
    var selection: Binding<String?>? = nil
    var preferences: Preferences? = nil
    var storageKey = ""
    var verticalScrolling = true
    var scrollResetKey = ""
    func makeCoordinator() -> Coordinator { Coordinator(self) }
    func makeNSView(context: Context) -> NSScrollView {
        let scroll = CompactScrollView(), table = NSTableView()
        scroll.hasVerticalScroller = verticalScrolling; scroll.hasHorizontalScroller = true
        scroll.autohidesScrollers = false; scroll.scrollerStyle = .legacy
        scroll.drawsBackground = true; scroll.backgroundColor = .textBackgroundColor
        if !verticalScrolling { table.style = .plain; table.rowSizeStyle = .custom }
        table.headerView = NSTableHeaderView()
        table.columnAutoresizingStyle = .noColumnAutoresizing
        table.intercellSpacing = NSSize(width:0,height:1)
        table.gridStyleMask = [.solidHorizontalGridLineMask]
        table.usesAlternatingRowBackgroundColors = false
        table.selectionHighlightStyle = selection == nil ? .none : .regular
        table.allowsEmptySelection = true; table.allowsMultipleSelection = false
        table.allowsColumnReordering = false; table.allowsColumnResizing = true
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
        if coordinator.scrollResetKey != scrollResetKey {
            coordinator.scrollResetKey = scrollResetKey
            (scroll as? CompactScrollView)?.needsScrollToTop = true
            scroll.needsLayout = true
        }
        let columnKey = columns.map {$0.id+":"+$0.title}.joined(separator:"|")
        if coordinator.columnsKey != columnKey {
            coordinator.fitting = true
            for column in table.tableColumns { table.removeTableColumn(column) }
            for definition in columns {
                let column = NSTableColumn(identifier:.init(definition.id))
                column.title = definition.title; column.minWidth = 48; column.maxWidth = 1200
                column.width = coordinator.widths[definition.id] ?? definition.width
                column.resizingMask = .userResizingMask
                column.headerCell.alignment = definition.alignment; column.headerCell.font = .systemFont(ofSize:11,weight:.medium)
                table.addTableColumn(column)
            }
            coordinator.columnsKey = columnKey; coordinator.lastWidth = -1
            coordinator.fitting = false
        }
        let key = revision+"|"+columnKey
        if coordinator.dataKey != key {
            let origin = scroll.contentView.bounds.origin
            coordinator.dataKey = key; coordinator.lastWidth = -1; table.rowHeight = rowHeight; table.reloadData()
            if let selected = selection?.wrappedValue, let row = rows.firstIndex(where:{$0.id == selected}) { table.selectRowIndexes(IndexSet(integer:row),byExtendingSelection:false) } else if selection != nil { table.deselectAll(nil) }
            coordinator.configureTimer()
            coordinator.fit()
            if (scroll as? CompactScrollView)?.needsScrollToTop == false {
                scroll.contentView.scroll(to:origin); scroll.reflectScrolledClipView(scroll.contentView)
            } else { scroll.needsLayout = true }
        }
        coordinator.fit()
    }
    static func dismantleNSView(_ scroll: NSScrollView,coordinator: Coordinator) {
        coordinator.timer?.invalidate()
        coordinator.flushWidths()
        if let observer = coordinator.observer { NotificationCenter.default.removeObserver(observer) }
    }
    final class Coordinator: NSObject,NSTableViewDataSource,NSTableViewDelegate {
        var parent: CompactTable
        weak var table: NSTableView?
        weak var scroll: NSScrollView?
        var columnsKey = "", dataKey = ""
        var scrollResetKey: String?
        var lastWidth: CGFloat = -1
        var observer: NSObjectProtocol?
        var timer: Timer?
        var widths: [String:CGFloat]
        var fitting = false
        private var saveWork: DispatchWorkItem?
        init(_ parent: CompactTable) {
            self.parent = parent; widths = TableColumnWidths.load(parent.preferences,key:parent.storageKey)
        }
        func tableViewColumnDidResize(_ notification: Notification) {
            guard !fitting, let table else { return }
            for column in table.tableColumns { widths[column.identifier.rawValue] = column.width }
            resizeDocument()
            saveWork?.cancel()
            let work = DispatchWorkItem { [weak self] in self?.flushWidths() }
            saveWork = work; DispatchQueue.main.asyncAfter(deadline:.now()+0.3,execute:work)
        }
        func flushWidths() {
            guard saveWork != nil else { return }
            saveWork?.cancel(); saveWork = nil
            TableColumnWidths.save(widths,preferences:parent.preferences,key:parent.storageKey)
        }
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
        func tableView(_ tableView: NSTableView,shouldSelectRow row: Int) -> Bool { parent.selection != nil }
        func tableViewSelectionDidChange(_ notification: Notification) {
            guard let table, let binding = parent.selection else { return }
            let value = parent.rows.indices.contains(table.selectedRow) ? parent.rows[table.selectedRow].id : nil
            if binding.wrappedValue != value { DispatchQueue.main.async { binding.wrappedValue = value } }
        }
        func fit() {
            guard let table, let scroll else { return }
            let available = scroll.contentSize.width
            guard abs(lastWidth-available) > 0.5 else { resizeDocument(); return }; lastWidth = available
            fitting = true; defer { fitting = false }
            let base = parent.columns.reduce(CGFloat(0)) {$0+(widths[$1.id] ?? $1.width)}
            let spare = max(0,available-base)
            let flexible = parent.columns.filter {widths[$0.id] == nil && ($0.maximum ?? $0.width) > $0.width}
            for (index,definition) in parent.columns.enumerated() where table.tableColumns.indices.contains(index) {
                let addition = flexible.isEmpty ? 0 : min(max(0,(definition.maximum ?? definition.width)-definition.width),spare/CGFloat(flexible.count))
                table.tableColumns[index].width = widths[definition.id] ?? (definition.width+addition)
            }
            resizeDocument()
        }
        private func resizeDocument() {
            guard let table, let scroll else { return }
            let total = table.tableColumns.reduce(CGFloat(0)) {$0+$1.width}
            let size = NSSize(width:max(scroll.contentSize.width,total),height:max(scroll.contentSize.height,CGFloat(parent.rows.count)*(parent.rowHeight+1)))
            if table.frame.size != size { table.setFrameSize(size) }
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
        case "cache_rate": return L("命中率", "Hit rate")
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
            let width: CGFloat = key == "content" ? 190 : key == "model" ? 180 : key == "time" ? 118 : key == "details" ? 50 : key == "cache_rate" ? 84 : key == "duration" ? 86 : key == "cost" ? 76 : 70
            return GridColumn(id:key,title:title(key),width:width,maximum:key == "content" ? 285 : key == "model" ? 230 : nil,alignment:.center)
        }
    }
    static func row(_ row: UsageRow,timeOnly: Bool,details: @escaping () -> [UsageRow]) -> GridRow {
        GridRow(id:row.id,text: { key in
            let raw = row.raw
            switch key {
            case "content":
                let tier = normalizedTier(raw.string("service_tier"))
                let metadata = [dateText(row.date,timeOnly:timeOnly),raw.string("reasoning_effort").isEmpty ? "" : logEffortName(raw.string("reasoning_effort")),tier == "priority" ? "Fast" : tier == "default" ? "Standard" : "",raw.number("model_context_window").map {compact($0)} ?? "",raw.flag("is_subagent") ? L("子代理", "Subagent") : raw.string("record_kind") == "unassigned" ? L("未归属调用", "Unassigned call") : ""].filter {!$0.isEmpty}.joined(separator:" · ")
                return GridText(main:raw.string("prompt_preview").isEmpty ? row.title : raw.string("prompt_preview"),secondary:metadata)
            case "time": return GridText(main:dateText(row.date,timeOnly:timeOnly))
            case "model":
                let upstream = raw.string("upstream_model"), requested = raw.string("model")
                return GridText(main:modelName(requested),upstream:modelName(upstream),upstreamMismatch:raw.flag("upstream_mismatched",!upstream.isEmpty && !requested.isEmpty && upstream != requested))
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
            case "effort": return GridText(main:logEffortName(raw.string("reasoning_effort")))
            case "speed": return GridText(main:normalizedTier(raw.string("service_tier")) == "priority" ? "Fast" : normalizedTier(raw.string("service_tier")) == "default" ? L("标准", "Standard") : L("未知", "Unknown"))
            case "context": return GridText(main:compact(raw.number("model_context_window")))
            case "status": return GridText(main:raw.string("status") == "running" ? L("进行中", "In progress") : raw.string("status") == "completed" ? L("已完成", "Completed") : L("未知", "Unknown"))
            default: return GridText(main:"—")
            }
        },dynamic:row.raw.flag("duration_running"),detail:{(row,details())})
    }
}
