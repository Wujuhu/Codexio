# 手机阅读与同步协议

**2026-10-01 本地准备：** 本目录新增 `request-images-v1`、独立图片存储、三天图片/七天消息清理及 `0003_request_images.sql`。本轮没有执行远程迁移、部署、修改账户资源或推送 Git；下文的 2026-09-30 记录仅描述此前生产状态。新行为要在明确获得云部署授权后，先迁移再部署才会生效。

此前已于 **2026-09-30 15:12:20（Asia/Shanghai）** 按用户明确授权完成 Codexio 同步服务的 D1 详情迁移和 Worker 部署。生产端启用 `request-details-v1` 与 `request-kinds-v1`，新版本接收 100% 流量。部署源码来自本地提交 `fbb33f0`，已回读远程模块并核对源码一致；没有推送 Git、创建 GitHub Release、修改 App 版本或 Windows。

## 部署前提与兼容

1. 部署须取得用户明确的云部署授权，并使用可用的 Cloudflare 管理 API 凭据。2026-09-30 的授权仅用于下方记载的历史部署；本轮本地修改没有复用该授权执行任何远程操作。
2. 对现有 D1 确认已执行 `migrations/0002_request_details.sql`，再显式执行 `migrations/0003_request_images.sql`。新迁移使用可重复的 `CREATE IF NOT EXISTS` / `INSERT OR IGNORE`，添加图片表、生命周期表、内容到期队列、索引和清理触发器；已有 payload 不在 SQL 中直接改写，交给 Worker 有界清理并重算 digest。`schema.sql` 供新库初始化使用。
3. 再部署本地 `worker.mjs` 与配置。`DETAILS_ENABLED=1` 启用详情；`IMAGES_ENABLED=1` 且图片/生命周期表均存在时声明 `request-images-v1`。关闭能力标志不停止到期清理。新 Worker 必须在 `0003` 后部署：缺少到期队列表时摘要返回 `RETENTION_UNSUPPORTED`，不会继续接受无法履行保留期限的上传。配置将定时任务改为每 15 分钟执行一次。
4. 发布前由获授权的协调者核对实际 Worker、D1 绑定、索引、能力响应、配对与撤销行为，再使用真实 D1 返回的 `meta.rows_read` / `meta.rows_written` 判断额度。不要从本地 SQL 审计推断已部署或云端验证通过。

正文能力名为 `request-details-v1`，请求分类能力为 `request-kinds-v1`，图片能力为 `request-images-v1`。新详情可携带最多 32 个 `images` 引用；原有 `attachments` 和旧版正文协议继续兼容，Mac 未协商到图片能力时省略 `images` 和图片上传。最近记录可携带可选 `kind: approval_review`；旧手机可忽略此字段，新手机也兼容 `codex-auto-review` 模型标识。Mac 先协商能力，旧 Worker 的最近记录继续使用不含审批记录及新增字段的独立云端投影，保留摘要同步；新版 Worker 才同步独立审批记录。两条链路使用单调版本，局域网版本不会落后于已上传的云端投影。

缺少详情表或明确不支持详情时，正文保持待提供状态，Mac 显示“云端正文同步尚未启用”。暂时的能力探测失败不等于不支持，一分钟后重试；成功探测或明确不支持后每小时最多检查一次。摘要和详情仍按各自内容变化发布，普通同步不重复传输相同正文。

Mac 正文来源按请求记录实际日志片段，旧记录复用既有扫描游标补齐映射；恢复只读取已登记的相关片段，并合并同一文件的恢复目标。手机主动重试通过可选 `force` 字段通知局域网宿主在第一个分块重新读取正文，其余分块复用同一个已刷新版本。云端重试读取已上传的正文，不启动 Mac 或创建独立采集服务。

没有远程执行、任务恢复、app-server 控制或任意文件下载端点。每次读详情、图片及每个分块都沿用该 Mac 的独立 reader ID / secret 校验；主机密钥停用或 reader 撤销同样生效。局域网使用既有证书固定和配对密钥。已使用的邀请码不能重新启用停用的主机密钥，重新开启需要新的未使用邀请码。

## 数据、范围与容量

