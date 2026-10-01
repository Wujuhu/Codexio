# Windows v0.3.4 开发交付

2026-09-30 按用户确认，以 Wails + Go 完成 Windows 重构。新 EXE 使用 Go 后台、Svelte/TypeScript 和系统 WebView2，不捆绑 Python/PySide。Wails 固定为 `v3.0.0-beta.26`，Go 为 1.26.1，SQLite 使用纯 Go 驱动；开发使用已有 Go/Node，不安装 Visual Studio、.NET 或 C++ SDK。

本次仅交付 Windows 本地开发包。Mac/iOS 源码与版本不变，统一清单保留另一平台字段。没有推送、Tag、GitHub Release、Windows CI 或云端部署。

## 自动审批审查

- 保留 `source.subagent.other=guardian`、父聊天、父轮次与原始来源；明确的 `codex-auto-review` 计量可作分类回退，正文和标题不能单独触发分类。
- 有明确父轮次、显式调用关系或可验证的唯一继承关系时并入主请求。仅父聊天、时间接近、摘要相同不构成合并依据。
- 无法关联时单列“自动审批审查”。两种情况均不新增用户请求数，也不替换最近用户任务、用户正文或最终回复。
- 保留真实 Token 和可定价费用；未知价格仍为未知，含未知调用的费用保留部分定价状态。概览、日志、趋势、模型、报告及手机摘要共用请求分类。

针对问题的一次性隔离核对得到：250 Token、1 个真实用户请求、2 个展示组、2 个未定价审查调用。显式父轮次审查归入主请求，仅父聊天的审查独立展示；复制响应和现代／旧版重复计量没有重复累加。

## 功能承接

| 功能 | v0.3.4 行为 |
| --- | --- |
| 概览 | 额度、费用／Token／用户请求／命中率、趋势、最多 3 条最近主请求；Token 组成按普通输入、缓存读取、缓存写入和输出拆分，缓存不重复加入输入 |
| 日志 | 请求／调用模式、时间／模型／速度／状态／搜索筛选，有界分页、列宽、详情浮层，来源原文、最终回复、附件与调用组成；历史上游观察独立于采集开关 |
| 用量 | 已计算图表与悬停、365 天本机热图、模式占比和活动指标、模型贡献、真实聊天名称及首屏 5 条排行 |
| 订阅 | 当前账户额度、失效状态、选择重置券后的确认和幂等重试、官方周期／模型／聊天报告、账户范围内成熟观察区间估值 |
| 定价 | 原有已核对 Standard 基础价、Codex 倍率、基础价覆盖和价格档案；关闭自动同步时不访问价格源，相同内容不重算 |
| 报告 | 上个完整自然日／周／月、三套猫咪样式、每天 08:00 后首次打开的展示标记、整张 PNG 导出；空周期零值与未知计量分开 |
| 桌面 | 主题、系统语言、字标与图标选择、托盘、悬浮窗及贴边停靠、窗口偏好；关闭主窗口后可重建，退出停止应用自有服务 |
| 上游检测 | 默认关闭，选中 provider／profile 的地址接管、HTTP/SSE/WebSocket 观察、受保护恢复及 v2 日志迁移；停止时关闭自有升级连接 |
| 手机同步 | 原有 TLS 后的原始 RFC6455 帧、Bonjour、二维码、DPAPI 身份、显式配对／拒绝／撤销、最多 3 台、有界详情和既有只读云端协议 |
| 自动更新 | 固定仓库检查、明确安装／稍后、下载进度、版本／大小／哈希／EXE 校验、正常退出后原位替换、真实界面就绪确认和失败回滚 |

## 数据和运行机制

复用当前 Python 以及 `v0.2.10` 的账本、变化检测、游标、物化索引和分页思路，参考实现保留在 `src/codexio`。Go 的原始 SQLite 表及记录身份延续既有格式，价格独立于原始计量；新增派生投影可重建。

- 目录／文件／WAL 变化才触发相关处理；未变化日志不重新解析，追加读取有截断／替换／删除和有界重新发现机制。
- 多个日志根目录使用完整来源与目录前缀管理游标，避免其他目录被清理后反复从头解析。
- 后台执行文件、网络、数据库和历史聚合；前端接收分页及展示快照，过期请求不能覆盖新筛选。缓存有容量、generation、价格、日期和时区依赖。
- 估值内容真正保存或重新计价后只通知订阅页；额度和重置券使用统一过期投影。数据没变化不会因普通采集轮询重新加载整个界面。
- 自建 app-server／探测程序在 Windows 上先挂起，加入 Kill-on-close Job Object 后再运行，关闭只回收本应用创建的进程树。
- 正常配置恢复仅改回接管字段；旧 v2 provider 必须完整匹配自有内容。并发修改或无效恢复记录不覆盖用户文件。

