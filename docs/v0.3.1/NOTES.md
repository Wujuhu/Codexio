# v0.3.1 实施注意事项与待定决定

> 本文件记录现状、迁移约束与后续关口，配合 [设计规划](PLAN.md) 使用。它不是开始编码的指令。目标版本 **v0.3.1** 已由用户提出；本轮不改 `__version__`、Widget 构建号、现有 App、构建脚本或发布文件。

## 1. 经仓库核对的现状

- 当前程序版本是 `0.2.10`。Mac 主程序入口、主窗口、后台工作线程和数据逻辑目前主要在 `src/codexio/` 的 Python/PySide6 代码中；SwiftUI 只承担 `macos/widget/CodexioWidget.swift` 的 WidgetKit 界面。现有 Mac 开发包由 `build_macos.sh` 调用 Python 打包流程生成。
- 主窗口有概览、日志、用量、订阅、定价、设置六页；Mac 的左侧导航可收起、可调宽。现有日志详情采用悬停浮窗，代码里仍有不少 `setToolTip`。这两处都需要在设计阶段确定新的明确交互，而不是机械地把 Qt 提示换成 SwiftUI `.help`。
- `docs/macos.md` 记录：0.2.9 已移除右上角菜单栏图标和旧预览；当前保留的是 macOS **应用菜单**与 Dock。`README.md` 前面的旧版本章节仍描述过菜单栏预览，那是历史行为。v0.3.1 要设计的是新的原生菜单栏状态项和面板。
- 现有 WidgetKit 只有一个 `Codexio 请求` 配置，支持小、中、大三个尺寸；小尺寸仍以最近请求为主。新增额度专用的小尺寸组件时，必须有独立配置和稳定标识，避免升级后现有请求组件消失或变成另一种内容。
- 小组件通过有界的 `widget_snapshot.json` 读取精简数据，不接触 Codex 原始日志或凭据。当前快照已包含额度和今日汇总，但快照的发布时机与读取权限仍需在 Swift 迁移时保持可靠。
- 当前 Mac 最低系统目标为 macOS 15；已在 Apple Silicon / macOS 27 实测，macOS 15 真机仍待验收。正式可支持的系统版本与芯片架构在设计冻结后、实施前再次核对。

## 2. Swift 迁移的范围

“整个 Mac App 用 Swift 重构”的目标应覆盖运行所需的全部 Mac 侧能力，不保留一个不可见的 Python/Qt 后台来承载实际功能。SwiftUI 构建页面和 Widget，AppKit 负责适合它的窗口、应用菜单、菜单栏状态项、Popover 与 Dock 生命周期。界面、数据层和后台服务共享同一状态模型，避免主窗口、菜单栏、小组件各算出不同数字。

| 现有责任 | 当前主要入口 | 迁移时的要求 |
| --- | --- | --- |
| App 生命周期、单实例、窗口恢复、菜单、Dock | `macos_app.py`、`dashboard_host.py` | 关窗不退出；重复打开恢复主窗口；菜单栏与主窗口状态一致 |
| 六页 UI、筛选、日志详情、设置 | `dashboard.py` 及相关绘制模块 | 转为 Swift 原生控件和状态驱动视图，不仅包一层 Swift 窗口 |
| ChatGPT 额度及账户模式 | `app_server.py`、`rate_limits.py`、`worker.py` | 仍只从本机 Codex `app-server` 读取真实额度；API Key／自定义 provider 模式显示不适用 |
| 日志索引、去重、请求分组、用量 | `usage_collector.py`、`usage_store.py`、`usage_queries.py`、`usage_worker.py` 等 | 保留只读扫描、已确认 Token、用户请求与子代理归属、时区和跨日口径；兼容旧索引或明确安全迁移方案 |
| 价格、费用、订阅估值 | `pricing.py`、`confirmed_usage.py`、`rolling_estimation.py` 等 | 保留现有价格目录、参考估值与未知值语义；费用不是实际账单 |
| 数据源、上游检测、代理与恢复 | `upstream_*.py`、配置与进程模块 | 保留本机／SSH 来源和已启用行为；配置恢复与退出协调不能因 UI 迁移失效 |
| Widget 数据、后台刷新、宿主接管 | `macos_widget_*.py`、`macos_app_takeover.py`、`macos/widget/` | 保留唯一稳定宿主、原子快照、现有组件、后台刷新及失败回滚 |
| Mac 更新与发布包 | `macos_updater.py`、`scripts/build_macos.py` 等 | 继续产出完整 `Codexio.app` ZIP、合并清单，核验签名、版本、架构、大小与哈希 |