- 记录 ID 继续是规范请求 ID 的 SHA-256，不因页面入口、筛选或传输路径改变。概览任务和用户请求统计只采用真实用户请求；记录列表另含未归属自动审批审查，其用量保留但不增加用户请求数。独立上下文压缩与子任务不作为概览最近完成任务。
- 账本提供原始用户 Markdown、最终 assistant Markdown、完整性、附件元数据及实际内容的 revision/digest。中间 commentary、工具调用和工具结果不进入详情。旧截短内容不能恢复时明确标记 partial/unavailable。
- `/sync` 只含三个摘要集合，以及最多 64 个详情版本号，不携带详情正文。详情初次打开读取用户约 1,200 字节与最终回复约 5,000 字节的预览；手机界面默认约 5/20 行。
- 展开按 64 KiB 分块读取。每条记录最多 1 MiB（UTF-8 JSON，含用户正文、最终回复和附件元数据），最多 16 块；所有分块必须同一版本、同一 digest，并在整条 SHA-256 校验通过后入缓存。期间版本变化需重新读取。展开完成显示源记录实际可用的全文；源记录本就不完整时继续明确提示。
- 超过单条 1 MiB 时只保留一个明确标记 `capacity` 的预览，全文请求返回 `DETAIL_CAPACITY`。Mac 账本原文保留，不通过静默截断生成所谓“全文”。
- 云端按每台 Mac **最多 64 条、正文合计最多 8 MiB、最多 7 天** 保留，任一限制先到即生效。并不承诺最近列表的 200 条都有云端详情。写入与容量检查在同一 D1 batch 中完成；每次新写入至多淘汰一条更早记录，仍放不下时返回容量错误，不进行无界删除。主机对此版本延后重试，其余记录继续同步。正文过期固定按 `floor(max(started, completed ?? started) + 7 × 86400)` 计算，重复上传不刷新日期；到期后的读取立即拒绝，定时任务实际删除到期行。
- 云端详情缺失、过期或容量不足时，手机明确说明尚未同步/已移除。局域网可按稳定 ID 向主 App 的账本按需读取，即使该详情不在云端 64 条集合内。主 App 不在运行时不会另起常驻服务。
- Mac 内存详情缓存最多 64 条/8 MiB，轻量 revision 台账有界；手机最多 16 条/8 MiB 正文缓存，最多两个详情读取同时进行。手机退到后台取消网络、定时器与未完成分块；切换 Mac 使用 generation 丢弃旧结果，并清空当前显示集合。磁盘缓存按主机隔离，删除配对时删除该主机缓存。

以上 8 MiB 是正文 payload 上限；D1 表、索引、页空间和 JSON 外层编码还有开销，不能直接当作账单存储值。

## Markdown 与附件

使用原生 SwiftUI 文本、列表、代码区域和表格；解析在后台按文本变化执行。只允许 HTTP(S) 网页链接。本地文件链接显示文件名，图片 Markdown 不直接发起 URL 请求；代码块保持代码文本。

旧 `attachments` 继续接收最多 6 项元数据和最多 2 张、每张 12 KiB 的 JPEG 缩略图，以兼容旧 Mac。旧缩略图缺少自身创建时间，因此保守使用请求 `started + 3 × 86400` 到期。到期后 Worker 从 JSON 实际删除 `thumbnail` 字段、重算 UTF-8 字节数和 SHA-256 并递增 revision；文件名和正文仍按七天期限保留。详情读取也先执行到期处理，不能从完整分块读回已过期缩略图。

新 `images` 引用格式为 `{id,name,mime,placement,width,height,expires,availability}`，不含缩略图字节、本地路径或 URL。`id` 为 64 位十六进制稳定 ID，名称最多 240 UTF-8 字节；`placement` 为 `user` 或 `final`，`availability` 为 `available`、`unavailable` 或 `expired`。可用图片必须有正整数尺寸；缺失/过期文件可使用 `width=height=0` 表示未知尺寸并显示占位。可用图片及上传的 MIME 白名单为 JPEG、PNG、GIF、WebP、HEIC、TIFF、BMP；零尺寸的缺失/过期占位可保留最多 100 UTF-8 字节的原始 `image/*` 类型（包括 SVG、AVIF、HEIF），该元数据不放宽图片上传白名单。当前 Mac 生成单帧 JPEG/PNG 预览，Worker 检查容器头、尺寸与已识别的动画/多页标记，手机仍需通过原生解码器核对图片尺寸。没有图片解码或远程图片抓取服务。

