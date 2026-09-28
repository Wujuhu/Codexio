import SwiftUI

struct SubscriptionView: View {
    @ObservedObject var state: AppState
    @State private var selectedReset: ResetCredit?
    @State private var confirming = false
    @State private var selectedAccount = ""
    @State private var editingProfile = false
    @State private var expandedPeriods: Set<String> = []
    @State private var showLocalHistory = true
    private var profile: Object { state.preferences.analytics.object("subscription_profile") }
    private var periods: [Object] { state.planHistory.objects("periods").filter {$0.integer("window_minutes") == 10080}.sorted {$0.string("starts_at") > $1.string("starts_at")} }
    var body: some View {
        ScrollView {
            VStack(alignment:.leading,spacing:23) {
                HStack(alignment:.top,spacing:28) {
                    VStack(alignment:.leading,spacing:12) {
                        Text(profile.string("plan").isEmpty ? (state.quota.account.string("planType").isEmpty ? L("个人订阅资料", "Subscription profile") : "ChatGPT "+planName(state.quota.account.string("planType"))) : profile.string("plan")).font(.system(size:16,weight:.semibold))
                        if !state.quota.account.string("email").isEmpty { Text(state.quota.account.string("email")).lineLimit(1).textSelection(.enabled) }
                        if let price = profile.number("price_usd") { Text(L("订阅价格", "Subscription price")+" · "+money(price)) }
                        if !profile.string("renewal_date").isEmpty { Text(L("续费日期", "Renewal date")+" · "+profile.string("renewal_date")) }
                        Button(L("编辑资料", "Edit profile")) { editingProfile = true }.padding(.top,4)
                    }.font(.system(size:12)).foregroundStyle(.secondary).frame(width:205,alignment:.leading)
                    Divider().frame(height:120)
                    HStack(alignment:.top,spacing:12) {
                        QuotaCard(window:state.quota.five,title:L("5 小时额度", "5-hour limit"),fresh:state.quota.fresh,size:31,stacked:true)
                        QuotaCard(window:state.quota.week,title:L("周额度", "Weekly limit"),fresh:state.quota.fresh,size:31,stacked:true)
                    }
                }
                if let error = state.quota.error { StatusNote(text:error) }
                planHistory
                HStack {
                    SectionHeading(title:L("主动重置", "Rate-limit resets"))
                    Text(state.quota.availableCount.map { "\($0) "+L("次可用", "available") } ?? "—").font(.system(size:12)).foregroundStyle(.secondary)
                }.padding(.top,4)
                VStack(spacing:0) {
                    ForEach(state.quota.credits) { credit in
                        Divider()
                        HStack(spacing:20) {
                            VStack(alignment:.leading,spacing:5) {
                                Text(credit.title).font(.system(size:13,weight:.medium))
                                Text(L("截止时间", "Expires")+" · "+credit.expiry).font(.system(size:12)).foregroundStyle(.secondary)
                                if !credit.available { Text(credit.raw.string("status") == "redeeming" ? L("正在使用", "Redeeming") : L("暂不可使用", "Unavailable")).font(.caption).foregroundStyle(.secondary) }
                            }
                            Spacer()
                            Button(state.pendingResets.contains(credit.id) ? L("重试本次", "Retry this reset") : L("使用重置", "Use reset")) { selectedReset = credit; selectedAccount = state.quota.account.string("identityKey"); confirming = true }
                                .disabled(state.resetBusy || (!credit.available && !state.pendingResets.contains(credit.id)) || !state.quota.fresh || state.paths.mock || state.quota.account.string("identityKey").isEmpty)
                        }.padding(.vertical,12)
                    }
                    Divider()
                }
                if !state.quota.detailsKnown || (state.quota.availableCount ?? 0) > state.quota.credits.count { StatusNote(text:state.quota.detailsKnown ? L("只显示服务端已提供的重置明细", "Only reset details returned by the service are shown") : L("逐次重置明细暂不可用", "Individual reset details are unavailable")) }
                if state.quota.detailsKnown && state.quota.availableCount == 0 { StatusNote(text:L("没有可用重置次数", "No resets available")) }
                if let message = state.actionMessage { StatusNote(text:message) }
                localEstimates
            }.padding(.horizontal,PageLayout.inset).padding(.bottom,PageLayout.inset)
        }
        .sheet(isPresented:$editingProfile) { SubscriptionEditor(state:state) }
        .alert(L("使用这次额度重置？", "Use this reset?"),isPresented:$confirming,presenting:selectedReset) { credit in
            Button(L("取消", "Cancel"),role:.cancel) { selectedReset = nil }
            Button(L("使用重置", "Use reset")) { state.consumeReset(credit,expectedAccount:selectedAccount); selectedReset = nil }
        } message: { credit in
            Text(state.quota.account.string("email")+"\n"+credit.title+"\n"+L("截止时间", "Expires")+" · "+credit.expiry+"\n\n"+L("这会消耗所选重置，无法撤销。", "This consumes the selected reset and cannot be undone."))
        }
        .onChange(of:state.quota.account.string("identityKey")) { _,_ in confirming = false; selectedReset = nil }
    }
    private var planHistory: some View {
        VStack(alignment:.leading,spacing:13) {
            HStack { SectionHeading(title:L("套餐用量历史", "Plan usage history")); Text(L("按模型", "By model")).font(.system(size:12)).foregroundStyle(.secondary) }
            if state.reportsLoading { ProgressView().controlSize(.small) }
            if let error = state.reportError, !periods.isEmpty { StatusNote(text:error) }
            if periods.isEmpty {
                StatusNote(text:state.reportError ?? L("当前账户尚未提供周期明细", "This account has not provided period details"))
                Button(L("刷新明细", "Refresh details")) { state.refreshReports(force:true) }.controlSize(.small)
            } else {
                VStack(spacing:0) {
                    HStack { Text(L("周期", "Period")); Spacer(); Text(L("已使用限额百分比", "Limit used")) }.font(.system(size:12)).foregroundStyle(.secondary).padding(15).background(Color.primary.opacity(0.055))
                    ForEach(periods,id:\.periodIdentity) { period in
                        let id = period.string("id"), open = expandedPeriods.contains(id) || (expandedPeriods.isEmpty && periods.first?.string("id") == id)
                        VStack(alignment:.leading,spacing:12) {
                            Button { if open { expandedPeriods = ["closed"] } else { expandedPeriods = [id] } } label: {
                                HStack { Text(periodRange(period)).help(reportDateText(parsedDate(period["starts_at"]))+" – "+reportDateText(parsedDate(period["ends_at"]))); Image(systemName:open ? "chevron.down" : "chevron.right").font(.caption); Spacer(); Text((state.planHistory.flag("approximate",true) ? L("约 ", "Approx. ") : "")+percent(period.number("used_basis_points").map {$0/100},digits:1)).monospacedDigit() }.font(.system(size:14))
                            }.buttonStyle(.plain)
                            if open {
                                StatusNote(text:L("统计截至", "Usage as of")+" "+reportDateText(parsedDate(state.planHistory["data_as_of"])))
                                let model = period.objects("breakdowns").first {$0.string("dimension") == "model"}
                                ForEach(model?.objects("rows") ?? [],id:\.modelIdentity) { row in
                                    HStack { Text(row.string("key")).foregroundStyle(.secondary); Spacer(); Text(percent(row.number("basis_points").map {$0/100},digits:1)).monospacedDigit() }.font(.system(size:13))
                                }
                                if !period.flag("accounting_complete") || !state.planHistory.flag("coverage_complete") { StatusNote(text:L("部分数据", "Partial data")) }
                            }
                        }.padding(18)
                    }
                }.clipShape(RoundedRectangle(cornerRadius:16)).overlay(RoundedRectangle(cornerRadius:16).stroke(.secondary.opacity(0.2)))
            }
        }
    }
    private var localEstimates: some View {
        VStack(alignment:.leading,spacing:12) {
            DisclosureGroup(L("周额度估算", "Weekly allowance estimates"),isExpanded:$showLocalHistory) {
                EstimateHistoryView(state:state)
            }.font(.system(size:13))
        }
    }
    private func periodRange(_ period: Object) -> String {
        let start = parsedDate(period["starts_at"]), end = parsedDate(period["ends_at"])
        return reportDateText(start,dayOnly:true)+" – "+reportDateText(end,dayOnly:true)
    }
}

