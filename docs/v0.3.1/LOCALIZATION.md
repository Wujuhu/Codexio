# v0.3.1 中英文界面与字体规范

> 适用范围：**macOS 原生 App、菜单栏、两个 WidgetKit 组件，以及 Windows 主窗口、悬浮窗、托盘和原生对话框**。中文指现有界面使用的简体中文 `zh-Hans`；英文为 `en`。本文件是重构前文案与行为规范，不是已写入程序的翻译文件。

## 1. 默认语言和字体

| 平台 | 默认界面语言 | 字体 | 实施要点 |
| --- | --- | --- | --- |
| macOS | 由系统对 App 的首选本地化决定；支持 `zh-Hans`、`en`，其他语言回退 `en`；尊重“系统设置 → 语言与地区”中的单 App 语言 | **中文和英文均使用 macOS 默认系统字体** | SwiftUI `Font.system`／系统文本样式，AppKit `NSFont.systemFont`；数字可使用系统字体的等宽数字特性，不捆绑或强制 Times New Roman、AnthropicSans 等界面字体 |
| Windows | 按设备 UI 语言的优先顺序选择 `zh-Hans` 或 `en`；其他语言回退 `en` | Windows 默认系统界面字体 | PySide6 从系统 UI 语言选择翻译资源，使用 `QFontDatabase.systemFont(GeneralFont)`；不加载 Mac 字体或按语言硬编码字体名 |

语言选择发生在启动时；用户更改系统或单 App 语言后重新启动 Codexio 即按新语言显示。不额外增加一套与系统语言竞争的 App 内语言开关。Mac 主 App 和 Widget 扩展均提供中英本地化资源；Windows 主窗口、悬浮窗、托盘与所有用户可见对话框使用同一语言选择结果。

