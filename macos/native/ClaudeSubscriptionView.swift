import SwiftUI

struct ClaudeSubscriptionView: View {
    @ObservedObject var state: AppState
    @State private var showLocalHistory = true
    private var accountLine: String {
        let account = state.claudeQuota.account
        let values = [account.string("planType"),account.string("email")].filter {!$0.isEmpty}
        return values.isEmpty ? "—" : values.joined(separator:"  ")
    }
    var body: some View {
        ScrollView {
            VStack(alignment:.leading,spacing:23) {
                Text(accountLine).font(.system(size:14,weight:.medium)).foregroundStyle(.secondary).lineLimit(1).help(accountLine).textSelection(.enabled)
                AdaptiveRow(spacing:12) {
                    QuotaCard(window:state.claudeQuota.five,title:L("5 小时额度", "5-hour limit"),fresh:state.claudeQuotaFresh,compactLayout:true)
                    QuotaCard(window:state.claudeQuota.week,title:L("周额度", "Weekly limit"),fresh:state.claudeQuotaFresh,compactLayout:true)
                }
                VStack(alignment:.leading,spacing:12) {
                    DisclosureGroup(L("周额度估算", "Weekly allowance estimates"),isExpanded:$showLocalHistory) {
                        EstimateHistoryView(state:state,rows:state.claudeWeeklyEstimates)
                    }.font(.system(size:13))
                }
            }.padding(.horizontal,PageLayout.inset).padding(.bottom,PageLayout.inset)
        }
    }
}
