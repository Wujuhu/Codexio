# 上游检测

上游检测默认关闭，macOS 和 Windows 复用代理、采集、恢复和界面协调器，仅客户端关闭／重开的实现随系统区分。版本保持 0.2.5。

## 数据与路由

实现参照 CC Switch v3.20.3 的官方 Codex 透传模式：创建临时 `codexio-upstream` provider，`name = "OpenAI"`、`requires_openai_auth = true`、`wire_api = "responses"`、`supports_websockets = false`。开启前通过原生 CLI 的 `login status` 确认 ChatGPT 登录。转发目的地固定为 `https://chatgpt.com/backend-api/codex`，保留客户端生成的 Authorization、ChatGPT-Account-ID 等官方请求头，不跟随上游重定向。请求流与 SSE 均流式转发；TLS 校验开启并随包带入 CA 根证书。

只监听 `127.0.0.1`，配置中加入随机路由令牌；控制接口使用独立令牌。恢复日志和运行描述文件位于 Codexio 数据目录的 `upstream/`，按当前用户权限保存。仅接管默认官方路由；不覆盖第三方 provider、修改路由的 profile 或 API 登录配置。配置写入使用临时文件和原子替换。恢复时比较归属，保留用户在期间作出的模型、功能等其他修改；冲突时保留转发和恢复日志，不盲目覆盖。

从 `response.created`、`response.completed`、`response.incomplete`、`response.failed` 或普通 JSON 响应提取响应 ID 与 `model`，最终响应优先。SSE 观察缓冲上限为 2 MiB，落库由独立队列处理；解析、数据库或磁盘错误不改变转发的响应。`upstream.sqlite` 仅保存响应 ID、模型、事件类型、优先级和观察时间，不记录请求正文、响应正文、OAuth 凭据或认证头。

界面通过响应 ID 将记录关联到原始调用，按用户请求展示时聚合其成员。没有关联就不显示检测标签。子代理只有经过同一路由、且有可关联响应 ID 才会显示。HTTP/SSE 会承接原先可用 WebSocket 的调用；不宣称获取未在官方响应中披露的真正内部模型。

## 生命周期

`upstream_detection_enabled` 记录用户偏好，代理是否采集另有运行状态。开启、关闭、退出都由共享协调器串行执行；普通设置更新不触发切换。

| 操作 | 配置与客户端 | 保存的开启偏好 |
| --- | --- | --- |
| 首次开启并确认 | 代理就绪后写路由，再自动重启正在运行的客户端 | 开启成功后为 true |
| 下次启动且确认 | 修复遗留状态后开启，再自动重启 | true |
| 取消本次启动提示 | 保持直连，不重启 | true |
| 手动关闭 | 停止采集、恢复配置、重启已运行客户端 | false |
| 正常退出 | 可取消／抑制提示；恢复直连、重启客户端 | 保留 |
| 仅关闭主窗口 | 后台照常运行 | 保留 |
| Codexio 异常退出 | 守护进程恢复配置，旧客户端继续纯转发；不强制重启 | 保留 |
| Codexio 自更新 | 独立运行副本继续服务，120 秒内由新版认领 | 保留 |
| 系统退出／注销 | 恢复配置，不重新打开客户端 | 保留 |

只有识别到正在运行的桌面客户端才会重启。macOS 使用官方 bundle ID 与主程序路径，先正常退出再必要时终止已识别进程；Windows 排除 CLI 和 Electron renderer，以 GUI 资源识别主程序，先发送 WM_CLOSE，再必要时结束已识别进程。MSIX 应用优先用原进程 AUMID 重开，普通安装按原 EXE 路径重开。不会结束独立终端中的 Codex CLI；这些进程仍使用旧地址时，守护进程可继续纯转发。

没有操作系统级的开机恢复任务。若整个进程树被强制结束或机器突然断电，退出恢复无法执行，下一次启动 Codexio 会根据私有恢复记录修复。这一边界不能依靠已被结束的进程消除。

## 开发验证

旧的专项测试文件和脚本已移除。日常开发仅保留程序启动、基本数据显示、主窗口关闭与重新打开三个最小冒烟检查，不再新增测试文件或扩充测试项；具体约束以 `AGENTS.md` 为准。

真实客户端退出／重启属于有影响的操作，自动测试不会结束开发者当前的 ChatGPT。Windows 原生进程和 MSIX 重启仍需要在 Windows 机器上进行实际验证及 `build_exe.ps1` 打包；不能用 Mac 上的模拟检查替代 Windows 验收。

参考实现：[CC Switch 官方路由](https://github.com/farion1231/cc-switch/blob/v3.20.3/src-tauri/src/codex_config.rs)、[CC Switch 请求转发](https://github.com/farion1231/cc-switch/blob/v3.20.3/src-tauri/src/proxy/forwarder.rs)。
