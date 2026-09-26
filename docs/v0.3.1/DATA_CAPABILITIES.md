# v0.3.1 数据来源、接口与计算口径

核对日期：2026-09-26。本文是开发方案，不是接口已在当前账户调通的证明。本轮只核对本地源码、Codex 官方文档及官方开源客户端，未读取账户私密统计、消耗重置或修改用户配置。

## 1. 最新范围：图 5 全部使用本机记录

用户最新修正优先：**图 5 的累计指标、全年热力图、Fast 占比和推理强度占比全部在本地计算，禁止用账户统计接口提供或补齐。** 一个账户可以登录多台设备；账户总量不能代表这台设备。

图 6 的套餐周期与图 7 的额度归因仍取服务端。两者与图 5 的本地统计使用独立数据模型、缓存和刷新时间，不能因为版式类似而相互混用。Mac 与 Windows 使用相同语义，Mac 使用 Swift 实现，Windows 使用现有 Python 数据层。

| 界面 | 数据范围 | 来源 | 页面上的最少标记 |
| --- | --- | --- | --- |
| 用量 → 活动（图 5） | 本机当前可读、去重后的 Codex 记录 | 本地扫描与索引计算 | 本机记录；本地扫描时间 |
| 原有用量趋势／今日费用 | 原有本机统计 | 本地调用、Token 和价格目录 | 日期区间／费用估算 |
| 概览、订阅、菜单栏百分比 | 当前登录账户额度，可能被其他设备共同消耗 | `account/rateLimits/read` | 5 小时／周、剩余、更新时间 |
| 订阅 → 套餐用量历史（图 6） | 服务端返回的账户历史周期 | `usage/plan_limit_history` | 周期、统计截至时间，必要时“约”／部分数据 |
| 用量 → 聊天排行（图 7） | 本机可识别聊天的服务端额度归因 | `usage/thread_usage/query_v2` | 本机可用聊天、当前周额度、统计截至时间 |

“本机可用聊天”决定排行候选；其额度可能包含同一聊天在其他设备上的继续使用。不能宣称此表等同于本机 Token 消耗。只在图 5 使用完全本地的活动统计。

## 2. 图 5 本地计算规则

复用并原生移植现有 `usage_collector.py`、`confirmed_usage.py`、`user_requests.py`、`durations.py`、`activity.py` 的扫描、去重、计量和计时语义，不另建第二套采集器。

| 指标 | 计算方法 |
| --- | --- |
| 累计 Token 数 | 本机索引中所有有效、已确认调用的 Token 之和；沿用现有总 Token 定义。输入已包含的缓存读取、输出已包含的推理 Token 不再重复相加 |
| 单日峰值 Token | 本机时区按自然日汇总后取最大值；保留日期供必要详情查看 |
| 最长聊天时长 | 将同一根聊天中已完成的有效运行区间合并后计算有效运行总时长，再取最长聊天。包含工具等待，剔除轮次间等待用户的空闲时间；并行子代理区间不叠加。缺少可靠起止或完成数据不能拿创建至最后更新时间代替；不完整记录显示“已记录”状态，全部不可确认时显示 `—` |
| 当前连续天数 | 某日本机记录存在有效模型调用则记活跃日；按本机自然日连续计算。今天已活跃从今天回溯；今天尚未活跃但昨天活跃时从昨天回溯；否则为 0 |
| 最长连续天数 | 在全部本机可读历史中计算最长连续活跃日区间；不拼接其他设备数据 |
| 全年 Token 活动 | 最近 365 个本机自然日，边缘周留空位。每日／每周／累计切换只聚合同一批本地桶，不重新请求账户统计；未来日期禁用 |
| Fast 占比 | 按去重后的有效模型调用次数计算；显式 `fast` / `priority` 归为快速，标准按实际字段识别；未知类型保留“未知”，不能补成标准 |
| 最常用推理强度与占比 | 同一批模型调用按明确记录的 reasoning effort 分组，取最多的一组；分母为同批调用总数，未知仍计入总数。缺失数据较多时就近显示简短覆盖信息 |

其他约束：