- Writer 使用 `PUT /v1/hosts/{host}/images/{imageID}` 上传标准 envelope：`{dataset:"image-"+id,revision,digest,payload}`。payload 是 `{id,request,mime,width,height,created,expires,data}` 的 JSON 字符串，`request` 必须是所属详情 ID，`data` 为图片字节的 base64。digest 覆盖完整 payload 的 UTF-8 字节。外层 JSON 请求最多 1,500,000 UTF-8 字节，压缩图片最多 1,048,576 字节，最长边 2048、总像素最多 4,194,304。
- `created` / `expires` 使用整数 Unix 秒，来自源请求/图片的原始时间而非上传时刻；`created <= now + 300`、`now < expires <= created + 3 × 86400`。同 ID 的请求绑定、创建时间和到期时间一旦登记就必须一致。图片字节到期删除后，保留不含图片、名称或正文的少量生命周期元数据，至 `created + 7 × 86400` 删除，避免七天请求窗口内重传为同一图片续期。
- 元数据与图片可以按任意顺序上传。图片最多等待一小时与详情关联，定时任务删除未关联的图片；可读取的图片必须在未过期详情的 `images` 内有 `available` 引用，且 ID、MIME、尺寸、到期时间均与图片行一致。删除详情会同时删除图片；详情移除已有图片引用也会触发删除。迟到的新图片必须继续使用原始到期时间。
- Reader 使用 `GET /v1/hosts/{host}/images/{imageID}?reader={readerID}&part=0`。响应为 `{action:"image",imageID,imagePart,imageManifest:{id,revision,digest,bytes,parts},imageChunk}`；chunk 是最多 64 KiB 的原始 payload UTF-8 字节的 base64，最多 24 块。各块 manifest 必须一致，整条 SHA-256 校验通过后再解析图片并显示。到期立即返回 `IMAGE_EXPIRED`，没有对应有效详情/图片时返回 `IMAGE_UNAVAILABLE`。
- 每台 Mac 图片最多 128 行、payload 合计 32 MiB；生命周期元数据也最多 128 个 ID，因此七天窗口内可能先达到身份容量。容量检查和写入同一事务完成，满额返回 `IMAGE_CAPACITY`；不会为新上传无界读取或批量淘汰现有图片。正文的 64 条 / 8 MiB 限额继续独立生效。

## 到期队列与版本一致性

`payload_retention` 按 host、类型和数据 ID 保存原始上传的 `source_revision/source_digest` 与下一次到期时间，队列行数随最多 3 个摘要和 64 条详情受限。`recent` 以每条记录自身 `started` 判定七天期限，`live.task` 也按自身时间处理；上传、读取及定时任务都会过滤过期记录。持续刷新额度、观测时间或其他概览字段不能延长旧用户摘要。汇总费用、Token、请求计数和 `trends` 聚合不因单条摘要到期而重算或清空。

服务端删除过期缩略图/引用状态变化/摘要记录时会重算 payload digest 并使用单调 canonical revision。上传成功额外返回 `{ok:true,revision,digest}`，原始上传 revision/digest 单独保留；旧客户端重试同一源 envelope 仍返回成功，不会把已删除内容重新写回。新 Mac 可以接收 canonical revision 作为下一次发布的下界，但应使用原始提交内容的 digest 记录“已发送”，不把服务端删减后的内容当作本机账本原文。`/sync` 和详情 manifest 返回 canonical revision/digest，客户端必须按这对值校验云端缓存。

到期即停止对外读取。实际行删除/JSON 字节剥离由每 15 分钟的任务执行，并在相关读取/写入时协助清理。每轮清理有明确上限；迁移积压、大量同时到期或任务失败可能需要后续轮次，不能把配置间隔当成“所有数据必在 15 分钟内物理清完”的承诺。这里的实际删除指 D1 当前业务表行/JSON 内容删除，不承诺 Cloudflare 提供商备份的逐字节擦除。