extension Dictionary where Key == String, Value == Any {
    var periodIdentity: String { string("id") }
    var modelIdentity: String { string("key") }
}

struct SubscriptionEditor: View {
    @ObservedObject var state: AppState
    @Environment(\.dismiss) private var dismiss
    @State private var plan = ""
    @State private var price = ""
    @State private var renewal = ""
    @State private var error = ""
    var body: some View {
        VStack(alignment:.leading,spacing:20) {
            Text(L("订阅资料", "Subscription profile")).font(.title2)
            Form {
                TextField(L("计划名称", "Plan name"),text:$plan)
                TextField(L("订阅价格（USD）", "Subscription price (USD)"),text:$price)
                TextField(L("续费日期（YYYY-MM-DD）", "Renewal date (YYYY-MM-DD)"),text:$renewal)
            }
            if !error.isEmpty { Text(error).font(.caption).foregroundStyle(.red) }
            HStack { Spacer(); Button(L("取消", "Cancel")) { dismiss() }; Button(L("保存", "Save")) { save() }.keyboardShortcut(.defaultAction) }
        }.padding(26).frame(width:440)
        .onAppear { let p = state.preferences.analytics.object("subscription_profile"); plan = p.string("plan"); price = p.number("price_usd").map {String($0)} ?? ""; renewal = p.string("renewal_date") }
    }
    private func save() {
        let value = price.isEmpty ? nil : Double(price)
        guard price.isEmpty || value.map({$0.isFinite && $0 >= 0 && $0 <= 1_000_000}) == true, renewal.isEmpty || parsedDate(renewal) != nil else { error = L("请填写有效的金额和日期", "Enter a valid amount and date"); return }
        state.setPreference("subscription_profile",["plan":String(plan.prefix(64)),"price_usd":value as Any? ?? NSNull(),"renewal_date":renewal]); dismiss()
    }
}
