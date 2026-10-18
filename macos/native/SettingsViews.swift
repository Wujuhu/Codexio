import SwiftUI
import AppKit

struct PricingHintText: View {
    let text: String
    var body: some View {
        Text(text).font(.system(size:12)).fixedSize(horizontal:false,vertical:true).padding(14).frame(maxWidth:.infinity,alignment:.leading)
    }
}

struct PricingView: View {
    @ObservedObject var state: AppState
    @State private var search=""
    @State private var expanded=Set(["gpt","claude","other"])
    private func rows(_ group: String) -> [PriceRow] {
        state.prices.filter { row in
            row.raw.string("group")==group && (search.isEmpty || ([row.model,row.raw.string("display_name")]+(row.raw["aliases"] as? [String] ?? [])).contains {$0.localizedCaseInsensitiveContains(search)})
        }
    }
    var body: some View {
        ScrollView {
            VStack(alignment:.leading,spacing:18) {
                StatusNote(text:L("美元 / 1M Token","USD / 1M tokens"))
                TextField(L("搜索模型","Search models"),text:$search).textFieldStyle(.roundedBorder)
                ForEach(["gpt","claude","other"],id:\.self) {group in
                    let values=rows(group)
                    DisclosureGroup(isExpanded:Binding(get:{expanded.contains(group)},set:{if $0 {expanded.insert(group)} else {expanded.remove(group)}})) {
                        priceTable(values,group:group).padding(.top,8)
                    } label: {
                        HStack(spacing:8) {Text(group=="gpt" ? "GPT":group=="claude" ? "Claude":L("其他","Other")).font(.system(size:14,weight:.medium));Text(String(values.count)).font(.caption).foregroundStyle(.secondary)}
                    }
                }
            }.padding(.horizontal,PageLayout.inset).padding(.bottom,PageLayout.inset)
        }
    }
    private func priceTable(_ values: [PriceRow],group: String) -> some View {
        let ttl=values.contains {$0.raw.number("cache_write_1h") != nil}
        let columns=[
            GridColumn(id:"model",title:L("模型","Model"),width:220,maximum:260,alignment:.center),
            GridColumn(id:"input",title:L("输入","Input"),width:90,maximum:110,alignment:.center),
            GridColumn(id:"cache_read",title:L("缓存读取","Cache read"),width:100,maximum:120,alignment:.center),
            GridColumn(id:"cache_write",title:ttl ? L("缓存写入 5 分钟","Cache write 5 minutes"):L("缓存写入","Cache write"),width:ttl ? 120:100,maximum:140,alignment:.center)
        ]+(ttl ? [GridColumn(id:"cache_write_1h",title:L("缓存写入 1 小时","Cache write 1 hour"),width:120,maximum:140,alignment:.center)]:[])+[GridColumn(id:"output",title:L("输出","Output"),width:90,maximum:110,alignment:.center)]
        return CompactTable(columns:columns,rows:values.map {row in GridRow(id:row.id,text:{column in
            if column=="model" {
                let hint=row.raw.string("tooltip")
                return GridText(main:row.model,hint:hint=="按基础价计费" ? "":L(hint,row.raw.string("tooltip_en",hint)))
            }
            return GridText(main:row.raw.number(column).map {"$"+$0.formatted(.number.locale(Locale(identifier:"en_US")).precision(.fractionLength(2...4)))} ?? "—")
        })},revision:state.pricesRevision+search+group,rowHeight:30,preferences:state.preferences,storageKey:"unified-pricing-"+group,verticalScrolling:false)
        .frame(height:CGFloat(values.count)*31+44)
        .overlay(RoundedRectangle(cornerRadius:10).stroke(.secondary.opacity(0.16)))
    }
}

