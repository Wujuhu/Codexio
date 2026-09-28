# v0.3.1 实施与交付记录

更新时间：2026-09-27。用户已确认设计审美并明确授权开发。当前完成本地开发与 Mac 开发包交付；正式推送和发布仍需开发验收后的第二次明确确认。

本次二次复查记录见 [REVIEW.md](REVIEW.md)；用户实际数据反馈后的性能、能耗和交互修复以 [PERFORMANCE.md](PERFORMANCE.md) 为准。

2026-09-28：iOS 开发前 A～G 七项 Mac 问题已完成本地中间构建，程序仍为 0.3.1，等待用户测试确认；实现、定向核对和开发包信息见 [PRE_IOS_FIXES.md](PRE_IOS_FIXES.md)。iOS 尚未开始，未推送或发布。

## 完成清单

- [x] Mac 运行端全量 Swift：原生设置、SQLite 兼容、只读增量扫描、请求归组、定价、本机统计和周期估值。
- [x] Swift App Server、逐张重置、官方套餐周期与聊天额度明细。
- [x] SwiftUI 六页、旧版导航图标、宽布局、较小额度数字、日志末列悬停详情。
- [x] Mac／Windows 中英文系统语言、系统字体；Widget 固定标签本地化。
- [x] 原生菜单栏图标加额度、Codex 组标题、Liquid Glass 与系统兼容材质。
- [x] 新增独立最小额度 Widget 的三种样式；旧请求小／中／大组件保留几何、颜色、字体层级和 kind。
- [x] 主进程拥有的刷新任务、唯一稳定宿主、Widget 注册、ZIP 更新与失败回滚；⌘Q 停止自有服务。
- [x] 原生上游转发及配置恢复，保留 provider 与鉴权配置。
- [x] 两端删除 SSH 采集、来源管理／筛选／列与历史归属入口；保留原始索引历史。
- [x] Windows 逐张重置、本机五项统计、活动图切换、服务端明细和双语文案。
- [x] 适配新版 ChatGPT/Codex 的 `codex-cli` 包路径，保留旧版路径、手动路径和独立 CLI。
- [x] Swift 编译、固定三项模拟冒烟、版本／签名／架构／ZIP／哈希核验、Mac 开发打包。
- [x] 更新开发说明与数据能力记录，并提交到本地 Git。

## 本轮具体交付

| 范围 | 实现 |
| --- | --- |
| Mac 主程序 | `macos/native/` 的 Swift 源码，SwiftUI/AppKit 直接运行，应用包不嵌入 Python、Qt 或 WebView |
| 界面 | 概览、日志、用量、订阅、定价、设置；日期／粒度／模型筛选；可复制的日志详情浮层与调用组成 |
| 本机统计 | 图 5 的五项指标、365 天热力图、Fast 与推理强度全部取本机已确认调用，时长仅含本机可靠运行区间 |
| 官方明细 | 图 6 按原周期边界与基点显示；图 7 用周限额和实际 Credits，展开模型、推理、速度构成 |
| 额度重置 | 完整列出逐张截止时间，用户选择后确认，固定 credit ID 和持久幂等键，超时保留重试入口 |
| 双语 | Mac String Catalog 与编译资源、Windows 集中文案；中文／英文按系统语言选择 |
| 小组件 | 请求三种尺寸保持原样，三个额度样式独立 kind；Widget 构建 16、短版本 1.15 |
| 参考资料 | 16 张参考图已在仓库本地，31 份静态 HTML 评审稿继续可查 |
| 文档 | README、Mac 开发说明、开发步骤和接口能力记录已更新到本次实现 |

### 新版客户端位置修复

本机安装为 `ChatGPT.app 26.924.22138`，其 Codex 包版本为 `0.158.0-alpha.2.1`。旧 `Contents/Resources/codex` 路径已消失。

新发现逻辑读取 `codex-package.json` 的受限相对入口，当前解析为：

```text
/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex
```