Windows 仍使用现有 Python 实现。两端共享的是**产品行为与数据规则**，不要求在代码层强行共用同一语言；需要建立清晰的口径清单，避免 Swift 端在余额、费用、缓存命中率、请求计数或重置时间上漂移。

## 3. 数据和状态边界

1. **额度**：只有 ChatGPT 适用账户显示 5 小时和周额度；成功时间、缓存、超时、离线、不适用必须可区分。不能用本地 Token 推算官方剩余额度，也不能在重置时刻自行清零或补满。
2. **用量**：沿用现有会话日志与增量索引，不改原始 Codex 配置或日志。今日以本机时区午夜为界；请求数按主用户请求统计；跨午夜请求的整轮费用与每日费用可能不同。
3. **金额**：按现有模型定价换算，保留已确认数据与未定价／参考估值标记。UI 统一以两位小数展示美元，底层不舍入存储精度。
4. **新鲜度**：主窗口、菜单栏与小组件对同一份状态使用一致的更新时间与过期规则；UI 未拿到数据时显示 `—` 和原因，不展示貌似真实的旧百分比。
5. **隐私**：菜单栏和额度小组件默认只放额度及摘要，不直接外露用户请求文本；Widget 扩展只读取精简快照，不读取凭据或完整日志。
6. **迁移**：旧用户的设置、数据源、已建立的索引、历史价格与窗口偏好需要有兼容或安全迁移方案。迁移失败时保留原数据，不以重建为由删除用户信息。

## 4. Widget 与菜单栏生命周期

- 现有 `com.wujuhu.codexio.widget` 只能由稳定的 `/Applications/Codexio.app` 提供。新原生宿主仍需先验证版本、Widget 构建号和签名，再接管；启动失败恢复旧 App。稳定宿主注册当前扩展成功后才清理旧路径。
- 新额度 Widget 使用独立 `kind`，原请求 Widget 的 `kind` 与显示语义保持稳定。Widget UI、注册或宿主生命周期变化时按项目规则递增 `WIDGET_VERSION`，并核对主 App 版本、Widget 短版本和构建号。
- 菜单栏状态项应跟随主 App 生命周期，不启动第二套独立采集器。用户关闭主窗口后它仍可工作；用户选择退出后一起停止。菜单栏显示开关必须有从 Dock 和应用菜单重新进入设置的路径。
- 开发 `--mock` 和打包冒烟不得操作真实 `/Applications`、LaunchAgent、运行进程或系统 Widget 注册。用户自行启动安装的 App；开发与核验不代用户启动。

## 5. 设计与实施顺序

### 设计阶段，当前进行中

1. 审阅 [设计规划](PLAN.md) 中的页面、组件、菜单栏和内容规则。
2. 制作并修订完整布局及视觉设计稿，统一关键状态、明暗外观、交互和文案。
3. 把用户反馈写回设计文档，形成最终选定的品牌区域、组件样式、菜单栏密度和页面细节。
4. **只有用户明确表示整体设计满意并同意开始实现，才结束本阶段。** 文档获得一次审阅或版本号被提出，都不自动代表代码获批。

### 实施阶段，设计通过后才可开始

1. 建立 Swift Mac App 工程与可维护的状态／数据边界；确认旧配置和索引的迁移策略。
2. 迁移数据采集与计算，再接入六页 SwiftUI 界面；数据准确性是菜单栏与 Widget 设计成立的前提。
3. 加入菜单栏面板、新额度 Widget，保护旧 Widget 的已安装状态。
4. 对接更新、稳定宿主接管、后台刷新和 Mac 打包；移除 Mac 包内不再需要的 Python/Qt 运行时。
5. 完成必要的最小运行检查与交付核验，生成 `build/dev/macos` 开发包，提交到本地 Git，交给用户验收。