## 全局每日预算

默认 UTC 每日硬上限仍为 **20,000 个获准请求**，配置可调低，代码不会接受更高上限。预算表的 `n` 改为“已预留的请求额度”，不是实际访问次数或精确访问统计。

每个 isolate 通过同一条原子 D1 `INSERT … ON CONFLICT … WHERE … RETURNING` 预留 32 个额度，只能在该 isolate 内消费；没有未经 D1 预留的本地计数。isolate 丢失、绑定变化、日期变化时丢弃未用额度，绝不归还或重建。单 isolate 的预留使用 single-flight。并发 isolate 共享同一个 UTC 日期主键，因此总获准请求不会超过数据库硬预算；冷启动、重置及尾部不足一个块时可能更早用尽。有效身份验证先于预算消费；无效凭据仍有鉴权查询成本，但不会花掉已预留请求额度。

正文和图片读取的每一块分别计为一个获准请求，图片上传同样使用现有全局预算。预算耗尽返回 429，手机保留已验证缓存。改造减少的是预算计数器的逐请求写入，不代表实际业务数据写入、索引写入或账单均减少相同倍数。

## 有界数据库工作量

主机、reader、记录 ID 均走既有/新增复合主键。详情每个主机最多 64 行，图片和图片生命周期分别最多 128 行；计数、容量求和及详情淘汰排序只读相应主机范围。新表均使用 `WITHOUT ROWID`，全局清理通过 `request_details_expires`、`request_images_expires`、`request_images_orphan_until`、`image_lifetimes_forget` 和 `payload_retention_due` 索引取有界批次。详情删除/引用变更的触发器只检查同一主机最多 128 个图片行。

以下是代码范围审计，**不是已测量账单或免费额度承诺**：

| 操作 | 范围与写入 |
| --- | --- |
| 预算预留 | 最多一次表行变更 / 32 个预留额度；默认全天最多 625 次成功预留，另计主键索引和首次插入 |
| 摘要读 | 最多 3 个摘要、64 个详情版本；到期时只改对应摘要，另可处理最多 2 条到期详情 |
| 详情/图片分块读 | 单个复合主键行、最多 64 KiB UTF-8 字节；图片额外验证所属详情最多 32 个引用；详情首次到期读取可能执行一次 canonical 清理 |
| 同源版本上传 | 原始 revision/digest 查验后返回 canonical ACK；不重新写正文/图片，不延长到期时间 |
| 详情变更 | 最多淘汰 1 条更早详情、写 1 条详情与 1 个队列状态；被淘汰/移除引用的图片由触发器删除，计入实际写量 |
| 图片变更 | 同一事务内检查最多 128 行图片 / 128 行生命周期；只写该图片与生命周期身份，过期主机行可在上传时删除 |
| 摘要变更 | 写 1 个数据行及 1 个队列状态；保留未到期记录与已有聚合 |
| 移除云密钥 | 保留既有停用 host / 删除最多 8 个 invite 的行为，所有 reader 随主密钥停用而不可读 |
| 每 15 分钟清理 | 最多 32 个预算、512 个 invite、512 个陈旧摘要；图片、详情、生命周期各最多 1,024 个到期行；另以两批各最多 512 个候选确认/删除孤立图片，并处理最多 16 个内容到期任务，连带触发器/索引开销须计入 |

