# Claude 订阅、定价与本地 beta 版本

版本：0.3.10-beta。用户于 2026-10-16 明确授权实施 Mac／Windows，并指定本地测试版本；未授权推送或发布。此前 0.3.9 Claude 首轮规格的订阅、计价排除项由本规格取代。

## 已确认范围

- Claude 额度采用官方 Mods 优先、statusLine 兼容，默认静默接入；保留已有配置与状态栏输出。Desktop 本地 Code 页使用自带引擎，无需另装 CLI。普通 Chat／Cowork 不宣称已接入。
- 订阅页复用 Codex 布局：套餐与邮箱、5 小时与周额度、周额度估算；不显示套餐用量历史和主动重置区域。缺失值为 `—`。
- 定价页显示官方 Claude API Standard 基础价格及条件，分开处理 Claude Code 的规则。复用既有 model、upstream_model／returned_model、upstream_models；不增加日志模型标识字段。label 仅帮助建立价格目录别名关系，不能用当前配置改写历史请求事实。
- 当前 Claude Code Fast 的受支持 Opus 模型按实际 Fast 价格计价，订阅账号 Fast 使用 usage credits，不纳入订阅周额度估算。不添加未经官方证实的订阅长上下文倍率；缓存读取、5 分钟／1 小时写入分别计算。
- Claude 活动热图与 Codex 一致补齐最近 365 天，保留缺失计量与无活动的区别。费用贯通概览、日志、详情、趋势及聊天排行。
- 两个来源的日志均采用 Claude 紧凑布局，去掉速度筛选并消除其残留过滤。日期、模式、模型、状态、搜索、分页、显示字段及列宽各自独立。
- 来源切换扩展到订阅和定价；未特别指定的页面行为与 Codex 一致。iOS 本轮仅处理版本兼容，不新增 Claude 页面。
- 本地 beta 遵循 SemVer，beta 低于同号正式版；正式更新继续读取稳定通道，正式发布入口继续拒绝预发布版本。Apple 系统 Bundle 版本保留数字格式，完整语义版本另存应用元数据。

## 参考与复用

- 当前 Pricing.swift／pricing.go 的内容价格版本、有界派生缓存；ClaudeLedger.swift／claude_queries.go 的增量账本和分组。
- 当前 QuotaCard／Quota.svelte、WeeklyEstimator／RollingEstimator、CompactTable 和筛选控件。
- Claude 官方 Mods `$.session.usage()`、`session.measure`；公开示例 jack21/claude-mod-usage、marmottajr/habblaud。桥接只传额度及必要来源信息，不读或转存令牌，不发送模型请求。
- Claude Code 官方 fast-mode、model-config 与 Claude Platform pricing／prompt-caching 文档，2026-10-16 核对。
- Apple Foundation Bundle.executableArchitectures；既有本地提交 0f0a71f 已去掉更新器运行时的 lipo 依赖。

## 交付

只运行现有三项隔离冒烟及必要的编译、签名、版本、归档和哈希检查，不新增测试套件。Mac／iOS 本机构建，Windows 经 SSH 在指定主机交互会话构建并保留产物。开发文件在 build，最终提交本地 Git。

## 实现边界与核验