官方入口再执行 `codex-cli/CodexCLI.app/Contents/MacOS/codex`。同时兼容直接 CLI 子包、旧 App 布局、失效的旧手动路径与常见独立安装位置。仅探测 CLI，不启动桌面客户端。

只读连接确认已成功完成 `initialize`、`account/read` 和 `account/rateLimits/read`，能获得 ChatGPT 账户类型与窗口字段。能力记录：`build/checks/codex-discovery.json`。

## 验证结果与范围

1. **Mac 开发构建**：`./build_macos.sh` 生成并验证 ARM64 原生 APP、APP ZIP 和合并清单。
2. **Mac 固定三项冒烟**：程序启动、基本数据显示、主窗口关闭与重开；使用隔离模拟数据，结果 `ok: true`，运行时 `SwiftUI/AppKit`。
3. **Windows 共用 Qt 界面**：复用原有三项冒烟入口，在本机 offscreen 环境通过；此结果验证共用界面启动流程，不等同于 Windows 操作系统实机或 EXE 验证。
4. **交付校验**：APP 版本 0.3.1，Widget 构建号 16／短版本 1.15；原生架构、完整签名、ZIP 解压、清单大小与 SHA-256 通过构建校验。
5. **只读接口能力**：当前账户套餐历史返回周期和模型分组；聊天接口返回分组，样本状态为 `partial`，界面显示部分数据。
6. **源码检查**：Python 语法检查及 Git 空白检查通过。旧请求 Widget 的改动限于固定标签本地化、共用快照和新增独立 Widget 注册，原布局保持。

本轮没有执行真实“使用重置”，没有修改真实 Codex 配置，没有启动或替换用户已安装的 Codexio。安装、更新、真实重置与上游路由的生产动作保留到用户实际使用对应功能时执行。开发包的模拟入口隔离真实宿主接管、LaunchAgent 和注册操作。

按照用户要求没有新增测试套件、额外测试项、UI 遍历或截图矩阵。仅沿用三项基础冒烟，并在发现实际问题时做对应的必要检查。

## 开发产物

```text
build/dev/macos/Codexio.app
build/dev/macos/Codexio.app.zip
build/dev/macos/latest.json
```

校验结果分别在 `build/checks/macos-smoke/result.json`、`build/checks/qt-smoke/result.json`、`build/checks/codex-discovery.json`、`build/checks/account-report-capabilities.json`；构建日志在 `build/logs/build-macos-0.3.1.log`。

开发清单只更新 `macos` 对象，顶层 Windows 字段仍为原有版本。正式发布时 Windows CI 才生成同版本 EXE 并替换为最终合并清单；当前开发清单不是已经发布的 0.3.1 三平台附件证明。

## 后续关口

- 用户自行启动开发 APP，验收本机真实数据与界面；开发过程不代用户启动安装版。
- 用户第二次明确确认发布 v0.3.1 后，按 AGENTS.md 执行发布协调脚本，由 Windows x64 CI 构建正式 EXE。
- Windows 构建、同版本、签名／归档或哈希任一校验失败时保持草稿。
- 当前未推送、未创建 Tag／Release、未写入正式 `release/0.3.1` 目录。

本轮生命周期与猫形 C 品牌修改见 [LIFECYCLE_AND_BRANDING.md](LIFECYCLE_AND_BRANDING.md)。

## 2026-09-27 界面验收反馈修复