20,000 请求硬预算不等于 20,000 次 D1 写入。极端写密集调用、邀请管理、首次建索引和定时清理可能超出免费层写额度；应按实际 `meta` 与账户计划调低请求预算或另行评估，不能继续沿用“每次请求固定写 4 行且索引不变”的旧估算。Cloudflare 将索引更新、DELETE 和建索引纳入相应读写统计，实际行数以 D1 元数据为准：[索引说明](https://developers.cloudflare.com/d1/best-practices/use-indexes/)、[统计说明](https://developers.cloudflare.com/d1/observability/metrics-analytics/)。单条 1 MiB 的设计也为 D1 当前 2 MB 行/字符串上限留出余量：[D1 限制](https://developers.cloudflare.com/d1/platform/limits/)。

## 复用与本地验证

复用既有 `MobileProtocol.swift` 的证书固定、消息编码、SHA-256 和凭据隔离，`MobileSync.swift` 的串行后台队列/内容签名发布，以及 `MobileStore.swift` 的前台轮询、请求合并、generation 和按主机缓存。账本原文恢复由 `Database.requestMessageDetail` 提供；没有新增采集器或历史重扫。Markdown 采用系统 `AttributedString` 与 SwiftUI 控件，没有第三方渲染执行环境。

本轮云端修改只做源码审阅、本地 `node --check`，并通过既有 `.venv/bin/python` 在内存 SQLite 应用 `schema.sql` 后重复执行 `0003_request_images.sql`；没有运行远程 D1 命令、部署或新增测试文件/维护性测试套件。统一 Swift 编译、既有最小运行冒烟和 ZIP/IPA 交付校验由本轮主执行者完成。图片传输沿用既有 envelope、SHA-256、64 KiB 分块、host/reader 鉴权和全局预算，保留正文原有有界容量事务。

## 生产部署核验 · 2026-09-30

- Worker 版本：`61b085f2-f3d3-41e3-aa27-939affa7d1f0`；部署 ID：`aac32598-6509-4edc-b9c6-065b0d8fa9f1`；流量 100%。
- 详情表与 `request_details_expires`、`invites_host`、`invites_expires`、`datasets_updated` 索引已核对。D1 绑定、限流绑定、每日 20,000 请求预算和既有 `19 0 * * *` 清理计划保留。
- 使用临时模拟 host/reader 核对生产端能力、审批类别与零用户请求统计、未变化摘要、原文/最终回复预览、UTF-8 全文两段传输及 SHA-256、reader 撤销后 403 REVOKED。12 次协议请求符合预期，模拟记录已全部清理；没有读取真实用户正文或启动/重启真实 App。
- 通过 [Cloudflare 官方 Worker 上传接口](https://developers.cloudflare.com/api/resources/workers/subresources/scripts/methods/update/) 部署，保留既有绑定及设置，并开启正文能力。Wrangler 已产出构建文件，但进程没有自行退出，结束本次自有 dry-run 进程后采用管理 API 上传。
- 本机代理按域名建立连接时返回隧道 503；核验客户端使用该域名公开解析的连接 IP，并继续校验原域名 TLS 证书及 Host，生产健康与协议核验通过。此方式只用于本次核验，未改系统网络或 App 地址，不在 App 中固定 IP。
- 核验结果与实际 D1 管理查询元数据：`build/checks/v034-work/cloud-deployment.json`；迁移结果：`cloud-migration.json`；源码及设置备份：`build/backups/cloudflare/20260930T070355Z`。
- 该核验是生产服务协议检查，不等于 iPhone 真机验收。Mac 须保持主程序运行以提供新正文；已有的否定能力缓存最多保留一小时，用户需要立即重新协商时，可自行关闭再开启 Mac 同步，然后在手机刷新/重试，配对凭据可继续使用。

## Windows 对齐核验 · 2026-10-01

本轮将 Mac 参考提交 `f59e32c` 的图片协议与保留期限实现准备到 Windows 开发分支。2026-10-01 20:34（Asia/Shanghai）使用既有 host writer 仅执行 GET capabilities，生产返回 HTTP 200，能力为 `request-kinds-v1`、`request-details-v1`，没有 `request-images-v1`。因此当前 Windows 对云端图片降级；局域网仍可由 Windows 提供图片。没有执行生产迁移、修改变量或部署。

上线前应在用户另行授权后核对生产 schema，按上文顺序应用 `0003_request_images.sql` 并部署/启用 Worker 的图片与清理配置，再重新查询能力。当前能力结果不能证明生产已采用本地三天图片/七天正文的完整清理实现。Windows 系统代理已作独立诊断：清空诊断进程的代理环境变量后，复用 Go 系统代理函数通过本机 Windows 代理访问健康接口，HTTP 200；没有改写系统网络设置。
