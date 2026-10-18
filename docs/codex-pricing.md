# Codexio 计价规则

核对日期：2026-10-18。当前源码版本为 0.3.11，已获本地开发与版本升级授权；推送、正式发布仍需新的明确指令。

费用是本地 Token 账本的美元参考估值，不是 ChatGPT 订阅实际扣款或官方额度百分比。普通输入、缓存读取、缓存写入、输出使用已核对的 API Standard 基础价，再按请求的认证与消费来源应用规则。上游额度百分比、重置时间仍直接读取，不由费用推算替代。

## GPT-6.1 Sol 的消费来源与倍率

API Standard 基础价（美元／百万 Token）为输入 2.00、缓存读取 0.10、缓存写入 2.50、输出 10.00。Mac、Windows 与 Python 使用相同的官方基础价；旧缓存和手工覆盖价不再参与官方目录，接口、套餐、速度倍率不作为新的基础价。

| 消费来源 | Standard | Fast | Ultrafast | 输入超过 272,000 Token |
| --- | ---: | ---: | ---: | --- |
| ChatGPT 订阅额度 | ×1 | ×2.5 | ×8 | 不加长上下文倍率 |
| OpenAI API Key | ×1 | ×2 | ×6 | 输入／缓存 ×2，输出 ×1.5 |
| 明确购买 credits | ×1 | ×2 | ×6 | 保留现行输入／缓存 ×2，输出 ×1.5 规则 |

速度与适用的长上下文倍率各乘一次。阈值取单次计量调用的全部输入，包括缓存部分；恰好 272,000 使用普通档，超过阈值时作用于该次整份输入／输出，不用整个用户请求累计输入重复判断。订阅豁免仅适用于 GPT-6.1 Sol，不推广到其他模型或明确购买的 credits。

API 模式始终保留缓存写入的 API 单列基础价，按速度和长上下文输入倍率缩放。GPT-6.1 Sol 订阅／credits 的缓存写入按普通输入价口径，不叠加 API 缓存写入附加费。其他模型继续保留现有计价规则，不因本次变更改变单价。

## 身份证据与历史记录

分类只读取调用自身的可选 `auth_mode`、`billing_source` 与明确可识别的 `usage_source`。API Key 的明确认证证据优先，不能被相矛盾的 subscription 字段免除 API 倍率。`chatgpt` 认证本身不能证明消耗订阅还是购买 credits。

官方常规 rollout 的 SessionMeta、TurnContext、TokenUsageRecord 不记录逐请求的认证／消费来源；线程创建身份不随 resume 更新，额度快照和余额也不是逐请求扣费证明。本次只在已有后台解析入口保留源记录明确提供的字段，按请求归属写入，缺失不继承前一请求，不新增采集或全量重扫。

来源不明的 OpenAI／旧 codexio-upstream 记录按用户明确口径统一按订阅计价，不因认证／消费来源缺失标为 `estimated`；内部记录采用的 `pricing_mode`／`assumed_billing_source`，原始认证与消费字段保持缺失。这个默认规则是用户选择的统计口径，不表示从日志恢复了真实身份。明确 API Key／credits 的记录按自身规则计价。第三方 provider 按已确认的模型及别名匹配官方参考价，保留估算状态。缺失 Token 或基础价保持缺价，不用零费用代替。

`billing_source` 是 Codexio 的可选归一化字段，不是声称官方日志已经提供该字段。只识别明确的 subscription／included allowance、credits／paid credits、API Key 值；`usage_source=local/cloud/codex`、账户余额或当前登录状态不能当消费来源。真实新请求清除上一请求的身份，后到的同轮上下文仍保留本轮明确身份，任务设置仅作用于后续请求。

## 计算、目录与失效

```text
普通输入 = 输入总量 I - 缓存读取 C - 缓存写入 W
费用 = [(I-C-W)×输入价 + C×缓存读取价 + W×缓存写入价 + O×输出价] / 1,000,000
```