依据：[Apple 的本地化说明](https://developer.apple.com/localization/)明确 macOS 支持在系统设置中为单个 App 选择语言，并推荐 String Catalog；[Apple NSFont 文档](https://developer.apple.com/documentation/appkit/nsfont)说明系统字体接口。[Qt 的 `QLocale.uiLanguages()`](https://doc.qt.io/qt-6/qlocale.html)用于选择界面语言，[`QFontDatabase.GeneralFont`](https://doc.qt.io/qt-6/qfontdatabase.html)是 Windows 上的默认系统字体。动态日期、数字和复数要用各平台本地化格式化接口，不在源码中拼接中文单位或英文复数。

## 2. 翻译覆盖边界

- 必须翻译：导航、页面标题、字段标签、表头、按钮、菜单栏、Widget 固定标签、空态、读取／错误状态、更新提示、上游检测确认、额度重置确认与结果、无障碍名称。
- 不翻译：用户原始请求和回复、用户自定义资料、模型 ID、文件路径、URL、服务端返回的原始诊断代码；服务端提供的可展示标题可保留原语言，但外层标签按界面语言显示。
- 同一概念只用一套术语。`Token` 作为计量单位在中文保留英文写法，英文中使用自然的 `tokens`；“费用”是现有模型价格换算值，必要时就近标注“估算／Estimated”，不称为订阅账单。
- 现有请求小组件小、中、大**不改布局、颜色、字级、图形、内容顺序**；英文系统只替换固定标签，容器可容纳英文较长文本，不能挤掉数字。中文版本保持现有视觉。
- 文案仅存在于中英资源中，不能在菜单栏或 Widget 扩展另写一套不同翻译。Mac 可用 Xcode String Catalog；Windows 可用 Qt 翻译资源或等价的集中翻译层，但文案键和语义与下表一致。

## 3. 固定界面文案清单

下表是开发时的核心文案键。正式实现若出现新的用户可见状态，须在同一中英文目录补齐并保持术语，不得只补一种语言。

### 3.1 导航与通用操作

| Key | 简体中文 | English |
| --- | --- | --- |
| `nav.overview` | 概览 | Overview |
| `nav.logs` | 日志 | Logs |
| `nav.usage` | 用量 | Usage |
| `nav.subscription` | 订阅 | Subscription |
| `nav.pricing` | 定价 | Pricing |
| `nav.settings` | 设置 | Settings |
| `action.refresh` | 刷新 | Refresh |
| `action.openMain` | 显示主界面 | Show main window |
| `action.closeWindow` | 关闭窗口 | Close window |
| `action.quit` | 退出 Codexio | Quit Codexio |
| `action.cancel` | 取消 | Cancel |
| `action.confirm` | 确认 | Confirm |
| `action.save` | 保存 | Save |
| `action.copy` | 复制 | Copy |
| `action.previous` | 上一页 | Previous |
| `action.next` | 下一页 | Next |
| `status.updatedJustNow` | 刚刚更新 | Updated just now |
| `status.lastUpdated` | 最近更新 | Last updated |
| `status.loading` | 正在读取 | Loading |
| `status.stale` | 数据已过期 | Data is out of date |
| `status.failed` | 读取失败 | Couldn’t load data |
| `status.unavailable` | 暂不可用 | Unavailable |

### 3.2 额度、概览与小组件

| Key | 简体中文 | English |
| --- | --- | --- |
| `quota.fiveHour` | 5 小时额度 | 5-hour limit |
| `quota.weekly` | 周额度 | Weekly limit |
| `quota.used` | 已用 | Used |
| `quota.remaining` | 剩余 | Remaining |
| `quota.resetsAt` | 重置时间 | Resets at |
| `quota.notApplicable` | 此账户不提供 ChatGPT 额度 | ChatGPT limits aren’t available for this account |
| `quota.waiting` | 等待额度数据 | Waiting for limit data |
| `overview.today` | 今日用量 | Today’s usage |
| `period.today` | 今日 | Today |
| `period.sevenDays` | 近 7 天 | Last 7 days |
| `period.thirtyDays` | 近 30 天 | Last 30 days |
| `period.history` | 历史 | All time |
| `metric.cost` | 费用 | Cost |
| `metric.estimated` | 估算 | Estimated |
| `metric.totalTokens` | Token 总数 | Total tokens |
| `metric.userRequests` | 用户请求 | User requests |
| `metric.cacheHitRate` | 缓存命中率 | Cache hit rate |
| `overview.trend` | 用量趋势 | Usage trend |
| `overview.recentRequests` | 最近请求 | Recent requests |
| `action.viewAll` | 查看全部 | View all |
| `empty.noRequests` | 暂无请求 | No requests yet |

额度大数字若显示“已用”，条形也表示已用；若显示“剩余”，条形也表示剩余。中文和英文不能因翻译改变这个方向。小组件中的时间相对文案要用复数与本地化时间格式，不写死 `1 min ago` 或“1 分钟前”的拼接规则。

### 3.3 日志与用量

| Key | 简体中文 | English |
| --- | --- | --- |
| `logs.userRequest` | 用户请求 | User request |
| `logs.modelCall` | 模型调用 | Model call |
| `logs.search` | 搜索输入、会话或 ID | Search prompt, session or ID |
| `filter.allModels` | 全部模型 | All models |
| `filter.allStatuses` | 全部状态 | All statuses |
| `filter.customRange` | 自定义范围 | Custom range |
| `column.time` | 时间 | Time |
| `column.model` | 模型 | Model |
| `column.actualModel` | 实际模型 | Actual model |
| `column.tokens` | Token | Tokens |
| `column.cost` | 费用 | Cost |
| `column.status` | 状态 | Status |
| `detail.request` | 请求详情 | Request details |
| `logs.detailsLink` | 详情 | Details |
| `detail.modelCall` | 模型调用详情 | Model call details |
| `detail.inputPreview` | 输入预览 | Prompt preview |
| `detail.replyPreview` | 回复预览 | Reply preview |
| `detail.duration` | 耗时 | Duration |
| `detail.cachedInput` | 缓存读取 | Cached input |
| `detail.parentRequest` | 所属主请求 | Parent request |
| `detail.copyId` | 复制 ID | Copy ID |
| `status.running` | 进行中 | In progress |
| `status.completed` | 已完成 | Completed |
| `status.unpriced` | 未定价 | Unpriced |
| `usage.dailyTokens` | 每日 Token | Daily tokens |
| `usage.hourly` | 按小时 | Hourly |
| `usage.daily` | 按天 | Daily |
| `usage.weekly` | 按周 | Weekly |
| `usage.noData` | 暂无用量记录 | No usage records yet |

### 3.4 订阅与“使用重置”

| Key | 简体中文 | English |
| --- | --- | --- |
| `subscription.currentLimits` | 当前额度 | Current limits |
| `subscription.personalPlan` | 个人订阅计划 | Personal plan |
| `subscription.planPrice` | 订阅价格 | Subscription price |
| `subscription.renewalDate` | 续费日期 | Renewal date |
| `subscription.editProfile` | 编辑资料 | Edit profile |
| `subscription.cycleHistory` | 周期记录 | Cycle history |
| `subscription.weeklyEstimate` | 整周额度估值 | Full-week estimate |
| `reset.section` | 主动重置 | Rate-limit resets |
| `reset.availableCount` | 可用次数 | Available resets |
| `reset.itemFallback` | 重置 {number} | Reset {number} |
| `reset.expiresAt` | 截止时间 | Expires |
| `reset.noExpiry` | 无到期限制 | No expiration |
| `reset.expiryUnknown` | 截止时间未提供 | Expiration unavailable |
| `reset.detailsUnavailable` | 逐次明细暂不可用，无法选择使用。 | Individual reset details are unavailable. You can’t choose a reset yet. |
| `reset.partialDetails` | 仅显示 {shown} / {available} 次重置 | Showing {shown} of {available} resets |
| `reset.redeeming` | 使用中 | Redeeming |
| `reset.redeemed` | 已使用 | Redeemed |
| `reset.use` | **使用重置** | **Use reset** |
| `reset.confirmTitle` | 使用这次额度重置？ | Use this reset? |
| `reset.confirmBody` | 这会消耗当前账户中所选的重置，无法撤销。 | This will consume the selected reset for the current account. It can’t be undone. |
| `reset.inProgress` | 正在使用重置… | Using reset… |
| `reset.success` | 重置已使用，正在同步额度… | Reset used. Syncing limits… |
| `reset.alreadyRedeemed` | 这次重置已完成，正在同步额度… | This reset was already completed. Syncing limits… |
| `reset.nothingToReset` | 当前没有可重置的额度。 | No eligible limit to reset. |
| `reset.noCredit` | 没有可用的重置次数。 | No resets available. |
| `reset.unknownResult` | 暂时无法确认结果，请重试本次操作。 | The result is unclear. Retry this attempt. |
| `reset.retryAttempt` | 重试本次 | Retry attempt |
| `reset.notSupported` | 当前账户不能使用额度重置。 | Resets aren’t available for this account. |

“主动重置”只在标题处显示可用总数，下面逐张列出服务端提供的名称、状态和截止时间，每张的右侧都有自己的 **使用重置 / Use reset** 按钮。确认框点名所选条目并再次显示它的截止时间；不提供会替用户选“下一张”的总按钮。说明正文负责交代“消耗所选一次、无法撤销”。数字 `0 / 1 / 2` 与“次可用”采用复数资源，不在源码中直接拼接。

### 3.5 定价、设置、更新与菜单栏

| Key | 简体中文 | English |
| --- | --- | --- |
| `pricing.standard` | 标准定价 | Standard pricing |
| `pricing.unit` | 美元 / 1M Token | USD / 1M tokens |
| `pricing.syncNow` | 立即同步 | Sync now |
| `pricing.lastSynced` | 上次同步 | Last synced |
| `pricing.searchModel` | 搜索模型 | Search models |
| `pricing.input` | 输入 | Input |
| `pricing.editBase` | 编辑基础价 | Edit base price |
| `pricing.restoreAuto` | 恢复自动基础价 | Restore automatic base price |
| `pricing.referenceEstimate` | 参考估值 | Reference estimate |
| `settings.appearance` | 外观 | Appearance |
| `settings.data` | 数据源 | Data |
| `settings.application` | 应用 | App |
| `settings.theme` | 主题 | Theme |
| `settings.followSystem` | 跟随系统 | Follow system |
| `settings.light` | 浅色 | Light |
| `settings.dark` | 深色 | Dark |
| `settings.codexPath` | Codex 路径 | Codex path |
| `settings.autoDetect` | 自动发现 | Detect automatically |
| `settings.rescan` | 重新扫描本地记录 | Rescan local records |
| `settings.rescanNow` | 开始扫描 | Rescan now |
| `settings.menuBar` | 菜单栏 | Menu bar |
| `settings.autoUpdate` | 自动下载更新 | Download updates automatically |
| `settings.checkUpdates` | 检查并更新 | Check for updates |
| `settings.upstreamDetection` | 上游检测 | Upstream detection |
| `settings.refreshLimits` | 额度刷新 | Limit refresh |
| `settings.refreshLogs` | 日志刷新 | Log refresh |
| `settings.estimateInterval` | 额度估算间隔 | Estimate interval |
| `settings.openDataFolder` | 打开数据目录 | Open data folder |
| `menu.today` | 今天 | Today |
| `menu.lastSevenDays` | 最近 7 天 | Last 7 days |
| `menu.estimatedCost` | 预估费用 | Estimated cost |
| `menu.uncachedInput` | 未缓存输入 | Uncached input |
| `menu.cachedInput` | 缓存输入 | Cached input |
| `menu.output` | 输出 | Output |
| `menu.reasoningOutput` | 推理输出 | Reasoning output |
| `menu.openWindow` | 打开主界面 | Open main window |
| `update.available` | 发现新版本 | Update available |
| `update.install` | 安装更新 | Install update |
| `update.waitingToQuit` | 退出后安装更新 | Install after quitting |

“数据源”页在 v0.3.1 只显示本机 Codex 路径与本地索引维护。**不为已删除的 SSH 来源管理和日志来源列新增英文翻译**，避免把已取消功能带回界面。

### 3.6 Windows 悬浮窗与现有请求组件

| Key | 简体中文 | English |
| --- | --- | --- |
| `floating.window` | 悬浮窗 | Floating window |
| `floating.alwaysOnTop` | 始终置顶 | Always on top |
| `floating.hide` | 隐藏悬浮窗 | Hide floating window |
| `floating.show` | 显示悬浮窗 | Show floating window |
| `widget.recentRequest` | 最近请求 | Recent request |
| `widget.currentRequest` | 进行中的请求 | Active request |
| `widget.waitingForRequest` | 打开 Codexio 查看最近请求 | Open Codexio to view recent requests |
| `widget.todayCost` | 今日费用 | Today’s cost |
| `widget.todayTokens` | 今日 Token | Today’s tokens |
| `widget.todayRequests` | 今日请求数 | Today’s requests |

Windows 悬浮窗与托盘随系统语言显示上述标签；其现有布局和功能范围继续保持。Mac 旧请求组件只替换固定标签的语言，不重做其小、中、大 UI。

### 3.7 本地活动、额度归因和菜单栏数字

| Key | 简体中文 | English |
| --- | --- | --- |
| `usage.activity` | 活动 | Activity |
| `usage.trend` | 用量趋势 | Usage trend |
| `usage.topChats` | 聊天排行 | Top chats |
| `usage.localRecords` | 本机记录 | Local records |
| `usage.localScan` | 本地扫描时间 | Local scan time |
| `usage.totalTokens` | 累计 Token 数 | Lifetime tokens |
| `usage.peakDailyTokens` | 单日峰值 Token | Peak daily tokens |
| `usage.longestChat` | 最长聊天时长 | Longest chat |
| `usage.currentStreak` | 当前连续天数 | Current streak |
| `usage.longestStreak` | 最长连续天数 | Longest streak |
| `usage.tokenActivity` | Token 活动 | Token activity |
| `usage.daily` | 每日 | Daily |
| `usage.weekly` | 每周 | Weekly |
| `usage.cumulative` | 累计 | Cumulative |
| `usage.insights` | 活动洞察 | Activity insights |
| `usage.fast` | 快速模式 | Fast mode |
| `usage.standard` | 标准 | Standard |
| `usage.mostUsedEffort` | 最常用的推理强度 | Most used reasoning |
| `usage.localShareBasis` | 按模型调用次数统计模式占比 | Mode shares by model calls |
| `usage.recorded` | 已记录 | Recorded |
| `usage.unknown` | 未知 | Unknown |
| `usage.unattributed` | 未归类 | Unattributed |
| `usage.partial` | 部分数据 | Partial data |
| `plan.history` | 套餐用量历史 | Plan usage history |
| `plan.byModel` | 按模型 | By model |
| `plan.period` | 周期 | Period |
| `plan.usedPercent` | 已使用限额百分比 | Limit used |
| `plan.asOf` | 统计截至 | Usage as of |
| `plan.approximate` | 约 | Approx. |
| `chat.ranking` | 聊天用量排行 | Chat usage ranking |
| `chat.weeklyPercent` | 占每周限额的 % | % of weekly limit |
| `chat.creditsUsed` | 已用额度 | Credits used |
| `chat.reasoning` | 推理强度 | Reasoning |
| `chat.speed` | 速度 | Speed |
| `chat.open` | 打开聊天 | Open chat |
| `chat.scope` | 当前周额度 · 本机可用聊天 | Current weekly allowance · local chats |
| `chat.noDetails` | 当前账户尚未提供这项明细 | This account has not provided these details |
| `chat.localRanking` | 查看本机 Token 排行 | View local token ranking |
| `menu.quotaService` | Codex | Codex |
| `menu.content` | 菜单栏内容 | Menu bar content |
| `menu.weeklyRemaining` | 图标 + 周额度剩余 | Icon + weekly remaining |
| `menu.fiveHourRemaining` | 图标 + 5 小时额度剩余 | Icon + 5-hour remaining |
| `menu.iconOnly` | 仅图标 | Icon only |

图 5 的 `Lifetime tokens` 指本机可读历史，必须同时保留 `Local records` 范围标记；不能翻成“账户累计”。图 7 的速度是服务端消费分组，不是 Token/s。模型 ID 保留原文；未知推理强度保留可辨识的原值，不擅自映射为最高。

## 4. 动态内容与版式规则

- 日期、星期、重置倒计时、相对更新时间和复数交给各平台本地化格式化能力；时区仍是用户设备本地时区。语言切换不能改变额度计算、存储的时间戳或费用数值。
- 本地热力图按设备时区分桶；服务端报告的统计截至时间可按参考图显示明确的 UTC，或完整转换成本机时区后标明时区。服务端周期开始／结束按实际字段，不能根据热力图自然周推测。
- 美元费用始终表示 USD，显示两位小数；分组分隔符和小数点按用户地区格式化。`1M Token` 等计量单位在两种语言下含义一致。
- 英文按钮和表头可能更宽。文字优先完整显示；窄窗口可换行或让表格水平滚动，不能压缩关键数值、截断“Use reset”或把确认正文放进悬浮提示。
- WidgetKit 小组件和菜单栏使用与主窗口相同的语言选择。Widget 文案空间不足时使用本目录定义的短标签，而不是单独发明术语；已有请求组件的版式不变。
- 用户输入、模型名称和原始日志内容保留原语言。搜索与排序不能因翻译丢失原始字符串匹配。

## 5. 交付时的最少核对

先按设计稿审阅一张中文和一张英文的主窗口、菜单栏与小组件关键画面，确认语言没有混用、系统字体与数字标签对齐。进入代码阶段后只在项目既有的隔离最小冒烟中检查启动、基本数据、关窗再开；不增加多语言截图矩阵、专项测试脚本或额外测试文件。发现具体截断或翻译错误时只检查对应界面。
