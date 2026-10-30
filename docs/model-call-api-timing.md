# 模型调用 API 返回字段核对（2026-10-30）

这份记录区分公开响应协议、本机客户端可识别的候选字段与实际落盘记录。不将“文档存在字段”或“二进制包含字段名”当作每次请求都返回该值的证据。本轮未修改路由、启用遥测、解密 HTTPS 流量或发起付费模型探测。

## 公开协议

| 协议／载体 | 可核对字段 | 可以表达什么 | 不能直接表达什么 |
| --- | --- | --- | --- |
| OpenAI Responses 响应体／终态事件 | id、status、created_at、completed_at、usage.output_tokens | 同一响应的服务端生命周期；完成且两端时间有效时可求时间差 | 首 Token 延迟、纯模型解码时长、客户端网络往返时间 |
| OpenAI HTTP 响应头 | openai-processing-ms、x-request-id | 请求处理耗时信息与排障身份 | 不能未经口径核对直接用作整段流式生成耗时或 TTFT |
| Gemini Developer API GenerateContentResponse | responseId、modelVersion、usageMetadata、candidates | 响应身份、实际模型、Token 及候选结果 | 当前公开结构没有逐次完整的开始／完成／首 Token 计时字段 |
| Google Cloud GenerateContentResponse | createTime、responseId、usageMetadata | 另有请求的服务端时间戳 | 只有一个时间戳不能求总耗时或 TTFT；不能将它反填到 Developer API 响应 |
| Claude Messages 响应 | id、model、stop_reason、usage；usage 含 Token／缓存／speed／service_tier | 身份、结束原因、用量和服务档位 | 当前公开响应结构未保证总耗时、TTFT 或 TPS |

