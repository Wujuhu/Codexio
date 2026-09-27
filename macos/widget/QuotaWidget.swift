import SwiftUI
import WidgetKit

enum QuotaStyle: String { case single, dual, segmented }
struct QuotaEntry: TimelineEntry {
    var date: Date
    var snapshot: Snapshot?
    var style: QuotaStyle
}
struct QuotaProvider: TimelineProvider {
    let style: QuotaStyle
    func placeholder(in context: Context) -> QuotaEntry { QuotaEntry(date:Date(),snapshot:nil,style:style) }
    func getSnapshot(in context: Context,completion: @escaping (QuotaEntry) -> Void) { completion(QuotaEntry(date:Date(),snapshot:Snapshot.read(),style:style)) }
    func getTimeline(in context: Context,completion: @escaping (Timeline<QuotaEntry>) -> Void) {
        let date = Date(), snapshot = Snapshot.read()
        completion(Timeline(entries:[QuotaEntry(date:date,snapshot:snapshot,style:style)],policy:snapshot?.host_running == true ? .after(date.addingTimeInterval(900)) : .never))
    }
}

struct QuotaWidgetView: View {
    @Environment(\.colorScheme) private var systemScheme
    let entry: QuotaEntry
    private var dark: Bool { entry.style == .dual || systemScheme == .dark }
    private var fresh: Bool { entry.snapshot.map {Date().timeIntervalSince1970-($0.quota.updated_at ?? $0.updated_at) < 900 && $0.quota.applicable != false && ($0.quota.week != nil || $0.quota.five_hour != nil)} ?? false }
    private var color: Color { entry.style == .dual ? Color(red:0.88,green:0.49,blue:0.36) : Color.blue }
    private func remaining(_ week: Bool) -> Double? { guard fresh else { return nil }; return week ? entry.snapshot?.quota.week : entry.snapshot?.quota.five_hour }
    private func reset(_ week: Bool) -> Double? { week ? entry.snapshot?.quota.week_reset_at : entry.snapshot?.quota.five_hour_reset_at }
    private func percentage(_ value: Double?) -> String { guard let value else { return "—" }; return value > 0 && value < 1 ? "<1%" : String(format:"%.0f%%",max(0,min(100,value))) }
    private func countdown(_ value: Double?) -> String {
        guard let value, fresh, value > Date().timeIntervalSince1970 else { return "—" }
        let seconds = Int(value-Date().timeIntervalSince1970), days = seconds/86400
        let time = String(format:"%02d:%02d",seconds%86400/3600,seconds%3600/60)
        return days > 0 ? "\(days)d "+time : time
    }
    var body: some View {
        card
            .containerBackground(for:.widget) { background }
            .widgetURL(URL(string:"codexio://subscription"))
    }
    private var background: Color { dark ? Color(red:0.055,green:0.058,blue:0.065) : Color.white }
    private var card: some View {
        content
            .background(alignment:.bottomTrailing) {
                if entry.style == .segmented {
                    Image("brand-mark",bundle:.main).resizable().renderingMode(.template).scaledToFit().frame(width:78,height:78).foregroundStyle(Color.primary.opacity(dark ? 0.035 : 0.045)).offset(x:15,y:10)
                }
            }
            .padding(14)
            .frame(maxWidth:.infinity,maxHeight:.infinity,alignment:.topLeading)
            .environment(\.colorScheme,dark ? .dark : .light)
    }
    #if CODEXIO_APP_WIDGET_PREVIEW
    var preview: some View { card.background(background) }
    #endif
    @ViewBuilder private var content: some View {
        if entry.snapshot == nil || entry.snapshot?.host_running == false { WidgetLaunchPrompt() }
        else if entry.style == .single { single }
        else {
            VStack(alignment:.leading,spacing:8) {
                HStack { Text("Codex").font(.system(size:13,weight:.semibold)); Spacer(minLength:4); freshness }
                dualRow(week:true)
                dualRow(week:false)
            }
        }
    }
    private var single: some View {
        let week = true, value = remaining(true)
        return VStack(alignment:.leading,spacing:6) {
            freshness
            HStack(alignment:.firstTextBaseline) {
                Text(week ? WL("每周", "Weekly") : WL("5 小时", "5 hour")).font(.system(size:14,weight:.semibold))
                Spacer(minLength:4)
                Text(percentage(value)).font(.system(size:30,weight:.bold)).monospacedDigit().lineLimit(1).minimumScaleFactor(0.7)
            }
            meter(value,segmented:false).frame(height:5)
            HStack(alignment:.top) {
                VStack(alignment:.leading,spacing:2) { Text(WL("已用", "Used")).foregroundStyle(.secondary); Text(percentage(value.map {100-$0})).fontWeight(.semibold) }
                Spacer()
                VStack(alignment:.trailing,spacing:2) { Text(WL("剩余", "Remaining")).foregroundStyle(.secondary); Text(percentage(value)).fontWeight(.semibold) }
            }.font(.system(size:10))
            Divider()
            HStack(spacing:5) { Image(systemName:"clock.arrow.circlepath"); Text(WL("重置时间", "Resets in")); Spacer(minLength:1); Text(countdown(reset(week))).monospacedDigit() }.font(.system(size:10))
            if fresh, let count = entry.snapshot?.quota.reset_count {
                HStack(spacing:5) { Image(systemName:"arrow.counterclockwise.circle.fill"); Text(WL("重置次数", "Resets")); Spacer(); Text("\(count)×").monospacedDigit() }.font(.system(size:10))
            }
        }
    }
    private func dualRow(week: Bool) -> some View {
        let segmented = entry.style == .segmented
        return VStack(alignment:.leading,spacing:4) {
            HStack(alignment:.lastTextBaseline) {
                VStack(alignment:.leading,spacing:3) {
                    Text((week ? WL("每周", "Weekly") : WL("5 小时", "5 hour"))+(segmented ? " · "+WL("剩余", "Remaining") : "")).font(.system(size:11,weight:.medium)).lineLimit(1).minimumScaleFactor(0.8)
                    if segmented { Text(countdown(reset(week))).font(.system(size:9)).foregroundStyle(.secondary) }
                }
                Spacer(minLength:2)
                Text(percentage(remaining(week))).font(.system(size:segmented ? 25 : 22,weight:segmented ? .light : .medium)).monospacedDigit().lineLimit(1).minimumScaleFactor(0.7)
            }
            meter(remaining(week),segmented:segmented).frame(height:segmented ? 12 : 8)
            if !segmented { HStack(spacing:3) { Text(WL("剩余", "Remaining")); Spacer(); Image(systemName:"clock.arrow.circlepath"); Text(countdown(reset(week))) }.font(.system(size:9)).foregroundStyle(.secondary) }
        }
    }
    private var freshness: some View {
        ViewThatFits(in:.horizontal) {
            if fresh, let snapshot = entry.snapshot {
                Text(widgetUpdateText(snapshot.quota.updated_at ?? snapshot.updated_at)).fixedSize()
            }
            Color.clear.frame(width:0,height:0)
        }.font(.system(size:9,weight:.medium)).foregroundStyle(.secondary)
    }
    private func meter(_ value: Double?,segmented: Bool) -> some View {
        GeometryReader { geometry in
            if segmented {
                HStack(spacing:2) {
                    ForEach(0..<26,id:\.self) { index in RoundedRectangle(cornerRadius:1).fill(value.map {Double(index)/26 < $0/100} == true ? color : Color.secondary.opacity(0.18)) }
                }
            } else {
                Capsule().fill(color.opacity(dark ? 0.18 : 0.12)).overlay(alignment:.leading) { Capsule().fill(entry.style == .single ? (dark ? .white : .black) : color).frame(width:geometry.size.width*max(0,min(100,value ?? 0))/100) }
            }
        }
    }
}