这不是并行开工许可。各步骤可以在设计获批后调整技术次序，但不能跳过数据和升级兼容性。

## 6. 验证与交付约束

- 设计文档阶段只校对文档与仓库事实，不运行现有 App、不打包、不修改代码。
- 后续代码阶段沿用项目固定的**最小冒烟**：程序能启动、基本数据显示正常、主窗口能关闭并重新打开；使用隔离模拟数据，不修改真实 Codex 配置、不重启用户正在使用的客户端。不新增测试文件、专项测试项、全量回归或截图矩阵。
- 开发包只进入 `build/staging/macos` 与 `build/dev/macos`，保持 `release` 不变。版本、签名、Widget 版本、ZIP 内容、架构和 SHA-256 仍属必要交付校验。
- Windows EXE 在正式发布授权后由 Windows CI 构建；两端仍需同版本与一份合并 `latest.json`。正式目录仅有 `Codexio.exe`、`Codexio.app.zip`、`latest.json`，不新建 DMG。
- 用户完成开发验收后，还需要**第二次明确确认发布及版本号**才能推送 `main`、创建 Tag 和 GitHub Release。当前“开始 v0.3.1”只确定规划目标，不授权远程操作。

## 7. 需在设计定稿前明确的选择

| 选择 | 目前建议 | 何时决定 |
| --- | --- | --- |
| 左上品牌 | 比较“字标为主”和“图标＋字标”完整明暗稿，优先选择更安静的一版 | 主窗口视觉审阅 |
| 日志详情 | 改为点击／键盘选择后的稳定详情，去掉大幅悬停浮窗 | 六页交互审阅 |
| 额度 Widget 默认样式 | 双额度 B；聚焦 A 和刻度 C 也作为可选样式交付 | Widget 样式审阅 |
| Widget 外观配置 | 先验证跟随系统；固定明暗作为可选方案 | Widget 明暗审阅 |
| 菜单栏图标文字 | 默认先比较仅图标与“图标＋周剩余”，狭窄菜单栏优先仅图标 | 菜单栏设计审阅 |
| 菜单栏趋势区 | 有可靠七日数据时显示紧凑趋势，无数据时保持简洁空态 | 菜单栏内容审阅 |
| Swift 工程与历史存储迁移 | 设计冻结后确定工程组织与安全迁移方式 | 实施前技术方案审阅 |

## 8. 参考依据与冲突说明

- 用户提供的六张附件是视觉参考；其内部出现的品牌、百分比、余额、数据来源与手机系统界面不构成额外功能要求。
- 当前功能与交付边界以仓库 `AGENTS.md`、`PRODUCT.md`、`docs/macos.md` 和实际源码为依据。`README.md` 中早期菜单栏段落属于历史版本说明，不能当作现状。
- [ChatGPT macOS 发行说明](https://help.openai.com/en/articles/9703738-chatgpt-macos-app-release-notes) 与 [Chat Bar 说明](https://help.openai.com/en/articles/9295241-how-to-launch-the-chat-bar)：用于核对原生菜单栏、窗口及快捷入口的设计参考，不作为 Codexio 的功能清单。
- [Claude 桌面端导航介绍](https://academy.claude.com/tutorials/navigating-the-claude-desktop-app)：用于核对桌面端入口与菜单栏的交互参考。
- [Apple WidgetFamily](https://developer.apple.com/documentation/widgetkit/widgetfamily/) 与 [macOS 小尺寸 Widget](https://developer.apple.com/documentation/widgetkit/widgetfamily/systemsmall)：用于限定最小尺寸的系统能力；[可配置 Widget](https://developer.apple.com/documentation/widgetkit/making-a-configurable-widget) 用于后续样式选择方案；[MenuBarExtra 窗口样式](https://developer.apple.com/documentation/swiftui/menubarextrastyle/window) 用于核对原生面板呈现。
