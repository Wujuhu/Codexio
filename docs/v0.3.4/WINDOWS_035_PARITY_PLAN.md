# Windows 对齐 Mac v0.3.5 通用修复

> 执行方式：复用现有 Wails + Go / Svelte 架构，按职责拆分修改并统一集成、开发打包和本地提交。用户本轮完整需求为实施依据；只补缺失或错误行为。

**目标：** 保持 Windows 0.3.4，为真实用户请求提供正确归属、图文详情和兼容手机协议的图片同步，并交付本地 amd64 EXE。

**参考：** 只读获取 `origin/main` 的 `f59e32c`；`docs/v0.3.5/DEVELOPMENT.md`、`macos/native/RequestResume.swift` / `Analytics.swift` / `RequestMedia.swift` / `MobileImages.swift` / `MobileSync.swift`、`apple/shared/MobileProtocol.swift` 和 `cloudflare`。参考副本在 `build/staging/macos-v0.3.5-f59e32c`。不合并整个 Mac 分支。

## 约束与现状

- 保持当前 Windows 版本、既有配对/鉴权/原始账本与计价；不修改 Mac/iOS、菜单栏、Dock、小组件、接管或更新器。
- 现有 Windows 已有明确父子 turn 图、模型切换续接、按请求恢复正文、SHA-256 和 64 KiB 详情分片；保留并扩展这些机制。
- 已确认缺失：普通 interrupted 续接和部分祖先归属；Markdown 明确禁用了图片；没有 `request-images-v1`；月份标签用固定宽度；外观页重复侧栏开关；云端与通用错误共用字段。
- 图片原始时间 + 3 天，正文请求开始/完成时间 + 7 天；重试与重启不能续期。读取立即拒绝过期内容，物理清理分批；本机原始日志和统计不删改。
- 图片预览最长边 2048，编码图片不超过 1 MiB，图片 JSON envelope 不超过 1,500,000 字节；分片 65,536 字节。保持实际 Swift/Worker 字段与 ID 规则。
- 不新增测试文件/测试项，不运行 pytest、全量回归或截图矩阵。统一前端检查、编译、原有启动/数据/关窗重开三项隔离冒烟及交付哈希核验。针对发现的问题只作必要核对。
- 不启动、关闭或覆盖正在运行的用户 EXE；不推送、发布、迁移或部署生产云端。

## 实施清单

- [x] 请求归属：`requests.go`、`collector_resume.go`、`collector_replay.go` 及 `collector.go` 的归属入口；按祖先线程/turn/明确 interrupted 证据判断，遇新用户输入断开。原始计量 ID/内容保持不动，历史元数据补修按有界游标推进。
- [x] 消息图文：新增 `request_media*.go`，复用 `messages.go` 的恢复和组成员聚合；Markdown/HTML/结构化/文件包装/data URI/工具结果统一提取，代码示例不执行。用户与最终回复各自引用图片，附件去重，保留非图片文件。
- [x] UI：`Markdown.svelte` / `Details.svelte` 支持按需图片和自适应表格；`ActivityCalendar.svelte` 测量标签并保留两端，`Settings.svelte` 仅移除重复开关。
- [x] 服务集成：`Store.media` 为请求来源受限的图片缓存；`GetDetails` 只返回正文和图片引用，`GetRequestImage(requestID,imageID)` 按引用取受限图片，不接受任意路径。
- [x] 手机同步：`mobile_images.go` 维护落盘传输队列，复用 `prepareRequestImages(ctx, raw, requestID, started, enforceRetention)`；公开引用 `{id,name,mime,placement,width,height,expires,availability}`，图片 payload `{id,request,mime,width,height,created,expires,data}`。局域网沿用 reader 鉴权，云端协商后上传；旧 Worker 不接收 `images` 新字段。
- [x] 同步状态/保留期限：错误按局域网、云端归类并在对应成功后恢复；保留权限/凭据/配置错误。请求正文、图片、recent/live 摘要分别按原始期限检查；保留待上传内容与重试记录，不因内存缓存淘汰丢失。
- [x] 云端准备：带入参考实现的 `0003_request_images.sql`、Worker/schema/config 与部署说明，只做本地语法和内存 SQLite 迁移核验。生产带鉴权能力查询 HTTP 200，只返回 `request-kinds-v1` / `request-details-v1`，没有图片能力；用实际协商结果控制发送。
- [x] 本轮追加的系统代理修复：云端同步、图片下载、价格同步和上游转发均复用 Windows 系统代理；手动系统设置优先于继承的环境变量。独立 Go 诊断清空全部代理环境变量后，仍通过系统设置 `127.0.0.1:7890` 收到生产健康接口 HTTP 200。没有改写系统代理或 Codex 配置。
- [x] 集成检查、原有最小冒烟、EXE 开发打包、源码指纹/版本/架构/SHA-256 核验；与本轮修改一并本地提交。

