import AppIntents
import SwiftUI
import WidgetKit

enum QuotaStyle: String, AppEnum {
    case single, dual, segmented
    static var typeDisplayRepresentation: TypeDisplayRepresentation { "额度样式" }
    static var caseDisplayRepresentations: [QuotaStyle:DisplayRepresentation] { [.single:"单额度",.dual:"双额度",.segmented:"双额度刻度条"] }
}
enum QuotaScope: String, AppEnum {
    case week, five
    static var typeDisplayRepresentation: TypeDisplayRepresentation { "额度窗口" }
    static var caseDisplayRepresentations: [QuotaScope:DisplayRepresentation] { [.week:"周额度",.five:"5 小时额度"] }
}
enum QuotaAppearance: String, AppEnum {
    case system, light, dark
    static var typeDisplayRepresentation: TypeDisplayRepresentation { "外观" }
    static var caseDisplayRepresentations: [QuotaAppearance:DisplayRepresentation] { [.system:"跟随系统",.light:"浅色",.dark:"深色"] }
}

struct QuotaConfiguration: WidgetConfigurationIntent {
    static var title: LocalizedStringResource { "Codex 额度" }
    static var description: IntentDescription { "选择额度样式、窗口和外观" }
    @Parameter(title:"样式",default:.single) var style: QuotaStyle
    @Parameter(title:"单额度窗口",default:.week) var scope: QuotaScope
    @Parameter(title:"外观",default:.system) var appearance: QuotaAppearance
}

struct QuotaEntry: TimelineEntry {
    var date: Date
    var snapshot: Snapshot?
    var configuration: QuotaConfiguration
}
struct QuotaProvider: AppIntentTimelineProvider {
    func placeholder(in context: Context) -> QuotaEntry { QuotaEntry(date:Date(),snapshot:nil,configuration:QuotaConfiguration()) }
    func snapshot(for configuration: QuotaConfiguration,in context: Context) async -> QuotaEntry { QuotaEntry(date:Date(),snapshot:Snapshot.read(),configuration:configuration) }
    func timeline(for configuration: QuotaConfiguration,in context: Context) async -> Timeline<QuotaEntry> {
        let date = Date(); return Timeline(entries:[QuotaEntry(date:date,snapshot:Snapshot.read(),configuration:configuration)],policy:.after(date.addingTimeInterval(900)))
    }
}

struct QuotaWidgetView: View {
    @Environment(\.colorScheme) private var systemScheme
    let entry: QuotaEntry
    private var dark: Bool { entry.configuration.appearance == .dark || entry.configuration.appearance == .system && systemScheme == .dark }
    private var fresh: Bool { entry.snapshot.map {Date().timeIntervalSince1970-$0.updated_at < 900 && $0.quota.applicable != false} ?? false }
    private var color: Color { entry.configuration.style == .dual ? Color(red:0.88,green:0.49,blue:0.36) : Color.blue }
    private func remaining(_ week: Bool) -> Double? { guard fresh else { return nil }; return week ? entry.snapshot?.quota.week : entry.snapshot?.quota.five_hour }
    private func reset(_ week: Bool) -> Double? { week ? entry.snapshot?.quota.week_reset_at : entry.snapshot?.quota.five_hour_reset_at }
    private func percentage(_ value: Double?) -> String { value.map {String(format:"%.0f%%",max(0,min(100,$0)))} ?? "—" }
    private func countdown(_ value: Double?) -> String {
        guard let value, fresh, value > Date().timeIntervalSince1970 else { return "—" }
        let seconds = Int(value-Date().timeIntervalSince1970), days = seconds/86400
        let time = String(format:"%02d:%02d",seconds%86400/3600,seconds%3600/60)
        return days > 0 ? "\(days)d "+time : time
    }
    var body: some View {
        content
            .padding(14)
            .frame(maxWidth:.infinity,maxHeight:.infinity,alignment:.topLeading)
            .containerBackground(for:.widget) { dark ? Color(red:0.055,green:0.058,blue:0.065) : Color.white }
            .environment(\.colorScheme,dark ? .dark : .light)
            .widgetURL(URL(string:"codexio://subscription"))
    }
    @ViewBuilder private var content: some View {
        if entry.configuration.style == .single { single }
        else {
            VStack(alignment:.leading,spacing:8) {
                HStack { Text("Codex").font(.system(size:13,weight:.semibold)); Spacer(minLength:4); freshness }
                dualRow(week:true)
                dualRow(week:false)
            }
        }
    }
    private var single: some View {
        let week = entry.configuration.scope == .week, value = remaining(week)
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
            if let count = entry.snapshot?.quota.reset_count {
                HStack(spacing:5) { Image(systemName:"arrow.counterclockwise.circle.fill"); Text(WL("重置次数", "Resets")); Spacer(); Text("\(count)×").monospacedDigit() }.font(.system(size:10))
            }
        }
    }
    private func dualRow(week: Bool) -> some View {
        let segmented = entry.configuration.style == .segmented
        return VStack(alignment:.leading,spacing:4) {
            HStack(alignment:.lastTextBaseline) {
                VStack(alignment:.leading,spacing:3) {
                    Text(week ? WL("每周", "Weekly") : WL("5 小时", "5 hour")).font(.system(size:11,weight:.medium))
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
        HStack(spacing:4) {
            Image(systemName:"arrow.triangle.2.circlepath")
            if !fresh { Text(entry.snapshot?.quota.applicable == false ? WL("不可用", "Unavailable") : WL("等待更新", "Waiting")) }
            else if let snapshot = entry.snapshot { Text(Date(timeIntervalSince1970:snapshot.updated_at),style:.relative) }
        }.font(.system(size:9,weight:.medium)).foregroundStyle(.secondary).lineLimit(1)
    }
    private func meter(_ value: Double?,segmented: Bool) -> some View {
        GeometryReader { geometry in
            if segmented {
                HStack(spacing:2) {
                    ForEach(0..<26,id:\.self) { index in RoundedRectangle(cornerRadius:1).fill(value.map {Double(index)/26 < $0/100} == true ? color : Color.secondary.opacity(0.18)) }
                }
            } else {
                Capsule().fill(color.opacity(dark ? 0.18 : 0.12)).overlay(alignment:.leading) { Capsule().fill(entry.configuration.style == .single ? (dark ? .white : .black) : color).frame(width:geometry.size.width*max(0,min(100,value ?? 0))/100) }
            }
        }
    }
}

struct CodexioQuotaWidget: Widget {
    var body: some WidgetConfiguration {
        AppIntentConfiguration(kind:"com.wujuhu.codexio.quota",intent:QuotaConfiguration.self,provider:QuotaProvider()) { entry in QuotaWidgetView(entry:entry) }
            .configurationDisplayName("Codex 额度")
            .description("仅显示额度，提供三种样式")
            .supportedFamilies([.systemSmall])
            .contentMarginsDisabled()
    }
}

func WL(_ chinese: String,_ english: String) -> String { NSLocalizedString(chinese,tableName:"Localizable",bundle:.main,value:english,comment:"") }