推理 Token 已包含在输出 O 中，不重复计价。价格页每个型号只展示一行官方 Standard 基础价，Codex 速度和适用长上下文倍率通过感叹号浮窗展示；逐调用 API／credits 计算从原始 Standard 基础价派生，不再从已经乘过倍率的目录行再次相乘。

共同种子中的 `billing_policies.gpt-6.1-sol` 保存各消费来源阈值、速度、缓存写入口径与未知来源默认值；Mac、Windows 和 Python 参考实现使用相同数值。实际基础价与规则进入价格版本，抓取时间、核验日期与来源链接不进入版本。相同单价同步不会重复计价或覆盖版本归档。

价格版本变更复用现有已索引账本的派生统计失效与重新计价，原始 Token、请求归属和日志游标不变，不重新采集整个历史。金额、请求、模型汇总、趋势与桌面同步继续共用已有本地计价结果。

## 依据与验证边界

- [Tibo 关于订阅长上下文费用的原帖](https://x.com/thsottiaux/status/2109430894604026039)：北京时间 2026-10-12 07:48，GPT-6.1 Sol 从订阅额度计量时不收长上下文附加费；通过 X 公开嵌入／syndication 接口核对原文。
- [GPT-6.1 Sol 官方 API 模型页](https://developers.openai.com/api/docs/models/gpt-6.1-sol)：API Standard 基础价、272K 阈值及整次调用倍率。
- [Codex / Work 速度说明](https://learn.chatgpt.com/docs/agent-configuration/speed)：订阅 Fast／Ultrafast 2.5／8，购买 credits 与 Enterprise pay-as-you-go 2／6；API Key 独立计价。
- [官方 Codex 记录 schema](https://github.com/openai/codex/blob/740e5af33c71225640e0c1c1555c514c2c93ab74/codex-rs/protocol/src/protocol.rs)：已有缓存 0.162.0-alpha.17.2 的逐调用身份字段限制，不据此承诺所有客户端版本的字段覆盖。

不新增维护性测试文件或冒烟项，不运行 pytest／全量回归。使用原有三项隔离冒烟和交付核验，必要的算术检查仅针对本次规则，不读取真实配置、认证或聊天内容，不启动用户安装的应用。

## v0.3.11 统一目录与模型识别

`pricing_seed.json`、`claude_pricing_seed.json` 保存官方基础价；`model_pricing_rules.json` 保存经确认的型号范围、别名、倍率文案和实际计价条件。三个文件在 Windows assets 中保存字节相同的副本，Mac 打包时复制为 Bundle 资源。GPT／Claude／其他三组默认展开，切换 Agent 不切换目录、搜索或展开状态。前两组仅展示客户端官方支持清单：GPT 7 款（6.1 Sol、6 Astra、6 Sol／Luna、5.6 Sol／Terra／Luna），Claude 14 款（Fable 5.1／5、Opus 5.5／5／4.8／4.7／4.6／4.5、Sonnet 5.5／5／4.6／4.5、Haiku 5.5／4.5）。Claude 4.x 中仍获客户端官方支持的型号继续显示；Mythos、已退役及仅 API 可用的旧型号不显示。历史计价目录完整保留；显示清单与来源链接不参与价格版本，不因隐藏型号重算费用。GPT／Claude 目录只读，移除手动编辑、恢复和自动同步入口，旧缓存／覆盖配置保留在磁盘但不参与计算。

Mac `ModelPricing.swift`、Windows `model_pricing.go` 与 Python `model_pricing.py` 使用相同规则。型号匹配先处理明确的供应商边界、大小写和分隔符，再匹配官方型号或白名单别名；保留 Flash／FlashX／Pro 及代际数字，不使用编辑距离猜型号。已确认的 `gemin`／`falsh` 拼写变体、DeepSeek V4.1 Flash 1017 等纳入别名。Windows 实际返回的 `gemini-3.8-flash-n` 明确映射到 `gemini-3.8-flash` 官方参考价；不将 `-n` 当作可任意删除的通用后缀，也不把该次路由的请求模型 magpie 全局绑定到 Gemini。`high` 等仅分离为强度修饰，`gemini-3.8-high` 不能据此认成 Flash。

返回模型按原有 response ID／逐次调用关联后优先匹配，不把一次 `magpie → Gemini` 观测永久绑定到所有 magpie。Codex 无明确可识别返回时可用已知请求模型作参考；Claude 继续保留返回模型缺失或无法识别时费用缺失的保护。Claude 配置 label 的别名沿用来源隔离、生效时间和有界历史，只影响确认后的记录。原模型、来源、Token 和原始日志不改写；规范型号仅写入选用的 price_rates。

其他组首批为 Gemini 3.8 Flash、DeepSeek Flash／Pro、GLM 5.3／5.3 Flash／5.3 FlashX、Kimi K3。GLM 仅 5.3 起、Kimi 仅 K3 起；Flash 与 FlashX 独立计价。新型号仍需核对具体官方 Standard 单价，不用品牌通用价或模糊匹配。

DeepSeek 主表使用峰值价：Flash 输入／缓存读取／输出分别为 $0.30／$0.006／$1.20，Pro 为 $1.32／$0.044／$3.96，每百万 Token。按调用发生时的北京时间判断：非法定节假日的周一至周五 09:00–12:00、14:00–18:00 为峰值，其他时间 ×0.5；调休周末仍按周末处理。内置国务院已发布的 2026 年假期日历，日历是价格规则依赖；未覆盖年份的潜在节假日峰值时段保留参考估算属性。

Gemini 3.8 Flash 当前输入／缓存读取／输出为 $0.75／$0.075／$3.75，每百万 Token；官方列出的 2027-01-01 起价格在相同调用时间边界乘 2。按用户最新确认口径，“其他”组模型统一使用 5 分钟缓存写入参考价；Kimi K3 固定为 $3／百万 Token，日志即使包含 1 小时分项也按写入总量使用 5 分钟价。该口径按模型分组生效，在 Codex／Claude Code 接入时一致；原始分项不改写。其他表格只展示 5 分钟写入列，Kimi 不再展示 1 小时倍率或感叹号；官方 1 小时价仍留在原始价格资源供归档核对，不参与该组计价。Claude 官方模型继续按已记录的 5 分钟／1 小时分项分别计算，缺分项沿用有估算标识的默认档；不重复计算缓存 Token。缓存存储不是缓存写入，本次不展示或计入按存储时长收取的独立费用。Claude 美国区域、Batch、Gemini 专用服务档位均不纳入本次规则。

## 复用与失效

- 价格内容签名沿用 v0.2.10 `pricing.py` 的稳定规则：实际单价、倍率、别名及节假日规则进入版本，核对时间、来源链接和 UI 文案不进入版本。
- Mac 复用 `UsageSnapshotCache.swift` 的逐调用缓存及 `ClaudeLedger.swift` 的已索引账本重新计价；不重新扫描原始日志。
- Windows 复用 `observations.go` 的 response ID 有界批次、`ledgerFileIdentity` 的文件身份和现有 revision 表。活动转发写入仅排队受影响的 ID，由已有工作队列更新派生计价；普通 WAL 检查点没有业务 revision 变化时复用结果。启动或外部替换无法确定变化集时使用一次有界分页修复。
- 两端浮窗复用已有详情浮窗的锚点／内容区域共同关闭机制，显示只读取已准备的文案，不访问数据库、网络或触发扫描。

客户端型号依据：[Codex](https://learn.chatgpt.com/docs/models)、[Claude Code](https://support.claude.com/en/articles/11940350-claude-code-model-configuration)。

官方依据：[Gemini](https://ai.google.dev/gemini-api/docs/pricing#gemini-3.8-flash)、[DeepSeek](https://api-docs.deepseek.com/quick_start/pricing/)、[GLM](https://docs.z.ai/guides/overview/pricing)、[Kimi](https://platform.kimi.ai/docs/pricing/chat)、[2026 年假期](https://www.beijing.gov.cn/zhengce/zhengcefagui/202511/t20251104_4258873.html)。
