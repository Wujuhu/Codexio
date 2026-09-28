import SwiftUI
import AppKit

struct PricingView: View {
    @ObservedObject var state: AppState
    @State private var search = ""
    @State private var selected: String?
    @State private var editing = false
    private var selectedModel: String? { state.prices.first {$0.id == selected}?.model }
    private var filtered: [PriceRow] { state.prices.filter {(state.modelIDs.isEmpty || state.modelIDs.contains($0.model)) && (search.isEmpty || $0.model.localizedCaseInsensitiveContains(search))} }
    var body: some View {
        VStack(alignment:.leading,spacing:20) {
            HStack { StatusNote(text:L("美元 / 1M Token", "USD / 1M tokens")); Spacer(); Text(lastUpdateText(state.priceUpdated)).font(.caption).foregroundStyle(.secondary) }
            if let warning = state.priceWarning { StatusNote(text:warning) }
            AdaptiveRow(spacing:8) {
                TextField(L("搜索模型", "Search models"),text:$search).textFieldStyle(.roundedBorder)
                Button(L("编辑基础价", "Edit base price")) { editing = true }.disabled(selectedModel == nil)
                Button(L("恢复自动基础价", "Restore automatic price")) { if let model = selectedModel { state.overridePrice(model:model,rates:nil) } }.disabled(selectedModel == nil)
            }
            CompactTable(columns:[
                GridColumn(id:"model",title:L("模型", "Model"),width:180,maximum:240,alignment:.center),
                GridColumn(id:"condition",title:L("条件", "Condition"),width:138,maximum:166,alignment:.center),
                GridColumn(id:"input",title:L("输入", "Input"),width:104,maximum:122,alignment:.center),
                GridColumn(id:"cache_read",title:L("缓存读取", "Cached input"),width:104,maximum:122,alignment:.center),
                GridColumn(id:"cache_write",title:L("缓存写入", "Cache write"),width:104,maximum:122,alignment:.center),
                GridColumn(id:"output",title:L("输出", "Output"),width:104,maximum:122,alignment:.center)
            ],rows:filtered.map { row in GridRow(id:row.id,text: { column in
                if column == "model" { return GridText(main:row.model) }
                if column == "condition" { return GridText(main:(row.raw.string("service_tier") == "priority" ? "Fast" : "Standard")+((row.raw.integer("threshold") ?? 0) > 0 ? " >272K" : "")) }
                return GridText(main:money(row.raw.number(column)))
            }) },revision:state.pricesRevision+search+state.modelIDs.sorted().joined(separator:","),rowHeight:30,selection:$selected,preferences:state.preferences,storageKey:"pricing")
                .overlay(RoundedRectangle(cornerRadius:10).stroke(.secondary.opacity(0.16)))
        }.padding(.horizontal,PageLayout.inset).padding(.bottom,PageLayout.inset).sheet(isPresented:$editing) { if let model = selectedModel { PriceEditor(state:state,model:model) } }
    }
}

