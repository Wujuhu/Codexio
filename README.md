<p align="center">
  <img src="src/codexio/icons/app-light.svg" width="96" alt="Codexio Logo">
</p>

<h1 align="center">Codexio</h1>

<p align="center">看清 Codex 的额度，以及 Codex 与 Claude Code 的用量和请求。</p>

<p align="center">
  <a href="https://github.com/Wujuhu/Codexio/releases/latest/download/Codexio.app.zip"><strong>下载 macOS 版</strong></a> ·
  <a href="https://github.com/Wujuhu/Codexio/releases/latest/download/Codexio.exe"><strong>下载 Windows 版</strong></a> ·
  <a href="https://github.com/Wujuhu/Codexio/releases/latest/download/Codexio.ipa"><strong>下载 iPhone 版</strong></a> ·
  <a href="https://github.com/Wujuhu/Codexio/releases">所有版本</a> ·
  <a href="https://github.com/Wujuhu/Codexio/issues">反馈问题</a>
</p>

Codexio 是 Codex 与 Claude Code 的用量和请求看板。它读取本机日志，汇总 Token、费用估算、请求记录和使用趋势，结合账户额度帮助你了解日常使用情况。Mac 提供菜单栏与桌面小组件，Windows 提供托盘与悬浮窗；配对 iPhone 后，还可以在手机上查看状态、阅读请求与最终回复。

![Codexio macOS 概览](docs/images/macos-overview.png)

<p align="center"><sub>macOS 主分支开发界面，使用隔离模拟数据；正式安装包的界面可能有所不同。</sub></p>

## 下载与安装

