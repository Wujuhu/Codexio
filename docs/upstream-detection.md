# 上游检测（0.3.9 开发版）

默认关闭。Mac 使用主 App 内的原生转发，Windows 使用主程序内的 Go ReverseProxy。采集随主程序退出而停止；关闭窗口可继续后台运行。开发包仅进入 `build`，Windows 在指定本机构建主机完成，正式发布仍需用户明确指令。

## 保留原客户端的提供者与路由

开启检测只临时改写当前有效提供者的 endpoint：内置 `openai` 使用 `openai_base_url`，自定义服务使用 `model_providers.<id>.base_url`。由 Codex app-server 的 `config/read` 解析有效配置和可写用户层，以配置版本保护写入；不更换 provider ID，不改认证文件、账户、模型、工具或其他客户端设置。配置被高优先级覆盖或并发修改时拒绝声称接管成功。内置 Bedrock、Ollama、LM Studio 及非 Responses wire API 不接管。Codex 的配置合并不会让 `model_providers.openai` 覆盖内置提供者，因此检测也不采用该表中的假覆盖。

启用前的实际地址为唯一上游：ChatGPT 默认 `https://chatgpt.com/backend-api/codex`；API Key 默认 `https://api.openai.com/v1`；显式 endpoint 保留原地址。账户模式由 `account/read` 的实际类型及有效配置确定，不按模型名称或环境变量中是否存在另一份 API Key 猜测。

本机地址始终为 `http://127.0.0.1:<端口>/<随机令牌><本地后缀>`。本地后缀按官方 `ModelProviderInfo::supports_codex_backend_routes()` 的现有条件选择：提供者名称为 `OpenAI` 且原始地址去掉末尾 `/` 后以 `/backend-api/codex` 结尾时，保留这个后缀；其他地址使用 `/v1`。这也覆盖本来具有该语义的同名自定义提供者，不为其他自定义服务新增 ChatGPT 语义。

核对版本为安装的 Codex `0.162.0-alpha.17.2`（提交 `740e5af33c71225640e0c1c1555c514c2c93ab74`）及调查时的 main（`806d9732c974bc8a51b8317c1bd8985544fe627c`）。该谓词的五处调用涉及 Codex 附加 Responses 请求头、符合资格的 `guardian_credits_requested`、experimental context/history notes、ChatGPT 工作区路由及 Guardian classifier 模式，各调用仍有自身的认证和功能条件，不能推断所有请求功能都由 URL 后缀决定。