- 本机来源由记录路径和来源标记确认；旧 SSH 索引可留存，但不混入这组“本机记录”统计。多源合并记录仍需保留可判断本机来源的依据。
- 不按当前登录账户切换图 5 的统计，也不在换账户后把账户历史导入本机统计。可在本机已有日期筛选范围内计算；默认顶部累计指标对应全部本地可读历史，热力图对应近一年。
- 去重优先使用可信调用／response ID 和现有继承记录排除规则；父任务汇总与子调用不能一起计数。
- 本地记录不完整不代表实际从未使用：缺失值为 `—`，已知零值为 `0`；纯空数据展示简洁空状态。
- 本地统计脱机可用。**图 5 不调用 `account/usage/read` 或 `profiles/me`，也不在本地扫描失败时回退到账户统计。**
- 界面保留图 5 的横向五指标、全年热力图、下方活动洞察版式；只增加“本机记录”与必要口径小字。

## 3. 已核实的官方额度接口

### A. 当前窗口：已有稳定 App Server 方法

`account/rateLimits/read` 的 `rateLimitsByLimitId` 提供各额度桶，窗口含 `usedPercent`、`windowDurationMins`、`resetsAt`。按窗口时长识别 5 小时和周窗口，不固定认为 primary/secondary 永远各是哪一种。剩余为 `100 - usedPercent`；未知不能变成 0%。同一快照供概览、订阅、菜单栏和额度 Widget 使用。