## 构建与核验

```powershell
.\build_exe.ps1 -Version 0.3.4
.\run.ps1 --mock
```

编译源快照、资源和前端输出在 `build/staging`；Go/npm 缓存在 `build/cache`；核对结果在 `build/checks/v0.3.4`。构建版本由 `windows/VERSION` 管理，构建命令不自行改版本。交付路径为：

```text
build/dev/windows/Codexio.exe
build/dev/windows/latest.json
```

前端最终检查为 0 errors / 0 warnings，Go/Wails 生产编译通过。修正启动显示时序后，原有三项隔离模拟冒烟全部通过：程序启动、基本数据显示、主窗口关闭与重开。UI 只查看了一张当前模拟截图；没有维护性测试套件、全量遍历或截图矩阵。版本、源码指纹、文件大小与 SHA-256 一致，EXE 未签名。

代码审查提出的数据和生命周期问题均已修正。真实账户报告、重置操作、手机配对、防火墙、客户端重启、真实原位更新／回滚、跨屏停靠和 Linux 桌面没有在本次开发环境执行验证。没有同条件的完整进程内存／CPU／能耗测量，不能据此宣称全部卡顿消失或具体节能倍数。

使用开发 EXE 前由用户自行退出旧版并手动打开新版，避免两个版本同时使用同一账本。开发过程没有启动、结束或重启用户安装的真实程序。

## 2026-09-30 Mac v0.3.3 界面对齐

依据用户十张截图反馈，Windows 0.3.4 本轮恢复 Mac 的平面主画布、48pt 工具条、32pt 留白、28pt 标题、紧凑导航和 18pt 圆角卡片；字体按用户选择使用本机已安装的 Noto Sans SC，报告的宋体层级使用 Noto Serif SC。Windows 原生标题栏与系统字体渲染存在平台差异，未宣称逐像素一致。

- 修正刷新弧线、单点趋势及低费用尺度；费用和 Token 独立缩放，未知计量断线。
- 日志按 Mac 分成请求、模型、输入、输出、命中率、费用、耗时、末列详情；列宽按列 key 持久化，兼容旧设置。摘要缩短，副行只显示 fast／ultrafast 特殊速度。详情为 360×500 玻璃浮层，全文左对齐，真实调用按需展开。
- 用量恢复活动、趋势、聊天排行三个页签；年度活动独立于趋势筛选，热图按星期一对齐及月份标记，悬停使用预计算数据的固定玻璃浮层。全部本机聊天按 Token 排序，首屏五条、最多 25 条一页。
- 重置券保留标题和真实截止时间，取消服务描述和倒计时。确认与所选账户绑定，账户变化或过期会清除选择，后台再次检查同一账户及幂等操作。
- 定价展示限定为用户给出的八个 Codex 模型，并按给定顺序排列；底层历史价格与计价规则保留。
- 打开报告目录改为官方 Windows ShellExecute 路径动作；报告照 Mac 的左侧纸张／右侧紧凑操作列、原猫耳路径、500px 画布和缩放公式显示，完整 2x PNG 导出保留不完整计量标记。

合并前端检查为 0 errors / 0 warnings；Go/Wails 生产构建及原有三项隔离模拟冒烟通过。只查看一张本轮模拟界面截图。代码审查提出的局部缺失值、账户、字体及筛选问题均已修正；没有执行真实重置或启动／重启用户安装的应用。
# Mac v0.3.4 migration follow-up

Reference source is remote `main` at `2c944cc`, including `fbb33f0` request-accounting and Mac/iOS synchronization changes. Those upstream commits were imported locally; the Windows follow-up keeps version 0.3.4 and does not modify the Mac/iOS implementation.

Shared UI dimensions are compacted together, without page zoom: sidebar resize/collapse and direct long-press ordering follow `MainViews.swift` and `Interaction.swift`; log field selection/widths follow `CompactTable.swift`, with the user's screenshot defaults. Quota/usage cards retain the reference structure with smaller padding, values and gaps. Rolling 7/30-day comparisons use the user's concise “较上周/较上月” labels; the exact preceding dates are available on hover, and incomplete values do not produce a percentage. Report numbers use actual font glyph widths and the Mac 0.65 minimum scale instead of character-count estimates.

