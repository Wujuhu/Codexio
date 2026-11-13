# Codexio 计价规则

价格核对日期：2026-10-29。当前源码版本为 0.3.12；本地开发版支持 GPT‑6.1 Terra 及用户指定的 Ultra Fast 计价，正式安装包以对应 Release 说明为准。其余型号继续沿用已确认内容。

费用是本地 Token 账本的美元参考估值，不是 ChatGPT 订阅实际扣款或官方额度百分比。普通输入、缓存读取、缓存写入、输出使用已核对的 API Standard 基础价，再按请求的认证与消费来源应用规则。上游额度百分比、重置时间仍直接读取，不由费用推算替代。

## GPT-6.1 Sol 的消费来源与倍率

API Standard 基础价（美元／百万 Token）为输入 2.00、缓存读取 0.10、缓存写入 2.50、输出 10.00。Mac、Windows 与 Python 使用相同的官方基础价；旧缓存和手工覆盖价不再参与官方目录，接口、套餐、速度倍率不作为新的基础价。

| 消费来源 | Standard | Fast | Ultrafast | 输入超过 272,000 Token |
| --- | ---: | ---: | ---: | --- |
| ChatGPT 订阅额度 | ×1 | ×2.5 | ×8 | 不加长上下文倍率 |
| OpenAI API Key | ×1 | ×2 | ×6 | 输入／缓存 ×2，输出 ×1.5 |
| 明确购买 credits | ×1 | ×2 | ×6 | 保留现行输入／缓存 ×2，输出 ×1.5 规则 |

速度与适用的长上下文倍率各乘一次。阈值取单次计量调用的全部输入，包括缓存部分；恰好 272,000 使用普通档，超过阈值时作用于该次整份输入／输出，不用整个用户请求累计输入重复判断。订阅豁免适用于 GPT-6.1 Sol，以及用户于 2026-10-29 明确确认采用相同口径的 GPT-6.1 Terra；不推广到其他模型或明确购买的 credits。

恢复旧日志时，源记录缺少速度字段的费用估值沿用 v0.2.10／Windows／Python 的 Standard 基础价口径；原始速度字段保持缺失，日志和详情仍显示 `—`。明确记录的未知或不支持速度不套用 Standard，不能把参考计价档位作为历史速度事实。

API 模式始终保留缓存写入的 API 单列基础价，按速度和长上下文输入倍率缩放。GPT-6.1 Sol 订阅／credits 的缓存写入按普通输入价口径，不叠加 API 缓存写入附加费。其他模型继续保留现有计价规则，不因本次变更改变单价。

## GPT-6.1 Terra