- 概览／趋势的四项指标统一圆角卡片；5 小时／周额度使用同一外观。文案为“费用、总 Token、用户请求、命中率”。概览日期靠右；日志及趋势筛选靠左。最近请求复用原生可调宽表格，显示请求／时间、模型、总 Token、费用、耗时和详情。
- 日志取消整行选择色，所有列居中，主副行间距收紧。复用 `CompactTable` 的原生列宽和有界可见行更新；Windows 共用界面同步简化文案与取消选择色。
- 模型延迟：参照 `v0.2.10:src/codexio/usage_collector.py` 的 `_turn_activate`，Swift 在任务开始及上下文到达时保存模型等元数据；请求尚无调用时汇总保留任务元数据。无需提高采集频率。
- 上游展示：参照 `src/codexio/upstream_store.py:enrich_rows`，保留相同模型和多模型观测。只读核查的本机样本：2592 条调用，19 条观测，17 条匹配，17 条请求／返回模型均相同；旧 Swift 展示过滤相同模型正是看不到箭头的原因。未为核查修改上游设置或重启客户端。
- 请求预览复用旧 `_user_preview` 的 My request 提取口径；对缓存中的附件包装同样处理。正文已截断的历史记录标为“附件消息”，不重新扫描全部文件，也不改变计费用量。
- 图表浮窗复用 `DetailsLink` 的 AppKit NSPopover；折线图和热图使用同一个悬停实现。Token、费用和请求数预先聚合，hover 仅定位桶。每日／每周／累计模式相应累加，移开关闭，移除热图下方文本及用户点名的解释性灰字；订阅未提供价格／续费日期时省略空字段，编辑入口保留。
- 菜单栏运行状态不再带文字，不再用中点分隔；动画移至居中子图层，维持 8 步／1.6 秒及减少动态效果支持。Dock 和 ICNS 使用一致的 82% 图标板占比，透明边距正常化；SVG 原参考中心不变。
- 本轮保持程序 0.3.1 和 Widget 构建 18／短版本 1.17；没有修改 Widget UI、注册和生命周期。本轮交付仍仅写 `build/dev/macos`，不发布。

本次验证：最终 Mac 开发构建通过，包含固定三项隔离冒烟、签名、版本和 ZIP／清单校验；Windows 共用 Qt 界面在本机 offscreen 环境通过同一三项冒烟（不等同于 Windows 实机／EXE 验证）。只查看一张概览模拟预览 `build/checks/v0.3.1-ui-refinement/overview-zh.png`，未建立截图矩阵。构建日志为 `build/logs/ui-refinement-build.log`。真实菜单栏动画、图表鼠标交互和 Dock 安装表现尚未在用户安装版上操作验证；开发过程没有启动、停止或替换用户的安装版 App。

## 2026-09-27 请求分组与最近请求修正

- 按 `v0.2.10:src/codexio/user_requests.py` 的 `iter_user_requests` 恢复按 session／turn 分组。取消“摘要必须非空”的门槛，只有归属标识缺失时才保留未归属调用；汇总前按调用 ID 去重，不建立第二份计费账本。
- 请求别名、完整／短形式 continuation 标识及明确 root_turn_id 用同一解析规则；有标识但任务元数据缺失时仍保留一个组。没有明确跨轮次关系的自动恢复记录保持独立任务组，不按时间或相同文本猜测父请求。
- 参照 Python `_turn_activate` 和 `_turn_before`：用户消息先到时创建临时请求，正式标识到达后迁移并记别名；按消息 ID／完整文本指纹及不同表示去重。空环境注入不产生新请求；两次独立发送的相同文本不因文本相同合并。
- 本次已存历史数据的空摘要分组直接由汇总修正，无需重扫全部日志；缺失正文使用“任务记录”，不再把聊天的旧附件标题当作该任务正文。页脚分别显示请求数和真正未归属调用数。
- 最近请求最多 3 条，原生表格使用实际行数高度、无纵向滚动条，纵向滚轮交外层页面。猫形菜单 Logo 仍为 18 点，任务状态框 23 点（勾圈可见高度约 18 点），周额度字体 16 点；预览与实际菜单栏共用组件。

本轮验证：只读提取用户截图对应的 20:18:56～20:25:28 历史调用，用实际 Swift 汇总实现隔离核对，24 条调用 → 1 个组，组内 24 个唯一成员，未归属数 0，Token 总量一致；未修改用户数据库。Mac 最终开发包完成固定三项模拟冒烟、版本／签名／ZIP／清单校验；仅查看 `build/checks/request-grouping/settings-menubar-zh.png` 一张相关预览。构建日志 `build/logs/request-grouping-build.log`。保持 0.3.1，Widget 18／1.17 不变，未启动安装版或执行远程发布。

## 2026-09-27 日志上游样式与菜单栏微调