private func quotaConfiguration(kind: String,title: LocalizedStringKey,style: QuotaStyle) -> some WidgetConfiguration {
    StaticConfiguration(kind:kind,provider:QuotaProvider(style:style)) { entry in QuotaWidgetView(entry:entry) }
        .configurationDisplayName(title)
        .description("查看 Codex 剩余额度与重置时间")
        .supportedFamilies([.systemSmall])
        .contentMarginsDisabled()
}
struct CodexioQuotaWidget: Widget {
    var body: some WidgetConfiguration { quotaConfiguration(kind:"com.wujuhu.codexio.quota",title:"Codex 单额度",style:.single) }
}
struct CodexioDualQuotaWidget: Widget {
    var body: some WidgetConfiguration { quotaConfiguration(kind:"com.wujuhu.codexio.quota.dual",title:"Codex 双额度",style:.dual) }
}
struct CodexioSegmentedQuotaWidget: Widget {
    var body: some WidgetConfiguration { quotaConfiguration(kind:"com.wujuhu.codexio.quota.segmented",title:"Codex 刻度额度",style:.segmented) }
}

func WL(_ chinese: String,_ english: String) -> String { NSLocalizedString(chinese,tableName:"Localizable",bundle:.main,value:english,comment:"") }

private func widgetUpdateText(_ timestamp: Double) -> String {
    let key = "com.wujuhu.codexio.widget.update-formatter"
    let formatter: DateFormatter
    if let cached = Thread.current.threadDictionary[key] as? DateFormatter { formatter = cached }
    else {
        formatter = DateFormatter(); formatter.locale = Locale(identifier:"en_US_POSIX")
        formatter.timeZone = .autoupdatingCurrent; formatter.dateFormat = "M.d HH:mm"
        Thread.current.threadDictionary[key] = formatter
    }
    return WL("上次更新：", "Last updated: ")+formatter.string(from:Date(timeIntervalSince1970:timestamp))
}
