# Codexio 计价规则

核对日期：2026-10-13。当前本地开发版本为 0.3.9。本次仅修改 Mac、Windows 与共用参考计价代码，版本不递增，不推送或发布。

费用是本地 Token 账本的美元参考估值，不是 ChatGPT 订阅实际扣款或官方额度百分比。普通输入、缓存读取、缓存写入、输出使用已核对的 API Standard 基础价，再按请求的认证与消费来源应用规则。上游额度百分比、重置时间仍直接读取，不由费用推算替代。

## GPT-6.1 Sol 的消费来源与倍率

API Standard 基础价（美元／百万 Token）为输入 2.00、缓存读取 0.10、缓存写入 2.50、输出 10.00。两个价格种子中的基础价相同，手工 Standard 基础价仍优先；接口、套餐、速度倍率不作为新的基础价。

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

来源不明的 OpenAI／旧 codexio-upstream 记录按用户明确口径统一按订阅计价，不因认证／消费来源缺失标为 `estimated`；内部记录采用的 `pricing_mode`／`assumed_billing_source`，原始认证与消费字段保持缺失。这个默认规则是用户选择的统计口径，不表示从日志恢复了真实身份。明确 API Key／credits 的记录按自身规则计价。第三方 provider 继续按原有参考规则估算，只有模型 ID 与目录精确匹配时才有价格。缺失 Token 或基础价保持缺价，不用零费用代替。

`billing_source` 是 Codexio 的可选归一化字段，不是声称官方日志已经提供该字段。只识别明确的 subscription／included allowance、credits／paid credits、API Key 值；`usage_source=local/cloud/codex`、账户余额或当前登录状态不能当消费来源。真实新请求清除上一请求的身份，后到的同轮上下文仍保留本轮明确身份，任务设置仅作用于后续请求。

## 计算、目录与失效

```text
普通输入 = 输入总量 I - 缓存读取 C - 缓存写入 W
费用 = [(I-C-W)×输入价 + C×缓存读取价 + W×缓存写入价 + O×输出价] / 1,000,000
```

推理 Token 已包含在输出 O 中，不重复计价。价格页中的 GPT-6.1 Sol 行展示订阅 Standard／Fast／Ultrafast 参考价，不再重复显示超过 272K 的订阅档位；逐调用 API／credits 计算从原始 Standard 基础价派生，不再从已经乘过倍率的目录行再次相乘。

共同种子中的 `billing_policies.gpt-6.1-sol` 保存各消费来源阈值、速度、缓存写入口径与未知来源默认值；Mac、Windows 和 Python 参考实现使用相同数值。实际基础价与规则进入价格版本，抓取时间、核验日期与来源链接不进入版本。相同单价同步不会重复计价或覆盖版本归档。

价格版本变更复用现有已索引账本的派生统计失效与重新计价，原始 Token、请求归属和日志游标不变，不重新采集整个历史。金额、请求、模型汇总、趋势与桌面同步继续共用已有本地计价结果。

## 依据与验证边界

- [Tibo 关于订阅长上下文费用的原帖](https://x.com/thsottiaux/status/2109430894604026039)：北京时间 2026-10-12 07:48，GPT-6.1 Sol 从订阅额度计量时不收长上下文附加费；通过 X 公开嵌入／syndication 接口核对原文。
- [GPT-6.1 Sol 官方 API 模型页](https://developers.openai.com/api/docs/models/gpt-6.1-sol)：API Standard 基础价、272K 阈值及整次调用倍率。
- [Codex / Work 速度说明](https://learn.chatgpt.com/docs/agent-configuration/speed)：订阅 Fast／Ultrafast 2.5／8，购买 credits 与 Enterprise pay-as-you-go 2／6；API Key 独立计价。
- [官方 Codex 记录 schema](https://github.com/openai/codex/blob/740e5af33c71225640e0c1c1555c514c2c93ab74/codex-rs/protocol/src/protocol.rs)：已有缓存 0.162.0-alpha.17.2 的逐调用身份字段限制，不据此承诺所有客户端版本的字段覆盖。

不新增维护性测试文件或冒烟项，不运行 pytest／全量回归。使用原有三项隔离冒烟和交付核验，必要的算术检查仅针对本次规则，不读取真实配置、认证或聊天内容，不启动用户安装的应用。