struct PriceEditor: View {
    @ObservedObject var state: AppState
    let model: String
    @Environment(\.dismiss) private var dismiss
    @State private var values = ["input":"","cache_read":"","cache_write":"","output":""]
    @State private var error = ""
    var body: some View {
        VStack(alignment:.leading,spacing:20) {
            Text(model).font(.title2)
            StatusNote(text:L("编辑 Standard 基础价 · 美元 / 1M Token", "Edit Standard base prices · USD / 1M tokens"))
            Form {
                field("input",L("输入", "Input")); field("cache_read",L("缓存读取", "Cached input")); field("cache_write",L("缓存写入", "Cache write")); field("output",L("输出", "Output"))
            }
            if !error.isEmpty { Text(error).font(.caption).foregroundStyle(.red) }
            HStack { Spacer(); Button(L("取消", "Cancel")) { dismiss() }; Button(L("保存", "Save")) { save() }.keyboardShortcut(.defaultAction) }
        }.padding(26).frame(width:460).onAppear {
            if let row = state.prices.first(where:{$0.model == model && $0.raw.string("service_tier") == "default" && $0.raw.integer("threshold") == 0}) { for key in Array(values.keys) { values[key] = row.raw.number(key).map {String($0)} ?? "" } }
        }
    }
    private func field(_ key: String,_ title: String) -> some View { TextField(title,text:Binding(get:{values[key] ?? ""},set:{values[key] = $0})) }
    private func save() {
        var result: Object = [:]
        for key in ["input","cache_read","cache_write","output"] {
            let text = values[key] ?? ""
            if text.isEmpty && key.hasPrefix("cache_") { result[key] = NSNull(); continue }
            guard let amount = Double(text), amount.isFinite, amount >= 0 else { error = L("请输入有限的非负单价", "Enter finite, nonnegative prices"); return }
            result[key] = amount
        }
        state.overridePrice(model:model,rates:result); dismiss()
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
            row(L("侧边栏", "Sidebar")) { Toggle("",isOn:Binding(get:{state.sidebarVisible},set:{ value in state.sidebarVisible = value; state.setPreference("sidebar_collapsed",!value) })).labelsHidden().toggleStyle(.switch) }
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
        }
    }
    private var app: some View {
        VStack(spacing:0) {
            row(L("当前版本", "Current version")+" · Codexio "+BuildInfo.version) {
                if state.updateAvailable { Button(L("退出并更新", "Quit and update")) { state.onInstallUpdate?() } }
                else { Button(L("检查更新", "Check for updates")) { state.onCheckUpdate?() }.disabled(state.paths.mock) }
            }
            if !state.updateStatus.isEmpty { StatusNote(text:state.updateStatus).padding(.vertical,8) }
            row(L("自动下载更新", "Download updates automatically")) { Toggle("",isOn:Binding(get:{state.preferences.analytics.flag("macos_auto_update",true)},set:{state.setPreference("macos_auto_update",$0)})).labelsHidden().toggleStyle(.switch) }
            row(L("上游检测", "Upstream detection")) { Toggle("",isOn:Binding(get:{state.preferences.analytics.flag("upstream_detection_enabled")},set:{desiredUpstream = $0; upstreamConfirmation = true})).labelsHidden().toggleStyle(.switch).disabled(state.paths.mock) }
            if !state.upstreamStatus.isEmpty { StatusNote(text:state.upstreamStatus).padding(.vertical,8) }
            row(L("自动同步价格", "Sync prices automatically")) { Toggle("",isOn:Binding(get:{state.preferences.analytics.flag("auto_sync_prices",true)},set:{state.setPreference("auto_sync_prices",$0)})).labelsHidden().toggleStyle(.switch) }
            row(L("额度刷新", "Limit refresh")) { Picker("",selection:Binding(get:{state.preferences.general.integer("refresh_interval_seconds") ?? 60},set:{state.setPreference("refresh_interval_seconds",$0,general:true)})) { ForEach([30,60,300],id:\.self) { Text("\($0) s").tag($0) } }.labelsHidden().frame(width:110) }
            row(L("日志刷新", "Log refresh")) { Picker("",selection:Binding(get:{state.preferences.analytics.integer("usage_refresh_interval_seconds") ?? 10},set:{state.setPreference("usage_refresh_interval_seconds",$0)})) { ForEach([5,10,30,60],id:\.self) { Text("\($0) s").tag($0) } }.labelsHidden().frame(width:110) }
            row(L("额度估算间隔", "Estimate interval")) { Picker("",selection:Binding(get:{state.preferences.analytics.integer("week_estimate_interval_minutes") ?? 30},set:{state.setPreference("week_estimate_interval_minutes",$0)})) { ForEach([10,30,60],id:\.self) { Text("\($0) min").tag($0) } }.labelsHidden().frame(width:110) }
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