- 价格以调用的既有返回模型为首选，原请求模型或已确认的标签别名为回退。缓存写入缺少 5m／1h 拆分、缺少速度或仅能回退模型时保留估算标识；未识别型号保留 `—`。标签改变或移除时，从新观测时间开始新映射，旧记录保留之前的映射；价格目录的服务倍率始终使用当前代码规则，不被旧价格缓存覆盖。聚合先逐次调用计算，再汇总到请求／日期／线程。
- Mods 桥接经过本机 Claude Code 2.1.301 的 `plugin validate` 校验，只注册 session.start、session.measure、turn.complete；只读取已经观测到的额度，经带随机凭据的 loopback 连接发送给主 App。主 App 退出关闭监听并移除运行地址，旧 CLI 包装保留原始 statusLine 输出。
- 桥接在 Claude 下一次加载插件时生效。仅普通 Desktop Chat／Cowork、WSL、组织禁止插件以及上游尚未报告限额的场景不伪造读数。公开额度结构没有邮箱和套餐；CLI 可以读取本地非令牌账号元数据，Desktop 无可确认身份时套餐／邮箱保留 `—`，不借用 CLI 身份；有效额度仍按本机来源和周周期进入固定窗口估算。
- 日历固定展示最近 365 天；历史单日峰值与最长连续天数继续计算全部已采集历史，跨年同周的费用正常累加。
- Mac／Windows 使用既有三个隔离冒烟；iOS 仅更新完整版本读取与语义比较并跟随桌面版本。未对真实订阅账号发送额度请求或消费模型额度进行实测。
- 官方参考：[Mods 及支持范围](https://code.claude.com/docs/en/plugins/mods/overview)、[公开事件与 API](https://code.claude.com/docs/en/plugins/mods/reference)、[statusLine 字段](https://code.claude.com/docs/en/statusline)、[Claude Code Fast](https://code.claude.com/docs/en/fast-mode)、[Claude Code 长上下文](https://code.claude.com/docs/en/model-config)、[Standard API 价格](https://platform.claude.com/docs/en/about-claude/pricing)。

## 2026-10-16 固定观察窗口与日志修正

- 用户再次确认：Claude 不增加 OAuth／Cookie／携带登录凭据的接口查询。Desktop-only 不再以邮箱、套餐或 account_verified 作为估算前提；未知身份使用本机配置目录及客户端来源的内部作用域，已知账号、来源或周重置周期变化时重新建立基准。
- Codex／Claude 默认结算间隔改为 10 分钟，保留 30／60 分钟选项。旧的 30 分钟默认迁移到 10 分钟，已经选用 10／60 分钟的配置保留。
- 每条新估算覆盖一个接近指定时长的连续观察区间，不要求与整点或采样时刻精确对齐。实际样本跨过目标终点后，从其前后两条读数中选择更近的一条；时长容差为一次额度采集间隔（Codex 跟随 30／60／300 秒设置，Claude 为 60 秒），不在刚进入容差范围时提前结算。记录保留真实采样起止时间、实际秒数与本次容差，消费查询使用同一真实区间。例如首条额度为 10:00:20，则 10:10:20 可以作为 10 分钟窗口终点，附近几十秒的偏移同样可用。缺少连续样本、明确采集错误、退出、休眠、时钟异常或采集间隔改变时，未完成窗口作废，恢复后使用新样本重建基准，不连接中断前后的读数；未来时间戳和时钟异常校验不随窗口容差放宽。
- 完整窗口无消费、额度比例未变化或消费数据不可计价时不生成结果，基准仍向前推进，不累计成跨越多个周期的估算。完整窗口保留原有 120 秒日志成熟等待，这只延迟结算，不改变观察时段。旧估算历史保留原始起止时间，不改写成新窗口。
- Mods 在主 App 可接收时按分钟边界调用无网络的 $.session.usage()，通过既有 loopback 接收器传递运行中 Claude Code 已观测的状态；本地接收器停止或不可达后定时读取结束。不发送模型请求或账号接口请求。Mods 事件及 statusLine 的新鲜回传也可作为窗口端点，不限于分钟定时样本；样本不足时跳过估算，不用 App 中缓存的读数伪造新采样。
- Windows 复用原日志工作循环的维护回调，在无账本变化时也能处理到期结算；不会为计时发布全局用量状态。Windows 用 QueryUnbiasedInterruptTime 检测休眠，Mac 使用系统休眠通知与运行时钟。
- 两种来源的日志首次默认及恢复默认字段均隐藏命中率，仍允许手动选择，来源间的字段和列宽继续独立。旧默认字段集合迁移，其他自定义集合保留。
- 请求副行及上下文列统一显示 model_context_window 容量。Mods 的 context.window 和 statusLine 的 context_window.context_window_size 通过明确的 transcript UUID／prompt ID 关联，最多保留 256 条待关联提示，新增的上下文索引在后台采集队列按需构建，不按时间相邻猜测，不把当前会话容量回填到历史记录，不新增模型标识字段。历史缺少容量显示 —。
- 参考：既有 WeeklyEstimator.swift／rolling.go 的账号及周期校验、120 秒成熟等待和本地消费查询；Claude Code 2.1.301 Mods 类型定义的 session.append UUID、clock.after 和 session.usage；[Windows 非休眠计时 API](https://learn.microsoft.com/en-us/windows/win32/api/realtimeapiset/nf-realtimeapiset-queryunbiasedinterrupttime)。验证范围仍为原有三项隔离冒烟、插件静态校验及交付校验，不新增测试套件。

## 2026-10-16 Mac 小组件与菜单栏双来源

- 用户确认修正后的本地原型 `build/prototypes/agent-widgets-menu-20261016/index.html`，授权本轮实现 Mac 小组件及菜单栏。桌面版本保持 0.3.10-beta；Widget 构建号由 26 增至 27。本轮不修改 Windows／iOS，不推送、不发布。
- 原有 ChatGPT 请求 kind 保留小／中／大尺寸，另保留三种额度 kind；Claude 增加对应四个独立 kind，合计十二种尺寸／样式展示。请求名称带 ChatGPT／Claude 后缀；额度名称为 Codex／Claude 单额度、双额度、刻度额度，无重复后缀。旧组件身份和旧版根快照保持兼容。
- 请求组件保留原布局，模型行右侧的 Agent 名称与模型信息共用字号和半粗字重，小尺寸使用仓库既有 Logo。Claude 显示返回模型的目录名称并去掉 Claude 前缀，未知型号保留原始标识；仅转换展示副本，不新增或改写账本模型字段。
- 复用当前 `ClaudeLedger.swift` 的明确请求归属、调用聚合和七日 SQL 桶；主 App 在已有后台队列准备一个请求、今天汇总及七日数据，不把整份 Claude 历史搬入 UI 快照。最近开始的有效运行主请求优先，结束后选择其他运行主请求，没有运行任务时选择最近完成者。
- Claude 的本地用户／助手／工具进度及明确完成、失败事件仅更新有界运行观测表（最多 256 项）；不建立独立采集服务、不请求账号接口。索引及最多 64 个新鲜未结束请求的升级恢复在后台按需准备，不重读历史日志、不重采 Token。运行状态沿用 15 分钟新鲜度；子代理不单独占一张请求卡片。
- Claude 统计依赖账本 revision、来源目录、价格实际物化结果、本机日期／时区和未来记录边界；纯运行观测依赖独立 revision 与到期边界，不使账本、用量页或价格缓存失效。无展示内容变化不发布 surface 状态；Widget 快照复用内容签名和五分钟保活，两个来源只由同一主 App 写入，退出统一离线。
- 菜单栏沿用当前 `MenuBar.swift` 的 420 点面板及两张用量卡片，上方左右显示 ChatGPT、Claude 的额度。选择保存在 `menu_bar_agent`，独立于主窗口来源切换；下方汇总、趋势与已有菜单栏字段使用所选来源，点击不访问数据库、不发起采集。复用 `MenuBarReadout` 供实际状态项与设置预览。
- 最小三项隔离冒烟保持不变。沿用已有 MockGallery 的单张按需原生排版预览，模拟窗口屏外运行且无 Dock／菜单栏条目；不增加测试文件、测试项或界面遍历。交付继续核验版本、Widget 构建、签名和 ZIP。

### 2026-10-17 后续范围

Mac／Windows 的 Claude 用量活动页接入执行行程，沿用既有 UI 和分页，按选中请求及明确 UUID 关系读取经校验的本地来源。iOS 首页增加两方 Logo，局域网及云同步均支持两组独立数据；通过 `agent-sources-v1` 与独立数据集保持旧端兼容。版本继续为 0.3.10-beta，本地开发包不作为正式 Release。

实现复用：执行行程沿用当前 `JourneyView.swift`／`JourneyReader.swift` 与 Windows `journey.go`、`Journey.svelte`，Claude 使用既有账本 UUID 归属、来源身份及偏移校验；手机投影沿用 `MobileSync.swift`、`MobileProtocol.swift` 和 `mobile_projection.go` 的独立 revision／digest、请求详情与图片缓存。Windows 复用 `usage_revisions` 为两方历史分别失效；额度变化只更新 live，跨日、时间回退及未来记录到期仍刷新对应投影。iOS 两方分别保留搜索条件，切换清除上一来源的详情导航及图表悬停选择。

交付记录：Mac 与 Windows 的原有三项隔离冒烟通过；Mac 的 APP／ZIP、签名、0.3.10-beta 版本及 Widget 1.28／29 已核验，Windows 239 个构建输入与本机逐项核对，新 EXE 和清单保留在该主机 `build/dev/windows/pending`（原产物正在使用），本机复制到标准开发目录。iOS arm64 未签名 IPA 完成构建、归档与源码指纹校验，尚未完成 iPhone 真机验证。云端 Worker 已部署，100% 流量版本为 `b0589e0e-3143-4775-a9ec-75d18bd5589a`，回读模块与本地 bundle 的 SHA-256 一致，绑定／保留期／清理计划未改变；没有新增生产模拟数据，也没有 Git 推送或 GitHub Release。完整记录位于 `build/checks/claude-journey-ios-v0310-beta`。

### 2026-10-17：Windows Claude 持续更新

用户反馈日志、任务及 Token 未更新。排查确认 Windows 主请求默认状态为 unknown，缺少 Mac `ClaudeLedger.swift` 已有的主请求活动跟踪。现将该机制适配到 `claude_live.go`，由现有采集循环维护最多 256 条有明确归属的主请求活动；开始、完成、失败及过期／时间回退引起可见状态变化时才失效对应请求分组，普通进度只续期，不重新计量或计价。Windows 的筛选条件补上 running，布局不变，未完成请求的缺失耗时仍为 `—`。

针对本次故障，使用现有 Store／SQLite 汇总在 Windows build 隔离目录读取一份实际日志的冻结副本：先保留回复前前缀，再在同一采集进程中追加真实后缀。前缀形成一条 running 用户请求、零模型调用；追加后形成三条 completed 用户请求、六次模型调用及 232,569 Token，generation 从 4 变为 5。该目录没有接入真实 Codex 目录，所以全局来源诊断包含 Codex 不可用；Claude 来源始终 ready。真实客户端、配置和账本均未改写，没有增加维护性测试套件。Windows 开发包沿用既有三项隔离冒烟，版本保持 0.3.10-beta。诊断和交付证据位于 `build/checks/windows-claude-data-fix`。
