# v0.3.3 开发范围与实施记录

用户于本轮明确要求开始 v0.3.3 开发，并提供 `build/dev/report-cards-234-source.zip`。此前讨论已经确认以下范围。本文件作为实施计划与进度记录；开发产物不等于正式发布。

## 约束

- Mac 版本升级为 0.3.3；iOS 保持独立的 0.3.1，生成最新 IPA。Windows 源码与版本冻结。
- 所有开发包、预览、日志和暂存留在 build；不推送、不发布、不部署 Cloudflare、不修改 release。
- 使用现有三项隔离冒烟及签名、版本、ZIP/IPA、哈希交付校验；不增加测试文件、测试项或维护性测试套件，不启动用户已安装 App。
- 复用现有 Swift 缓存、账本、原生控件及 v0.2.10 的归组、变化检测和分页机制；后台计算，避免无变化全量重算。
- 当前工作区仅有用户已有的未跟踪 .DS_Store；在本地开发分支中共享文件所有权明确的并行工作，保留它。

## 已确认行为

1. 自动更新先检查并询问，再由“更新”启动下载、校验和替换；显示真实下载进度。可展示同版本 Release 正文，空正文收起。稍后不反复弹窗。下载、校验、安装失败均提供该版本 Mac ZIP 直链，清理无用包，保留回滚直至新版确认启动。窗口未显示时不抢焦点。
2. 报告每天 08:00 后首次打开主窗口时显示一次；日报、周报、月报每天均可切换，月初优先月报、周一优先周报（重合时月报优先）、其余日期默认日报。分别统计昨天、上一完整自然周和上一完整自然月。报告采用用户素材，猫咪提示清晰、不用紫色、费用标签不写“估算”；使用一张连续长图在小窗口内滚动阅读，末尾提供分享按钮；以用户后续“一页版”要求替代原分页方案。设置“数据”显示存放路径。
3. 不增加远控。优化 Worker 的逐请求预算写入，维持全局预算保护。iOS 详情按记录读取用户原文及最终回复，排除中间消息；约 5/20 行预览，展开加载全文，支持 Markdown、表格、网页链接；文件只显示文件名，图片采用受控缩略图与容量限制。增量、缓存、有界保留，不重复同步整份历史。
4. iOS 无运行任务时显示最近完成的真实用户任务及绿色勾圈；概览任务入口跳到记录页同一详情。
5. 删除活动热图与活动洞察之间多余预留高度。
6. 已确认的独立 ContextCompaction 记录显示“上下文压缩”，保留实际计量，不增加用户请求数，不冒充最近完成用户任务。
7. 定价页展示顺序按新系列优先、同系列 Astra / Sol / Terra / Luna 排列；只改变展示，不改变价格规则指纹。

## 任务与文件所有权