API Standard 基础价（美元／百万 Token）为输入 1.00、缓存读取 0.05、缓存写入 1.25、输出 5.00，来自 [官方模型页](https://developers.openai.com/api/docs/models/gpt-6.1-terra)。[Codex 速度说明](https://learn.chatgpt.com/docs/agent-configuration/speed)确认支持 Fast，订阅使用倍率 2.5，购买 credits／Enterprise pay-as-you-go 为 2。官方当前速度说明尚未列出 Terra 的 Ultra Fast；按用户本次 v0.3.12 要求，将此前确认的 Sol 分模式口径扩展到 Ultra Fast：订阅 ×8，credits／API Key ×6。这是 Codexio 的参考计价规则，不表示提供方已向所有账号开放该速度。

用户于 2026-10-29 明确确认沿用 GPT-6.1 Sol 的分模式口径：订阅不加长上下文倍率、缓存写入按普通输入价；credits 的缓存写入按普通输入价；API Key 保留独立 API 缓存写入价。credits／API Key 超过 272,000 输入 Token 时，对整次调用的输入和缓存乘 2、输出乘 1.5，再叠加所选速度。来源缺失按订阅计价且不因该缺失标记估算；明确 API Key／credits 原始证据优先。订阅长上下文和缓存写入口径是用户确认的统计规则，不声称由 API 资料推导出真实订阅扣费。

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

共同种子中的 `billing_policies.gpt-6.1-sol` 和 `billing_policies.gpt-6.1-terra` 保存各消费来源阈值、速度、缓存写入口径与未知来源默认值；Mac、Windows 和 Python 参考实现使用相同数值。实际基础价与规则进入价格版本，抓取时间、核验日期与来源链接不进入版本。相同单价同步不会重复计价或覆盖版本归档。

价格版本变更复用现有已索引账本的派生统计失效与重新计价，原始 Token、请求归属和日志游标不变，不重新采集整个历史。金额、请求、模型汇总、趋势与桌面同步继续共用已有本地计价结果。

## 依据与验证边界

- [Tibo 关于订阅长上下文费用的原帖](https://x.com/thsottiaux/status/2109430894604026039)：北京时间 2026-10-12 07:48，GPT-6.1 Sol 从订阅额度计量时不收长上下文附加费；通过 X 公开嵌入／syndication 接口核对原文。
- [GPT-6.1 Sol 官方 API 模型页](https://developers.openai.com/api/docs/models/gpt-6.1-sol)：API Standard 基础价、272K 阈值及整次调用倍率。
- [Codex / Work 速度说明](https://learn.chatgpt.com/docs/agent-configuration/speed)：订阅 Fast／Ultrafast 2.5／8，购买 credits 与 Enterprise pay-as-you-go 2／6；API Key 独立计价。
- [官方 Codex 记录 schema](https://github.com/openai/codex/blob/740e5af33c71225640e0c1c1555c514c2c93ab74/codex-rs/protocol/src/protocol.rs)：已有缓存 0.162.0-alpha.17.2 的逐调用身份字段限制，不据此承诺所有客户端版本的字段覆盖。

不新增维护性测试文件或冒烟项，不运行 pytest／全量回归。使用原有三项隔离冒烟和交付核验，必要的算术检查仅针对本次规则，不读取真实配置、认证或聊天内容，不启动用户安装的应用。

## v0.3.11 统一目录与模型识别

`pricing_seed.json`、`claude_pricing_seed.json` 保存官方基础价；`model_pricing_rules.json` 保存经确认的型号范围、别名、倍率文案和实际计价条件。三个文件在 Windows assets 中保存字节相同的副本，Mac 打包时复制为 Bundle 资源。GPT／Claude／其他三组默认展开，切换 Agent 不切换目录、搜索或展开状态。前两组仅展示客户端官方支持清单：GPT 8 款（6.1 Sol／Terra、6 Astra、6 Sol／Luna、5.6 Sol／Terra／Luna），Claude 15 款（Fable 5.2／5.1／5、Opus 5.5／5／4.8／4.7／4.6／4.5、Sonnet 5.5／5／4.6／4.5、Haiku 5.5／4.5）。Claude 4.x 中仍获客户端官方支持的型号继续显示；Mythos、已退役及仅 API 可用的旧型号不显示。历史计价目录完整保留；显示清单与来源链接不参与价格版本，不因隐藏型号重算费用。GPT／Claude 目录只读，移除手动编辑、恢复和自动同步入口，旧缓存／覆盖配置保留在磁盘但不参与计算。

Claude Fable 5.2（`claude-fable-5-2`）按每百万 Token 计价。标准价为输入 $8、缓存读取 $0.20、5 分钟缓存写入 $10、1 小时缓存写入 $16、输出 $40。记录明确为 Fast 时，这五项均按标准价的 2 倍计算。沿用现有的分档缓存计价与型号匹配机制，原始模型 ID 保留在账本中。[Anthropic 模型规格](https://platform.claude.com/docs/en/models/fable-5-2/overview) · [Claude Code Fast 费率](https://code.claude.com/docs/en/fast-mode#understand-the-cost-tradeoff) · [Fast 缓存计价](https://platform.claude.com/docs/en/about-claude/pricing#fast-mode-pricing)

两端定价表按共享目录的 `client_models` 清单顺序展示。Claude 先按版本号降序，同版本按标准输出单价降序。显示顺序不参与计价版本。

Mac `ModelPricing.swift`、Windows `model_pricing.go` 与 Python `model_pricing.py` 共用 `model_matching` 的结构化规则。初始化时预编译表达式；完整官方型号／已有明确别名优先于强度剥离，避免把型号本体的 Max 当成修饰词。匹配时先处理宽字符、大小写、驼峰和上下文容量，再分离供应商边界，识别品牌、核心代际和型号。完整基础型号可位于供应商前后缀之间，日期、构建号、版本标签、思考强度及 `-n` 等路由修饰不再依赖逐项追加完整别名。支持分开的代际数字（如 `gemini-3-8-flash`）、DeepSeek V4 系列、GLM Flash／FlashX 的不同写法及 Kimi K3。

Flash、FlashX、Pro、Lite、Air、视觉／图像专用型号等语义标记独立判断，不通过编辑距离猜型号。DeepSeek 的明确代际须属于当前 V4 系列；Gemini／GLM／Kimi 的新核心代际仍需新增已核对的价格和对应规则。多个基础型号、未知核心代际或冲突变体保持无法确定，不能将新型号套用旧价。`gemini-3.8-high` 缺少 Flash／Pro 依据时仍不认成 Flash。实际返回的 `gemini-3.8-flash-n` 及其供应商、日期和强度组合按 Gemini 3.8 Flash 参考价计算，不把请求模型 magpie 全局绑定到 Gemini。

返回模型按原有 response ID／逐次调用关联后优先匹配，不把一次 `magpie → Gemini` 观测永久绑定到所有 magpie。Codex 无明确可识别返回时可用已知请求模型作参考；Claude 继续保留返回模型缺失或无法识别时费用缺失的保护。Claude 配置 label 的别名沿用来源隔离、生效时间和有界历史，只影响确认后的记录。原模型、来源、Token 和原始日志不改写；规范型号仅写入选用的 price_rates。

其他组支持 Gemini 4 Carbon／Argon／Hydrogen、Gemini 3.8 Flash、DeepSeek Flash／Pro、GLM 5.3／5.3 Flash／5.3 FlashX、Kimi K3。GLM 仅 5.3 起、Kimi 仅 K3 起；Flash 与 FlashX 独立计价。新型号仍需核对具体官方 Standard 单价，不用品牌通用价或模糊匹配。

DeepSeek 主表使用峰值价：Flash 输入／缓存读取／输出分别为 $0.30／$0.006／$1.20，Pro 为 $1.32／$0.044／$3.96，每百万 Token。按调用发生时的北京时间判断：非法定节假日的周一至周五 09:00–12:00、14:00–18:00 为峰值，其他时间 ×0.5；调休周末仍按周末处理。内置国务院已发布的 2026 年假期日历，日历是价格规则依赖；未覆盖年份的潜在节假日峰值时段保留参考估算属性。

Gemini 4 Argon（gemini-4-argon）使用官方 Standard 参考价：2026-11-30（含）之前每百万 Token 输入 $2、缓存读取 $0.10、输出 $10，2026-12-01 起分别为 $4／$0.20／$20。日期切换沿用项目已有 Gemini 规则的 UTC 边界，即 2026-12-01T00:00:00Z。只支持隐式缓存，缓存写入价格保留缺失，不虚构显式缓存写入费用。带 Low／Medium／High、供应商与日期修饰的 Argon 型号复用已有结构化识别；同时含 Argon 与 Carbon／Hydrogen／Flash 的冲突名及未核对代际不借用 Argon 单价。保持原始模型标识、客户端来源和 Token 不变，第三方返回模型仍按实际返回型号匹配。此处沿用“其他”组 Standard API 参考估值，不把 Gemini Priority／Flex／Batch 价格当成 Codex Fast 倍率。[官方型号](https://ai.google.dev/gemini-api/docs/models/gemini-4-argon)、[官方定价](https://ai.google.dev/gemini-api/docs/pricing#gemini-4-argon)。

Gemini 4 Carbon（`gemini-4-carbon`）已于 2026-11-13 核对 Standard 单价。每百万 Token 的输入、缓存读取、输出价格在 2026-11-30（含）之前分别为 $2／$0.10／$10，2026-12-01 起为 $4／$0.20／$20。复用 Argon 的 `price_changes`，以调用时间和 `2026-12-01T00:00:00Z` 边界计价，优惠期内的调用继续按当时费率计算。Google 价目表提供生效日期，UTC 边界沿用本项目已有口径。[Google 官方定价](https://aistudio.google.com/docs/pricing#gemini-4-carbon)

Gemini 4 Hydrogen（`gemini-4-hydrogen`）的 Standard 输入、缓存读取、输出单价分别为 $1.50／$0.075／$7.50，每百万 Token。截至 2026-11-13，官方未列出优惠截止或后续调价日期，因此目录采用该费率。Carbon、Hydrogen 均只支持隐式缓存，缓存写入价格显示 `—`。两者复用已有供应商、日期、上下文和思考强度修饰的结构化匹配，并分别保留型号边界。[Google 官方定价](https://aistudio.google.com/docs/pricing#gemini-4-hydrogen)

Gemini 3.8 Flash 当前输入／缓存读取／输出为 $0.75／$0.075／$3.75，每百万 Token；官方列出的 2027-01-01 起价格在相同调用时间边界乘 2。按用户最新确认口径，“其他”组模型统一使用 5 分钟缓存写入参考价；Kimi K3 固定为 $3／百万 Token，日志即使包含 1 小时分项也按写入总量使用 5 分钟价。该口径按模型分组生效，在 Codex／Claude Code 接入时一致；原始分项不改写。其他表格只展示 5 分钟写入列，Kimi 不再展示 1 小时倍率或感叹号；官方 1 小时价仍留在原始价格资源供归档核对，不参与该组计价。Claude 官方模型继续按已记录的 5 分钟／1 小时分项分别计算，缺分项沿用有估算标识的默认档；不重复计算缓存 Token。缓存存储不是缓存写入，本次不展示或计入按存储时长收取的独立费用。Claude 美国区域、Batch、Gemini 专用服务档位均不纳入本次规则。

## 汇总范围与不完整数据

Mac／Windows 的费用、Token、用户请求及缓存命中率按当前已采集来源和所选时间范围汇总，不以当前登录的订阅账号筛选；上方额度与订阅资料仍对应当前账号。A、B 两个账号在同一已采集范围内分别产生 $500 与 $1,000 时，汇总费用为 $1,500。不会用当前账号反填缺少身份信息的历史记录。

汇总复用 v0.2.10 `usage_queries.py` 累加已知指标的口径：费用仅累计可计价调用，总 Token 仅累计完整且有效的总量，缓存命中率只用输入与缓存读取均有效、且缓存读取不大于输入的同一组调用计算。缺少某项指标仅跳过该指标；真实用户请求仍按请求归属计数。所有值均缺失时保留 `—`，有效的零值仍为 `0`。逐条日志与请求详情继续保留缺失状态，不改写原始计量、不为填补历史重采数据。

数据不完整提醒可以关闭；关闭状态仅保存在主应用进程内，跨页面、Agent、时间范围、主窗口关闭重开均有效。完全退出 Codexio 后再次启动才允许重新提示，不持久化为永久偏好。关闭提醒不影响错误与同步状态的正常处理，不改变统计和数据质量元信息。

## 复用与失效

- 价格内容签名沿用 v0.2.10 `pricing.py` 的稳定规则：实际单价、倍率、别名及节假日规则进入版本，核对时间、来源链接和 UI 文案不进入版本。
- Mac 复用 `UsageSnapshotCache.swift` 的逐调用缓存及 `ClaudeLedger.swift` 的已索引账本重新计价；不重新扫描原始日志。
- Windows 复用 `observations.go` 的 response ID 有界批次、`ledgerFileIdentity` 的文件身份和现有 revision 表。活动转发写入仅排队受影响的 ID，由已有工作队列更新派生计价；普通 WAL 检查点没有业务 revision 变化时复用结果。启动或外部替换无法确定变化集时使用一次有界分页修复。
- 两端浮窗复用已有详情浮窗的锚点／内容区域共同关闭机制，显示只读取已准备的文案，不访问数据库、网络或触发扫描。

客户端型号依据：[Codex](https://learn.chatgpt.com/docs/models)、[Claude Code](https://support.claude.com/en/articles/11940350-claude-code-model-configuration)。

官方依据：[Gemini](https://ai.google.dev/gemini-api/docs/pricing#gemini-3.8-flash)、[DeepSeek](https://api-docs.deepseek.com/quick_start/pricing/)、[GLM](https://docs.z.ai/guides/overview/pricing)、[Kimi](https://platform.kimi.ai/docs/pricing/chat)、[2026 年假期](https://www.beijing.gov.cn/zhengce/zhengcefagui/202511/t20251104_4258873.html)。
