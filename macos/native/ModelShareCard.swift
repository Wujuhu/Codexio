import SwiftUI

struct ModelShareCard: View {
    let projection: TrendProjection
    @State private var metric = 0
    @State private var visible = 5
    nonisolated static func amount(_ value: MobileMetric,_ index: Int) -> Double { index == 0 ? value.cost ?? 0 : index == 1 ? Double(value.tokens ?? 0) : Double(value.requests) }
    private func share(_ value: MobileMetric,_ index: Int) -> Double {
        let total = index == 0 ? projection.summary.cost ?? 0 : index == 1 ? Double(projection.summary.tokens ?? 0) : Double(projection.summary.requests)
        return total > 0 ? min(1,max(0,Self.amount(value,index)/total)) : 0
    }
    private var names: [String] {[L("费用", "Cost"),"Token",L("请求", "Requests")]}
    var body: some View {
        VStack(alignment:.leading,spacing:16) {
            HStack { SectionHeading(title:L("模型占比", "Model share")); Spacer(); Picker("",selection:$metric) {ForEach(0..<3) {Text(names[$0]).tag($0)}}.labelsHidden().pickerStyle(.segmented).frame(width:210) }
            if projection.modelRows[metric].isEmpty {Text(L("暂无单模型数据", "No single-model data")).foregroundStyle(.secondary).font(.caption)}
            ForEach(Array(projection.modelRows[metric].prefix(visible))) { model in
                VStack(alignment:.leading,spacing:8) {
                    HStack {Text(model.name).font(.system(size:13,weight:.semibold)); Spacer(); Text(metric == 0 ? money(model.metric.cost) : metric == 1 ? compact(model.metric.tokens.map(Double.init)) : String(model.metric.requests)).monospacedDigit()}
                    GeometryReader {geometry in ZStack(alignment:.leading) {Capsule().fill(Color.accentColor.opacity(0.1)); Capsule().fill(Color.accentColor).frame(width:geometry.size.width*share(model.metric,metric))}}.frame(height:7)
                    HStack {ForEach(0..<3) {index in Text(names[index]+" "+String(format:"%.1f%%",share(model.metric,index)*100)).foregroundStyle(index == metric ? .primary : .secondary).frame(maxWidth:.infinity,alignment:.leading)}}.font(.caption)
                }.padding(12).background(Color.primary.opacity(0.025),in:RoundedRectangle(cornerRadius:12))
            }
            if visible < projection.modelRows[metric].count {Button(L("显示更多", "Show more")) {visible += 5}.buttonStyle(.plain).foregroundStyle(Color.accentColor)}
        }.padding(18).overlay(RoundedRectangle(cornerRadius:14).stroke(.secondary.opacity(0.15)))
        .onChange(of:metric) {_,_ in visible = 5}
    }
}