The request collector and detail recovery follow current `UsageIndexer.swift`, `Database.swift`, `RequestResume` and `RequestClassification`: explicit continuations and duplicate message identities are preserved; true user requests exclude context-only and automatic-approval records, while their observed usage remains in accounting. Metadata recovery is bounded and does not replay unchanged meter records. Desktop and mobile share the same source-message state machine.

`CodexClient.swift`'s two WHAM endpoints supply the default chat allowance ranking and weekly plan history. Local Token ranking remains an explicit alternate view. Ranking transfers are bounded to 25 rows, initially showing five, with model/reasoning/speed breakdowns and UTC report times.

The iPhone INVALID bug was the approved-reader pair retry: iOS continues `pair` until its first accepted `sync`. The Windows protocol now follows Mac's transition, request-kind capability, force-first detail chunk, business-content cache dependencies and cloud fallback projections. A transient isolated check verified that transition; actual phone/TLS/Bonjour/cloud connections were not exercised during development.

The floating window uses the existing Python `window.py`, `visuals.py` and `theme.py` appearance and native dimensions. The sidebar footer exposes a persisted switch. Native main-window minimum size follows Mac's 720×480 limit. Routine verification remains the existing three isolated smoke checks plus compilation and artifact/source validation.
# Exact Mac layout and reported-data repairs

The latest user instruction supersedes the preceding compact-density and comparison preferences. Main page geometry, log/table allocation, model-share cards, settings rows and chart structure now follow the current Mac v0.3.4 source directly. Approved Windows Noto fonts remain in use; this is source/layout parity, not a claim of pixel-identical platform rendering. Settings content scrolls independently; idle update progress and Release body are omitted. Empty charts keep their axis baseline without assigning zero to unknown data.

The running EXE matched the prior delivered hash. A read-only ledger audit found four complete external app context envelopes still marked as human inputs. Metadata/projection migration 2 reverses only proven complete control messages and preserves valid byte cursors/meters; incomplete previews with missing bodies keep their ownership. A separate scan-clock callback updates only the header, initially and once per display minute.

Both WHAM routes returned valid data in a bounded read-only diagnostic. Account HTTP requests now use current-user Windows static system proxy settings when an explicit environment proxy is absent, preserving mature NO_PROXY semantics. A temporary Go process with proxy environment variables cleared selected the native proxy and fetched three weekly periods successfully. PAC/autodiscovery and the previously running App's environment were not verified. Candidate start-date preparation uses a grouped scan and stable ordering; missing dates do not send empty-string timestamps.

Floating retains the Python layout/font/per-widget shadow and transparency settings. Its native host uses a transparent RGBA background, dark appearance and no frameless system decorations. The existing hit-target alpha and window lifecycle are preserved. No Computer Use, screenshots, real App restart or live-data repair was used for verification. Compilation, the existing three isolated smoke checks and artifact freshness checks remain the routine validation.

# 2026-09-30 collection and floating repairs

A read-only audit and an isolated SQLite backup reproduced the stalled collector: one valid historical JSONL line was 11,349,839 bytes, exceeding the Go collector's 8 MiB limit. The v0.2.10 `usage_collector.py` readline behavior is retained within a 64 MiB bound. Both normal collection and metadata recovery use that limit. File errors keep their cursor/origins for retry and no longer abort later files; committed records are projected even during partial failure. Usage notifications include scan status so the header/error surface can report failures on every page, independently of business data. No unchanged logs are replayed to update the clock.

The same isolated ledger recovered today's real user requests and current records, with a successful scan timestamp. The real database and running EXE were left untouched. Floating content is display-only: no buttons, click/double-click actions or context menu. Browser image/text dragging is disabled while the native window drag region remains. Edge changes reuse `v0.2.10:src/codexio/dock.py:snap_geometry`'s center anchor instead of preserving the resized bar's corner. Settings children cannot shrink along their vertical scroll axis, so the preview keeps its full height.

Verification uses the existing three mock smoke checks, production compilation and delivery hashes. No new test suite, Computer Use, screenshot matrix or version bump is introduced.

# 2026-10-01 visual and data parity

Windows bundles `InterVariable.woff2` from the user-supplied Inter 4.1 archive together with `Inter-OFL-1.1.txt` (SIL Open Font License 1.1). Latin text and numerals use the bundled variable font; Chinese falls back to the Windows interface font without a system installation. Application compact numbers are locale-independent K/M/B values, while AI report cards keep their established report-specific Chinese notation.

Trend queries now materialize bounded local-time buckets. A bucket with no calls is a known zero and stays connected on the baseline; a bucket with known Token but incomplete pricing retains Token and leaves only cost unknown. The Svelte chart renders that backend contract without synthesizing null gaps.