struct SettingsView: View {
    @Environment(\.compactPage) private var compact
    @ObservedObject var state: AppState
    @State private var codexPath = ""
    @State private var logRoot = ""
    @State private var upstreamConfirmation = false
    @State private var desiredUpstream = false
    private func sectionTitle(_ section: String) -> String { section == "mobile" ? L("同步", "Sync") : section == "appearance" ? L("外观", "Appearance") : section == "data" ? L("数据", "Data") : section == "menubar" ? L("菜单栏", "Menu bar") : L("应用", "App") }
    var body: some View {
        ScrollView {
            VStack(alignment:.leading,spacing:20) {
                AdaptiveRow(spacing:28,alignment:.top) {
                    if compact {
                        Picker("",selection:$state.settingsSection) {
                            ForEach(["appearance","data","app","mobile","menubar"],id:\.self) { Text(sectionTitle($0)).tag($0) }
                        }.labelsHidden().pickerStyle(.menu).fixedSize()
                    } else {
                        VStack(spacing:5) {
                            ForEach(["appearance","data","app","mobile","menubar"],id:\.self) { section in
                                Button { state.settingsSection = section } label: { Text(sectionTitle(section)).frame(maxWidth:.infinity,alignment:.leading).padding(10).contentShape(Rectangle()).background(state.settingsSection == section ? Color.primary.opacity(0.07) : .clear,in:RoundedRectangle(cornerRadius:7)) }.buttonStyle(.plain)
                            }
                        }.frame(width:145)
                        Divider()
                    }
                    VStack(alignment:.leading,spacing:0) {
                        SectionHeading(title:sectionTitle(state.settingsSection)).padding(.bottom,20)
                        switch state.settingsSection {
                        case "appearance": appearance
                        case "data": data
                        case "menubar": menuBar
                        case "mobile": MobileSyncSettings(sync:state.mobileSync) { state.mobileSync.update(state.usage,quota:state.menuQuota) }
                        default: app
                        }
                    }.frame(maxWidth:.infinity,alignment:.leading)
                }
            }.padding(.horizontal,PageLayout.inset).padding(.bottom,PageLayout.inset)
        }.onAppear { codexPath = state.preferences.general.string("codex_path"); logRoot = state.preferences.roots.first?.path ?? "" }
        .alert(desiredUpstream ? L("开启上游检测？", "Enable upstream detection?") : L("关闭上游检测？", "Disable upstream detection?"),isPresented:$upstreamConfirmation) {
            Button(L("取消", "Cancel"),role:.cancel) {}
            Button(desiredUpstream ? L("开启", "Enable") : L("关闭", "Disable")) { state.onUpstreamChange?(desiredUpstream) }
        } message: {
            Text(L("Codex 将通过本机转发请求，仅记录响应中的模型名称。更改路由后需要重新打开 Codex 才能让现有会话使用新配置。", "Codex requests will pass through a local relay that records only response model names. Reopen Codex after the route changes to apply the configuration to existing sessions."))
        }
    }
    private var appearance: some View {
        VStack(spacing:0) {
            row(L("主题", "Theme")) { Picker("",selection:Binding(get:{state.theme},set:{state.setPreference("theme",$0)})) { Text(L("跟随系统", "Follow system")).tag("system"); Text(L("浅色", "Light")).tag("light"); Text(L("深色", "Dark")).tag("dark") }.labelsHidden().frame(width:155) }
            AppIconPicker(state:state).padding(.top,22)
        }
    }
    private var data: some View {
        VStack(alignment:.leading,spacing:16) {
            Text(L("Codex 组件路径", "Codex executable")).font(.system(size:13,weight:.medium))
            HStack { TextField(L("自动发现 Codex / ChatGPT.app", "Detect Codex / ChatGPT.app automatically"),text:$codexPath).textFieldStyle(.roundedBorder).onSubmit { state.setPreference("codex_path",codexPath,general:true); state.refreshQuota() }; Button(L("选择", "Choose")) { choose(directory:false) } }
            Button(L("应用路径", "Apply path")) { state.setPreference("codex_path",codexPath,general:true); state.client.close(); state.refreshQuota() }.controlSize(.small)
            Divider().padding(.vertical,6)
            Text(L("本机日志目录", "Local log directory")).font(.system(size:13,weight:.medium))
            HStack { TextField("~/.codex",text:$logRoot).textFieldStyle(.roundedBorder); Button(L("选择", "Choose")) { choose(directory:true) } }
            Button(L("应用目录", "Apply directory")) { state.setPreference("codex_roots",[logRoot]); state.rescan() }.controlSize(.small)
            Divider().padding(.vertical,6)
            row(L("本地索引", "Local index")) { Text("\(state.usage.calls.count)").foregroundStyle(.secondary).monospacedDigit() }
            row(L("重新扫描本地记录", "Rescan local records")) { Button(L("开始扫描", "Rescan now")) { state.rescan() }.disabled(state.loading || state.paths.mock) }
            row(L("数据目录", "Data folder")) { Button(L("在 Finder 中打开", "Show in Finder")) { NSWorkspace.shared.open(state.paths.data) } }
            Divider().padding(.vertical, 6)
            HStack { Text(L("AI 使用报告", "AI usage reports")).font(.system(size: 13, weight: .medium)); Spacer(); Button(L("打开报告", "Open reports")) { state.onOpenUsageReport?() } }
            Text(state.usageReportDirectory.path).font(.system(size: 11)).foregroundStyle(.secondary).textSelection(.enabled)
            HStack {
                Button(L("在 Finder 中打开", "Show in Finder")) { NSWorkspace.shared.selectFile(nil, inFileViewerRootedAtPath: state.usageReportDirectory.path) }
                Spacer()
                Toggle(L("每天 08:00 后首次打开时显示", "Show on first open after 08:00"), isOn: Binding(get: { state.preferences.analytics.flag("usage_report_auto", true) }, set: { state.setPreference("usage_report_auto", $0) })).toggleStyle(.switch)
            }.font(.system(size: 12))
        }
    }
    private var app: some View {
        VStack(spacing:0) {
            row(L("当前版本", "Current version")+" · Codexio "+BuildInfo.version) {
                if state.updateAvailable { Button(L("更新…", "Update…")) { state.onInstallUpdate?() } }
                else { Button(L("检查更新", "Check for updates")) { state.onCheckUpdate?() }.disabled(state.paths.mock) }
            }
            if !state.updateStatus.isEmpty { StatusNote(text:state.updateStatus).padding(.vertical,8) }
            row(L("自动检查更新", "Check for updates automatically")) { Toggle("",isOn:Binding(get:{state.preferences.analytics.flag("macos_auto_update",true)},set:{state.setPreference("macos_auto_update",$0)})).labelsHidden().toggleStyle(.switch) }
            row(L("上游检测", "Upstream detection")) { Toggle("",isOn:Binding(get:{state.preferences.analytics.flag("upstream_detection_enabled")},set:{desiredUpstream = $0; upstreamConfirmation = true})).labelsHidden().toggleStyle(.switch).disabled(state.paths.mock) }
            if !state.upstreamStatus.isEmpty { StatusNote(text:state.upstreamStatus).padding(.vertical,8) }
            row(L("额度刷新", "Limit refresh")) { Picker("",selection:Binding(get:{state.preferences.general.integer("refresh_interval_seconds") ?? 60},set:{state.setPreference("refresh_interval_seconds",$0,general:true)})) { ForEach([30,60,300],id:\.self) { Text("\($0) s").tag($0) } }.labelsHidden().frame(width:110) }
            row(L("日志刷新", "Log refresh")) { Picker("",selection:Binding(get:{state.preferences.analytics.integer("usage_refresh_interval_seconds") ?? 10},set:{state.setPreference("usage_refresh_interval_seconds",$0)})) { ForEach([5,10,30,60],id:\.self) { Text("\($0) s").tag($0) } }.labelsHidden().frame(width:110) }
            row(L("额度估算间隔", "Estimate interval")) { Picker("",selection:Binding(get:{state.preferences.analytics.integer("week_estimate_interval_minutes") ?? 10},set:{state.setPreference("week_estimate_interval_minutes",$0)})) { ForEach([10,30,60],id:\.self) { Text("\($0) min").tag($0) } }.labelsHidden().frame(width:110) }
        }
    }
    private var menuBar: some View {
        VStack(spacing:0) {
            row(L("在菜单栏显示", "Show in menu bar")) { Toggle("",isOn:Binding(get:{state.menuVisible},set:{state.setPreference("menu_bar_visible",$0)})).labelsHidden().toggleStyle(.switch) }
            MenuBarSettings(state:state).padding(.top,16)
        }
    }
    private func row<V:View>(_ title: String,@ViewBuilder content: ()->V) -> some View { VStack(spacing:0) { HStack(spacing:20) { Text(title).font(.system(size:13)); Spacer(minLength:16); content() }.padding(.vertical,16); Divider() } }
    private func choose(directory: Bool) {
        let panel = NSOpenPanel(); panel.canChooseDirectories = true; panel.canChooseFiles = !directory; panel.allowsMultipleSelection = false
        if panel.runModal() == .OK, let url = panel.url { if directory { logRoot = url.path } else { codexPath = url.path } }
    }
}