- 猫形菜单 Logo 保持 18 点；状态图标从 23 调至 21 点，周额度字从 16 调至 14 点，实际菜单栏和设置预览共用。
- 日志请求副行改为“时间 · High/Max 等英文强度 · Fast/Standard · 上下文”，强度可选列与详情也固定英文。Windows 日志复用既有英文 `display_effort`，同步副行字段顺序。
- Swift 模型单元格直接参考 `src/codexio/desktop_widgets.py:LedgerDelegate.paint`：上游小字在上、请求模型在下，右侧折线向上连接。差异色按每个调用实际的请求／返回模型比较后汇总，不把多模型字符串的顺序差异误标绿。
- 历史上游仍由 UsageSnapshotCache 按 response_id 读取本地 observations，未引入检测开关的显示过滤；UpstreamCoordinator 关闭时只恢复路由，不删除 observations。

本轮验证：Mac 开发打包、固定三项隔离冒烟与版本／签名／ZIP／清单校验通过；Python 改动通过语法检查，未做 Windows 实机验证。未新增测试项、截图矩阵或运行真实安装版。日志 `build/logs/upstream-layout-build.log`，开发产物仍为 `build/dev/macos`，版本 0.3.1，Widget 18／1.17 不变。

## 2026-09-27 排行标题、周期时区和菜单对齐

- 标题来源修复参照 Python `usage_collector.py` 的索引名称优先、数据库初始标题回退机制，并兼容新版 threads.name。只读核对本机四条聊天：name／session_index 分别为 v0.3.1、v0.2.10、v0.2.9、v0.2.8；旧 Swift 随后用 threads.title 的首条提示覆盖了有效名称。现改为 name → 索引显示名 → 非包装的 title；保留文件／WAL 变化检测，无变化不重读。
- 排行聊天标题和表头左对齐，首屏 5 条、“显示更多”分批增至单页最多 25 条，保持后续分页。已用 Credits 标题与参考统一为“已用额度”，计量数据不变。
- 套餐周期原先使用设备时区。参考图官方 2026-09-26 17:08 UTC 等于上海 9/27 01:08；原本显示的是同一时刻但日期口径不同。周期日期、完整时间悬停与统计截至统一 UTC，复用线程本地日期格式器；日志及本机活动日期保留本地时区。
- 菜单栏保留 18／21 点图标和 14 点周额度字体。共用视觉中心对齐引导：图标参考已有猫形 SVG 的视觉偏移（18 点下约 -0.65 点），各文字字段按原生字体 capHeight 与基线对齐，其他费用／Token 字段也使用同一机制；不改变 SVG 或增加定时刷新。

本轮验证：Mac 最终开发构建的三项隔离冒烟、版本／签名／ZIP／清单核验通过；只查看一张菜单栏设置模拟预览 `build/checks/ranking-time-alignment/settings-menubar-zh.png`。聊天名称问题通过只读本机索引／SQLite 字段确认，未修改真实 Codex 数据或启动用户安装版；时区显示按用户官方 UTC 参考修正。构建日志 `build/logs/ranking-time-alignment-build.log`，版本保持 0.3.1，Widget 18／1.17 不变。

## 发布后文案修正（用户明确确认替换 v0.3.1）

- 请求小／中／大组件的思考强度统一英文，快照复用日志的英文格式化；Widget 同时兼容转换旧中文快照，布局保持原样。
- 菜单栏今日／当前任务的费用、Token 均只显示数值；设置中的字段名称和无障碍说明仍保留含义，显示顺序及口径不变。
- Widget UI 文案变化，构建号从 18 递增至 19，短版本 1.18；程序保持 0.3.1；用户已明确确认替换已发布 v0.3.1。发布前将原三个附件及发布信息保存在 build/backups，再由协调脚本和 Windows CI 完成重发。

本轮 Mac 开发包固定三项隔离冒烟、签名、ZIP、版本和清单校验通过，Widget 构建 19／短版本 1.18；不启动用户安装版。构建日志 build/logs/widget-effort-menu-values-build.log。