依据：[OpenAI Responses 事件](https://developers.openai.com/api/reference/resources/responses/streaming-events)、[OpenAI HTTP 头](https://developers.openai.com/api/reference/overview#debugging-requests)、[Gemini Developer API](https://ai.google.dev/api/generate-content#GenerateContentResponse)、[Google Cloud 响应](https://docs.cloud.google.com/gemini-enterprise-agent-platform/reference/rest/v1/GenerateContentResponse)、[Claude Messages](https://platform.claude.com/docs/en/api/messages/create)。

时间差还受字段精度、终态是否完成、响应内是否包含排队或服务端工具等因素影响。这里的差值是响应生命周期口径，不能改称纯推理时间。尤其流式 HTTP 头在后续正文之前就发送，不能仅凭头中的处理时间断言它包含整个后续生成过程；这是对 HTTP 流式时序的限制分析，并非官方承诺的具体计时边界。

TPS 需要同时具备输出 Token 和明确的时间分母；输出 Token／整次响应耗时得到整次调用的平均输出 TPS，与排除首 Token 等待后的解码速度不同。TTFT 需要明确的首 Token 计时或经过定义和验证的实时事件时钟；首个元数据事件、完整消息、输出项完成和首字节均不能直接冒充首 Token。

Gemini 的 usageMetadata 还将候选输出 candidatesTokenCount 与思考 thoughtsTokenCount 分开列出，totalTokenCount 还可能包含工具提示。不能把它原样套成另一协议的 output_tokens 口径；现有 Codex／Claude 账本的 reasoning_output_tokens 已是输出子集，不重复相加。

## 本机 Codex 客户端与落盘证据

核对的本机版本为 codex-cli 0.166.0-alpha.5.1。二进制包含 timing_metrics、engine_service_total_ms、engine_service_ttft_total_ms、engine_iapi_ttft_total_ms、responses_duration_excl_engine_and_client_tool_time_ms 等名字；[官方 Codex 遥测资料](https://learn.chatgpt.com/docs/config-file/config-advanced#observability-and-telemetry)也列出响应推理／首 Token 相关指标。

这些是候选能力证据，当前不能据此确定私有线上响应的嵌套位置、字段是否每次出现、是否为多个内部 engine 调用的汇总，或是否只出现在客户端遥测中。名称带 total 的字段尤其不能未经样本核验直接归为单次首 Token 延迟；排除 engine 的 overhead 也不是模型总耗时。本轮没有把这些候选键重新接入采集或上游检测。

只检查当前线程 rollout 尾部 8 MiB 的 token_usage_record，取最近 8 条计量记录：均有响应身份和 Token 用量，没有已识别的调用耗时、开始／完成或首 Token 字段。检查严格按记录类型，不从工具输出中的示例 JSON 或本次诊断文本提取证据。详细脱敏结果在 build/checks/gemini-argon-0312/local-api-field-evidence.json。

因此，“API 协议可能含时间字段”与“Codexio 从默认本地日志取得了这些字段”是两层事实。当前仍无法保证逐次完整读取耗时／TPS／TTFT；已有明确来源值按原规则显示，缺失值保留长横杠。分析保持独立，不恢复已回退的计时与上游检测关联。

## Windows Argon 原始对象核对

默认 Windows 构建主机的本地账本存在实际返回 gemini-4-argon-e／gemini-4-argon-trial 的调用。抽取的 16 条有效用量可用本次新增的 Argon Standard 参考规则计价；不将其请求侧的其他模型标签永久绑定为 Argon，不将第三方路由尾缀当作 Google 官方计费 SKU。

另外按 claude_locators 现有文件／字节偏移定位最近 8 条原始 assistant JSONL 记录，校验 UUID 和 message.id 后只提取字段结构，未遍历历史正文。保存的 Messages 对象包含 content、id、model、role、stop_details、stop_reason、stop_sequence、type、usage；usage 元数据及其数组中均未发现明确耗时或首 Token 计时。账本的 duration_ms 为 null，不能将“键存在”统计为“有计时值”。

这些是 Claude Code 日志保存的 SDK Messages 对象，原始 HTTP 头未落在该对象中。不能排除服务器响应头或被中间转换／SDK 丢弃的扩展字段；也不能把流式连接后续完成时间补写为首 Token 时间。脱敏结构证据见 build/checks/gemini-argon-0312/argon-original-message-fields.json。

## OpenAI 平均 TPS 与通用备用方法

同一条已完成的 Responses 记录具备有效 created_at、completed_at 和 output_tokens 时，平均 TPS = output_tokens / (completed_at - created_at)。时间单位是秒；零或负时间差、缺失结束时间或未完成状态不计算 TPS。这里可以计算整次响应的平均 TPS，不能把“落盘时未保留字段”误写成“有这两个字段也不能算 TPS”。它不产生 TTFT。

Mac／Windows 的既有被动计时解析已兼容上述现成字段，规范保存调用起止时间和 duration_source=response_lifecycle，复用原耗时栏／平均 TPS。已有明确 duration_ms 或调用起止字段继续优先；不为补计时重新扫描历史，也不新增转发或改写上游配置。

Claude Code 有另一条独立的官方来源：claude_code.api_request 日志事件包含 duration_ms、output_tokens 和 request_id，可用 output_tokens * 1000 / duration_ms 计算请求平均 TPS。需要实际调用进程启用并导出相应遥测，不能认为这些事件已经写入默认会话 JSONL。[官方请求事件](https://code.claude.com/docs/en/monitoring-usage#api-request-event)

对于自己接入 Claude SDK 的调用方，官方 anthropic.messages.create span 可携带响应 ID 和输出 Token，并记录请求起止；流式 span 到流读取完毕或关闭才结束。应排除未完成或取消的流，去重 SDK／服务端双份 span，并明确是否含重试。gen_ai.response.time_to_first_chunk 是首内容块时延，不自动改称 TTFT。[SDK 追踪](https://platform.claude.com/docs/en/manage-claude/opentelemetry/sdk#tracing-a-stream)、[span 字段](https://platform.claude.com/docs/en/api/opentelemetry/spans)

最通用的备用方法是在实际调用方发送请求前与完整接收终态后，用同一单调时钟记录时长，再结合该调用最终的输出 Token。这个端到端平均 TPS 包含网络／等待等时间，不能与纯解码速度混称。不使用相邻日志间隔、整轮工具执行时间、平均分摊时长或字符／chunk 数来补造逐调用 TPS。

Claude 官方事件／span 的消费接入在本轮仅完成能力核对，尚未实现独立接收器，也没有自动开启任何遥测。现有 Windows Argon 原始 assistant 日志只有 requestRef 等关联元数据，不能未经核对将它当成官方事件的 request_id；优先验证响应 ID／请求 ID 的准确映射，不按时间邻近拼接。