正式版本、各平台包版本与更新说明以 **[最新 Release](https://github.com/Wujuhu/Codexio/releases/latest)** 和其中的 `latest.json` 为准。当前桌面源码版本为 **0.3.10-beta**，仅用于本地开发测试，不作为正式 Release。已发布安装包仍以 Release 页面为准。

| 平台 | 系统要求 | 安装包 |
| --- | --- | --- |
| macOS | macOS 15+ · Apple Silicon（arm64） | [Codexio.app.zip](https://github.com/Wujuhu/Codexio/releases/latest/download/Codexio.app.zip) |
| Windows | Windows 10/11 x64 · WebView2 Runtime | [Codexio.exe](https://github.com/Wujuhu/Codexio/releases/latest/download/Codexio.exe) |
| iPhone | iOS 26+ | [Codexio.ipa](https://github.com/Wujuhu/Codexio/releases/latest/download/Codexio.ipa) |

**iPhone IPA 为未签名设备包，需要自行重签后安装。** 包版本、大小与 SHA-256 以 Release 中的 `latest.json` 为准。后续正式发布默认同时提供 Mac ZIP、Windows EXE、iOS IPA 和共享清单；历史版本的附件保持原样。`main` 可能包含尚未发布的更改，以下功能介绍按当前源码说明，正式包以对应 Release 更新说明为准。

### macOS

1. 下载并解压 ZIP，将 `Codexio.app` 放入“应用程序”后打开。
2. 在这台 Mac 上登录并使用 Codex。Codexio 会尝试发现可用的 Codex 组件，并读取本机日志。
3. 使用自定义安装或日志目录时，在 **设置 → 数据** 配置组件路径与日志目录；默认日志根目录为 `~/.codex`。
4. 在 **设置 → 菜单栏** 选择显示字段和顺序；桌面小组件可从 macOS 小组件库添加。

Mac 使用 `/Applications/Codexio.app` 作为稳定安装位置。从其他目录打开完整 App 时，会校验并接管该位置后重新启动，以保持唯一的小组件扩展。应用内更新在下载完成后由你主动选择安装。

### Windows

1. 下载 `Codexio.exe`，确认系统已安装 WebView2 Runtime，然后运行。
2. 登录并使用本机 Codex；如目录未自动识别，在设置中配置数据路径。
3. 根据需要启用悬浮窗。关闭主窗口后，可从系统托盘重新打开；退出应用使用托盘菜单。

网络请求和额度查询组件使用 Windows 当前配置的手动系统代理，未配置时回退到环境变量；应用不固定代理地址或端口。运行期间变更代理后，重新打开 Codexio 可让额度查询组件立即采用新设置。

### iPhone

1. 下载 IPA，使用重签工具安装到 iPhone。
2. 让手机与电脑处于**可互通的同一局域网**，保持电脑上的 Codexio 运行，在 **设置 → 同步** 开启同步并生成二维码。
3. 在手机点击“连接我的电脑”，允许相机和本地网络权限后扫码，再在电脑确认配对。二维码单次有效，5 分钟后过期。

配对后优先通过局域网同步。外出阅读需在电脑上单独启用云同步，并使用服务管理员提供的邀请码；手机下一次成功连接电脑时会取得云端配置。目前每台电脑最多授权 3 部手机，每部手机最多保存 3 台电脑。

## 功能概览

| 功能 | 内容 |
| --- | --- |
| **客户端切换** | Mac／Windows 概览、日志、用量、订阅及定价支持切换到本地 Claude Code CLI 与 Claude Desktop Code；每次启动默认 ChatGPT／Codex |
| **额度与任务** | 5 小时／周剩余额度、重置时间、当前任务与最近请求 |
| **请求记录** | 按日期、模型和状态筛选，查看 Token、费用、思考强度、速度、耗时与可用正文 |
| **用量分析** | 费用、总 Token、用户请求、命中率，以及趋势、活动热图、模型构成；聊天排行按本地线程费用或 Token 排序 |
| **执行行程** | 按项目、线程和用户请求查看模型调用、工具与命令、文件操作、子代理和中断／继续事件，支持返回 Codex 对话 |
| **AI 使用报告** | 日报、周报与月报，回顾用量和模型偏好，并导出报告图片 |
| **订阅与定价** | 订阅周期统计、模型基础价格与手动价格覆盖 |
| **桌面集成** | Mac 菜单栏字段选择与排序、请求／额度小组件；Windows 托盘、悬浮窗与贴边停靠 |
| **手机阅读** | 概览与趋势、用户消息和最终回复、可用图片预览，以及多台已配对电脑切换 |

执行行程位于 Mac／Windows 的 **用量 → 活动** 底部。两栏分别显示执行过程与资源详情：按整个线程、当前用户请求和选中步骤查看费用、Token、模型调用、耗时及可用配置。已有项目保留归属，其余对话统一归入“聊天”。命令通过工具名旁的按钮打开，支持自动换行与复制；模型调用和用户引导使用不同的淡色区分。

过程来自本机可读取的 Codex 记录，按需分页加载。单个工具通常没有独立的 Token 或费用数据，应用不会分摊模型调用的消耗；日志缺失的摘要、计时等字段显示 `—`，不重建不可见的思考过程。文件操作展示路径和类型，不展开文件差异或完整命令输出。此执行行程仅在桌面端展示。

Claude Code 入口合并本机终端与 Desktop 的 Code 会话，默认读取 `~/.claude/projects`（Windows 为 `%USERPROFILE%\.claude\projects`），启动时也可读取 Codexio 自身环境中的 `CLAUDE_CONFIG_DIR`；本轮不处理目录迁移。第三方 API 返回的 GPT／Gemini 等模型仍归 Claude 客户端来源；Token 按真实回复去重，完整输入包括缓存读取和创建，推理 Token 不再次加进输出。Claude 订阅页显示套餐／邮箱、5 小时和周额度、周额度估算，不提供套餐历史或主动重置。额度由 Claude Code 的官方 Mods 被动提供，旧 CLI 兼容 statusLine；不发模型请求、不直接请求 OAuth 额度接口。主 App 运行时默认安装本地桥接，保留已有配置与 statusLine 输出，已有会话在下一次加载插件后生效。CLI Mods 需 2.1.287+，Desktop 本地 Code 引擎需 2.1.286+，无需单独安装 CLI；未支持 Mods 的 Desktop、WSL 和未提供额度的数据源显示 `—`。公开数据不能确认账号时，邮箱／套餐保留 `—`；Desktop-only 有效额度仍可按本机来源和周周期生成估算，不要求额外账号接口查询。Codex／Claude 周估算默认使用 10 分钟完整窗口，也可选 30／60 分钟；采样中断的窗口作废，无消费或额度比例未变化时跳过，不跨窗口累计。费用使用官方 Claude Standard API 基础价及明确的 Claude Code 规则，缓存读取、5 分钟和 1 小时写入分开计算；Fast 按当前受支持型号规则处理，无法确定的费用保留 `—` 或标为估算。速度仅使用原始记录，缺失显示 `—`。活动热图补齐最近 365 天；两种客户端的日志筛选、字段及列宽分别保存，默认隐藏命中率；请求副行显示上下文窗口容量，缺失为 `—`。Claude 暂不提供执行行程；请求耗时优先采用明确轮次计时，缺少时使用最后一次主调用以 `end_turn`／`stop_sequence` 结束整轮的时间减请求开始时间，排除子代理结束时间；工具执行阶段显示 `—`。TPS 仅在请求完成且输出和正耗时有效时计算，TTFT 仍只采用原始首 Token 计时。普通 Chat、Cowork、未落盘的 side chat 和仅存在远端的会话不在本轮范围。

本地 0.3.10-beta 的小猫报告支持在 ChatGPT／Claude 之间切换日报、周报和月报，使用各自的本地账本及计价结果。Mac 菜单栏可分别选择双方的任务、额度和用量字段，小组件为双方各提供六种展示；手机同步目前仍使用 Codex 数据。Claude 原始日志被清理或删除后，已经采集的统计和请求摘要保留，已删除正文／附件显示缺失。详细范围见 [Claude 适配规格](docs/superpowers/specs/2026-10-13-claude-code-desktop-design.md)。0.3.10-beta 的订阅、定价及版本比较范围见 [本地 beta 规格](docs/superpowers/specs/2026-10-16-claude-subscription-v0310-beta.md)。正式安装包以 Release 说明为准。

## 数据与隐私

**费用是按模型价格与应用规则折算的 API 等价值，不是实际账单、ChatGPT 订阅扣款或官方 Credits。** 基础价、认证／消费来源与 Fast／Ultrafast／长上下文规则分别计算；第三方服务的实际计费可能不同，缺少价格时显示 `—`，部分定价状态单独标记。总 Token 为输入与输出之和，缓存与推理 Token 不重复累加；用户请求数与模型调用数分别统计。详见 [计价规则](docs/codex-pricing.md) 与 [数据口径](docs/v0.3.1/DATA_CAPABILITIES.md)。

Codexio 只读采集原始 Codex 与本地 Claude Code 日志，设置、账本和缓存保存在本机。**手机同步默认关闭，云同步需单独启用，当前仍仅同步 Codex 数据。** 启用后，配对手机可读取用量、额度、请求摘要、可用正文、最终回复与图片预览；云同步会将相应内容上传至 Cloudflare 服务，内容不会自动脱敏。

| 云端内容 | 保留与容量 |
| --- | --- |
| 请求摘要与正文 | 最长 7 天；正文每台电脑最多 64 条、合计 8 MiB，单条最多 1 MiB |
| 图片预览 | 最长 3 天；最长边 2048 像素、每张最多 1 MiB；每台电脑最多 128 张、云端预览数据合计 32 MiB |
| 用量聚合 | 范围由电脑端版本决定，不因单条消息到期而清空 |

保留期按源记录时间计算，重复上传不续期；容量限制可能使内容更早不可用。到期内容立即停止读取，实际删除由后台任务分批处理。云服务还受管理员配置、全局每日请求预算与 Cloudflare 平台限额约束，达到限制时手机保留已验证缓存。

手机趋势覆盖全部历史：近 30 天按日展示，更早数据合并为最多 60 个区间，周期统计提供近 7 天、近 30 天和全部历史。完整历史聚合不等于保存全部消息正文。协议、预算和容量细节见 [手机阅读与同步](cloudflare/README.md)。

局域网使用 TLS 并校验配对证书指纹，云端使用 HTTPS 和设备凭据鉴权。同步不上传原始日志文件、完整工具执行过程或 Codex 登录凭据，也不提供远程执行任务或任意文件下载。关闭同步会停止服务，已上传副本继续按保留规则清理；撤销授权不会抹除离线设备已经保存的内容。

可选的 **上游检测** 默认关闭。开启后，相关请求经本机转发以关联响应 ID 和模型名称；关闭或正常退出时恢复原服务配置。使用前可阅读 [上游检测说明](docs/upstream-detection.md)。

## 常见问题

**为什么账户额度与本机用量不同，或者额度不可用？**

账户额度可能由多台设备共同消耗，本机统计只来自当前电脑可读取的日志。ChatGPT 登录模式可查询订阅额度；API Key 或自定义 provider 模式保留本机用量统计，官方订阅额度显示为不适用。缺少的请求信息显示 `—`，连接或同步错误保留具体提示。

**关闭 Mac 窗口后还会继续运行吗？**

会，主 App 可继续在菜单栏运行。⌘Q 会退出主 App，停止采集、额度查询、同步和上游转发；小组件会提示“打开 Codexio 主程序”。小组件不独立采集，也不会自行启动主 App。

**电脑关机后，手机还能看到内容吗？**

启用云同步后可读取此前上传且仍有效的内容，但不会产生新数据；未启用时可查看已有本地缓存。手机进入后台后暂停连接与轮询，返回前台恢复。

## 开发与贡献

Mac 使用 Swift／SwiftUI／AppKit／WidgetKit，iPhone 使用 SwiftUI／Charts，Windows 使用 Wails + Go 与 Svelte／TypeScript。旧 Python 桌面程序已退役；Python 仅保留原生 Mac 启动、构建／交付工具和必要的规则参考，共享图标、价格种子与翻译资源继续保留。详见 [Python 目录说明](src/codexio/README.md)，完整旧版可从 [v0.2.10 源码](https://github.com/Wujuhu/Codexio/tree/v0.2.10/src/codexio)及 Git 历史查阅。

| 平台 | 构建环境 | 开发打包入口 |
| --- | --- | --- |
| macOS | 完整 Xcode（macOS 26+ SDK）、Python 3.12／3.13 | `./build_macos.sh` |
| iOS | `/Applications/Xcode.app`、iOS 26+ SDK、项目 Python 环境 | `.venv/bin/python scripts/build_ios.py` |
| Windows | Windows x64、Go 1.26.1、Node.js/npm；脚本固定 Wails 版本 | `.\build_exe.ps1` |

Mac 入口自动创建或复用 `.venv`；Python 仅参与构建与交付，原生 App 运行不依赖 Python。开发产物位于 `build/dev/<平台>`，打包不会自动推送或发布。修改前请阅读 [AGENTS.md](AGENTS.md)，遵守平台范围、最小冒烟与交付约定。日常桌面功能与适用修复默认同时覆盖 Mac 和 Windows；源码在本机修改，Windows 通过 SSH 在本地 Windows 主机构建。收到明确的推送并发布指令后，由发布协调脚本统一核验三端产物、推送、发布及归档；未指定版本时使用仓库现有最新源码版本，不自行递增版本号，无需二次确认。云端构建需要单独授权。

欢迎通过 [Issues](https://github.com/Wujuhu/Codexio/issues) 反馈问题、建议功能或提交文档修正。问题报告请包含应用版本、系统版本与复现步骤，并移除个人请求内容和凭据。

## 文档

- [macOS 开发说明](docs/macos.md) · [iOS 开发说明](docs/ios/DEVELOPMENT_HANDOFF.md) · [Windows Wails 开发说明](docs/v0.3.4/WINDOWS_WAILS.md)
- [手机阅读与同步](cloudflare/README.md) · [计价规则](docs/codex-pricing.md) · [上游检测](docs/upstream-detection.md)
- [当前开发记录](docs/v0.3.5/DEVELOPMENT.md) · [协作与交付约定](AGENTS.md)

开发记录包含历史过程，具体行为请以对应版本的源码与安装包为准。
