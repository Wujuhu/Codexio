# Windows 0.3.4：按 Mac v0.3.3 修正界面

用户 2026-09-30 的十张 Windows 截图及六项反馈是本轮问题证据。视觉权威为当前 `macos/VERSION=0.3.3` 的原生实现，用户明确允许完整复刻并要求不创新；本机没有苹方，用户选择已安装的 Noto Sans SC。维持 Windows 0.3.4，Mac/iOS 不改，未经新授权不推送、不发布。

## 约束和方向

- 复用 `MainViews.swift`、`Components.swift` 的侧栏、48pt 顶部工具条、32pt 页边距、28pt 页面标题、12/14/16pt 控件层级、18pt 气泡卡片。减少解释文字，保留必要操作及数据来源。
- 复用 `CompactTable.swift`、`Components.swift` 中日志双行、末列详情、360×500 浮层、左对齐 Markdown 内容、指标层级和复制操作。短预览、不显示普通速度，只显示 fast/ultrafast。输入／输出独立列，所有列可调整与持久化。
- 复用 `UsageViews.swift` 活动／趋势／聊天排行三页签、全年对齐周历热图、Mac 蓝色强度、聚合与月份轴。详情为固定定位圆角玻璃浮层，不挤占下面布局；悬停只使用已经计算的数据。
- 费用尺度使用真实最大费用而非强制一美元；单点要可见，未知值要断线。既有缓存／分页／请求分类口径保持。
- 重置卡保留标题和实际截止时间，移除剩余天数与英文服务描述。点击先确认，换账户或券失效时清除选择，后台仍保留验证和幂等。
- 定价展示只包括用户截图的八个 Codex 模型，按 GPT-6.1 Sol、GPT-6 Astra、GPT-6 Sol、GPT-6 Luna、GPT-5.6 Sol、GPT-5.6 Terra、GPT-5.6 Luna、GPT-5.5 排序；历史价格表与账本不删除。
- 报告严格复用 `UsageReportReader.swift` 的左内容、右 96pt 控制栏及缩放，以及 `ReportArtwork.swift`／`ReportArtworkVariants.swift` 的三款原有形状、字体层级与布局。Noto Sans SC 用于应用，报告可沿用已安装的 Noto Serif SC 对应原生报告 Songti；不继续用 SimSun 回退。
- 打开目录复用 Windows 官方 shell API，并核对真实路径；只修复目录动作，不在验证中启动用户已安装应用或重启 Codex。
- 仅原有三项隔离模拟冒烟；不增加测试文件、测试项、页面遍历或截图矩阵。当前 UI 实际需要只查看一张模拟截图，一次合并修正后构建交付并本地提交。

## 并行文件所有权

| 任务 | 文件 | 参考 | 产出 |
| --- | --- | --- | --- |
| 1 主框架与共同控件 | `frontend/src/style.css`, `App.svelte`, `Icon.svelte`, `Filters.svelte`, `Metrics.svelte`, `Quota.svelte`, `Overview.svelte`, `Settings.svelte`, `Subscription.svelte`, `Prices.svelte` | Mac MainViews/Components/SettingsViews/SubscriptionView | 原生布局、字体、卡片、精简文案、重置确认、紧凑定价 |
| 2 日志与详情 | `Logs.svelte`, `DetailPopover.svelte`, `Details.svelte`, `Markdown.svelte`, `Model.svelte` | Mac CompactTable/LogDetail/Presentation | 双行紧凑日志、独立输入输出和末列详情、持久化列宽、玻璃详情 |
| 3 图表与活动 | `Chart.svelte`, `Trends.svelte`, `Insights.svelte` 及局部 hover 组件 | Mac UsageViews/TrendChart/UsageHoverSurface | 单点、正确费用尺度与断线、周历活动及固定玻璃悬停 |
| 4 协调整合 | Go `service.go` 的定价投影、桌面目录动作、`Report.svelte` 及局部样式、合同和构建 | Mac PricingDisplayOrder（用户顺序优先）/UsageReportReader/ReportArtwork | 八模型展示、目录可用、原样报告、合并/打包/本地交付 |

共享样式由任务 1 统一；任务 2/3 的专有布局优先写组件 scoped 样式，避免跨任务覆盖。普通绑定和后端字段不更名；任何接口需求先通知协调者。所有临时报告在 `build/checks/v0.3.4/ui-parity`。

## 执行与核验

- [x] 读取实际 Mac 界面实现和用户截图，确认字体及原生尺寸。
- [x] 各任务实施并记录采用的参考文件和机制，直接定位六个问题。
- [x] 合并接口、审查数据缺失值、单点/热图边界、列宽与重置安全，修复具体问题。
- [x] 前端编译、Go 生产构建，原有三项隔离冒烟与一张当前 UI 检查。
- [x] 源码指纹、0.3.4 版本、EXE/清单大小与哈希校验；有占用时保留原 EXE 与暂存新版。
- [x] 本地提交、主工作区交付；报告已验证和运行边界。