- [x] 报告与集成（主执行者）：新增原生报告模块及资源，接入 AppState/AppMain/SettingsViews、版本、构建资源及报告预览导出。
- [x] 更新流程（独立子任务）：NativeUpdater.swift、新增 UpdateViews.swift；通过明确接口交由主执行者接入 AppMain/AppState/设置。
- [x] iOS 与云同步（独立子任务）：MobileSync.swift、apple/shared/MobileProtocol.swift、ios/App/*、cloudflare/*；共享账本的新详情接口由采集任务提供。
- [x] 采集与展示修正（独立子任务）：UsageIndexer.swift、Database.swift、Analytics.swift、UsageSnapshotCache.swift、UsageViews.swift、独立价格排序辅助；提供请求详情读取、项目 cwd 与独立压缩分类。
- [x] 汇总编译、最小冒烟、交付校验、报告实际预览、代码审阅、本地 Git 提交。

## 共享接口与重点检查

- 报告从不可变 UsageSnapshot/有界账本查询构造周期投影，缓存键包含账本 revision、价格规则、来源身份、时区和周期边界；读取/动画不触发重新扫描。
- 采集任务提供 `Database.requestMessageDetail(_:)` 后台按需接口，并与同步任务明确统一记录 ID、原文、最终回复、附件元数据及过期策略；旧记录只在有注册来源时有界恢复，不从截短预览反推正文。
- ContextCompaction 的分类与真实请求/续跑归属分开处理；用量保留、请求数和最近任务选择排除独立维护操作。
- 更新与报告使用同一主窗口呈现协调，避免同时叠加弹窗；安装仍复用现有稳定宿主、安装锁和启动确认/回滚逻辑。
- 云协议需要向后兼容旧 Worker：新能力未部署时明确提示详情暂不可用，不把占位摘要当全文。任何远程部署另需明确授权。

## 参考

- 用户素材包 README、ReportData.swift、SharedViews.swift、ReportVariants.swift：采用原生卡片布局、装饰素材与数据驱动洞察；示例数据不写入真实账本。
- 素材 ZIP SHA-256：`d894628e76301cd71d5e7a429070545f035ddeb3b94f5d06b66598ccc62b4030`；保留 4 张原生图片，布局适配在 ReportArtwork / ReportArtworkVariants / UsageReportReader。
- 当前 UsageIndexer.swift、UsageSnapshotCache.swift、Presentation.swift、NativeUpdater.swift、MobileSync.swift、MobileProtocol.swift。
- v0.2.10 的 usage_collector.py / user_requests.py / usage_queries.py：变化检测、真实请求归组与有界读取。

## 进度

- 已核对素材 ZIP 目录并安全解包至 build/staging/v0.3.3/report-source。
- 已确认本次只做开发交付；生产云服务与 GitHub 保持现状。

## 集成与审阅记录

- 默认报告样式已按用户回复设为第 3 套薄荷猫咪花园；第 2 套改为奶油色，三套均可选。
- 用户补充的首次选中优先级：每月 1 日为月报，其次每周一为周报，其余日期日报；三种周期仍全部可切换。
- 已完成一轮 Mac 0.3.3 / Widget 22（短版本 1.21）构建及三项隔离冒烟、签名和 ZIP 校验；iOS 0.3.1 IPA 构建及设备架构/ZIP校验通过。
- 独立代码审阅提出 4 项重要修正：报告跨日陈旧集合、元数据迁移剩余候选、iOS 前台恢复阅读路径、报告快照与账本修订绑定。已落实修正，随后重新构建。
- 额外集成修正：最近完成任务使用实际完成时间；上午 8 点前手动阅读不消耗当天自动提示；缺失计价/Token 时不伪造完整比较。
- Cloudflare 仅本地代码与迁移文件已完成，未部署；旧云端继续同步摘要，新详情能力等待单独部署。

- 报告导出静态标注的中英文一致性已一并修正；未保留审阅中的本地化待办。
- 已用隔离的原生 `--mock --render-report-preview` 导出默认第 3 套长图，并查看一张实际输出。预览使用固定示例数据，不读取或修改用户真实账本。
- iOS 最新 IPA 的源码指纹、SHA-256、设备 arm64、Bundle 版本和 ZIP 完整性再次核对通过；版本保持 0.3.1，包未签名、未进行真机安装验证。
- 最终 Mac 构建无编译警告；分享图片编码／写盘放在后台，分享面板回到主线程；数字过渡使用原生 numericText，并遵循“减少动态效果”。
- 最终 Mac 0.3.3 / Widget 1.21（22）签名、版本、ZIP 提取和原有三项冒烟通过；交付清单与哈希记录在 `build/checks/v0.3.3/delivery.json`。
- 交付路径：`build/dev/macos/Codexio.app.zip`、`build/dev/ios/Codexio.ipa`；实际原生长图为 `build/checks/v0.3.3/report-preview.png`。
- 本次保留在本地分支 `codex/v0.3.3`，不合并、不推送、不发布；生产 Cloudflare 也未更改。

## 报告视觉修正（2026-09-29）

- 用户指出窗口阅读界面与此前给出的分享长图不同。根因是阅读页另写 ReportReaderPage，未使用素材的 UsageReportCard。
- 用户进一步确认使用“长图一页版”：移除分页／页码和独立面板布局，窗口与分享直接共用 UsageReportCard，保留日报／周报／月报切换、三套样式、数字过渡、下滑提示和底部分享。
- 使用既有隔离 MockGallery 的显式 report-window 选择项捕获实际原生阅读视图；不加入默认截图列表，不改变三项冒烟，也不启动已安装应用。
- 实际窗口首屏：build/checks/v0.3.3/report-reader-fix/report-window-zh.png；同一组件的长图：该目录中的 report-long.png。均为隔离示例数据。
- Mac 版本仍为 0.3.3；重新完成开发打包、原有三项冒烟、签名／ZIP／版本／哈希校验。iOS 和 Widget 版本不因这次报告 UI 调整递增。
