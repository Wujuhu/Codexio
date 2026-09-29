// Adapted from user-provided report-cards-234-source.zip.
import SwiftUI

struct ReportArtBookmarkReport: View {
    let data: UsageReportData
    let palette: ReportArtCardPalette

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            ReportArtBrandHeader(data: data, palette: palette, centered: false)
            Text(data.modelHeadline)
                .font(reportEditorial(48)).fontWeight(.semibold)
                .lineSpacing(5)
                .foregroundStyle(palette.ink)
                .padding(.top, 35)
            Text(data.topModel?.name ?? L("暂无模型调用", "No model calls"))
                .font(.custom("Georgia", fixedSize: 58)).fontWeight(.medium)
                .foregroundStyle(palette.ink)
                .padding(.top, 24)
            if let top = data.topModel {
                Text("\(top.calls) / \(data.modelCalls) 次模型调用 · \(data.percent(top.calls, of: data.modelCalls))%")
                    .font(.system(size: 16, weight: .medium)).monospacedDigit()
                    .foregroundStyle(palette.muted)
                    .padding(.top, 4)
            }
            HStack(spacing: 0) {
                ReportArtSmallMetric(value: "\(data.requests)", label: L("次用户请求", "user requests"), palette: palette)
                ReportArtFineMetricDivider(palette: palette)
                ReportArtSmallMetric(value: data.costLabel, label: L("费用", "Cost"), palette: palette)
                ReportArtFineMetricDivider(palette: palette)
                ReportArtSmallMetric(value: data.tokenSummary, label: "Token", palette: palette)
            }
            .padding(.vertical, 20)
            .padding(.top, 16)
            ReportArtFineRule(color: palette.secondary)
            VStack(alignment: .leading, spacing: 10) {
                HStack {
                    Text(L("模型调用分布", "Model calls")).font(reportEditorial(22)).fontWeight(.semibold)
                    Spacer()
                    Text("\(data.modelCalls) " + L("次调用", "calls")).font(.system(size: 11)).foregroundStyle(palette.muted)
                }
                GeometryReader { geometry in
                    HStack(spacing: 2) {
                        ForEach(Array(data.models.enumerated()), id: \.element.id) { index, model in
                            Rectangle().fill(modelColor(index))
                                .frame(width: max(0, geometry.size.width * CGFloat(model.calls) / CGFloat(max(data.modelCalls, 1)) - 2))
                        }
                    }
                    .clipShape(Capsule())
                }.frame(height: 14)
                HStack(alignment: .top, spacing: 6) {
                    ForEach(Array(data.models.enumerated()), id: \.element.id) { index, model in
                        VStack(alignment: .leading, spacing: 3) {
                            Circle().fill(modelColor(index)).frame(width: 7, height: 7)
                            Text(model.name).font(.system(size: 12, weight: .medium)).lineLimit(1)
                            Text("\(model.calls) " + L("次", "calls")).font(.system(size: 12)).foregroundStyle(palette.muted)
                        }.frame(maxWidth: .infinity, alignment: .leading)
                    }
                }
            }
            .foregroundStyle(palette.ink)
            .padding(.top, 24)
            ReportArtProjectRows(data: data, palette: palette, showBars: true)
                .padding(.top, 30)
            ReportArtTinyCacheNote(data: data, palette: palette)
                .padding(.top, 18)
            Spacer(minLength: 16)
            HStack(alignment: .bottom, spacing: 8) {
                reportResourceImage("cat-bookmark")
                    .resizable().scaledToFit().frame(width: 168, height: 116, alignment: .bottom)
                ReportArtCatInsight(text: data.projectInsight(), palette: palette)
            }
            Text(L("Codexio · 陪你看见每一点进展", "Codexio · Every little step counts"))
                .font(.system(size: 10)).tracking(2).foregroundStyle(palette.muted)
                .frame(maxWidth: .infinity).padding(.top, 18)
        }
    }

    private func modelColor(_ index: Int) -> Color {
        switch index {
        case 0: palette.accent
        case 1: palette.secondary
        default: palette.warm
        }
    }
}