参考：[官方路由谓词与 provider 合并](https://github.com/openai/codex/blob/740e5af33c71225640e0c1c1555c514c2c93ab74/codex-rs/model-provider-info/src/lib.rs#L616-L733)、[Responses 请求设置](https://github.com/openai/codex/blob/740e5af33c71225640e0c1c1555c514c2c93ab74/codex-rs/core/src/client.rs#L1108-L1161)、[工作区路由](https://github.com/openai/codex/blob/740e5af33c71225640e0c1c1555c514c2c93ab74/codex-rs/model-provider/src/provider.rs#L216-L250)。工作区绑定还受官方 app-server 的检查约束；保留谓词不能保证所有工作区分支与远程直连完全相同。

路径只剥离一次本地前缀，再接到原始上游前缀。保留百分号编码、重复查询键与 `+`，不解码后重编码、不拼接两次上游路径；请求不能指定另一目的地。源地址含用户名、密码、query 或 fragment 时不接管。

## HTTP/SSE 与 WebSocket

透传真实客户端提供的 Authorization、ChatGPT-Account-ID、User-Agent、originator、version、会话／线程标识和 `x-codex-*` 等业务头。不把桌面客户端改称 CLI，不伪造固定版本；正文中的模型、推理强度、速度、instructions、工具定义、previous_response_id、prompt_cache_key 均不重写。协议必须的 Host、长度及 hop-by-hop 头由传输层处理。上游状态、错误正文、限流和会话路由响应头继续交给客户端，重定向不由代理自动跟随。

Windows 允许上游 HTTP/2 协商，关闭 Go 自动压缩，及时刷新 SSE，不自动追加 loopback 的 X-Forwarded-For。上游响应头等待时间为 10 分钟。已有客户端提供的 X-Forwarded-For 也被这个 nil-header 机制省略；它不是账户或会话标识。

Mac 使用无 Cookie、凭据存储和响应缓存的 ephemeral URLSession。HTTP 101 转换为 URLSessionStreamTask，禁用 HTTP pipelining，分别转发两个方向的原始字节；握手尾部已经收到的客户端首帧被保留。101 不使用普通 HTTP 的 chunked/close 包装；Windows 在 Hijack 后继续读取原预读缓冲，避免遗漏随请求头到达的首帧。分片、ping、pong、close 不重新编码，任一取消或异常会清理连接和任务；普通 HTTP/SSE 和尚未完成的 WebSocket 握手均有客户端断连监控，输出采用背压。Mac 普通 HTTP 使用 Connection: close，收到客户端 FIN 视为取消，不支持请求方向的半关闭或请求流水线；已建立的 WebSocket 支持双向 EOF 并先排空最后的字节。

仍保留两项有意差异：`Accept-Encoding: identity`，以及不向上游协商 `Sec-WebSocket-Extensions`。观察器不能解释扩展压缩帧，保留这些限制避免响应损坏或误采型号。若上游仍返回 URLSession 支持的压缩编码，Mac 传输层会自动解压；转发有效的解压表示时移除原压缩头，并跳过该次压缩响应的型号观察。Windows 不自动解压，保留实际编码及字节并跳过压缩响应观察。代理不承诺 TLS、连接复用、HTTP 协议协商或网络特征与直连逐字节一致，也不能保证服务端无法识别代理。

Mac 的原生升级机制参考：[becomeStream](https://developer.apple.com/documentation/foundation/urlsession/responsedisposition/becomestream)、[didBecome streamTask](https://developer.apple.com/documentation/foundation/urlsessiondatadelegate/urlsession(_:datatask:didbecome:)-7nqzu)、[URLSessionStreamTask](https://developer.apple.com/documentation/foundation/urlsessionstreamtask)。已经用本机合成服务器核对 101 后立即到达的数据及先读后写的双向通信；不能据此代替真实账户的服务端验证。

## 只读型号观察

只从上游实际返回的响应事件或 Responses JSON 中提取 response ID 与 model，再按 response ID 关联本地计量调用。只接受明确的响应创建、完成、不完整或失败事件；缺少 ID 或 model 时不猜测。创建事件不能覆盖终态观察，相同观察不反复增加数据库 revision。费用、筛选及请求配置仍以本地请求记录为准。

HTTP 的 SSE/JSON 单事件及 WebSocket 单消息观察上限为 2 MiB；Windows 帧片段缓存额外限制在 4 MiB 内，Mac 流式解析只保留有限帧头和单消息；异常、过大或无法解释的事件只丢弃观察，不改变转发字节。SSE 支持多行 data。Mac 后台观察队列最多 64 项／8 MiB，Windows 队列最多 64 项且仅保存已经提取的有限字段；数据库忙或队列满不阻塞网络转发。历史关联保存在独立 `upstream.sqlite`，不保存正文、认证头或凭据。事件正文只在有界内存中暂留，解析后释放。

检测证明的是“服务端响应披露的型号”，不能证明服务端内部实际执行的模型、调度、蒸馏或转发方式。不同响应各自关联，不按聊天名或时间邻近合并。

Magpie 的订阅提供者适配会主动构造身份和正文，不能复用到此只读代理；其真正 Codex 客户端接入路径保留 `/backend-api/codex` 是可参考的语义，但它通过 426 回退 HTTP 的做法不能证明透明 WebSocket 已实现。参考调查提交 `f1fc8c65d3f50dc99032afe229544a6779c126bb` 的 `internal/provider/codex_request.go` 与 `internal/gateway/codex_backend.go`；本次实现优先复用 Codexio 原有转发、恢复和观察机制。

## 配置恢复与旧地址

恢复日志保留原 endpoint、临时 endpoint、字段位置、摘要和上游，不复制整份配置。Mac 保持 v3 恢复记录，Windows 保持 go-rpc-1，并继续读取已有旧格式；修改本地后缀不会使已有摘要失效。新记录另外记下 local_path。恢复时仍比较实际字段和原摘要，保留无关编辑；冲突则报告并保留恢复资料和转发。

升级或重新开启时，先校验和恢复旧记录。原提供者及上游相同且旧 loopback 地址有效时尝试复用端口与令牌；端口不可用时保留占用者并选择新端口。新代理同时接受旧 `/<令牌>/v1` 别名，使仍加载旧地址的客户端可以继续转发；别名不会把客户端内存中的后端功能判断改成新值。旧端口不能复用、上游改变、检测被关闭或主 App 退出时，旧客户端仍需自行重新打开。

`config/read` 确認的是磁盘有效配置，不能证明另一个运行进程已加载。状态明确区分检测配置已更新与客户端生效；沿用已有稍后自行重启／用户主动重启选择，不自动重启真实客户端。开发验证不读写真实配置、不启动已安装应用、不结束用户进程；强制退出后的恢复只在下次正常启动处理，不新增守护程序。

## 本次开发验证边界

不新增维护性测试文件，不运行全量回归。开发包仍只执行原有三个隔离基本冒烟事项：程序启动、基本数据显示、主窗口关闭与重新打开。另在 `build/checks` 保存一次性合成路由／协议核对，覆盖本次改变的前缀、编码、业务头、正文、SSE、WebSocket 及异常清理；不使用真实聊天或认证信息。Mac 必须在本机编译打包，Windows 必须在指定主机交互会话执行已有构建入口，结果和产物保留后再复制回本机。
