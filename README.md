<p align="center">
  <img src="src/codexio/icons/app-light.svg" width="96" alt="Codexio Logo">
</p>

# Codexio

**在 Mac 上查看 Codex 额度、用量与请求记录，在 iPhone 上继续阅读。**

Codexio 将本机 Codex 日志、账户额度与用量报告汇集到原生界面，帮助你了解 Token 消耗、模型使用和任务进展。Mac 提供菜单栏与桌面小组件；iPhone 通过配对读取 Mac 准备的数据，并可选择启用云同步。

[下载安装](https://github.com/Wujuhu/Codexio/releases/latest) · [历史版本](https://github.com/Wujuhu/Codexio/releases) · [反馈问题](https://github.com/Wujuhu/Codexio/issues)

## 主要功能

- **额度与任务概览**：5 小时／周剩余额度、重置时间、当前任务、今日指标和最近请求。
- **请求与调用记录**：按日期、模型、速度和状态筛选，查看请求归组、Token、费用、思考强度、耗时与可用正文。
- **用量与订阅分析**：趋势图、年度活动热图、模型构成、聊天排行与订阅周期报告。
- **菜单栏与小组件**：自选菜单栏字段和顺序，显示额度、数值和任务图标；桌面提供请求及不同样式的额度小组件。
- **定价与估值**：查看模型基础价格，支持手工覆盖，按统一规则计算本地费用。
- **手机阅读**：查看概览、趋势和最近记录，按需展开用户消息、最终回复与可用图片预览，支持切换已配对的 Mac。

## 平台与下载

| 平台 | 实现与系统要求 | 当前仓库状态 |
| --- | --- | --- |
| macOS | Swift、SwiftUI、AppKit、WidgetKit；macOS 15+ | 主要开发平台，当前源码版本 [0.3.5](macos/VERSION) |
| iPhone | SwiftUI、Charts；iOS 26+、arm64 | 当前源码版本 [0.3.1](ios/VERSION)，IPA 需自行重签 |
| Windows | Python、PySide6；Windows 10/11 x64 | 保留历史实现与安装包，默认冻结维护 |

本文介绍当前仓库源码。**开发版本不代表已经发布**；可下载的版本和功能以 [最新 Release](https://github.com/Wujuhu/Codexio/releases/latest) 为准。Mac 与 iOS 独立管理版本，Release 标签不代表 IPA 内的版本号。

**macOS：** 下载 Release 附件中的 `Codexio.app.zip`，解压后将 `Codexio.app` 放入“应用程序”并打开。当前正式 Mac 包为 Apple Silicon（arm64）版本，具体架构可查同一 Release 的 `latest.json`。

应用以 `/Applications/Codexio.app` 为稳定安装位置，供系统注册唯一的小组件扩展。从下载目录等其他位置首次打开完整 App 时，会校验并安装到该位置后重新启动；接管失败时保留回滚能力。后续可在 App 内检查更新，下载完成后由你主动选择安装。

**iPhone：** 下载 `Codexio.ipa`，通过 SideStore 等工具**自行重签后安装**。当前 IPA 为未签名的设备包；安装与续签由所用工具及签名环境管理。iPhone 版需与 Mac 配对，手机进入后台后暂停连接与轮询，返回前台恢复。历史 Release 可能没有 IPA，请以附件列表为准。

**Windows：** 在 [历史 Releases](https://github.com/Wujuhu/Codexio/releases) 查找已有 EXE。本仓库的 Windows 端仍是 Python/PySide6，历史实现与差异见 [Windows 平台记录](docs/v0.3.3/WINDOWS_PARITY.md)。

## 首次使用

### 在 Mac 上查看数据

1. 在这台 Mac 上正常使用并登录 Codex。Codexio 会尝试发现 Codex／ChatGPT 内置的 Codex 组件或独立 CLI，并读取本机日志。
2. 打开 Codexio，检查概览中的额度与用量。默认日志根目录为 `~/.codex`；使用自定义目录或组件时，可在 **设置 → 数据** 配置“Codex 组件路径”和“本机日志目录”。
3. 在 **设置 → 菜单栏** 选择需要显示的字段；桌面小组件可从 macOS 的小组件库添加。

ChatGPT 登录模式可显示账户额度。API Key 或自定义 provider 模式保留本机用量与费用，官方订阅额度显示为不适用。未记录的历史、无法读取的报告或缺失价格会保持未知状态。

关闭主窗口后，主 App 可继续在后台运行，并可通过 Dock 或菜单栏重新打开。**⌘Q 会退出主 App，停止采集、额度查询、同步和上游转发**；小组件随后提示“打开 Codexio 主程序”。小组件自身不采集数据，也不会自行启动主 App；普通退出不会因为已下载更新而重开应用。

常用快捷键：⌘K 搜索、⌘, 设置、⌘R 刷新、⌘W 关闭窗口、⌘Q 退出。

### 配对 iPhone

1. 让 iPhone 与 Mac 处于**可互通的同一局域网**，保持 Mac 上的 Codexio 运行。
2. 在 Mac 的 **设置 → 同步** 开启同步，点击“生成二维码”。
3. 在 iPhone 点击“连接我的电脑”，允许相机与本地网络权限后扫码。
4. 在 Mac 点击“确认配对”。二维码单次有效，5 分钟后过期。

配对后优先使用局域网。若需要离开局域网后查看数据，在 Mac 同步设置中填入服务管理员提供的邀请码并启用云同步；已配对手机下次成功连接 Mac 时会获得云端配置，此后可通过蜂窝网络读取已上传内容。首次配对仍需局域网。

目前每台 Mac 最多授权 3 部手机，每部手机最多保存 3 台 Mac。Mac 可撤销手机权限，手机设置可移除设备。Mac 退出后，云端展示上次上传的数据。手机的“上次更新”表示手机成功接收并应用同步数据的本地时间；额度观测、重置和内容到期时间仍使用各自的业务时间。

## 数据口径

**本地“费用”是按模型价格和应用规则折算的 API 等价值，不是实际账单、ChatGPT 订阅扣款或官方 Credits。**

| 指标 | 来源与含义 |
| --- | --- |
| 5 小时／周额度、重置明细 | 当前账户的 Codex 数据；同一账户在其他设备上的使用也可能消耗额度 |
| 总 Token | 已确认输入与输出之和；缓存已含在输入内，推理 Token 已含在输出内，不重复累加 |
| 用户请求 | 按明确的聊天、消息及续接关系归组，可关联的子任务归入主请求；与模型调用数分别统计 |
| 费用、模型构成、本机活动 | 从本机已记录的日志计算，完整程度取决于可用日志与价格信息 |
| 订阅周期、模型贡献、聊天额度 | 来自账户的只读用量报告，与本机 Token 统计分开呈现 |
| 缺失与参考估值 | 保留未知、未定价或参考估值状态；第三方 provider 的实际计费可能不同 |

普通基础价与 Codex 登录模式的 Fast／长上下文换算分别处理，具体计算见 [计价规则](docs/codex-pricing.md)。账户范围与缺失值说明见 [数据口径](docs/v0.3.1/DATA_CAPABILITIES.md)。本机日志时间按设备时区展示，套餐历史的周期及统计截至时间使用 UTC。

## 本地数据与可选同步

原始 Codex 日志以只读方式采集。Mac 默认将设置、SQLite 账本、价格版本和缓存保存在 `~/Library/Application Support/Codexio`；已有的 `AIQuotaWidget`／`AIQuota` 数据目录继续兼容。`CODEXIO_DATA_DIR` 可指定应用数据目录，`CODEX_HOME` 可指定默认 Codex 根目录。

**手机同步默认关闭，云同步需单独启用。** 启用后，配对设备可读取用量、额度、请求预览，以及可用的用户正文、最终回复和图片预览。开启云同步会将这些内容上传到 Cloudflare 服务；短预览和正文不提供自动脱敏保证。

局域网使用 TLS 并校验配对证书指纹，云端通过 HTTPS 和每台设备的凭据鉴权。同步不上传原始日志文件、完整工具执行过程或 Codex 登录凭据，也不提供远程执行任务或任意文件下载接口。

| 同步内容 | 范围与云端限制 |
| --- | --- |
| 用量摘要 | 当前状态、最近记录、近 90 天日汇总及 7／30／90 日模型统计 |
| 最近记录 | 最近 7 天、最多 200 条；列表中的每条记录不保证都有云端正文 |
| 消息正文 | 每台 Mac 最多 64 条、合计 8 MiB，单条最多 1 MiB；最长保留 7 天，容量限制可能使内容更早被移除 |
| 图片 | 压缩预览最长边 2048 像素、每张不超过 1 MiB；动画使用静态预览，最长保留 3 天；每台 Mac 最多 128 张、图片同步数据合计 32 MiB |

正文与图片按源记录时间计算期限，重复上传不会续期；到期后停止读取，实际删除由后台任务分批执行。正文或源图片缺失、已过期、超过容量时会显示相应状态。统计汇总不因单条消息到期而清空。

关闭同步会停止服务，已上传副本继续按保留规则清理；“移除密钥”会停用该 Mac 的云端读写权限。手机移除设备会删除对应本地缓存，远程撤销权限不会抹除离线设备已保存的内容。完整协议与清理机制见 [手机阅读与同步协议](cloudflare/README.md)。

可选的 **设置 → 应用 → 上游检测** 默认关闭。开启后，Responses 请求通过本机转发以关联响应 ID 与模型名称；关闭或正常退出时恢复原服务配置。更改路由后，已有 Codex 会话可能需要重新打开客户端才能采用新配置，详见 [上游检测说明](docs/upstream-detection.md)。

## 从源码构建

Mac 构建需要完整 Xcode（含 macOS 26+ SDK）及 Python 3.12／3.13。Python 用于组装与交付校验，交付的原生 Mac App 不依赖 Python、Qt 或 WebView。iOS 构建还需要支持 iOS 26+ 的 iPhoneOS SDK。

```bash
git clone https://github.com/Wujuhu/Codexio.git
cd Codexio

# 构建 Mac 开发包，并完成现有隔离冒烟与交付校验
./build_macos.sh

# 可选：启动隔离模拟数据预览
./run_macos.sh --mock

# 构建未签名 iPhone IPA（使用上一步准备的 Python 环境）
.venv/bin/python scripts/build_ios.py
```

Mac 构建脚本自动创建或复用项目 `.venv`，按本机架构编译。iOS 脚本使用 `/Applications/Xcode.app`，生成 arm64 设备包；编译与归档校验不等同于真机安装验收。

| 目录 | 内容 |
| --- | --- |
| [`macos/native/`](macos/native/) | Mac 主程序、界面、账本、菜单栏、更新与同步 |
| [`macos/widget/`](macos/widget/) | WidgetKit 请求与额度组件 |
| [`ios/App/`](ios/App/) | iPhone 主 App、阅读界面与同步客户端 |
| [`apple/shared/`](apple/shared/) | Mac／iOS 共用同步协议与数据结构 |
| [`cloudflare/`](cloudflare/) | 云同步 Worker、数据库结构与迁移 |
| [`src/codexio/`](src/codexio/) | 历史 Windows 实现及共用品牌、价格资源 |
| [`scripts/`](scripts/) | 构建、归档、版本与交付工具 |
| `build/dev/macos/` | 开发 App、`Codexio.app.zip` 与 `latest.json` |
| `build/dev/ios/` | 未签名 `Codexio.ipa` 与 `build-info.json` |

日常构建产物留在 `build`；中间文件、缓存、校验结果、日志和临时备份分别使用其下的 `staging`、`cache`、`checks`、`logs`、`backups`。本地开发构建不会自动推送或发布。正式发布默认包含 Mac ZIP、iOS IPA 和清单，具体开发、最小验证与发布约定见 [AGENTS.md](AGENTS.md)。

## 相关文档

- [macOS 开发说明](docs/macos.md)：原生结构、构建入口与稳定安装路径。
- [iOS 开发与配对记录](docs/ios/DEVELOPMENT_HANDOFF.md)：手机端安装、配对与生命周期。
- [手机阅读与同步协议](cloudflare/README.md)：当前正文／图片能力、保留期限和容量约束。
- [计价规则](docs/codex-pricing.md)与[数据口径](docs/v0.3.1/DATA_CAPABILITIES.md)：理解费用、Token、账户额度与缺失值。
- [v0.3.5 开发记录](docs/v0.3.5/DEVELOPMENT.md)：当前开发版的实现和验证记录。

开发记录按时间保留历史内容；当前功能以源码及对应版本的安装包为准。