struct ReportArtGardenReport: View {
    let data: UsageReportData
    let palette: ReportArtCardPalette

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            ReportArtBrandHeader(data: data, palette: palette, centered: true)
            Text(data.dateLabel)
                .font(.system(size: 12)).foregroundStyle(palette.muted)
                .frame(maxWidth: .infinity)
                .padding(.top, 8)
            ReportArtFineRule(color: palette.accent).padding(.top, 22)
            Text(data.projectHeadline)
                .font(reportEditorial(50)).fontWeight(.semibold)
                .lineSpacing(4)
                .fixedSize(horizontal: false, vertical: true)
                .layoutPriority(1)
                .foregroundStyle(palette.ink)
                .padding(.top, 28)
            HStack(alignment: .bottom, spacing: 8) {
                VStack(alignment: .leading, spacing: 5) {
                    if let top = data.topProject {
                        HStack(alignment: .firstTextBaseline, spacing: 6) {
                            ReportArtNumber(value: "\(top.requests) / \(data.requests)")
                                .font(reportEditorial(49)).fontWeight(.semibold).monospacedDigit()
                            Text(L("次请求", "requests")).font(.system(size: 13))
                        }
                        Text("\(data.percent(top.requests, of: data.requests))% " + L("在 ", "in ") + top.name)
                            .font(.system(size: 16, weight: .medium))
                            .foregroundStyle(palette.accent)
                    }
                }
                Spacer(minLength: 0)
                reportResourceImage("cat-garden")
                    .resizable().scaledToFit().frame(width: 173, height: 137, alignment: .bottom)
            }
            .foregroundStyle(palette.ink)
            .padding(.top, 24)
            if let top = data.topProject {
                ReportArtPercentBar(fraction: CGFloat(top.requests) / CGFloat(max(data.requests, 1)), color: palette.accent, track: palette.secondary.opacity(0.3), height: 12)
                    .padding(.top, 12)
            }
            ReportArtFineRule(color: palette.accent).padding(.top, 26)
            ReportArtProjectRows(data: data, palette: palette, showBars: false)
                .padding(.top, 23)
            ReportArtFineRule(color: palette.accent).padding(.top, 24)
            HStack(spacing: 0) {
                ReportArtSmallMetric(value: "\(data.modelCalls)", label: L("次模型调用", "model calls"), palette: palette)
                ReportArtFineMetricDivider(palette: palette)
                ReportArtSmallMetric(value: data.tokenSummary, label: "Token", palette: palette)
                ReportArtFineMetricDivider(palette: palette)
                ReportArtSmallMetric(value: data.costLabel, label: L("费用", "Cost"), palette: palette)
            }
            .padding(.vertical, 20)
            ReportArtFineRule(color: palette.accent)
            ReportArtModelSpotlight(data: data, palette: palette)
                .padding(.top, 24)
            ReportArtTinyCacheNote(data: data, palette: palette)
                .padding(.top, 20)
            Spacer(minLength: 20)
            ReportArtCatInsight(text: data.projectInsight(), palette: palette)
            Text(L("专注 · 与 AI 共成长", "Focus · Grow with AI"))
                .font(.system(size: 10)).tracking(3).foregroundStyle(palette.muted)
                .frame(maxWidth: .infinity).padding(.top, 17)
        }
    }
}

struct ReportArtAfternoonReport: View {
    let data: UsageReportData
    let palette: ReportArtCardPalette

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            ReportArtBrandHeader(data: data, palette: palette, centered: false)
            Text(data.activityHeadline)
                .font(reportEditorial(48)).fontWeight(.semibold)
                .foregroundStyle(palette.ink)
                .padding(.top, 36)
            Text(L("用户请求时段", "User request times"))
                .font(reportEditorial(27)).fontWeight(.semibold)
                .foregroundStyle(palette.ink)
                .padding(.top, 40)
            if let peak = data.timeSlices.max(by: { $0.requests < $1.requests }) {
                Text(peak.name + " · \(data.percent(peak.requests, of: data.requests))%")
                    .font(.system(size: 13)).foregroundStyle(palette.accent)
                    .padding(.top, 4)
            }
            HStack(alignment: .bottom, spacing: 25) {
                ForEach(data.timeSlices) { slice in
                    VStack(spacing: 7) {
                        ReportArtNumber(value: "\(slice.requests)")
                            .font(reportEditorial(19)).fontWeight(.semibold).monospacedDigit()
                        RoundedRectangle(cornerRadius: 9)
                            .fill(slice.name == "午后" ? palette.accent : palette.secondary.opacity(0.65))
                            .frame(height: CGFloat(slice.requests) / CGFloat(max(data.timeSlices.map(\.requests).max() ?? 1, 1)) * 165)
                        Text(slice.name).font(.system(size: 13))
                    }
                    .frame(maxWidth: .infinity, alignment: .bottom)
                }
            }
            .foregroundStyle(palette.ink)
            .frame(height: 225, alignment: .bottom)
            .padding(.trailing, 42)
            .overlay(alignment: .trailing) {
                ReportArtTimeTail().stroke(palette.accent.opacity(0.68), style: StrokeStyle(lineWidth: 2, lineCap: .round))
                    .frame(width: 48, height: 213)
                    .allowsHitTesting(false)
            }
            .padding(.top, 18)
            ReportArtFineRule(color: palette.accent).padding(.top, 28)
            HStack(spacing: 0) {
                ReportArtSmallMetric(value: "\(data.requests)", label: L("次用户请求", "user requests"), palette: palette)
                ReportArtFineMetricDivider(palette: palette)
                ReportArtSmallMetric(value: "\(data.modelCalls)", label: L("次模型调用", "model calls"), palette: palette)
                ReportArtFineMetricDivider(palette: palette)
                ReportArtSmallMetric(value: data.tokenSummary, label: "Token", palette: palette)
                ReportArtFineMetricDivider(palette: palette)
                ReportArtSmallMetric(value: data.costLabel, label: L("费用", "Cost"), palette: palette)
            }
            .padding(.top, 22)
            .padding(.bottom, 22)
            ReportArtFineRule(color: palette.accent)
            ReportArtModelSpotlight(data: data, palette: palette).padding(.top, 26)
            ReportArtFineRule(color: palette.accent).padding(.top, 26)
            ReportArtProjectRows(data: data, palette: palette, showBars: true).padding(.top, 23)
            Spacer(minLength: 15)
            HStack(spacing: 10) {
                reportResourceImage("cat-afternoon")
                    .resizable().scaledToFit().frame(width: 120, height: 145)
                ReportArtCatInsight(text: afternoonInsight, palette: palette)
            }
            ReportArtTinyCacheNote(data: data, palette: palette).padding(.top, 18)
            Text(L("Codexio · 让 AI 融入每一个灵感时刻", "Codexio · A little AI in every idea"))
                .font(.system(size: 10)).tracking(1).foregroundStyle(palette.muted)
                .frame(maxWidth: .infinity).padding(.top, 13)
        }
    }

    private var afternoonInsight: String {
        return data.activityInsight
    }
}

private struct ReportArtFineMetricDivider: View {
    let palette: ReportArtCardPalette
    var body: some View { Rectangle().fill(palette.secondary.opacity(0.6)).frame(width: 1, height: 43) }
}