## 审阅重点

新用户消息与上下文包装的边界；子代理祖先候选唯一性；图片生成结果跨事件累积；手机 schema/UTF-8 分块/digest 一致；能力升级/降级后的 revision；源内容生命周期与重启后的待上传队列；临时云故障不污染局域网状态；源码准备不被描述为生产已部署。

## 暂缓项

紧凑账本与 SQLite 分页重构；没有同条件证据的整体内存优化、数字动画变更和性能结论。仅随图片实现采用按需加载、关闭详情释放前端资源和落盘传输队列，不增加强制 GC、额外采集服务或定时器。

## 集成审阅修正

- 详情成员与 prompt 来源使用已解析的同一请求归属，恢复后重新选择，避免账单已分开而正文仍串入下一条用户消息。
- 只有图片包装的新用户输入，也通过共享的无 I/O 判定打断自动续接；环境与内部包装仍排除。
- 图片加载按引用计数共享，最后一个消费者取消才中止底层请求；取消不进入失败缓存，仍存活的等待者可重试。
- 云端 ACK/冲突返回的版本作为局域网详情的下界；云端降级丢弃图片字段时，本地版本严格更高，避免手机回到局域网仍复用无图缓存。
- 图片字节和所属请求快照/恢复指针都写入独立的 SQLite 待办表；来源暂不可用也保留重试期限。显示缓存淘汰不删除待办。请求后来不含图片时，同步新的无图正文并沿用原截止时间，不无限重传旧快照。
- 本轮能力新增 `golang.org/x/image v0.43.0`，其余依赖版本保持。可解码 PNG/JPEG/GIF 静态帧/WebP/BMP/TIFF；SVG、HEIC/HEIF、AVIF 及无法访问的 sandbox/API 标识保留引用并显示不可用，不伪造图片。

## 开发交付与验证

- Windows 版本：0.3.4；架构：amd64 / x64，PE Machine `0x8664`；未签名开发 EXE。
- 文件：`build/dev/windows/pending/Codexio.exe`；大小：46,460,416 字节。
- SHA-256：`5c9fb9393a043ab8baacadb70288bab4255d8fc0b4bd82e1ef665fc8b5e3eca8`。
- 当前开发目录中的旧程序仍在运行，构建流程保留其原路径。用户需从托盘退出旧程序后再打开新包，否则同版本单实例机制会唤起旧实例。
- 最终前端检查 0 errors / 0 warnings，生产构建及原有三项隔离冒烟通过；源码指纹、EXE/清单哈希、版本及架构一致。记录在 `build/checks/v0.3.4/delivery.json`。
- 云端源码 `node --check` 通过；内存 SQLite 新建 schema、重复执行图片迁移及外键检查通过。没有新增维护性测试或测试项，没有真机 iPhone 图片传输验收。
- 系统代理诊断复用最终 Go 代理函数，只在独立诊断进程清空环境变量；Windows 手动系统代理选中 `http://127.0.0.1:7890`，生产健康接口 HTTP 200。记录在 `build/checks/windows-035-parity/system-proxy.json`。
- 2026-10-01 20:34（Asia/Shanghai）生产能力只读查询 HTTP 200，返回正文与请求类别能力，尚未提供 `request-images-v1`。记录在 `build/checks/windows-035-parity/cloud-capabilities.json`。需另行授权后核对生产 schema、应用 `0003_request_images.sql`、部署 Worker 并启用图片配置/清理调度；不将本地源码准备视为已部署。
- 原始调用守恒的结论来自代码路径审阅：补修不重放计量、不改原始日志；未另外运行真实账本前后对比或套用 Mac 内存测量。紧凑账本/分页重构及无证据的整体内存优化继续暂缓。