private struct AppIconPicker: View {
    @ObservedObject var state: AppState
    @State private var pending = "main"
    @State private var previews: [Branding.IconPreview] = []
    var body: some View {
        VStack(alignment:.leading,spacing:16) {
            Text(L("应用图标", "App icon")).font(.system(size:13,weight:.medium))
            LazyVGrid(columns:[GridItem(.adaptive(minimum:76,maximum:92),spacing:12)],alignment:.leading,spacing:12) {
                ForEach(previews) { option in
                    Button { pending = option.id } label: {
                        Image(nsImage:option.image).resizable().scaledToFit().frame(width:60,height:60)
                            .padding(8).background(pending == option.id ? Color.accentColor.opacity(0.10) : Color.clear,in:RoundedRectangle(cornerRadius:17))
                            .overlay(RoundedRectangle(cornerRadius:17).stroke(pending == option.id ? Color.accentColor : Color.secondary.opacity(0.15),lineWidth:pending == option.id ? 2 : 1))
                    }.buttonStyle(.plain).disabled(state.appIconApplying)
                        .accessibilityLabel(L("图标", "Icon")+" \((Branding.iconIDs.firstIndex(of:option.id) ?? 0)+1)")
                        .accessibilityValue(pending == option.id ? L("已选择", "Selected") : "")
                }
            }
            HStack {
                Button(L("确认应用", "Apply selection")) { state.applyAppIcon(pending) }
                    .disabled(pending == state.appIconStyle || state.appIconApplying)
                Button(L("取消选择", "Cancel selection")) { pending = state.appIconStyle }
                    .disabled(pending == state.appIconStyle || state.appIconApplying)
                Spacer()
            }
        }.onAppear { pending = state.appIconStyle }
            .task { previews = await Task.detached(priority:.utility) { Branding.iconPreviews() }.value }
    }
}