依据：[官方 App Server 文档](https://learn.chatgpt.com/docs/app-server)。

### B. 图 6：存在官方客户端使用的套餐历史接口

官方开源实现：[`plan_history.rs`](https://github.com/openai/codex/blob/main/codex-rs/backend-client/src/client/plan_history.rs)。

- GET `/{prefix}/usage/plan_limit_history?days=7`；官方客户端根据路由使用 `wham` 或 `api/codex` 前缀。ChatGPT 后端基址包含 `/backend-api`。
- 顶层：`data_as_of`、`coverage_start`、`coverage_complete`、`approximate`、`boundary_tolerance_seconds`、`periods`。
- 周期：`id`、`window_minutes`、`plan_type`、`starts_at`、`ends_at`、`accounting_complete`、`used_basis_points`、`breakdowns`。
- `used_basis_points / 100` 是本周期已使用限额百分比；模型行的 `basis_points / 100` 是该模型占**整个周期限额**的百分比。它们不是模型 Token 占比，也不强制把模型行归一到 100%。
- 从 `breakdowns` 中选择 `dimension == model`，模型名和数值由后端返回；未识别的新维度不阻塞已知维度。
- `approximate` 缺失时官方实现按 true 处理；周期不完整或覆盖不足时保留状态。历史统计截至时间与当前实时窗口更新时间分别显示。
- 404 表示该路由未提供报告，不能据此显示零使用。官方客户端当前只请求七天快照历史，不承诺能追溯全年套餐周期。

**接入级别**：已核实是官方开源客户端实际使用的后端合同，尚未在本次检查的 App Server `ClientRequest` 方法清单中发现对应 RPC。设计可以落地，开发需单独的受控只读适配器，并先确认当前账户可用性。

### C. 图 7：存在真实的聊天额度归因接口

官方开源实现：[`task_usage.rs`](https://github.com/openai/codex/blob/main/codex-rs/backend-client/src/client/task_usage.rs)。

- POST `/{prefix}/usage/thread_usage/query_v2`，它是查询，不消耗重置或运行模型。
- 请求 `threads[]`：`thread_id`、可选创建时间 `created_at`、`descendant_thread_ids`。
- 单批最多 100 个互不重叠的根任务、合计最多 1,000 个唯一 thread ID。先用本机索引或 `thread/list` 建候选，识别真实后代关系；一个后代不能同时放入多个任务，也不能作为根行重复计费。
- 顶层 `data_as_of`；各聊天含 `thread_id`、`data_status`（available / partial / unavailable）、`usage_source`。
- 总量及各 `groups[]` 都可提供 `five_hour_limit_percent`、`weekly_limit_percent`、`balance_usage_credits`；分组还含 `model`、`reasoning_effort`、`speed`、`product_experience`。
- 表格默认按当前周额度百分比排序，使用未格式化原值，微小非零值保留有效数字。`balance_usage_credits` 是十进制字符串，按精确十进制处理，不能先转整数。
- 每个聊天的模型／速度／推理强度占比按所选额度指标归组后，除以该聊天同一指标的总量。周额度为默认依据；总量为零或不可用时不制造百分比。分组未覆盖总量时保留“未归类／部分数据”，不能偷偷把已知部分放大到 100%。
- “占每周限额的 %”与“已用额度”分列；余额扣除为 0 但周额度有消耗是允许的。未知为 `—`，不得照抄截图把缺失统一填 0。
- 点击行展开；模型、推理强度、速度各一行，底部“打开聊天”。链接只使用确实可解析的本机聊天标识与已核实的客户端入口，无法定位时不生成猜测 URL。

**接入级别**：官方开源后端查询，尚非现有稳定 App Server 方法。账户、套餐和后端部署差异可能影响返回；不要将接口存在表述为当前用户账户已调通。

### D. 不可混同的已有线程估算方法

已存在的 `account/usage/read({threadId})` 可返回 `threadUsage`，其金额叫 `estimatedUsageCreditsMicros`，分组含模型、推理、速度，属于线程估算。它与图 7 的当前周额度百分比、实际余额扣除不同，不可以拿估算金额替换该表的原列。

如当前账户只能提供这一类数据，可单独显示“聊天额度估算”，保留估算标签；或者提供“本机 Token 排行”入口。两种替代均需改变列名、范围说明和排序单位。**禁止把 Token、估算美元或推算 Credits 伪装成官方周额度百分比。**

依据：[官方请求参数](https://github.com/openai/codex/blob/main/codex-rs/app-server-protocol/schema/typescript/v2/GetAccountTokenUsageParams.ts)、[线程返回类型](https://github.com/openai/codex/blob/main/codex-rs/app-server-protocol/schema/typescript/v2/ThreadUsage.ts)。

## 4. 接入与降级步骤

1. 先完成纯本地活动统计；独立于账户和网络连接，确保图 5 没有账户统计依赖。
2. 当前额度继续走 App Server。周期和聊天明细按上述官方开源后端合同设计只读适配器；Mac 不额外嵌入 Python。
3. 适配器沿用 Codex 的账户路由、身份绑定与正常凭据刷新方式；不能把 ChatGPT 登录凭据送往任意自定义 provider。账户切换时取消旧请求、分开缓存并丢弃旧响应。不得在日志／设计稿中输出令牌或完整鉴权头。
4. 开发接入时一次确认当前账户接口能力；不绕过未开放功能或权限。返回 401/403 时显示登录／权限状态；404 显示未提供；429 遵守退避；网络失败保留有日期的旧快照。
5. 支持时显示图 6／7 的完整版式。报告缺失时保留简洁空状态：订阅仍有当前周额度；排行提供明确标名的本机 Token 视图。不会为补齐表格伪造模型比例。
6. 主窗口可见时按需刷新统计；历史数据不跟随额度的高频刷新。聊天列表分页、批量查询、按需展开并缓存，不为打开菜单栏扫描全部聊天。
7. 若源代码内容将被移植，保留 OpenAI Codex 的 Apache-2.0 许可及适用声明。此轮仅引用字段与方案，不复制依赖或增加产品实现。

## 5. Liquid Glass 平台能力

Apple 的 [`NSGlassEffectView`](https://developer.apple.com/documentation/appkit/nsglasseffectview)与 SwiftUI [`glassEffect(_:in:)`](https://developer.apple.com/documentation/swiftui/view/glasseffect(_:in:))从 macOS 26.0 开始提供。此版本继续保留项目现有最低 macOS 15 的兼容边界：26 及以上使用原生 Liquid Glass；15 使用 `NSVisualEffectView` 系统材质，布局与功能一致。减少透明度／提高对比度按系统偏好切换更实的背景。

静态 HTML 的透明与模糊只是材质目标示意，不能当成原生折射效果已经实现。主内容仍保持清晰中性平面；玻璃主要用于菜单栏浮层和它的控件。
