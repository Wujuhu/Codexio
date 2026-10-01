<p align="center">
  <img src="src/codexio/icons/app-light.svg" width="96" alt="Codexio Logo">
</p>

<h1 align="center">Codexio</h1>

<p align="center">在 Mac 上查看 Codex 额度、用量与任务，在 iPhone 上继续阅读。</p>

<p align="center">
  <a href="https://github.com/Wujuhu/Codexio/releases/latest/download/Codexio.app.zip"><strong>下载 macOS 版</strong></a> ·
  <a href="https://github.com/Wujuhu/Codexio/releases/latest/download/Codexio.ipa"><strong>下载 iPhone 版</strong></a> ·
  <a href="https://github.com/Wujuhu/Codexio/releases/latest">最新版本</a> ·
  <a href="https://github.com/Wujuhu/Codexio/issues">反馈问题</a>
</p>

Codexio 是围绕 Codex 日常使用打造的原生应用：在一个界面中查看账户额度、本机 Token 消耗、请求记录和使用趋势，通过菜单栏与桌面小组件关注任务进展，并将需要阅读的内容同步到 iPhone。

## 下载与系统要求

| 平台 | 版本 | 系统要求 | 下载 |
| --- | --- | --- | --- |
| macOS | [0.3.5](macos/VERSION) | macOS 15+，Apple Silicon（arm64） | [Codexio.app.zip](https://github.com/Wujuhu/Codexio/releases/latest/download/Codexio.app.zip) |
| iPhone | [0.3.1](ios/VERSION) | iOS 26+ | [Codexio.ipa](https://github.com/Wujuhu/Codexio/releases/latest/download/Codexio.ipa) |

**iOS IPA 为未签名设备包，需要通过 SideStore 等工具自行重签后安装。** Mac 与 iOS 的版本独立管理，安装包及校验信息以 [最新 Release](https://github.com/Wujuhu/Codexio/releases/latest) 和其中的 `latest.json` 为准。

Windows 保留 Python/PySide6 历史实现，面向 Windows 10/11 x64，默认冻结维护；已有 EXE 可在 [历史 Releases](https://github.com/Wujuhu/Codexio/releases) 查找。

## 快速开始

### Mac

1. 下载并解压 `Codexio.app.zip`，将 `Codexio.app` 放入“应用程序”并打开。
2. 在这台 Mac 上正常使用并登录 Codex。Codexio 会尝试发现 Codex／ChatGPT 内置的 Codex 组件或独立 CLI，并读取本机日志。
3. 若使用自定义目录，在 **设置 → 数据** 配置“Codex 组件路径”和“本机日志目录”。默认日志根目录为 `~/.codex`。
4. 在 **设置 → 菜单栏** 选择显示字段和顺序；桌面小组件可从 macOS 的小组件库添加。

应用使用 `/Applications/Codexio.app` 作为稳定安装位置。若从下载目录等其他位置打开完整 App，会校验并安装到该位置后重新启动，以保持唯一的小组件扩展。后续可在 App 内检查更新，下载完成后由你主动选择安装。

### iPhone

1. 下载 `Codexio.ipa`，完成重签并安装到 iPhone。
2. 让手机与 Mac 处于**可互通的同一局域网**，保持 Mac 上的 Codexio 运行，在 **设置 → 同步** 开启同步并点击“生成二维码”。
3. 在手机点击“连接我的电脑”，允许相机和本地网络权限后扫码。
4. 在 Mac 点击“确认配对”。二维码单次有效，5 分钟后过期。

配对后优先使用局域网。需要离开局域网后阅读时，可在 Mac 的同步设置中输入服务管理员提供的邀请码，单独启用云同步。已配对手机下一次成功连接 Mac 后会获得云端配置。目前每台 Mac 最多授权 3 部手机，每部手机最多保存 3 台 Mac。

## 特色功能

| 功能 | 可以查看或完成的内容 |
| --- | --- |
| **额度与任务概览** | 5 小时／周剩余额度、重置时间、当前任务、今日指标和最近请求 |
| **请求与调用记录** | 按日期、模型、速度和状态筛选，查看 Token、费用、思考强度、耗时与可用正文 |
| **用量与订阅分析** | 趋势图、年度活动热图、模型构成、聊天排行与订阅周期报告 |
| **菜单栏与桌面小组件** | 自选菜单栏字段和顺序，显示额度、数值和任务图标；提供请求及不同样式的额度组件 |
| **模型定价** | 查看基础价格、手工覆盖价格，按统一规则计算本地费用 |
| **手机阅读** | 查看概览与趋势，按需展开用户消息、最终回复及图片预览，在已配对 Mac 之间切换 |

## 常见问题

<details>
<summary><strong>关闭 Mac 窗口后，还会继续运行吗？</strong></summary>

会。关闭主窗口后，主 App 可继续在后台运行，并可通过 Dock 或菜单栏重新打开。**⌘Q 会退出主 App，停止采集、额度查询、同步和上游转发**；小组件随后提示“打开 Codexio 主程序”。小组件自身不采集数据，也不会自行启动主 App。普通退出不会因为已下载更新而重开应用。

常用快捷键：⌘K 搜索、⌘, 设置、⌘R 刷新、⌘W 关闭窗口、⌘Q 退出。

</details>

<details>
<summary><strong>为什么账户额度和本机统计不一致，或者没有显示额度？</strong></summary>

账户额度可能由同一账户的多台设备共同消耗；本机统计来自这台 Mac 可读取的日志。ChatGPT 登录模式可显示账户额度，API Key 或自定义 provider 模式保留本机用量与费用，官方订阅额度显示为不适用。缺少日志、报告或价格时会保留未知状态。

</details>

<details>
<summary><strong>手机可以在外出时查看数据吗？</strong></summary>

首次配对需要手机与 Mac 处于可互通的局域网。完成配对并启用云同步后，手机可通过蜂窝网络读取已上传内容。Mac 退出后，云端保留的是上次上传的数据；手机进入后台后暂停连接与轮询，返回前台恢复。

</details>

<details>
<summary><strong>手机的“上次更新”表示什么时间？</strong></summary>

表示手机成功接收并应用同步数据的本地时间。它与源数据生成时间不同；额度观测、重置和内容到期时间仍使用各自的业务时间。

</details>

## 数据与隐私

**“费用”是按模型价格和应用规则折算的 API 等价值，不是实际账单、ChatGPT 订阅扣款或官方 Credits。** 普通基础价与 Codex 登录模式的 Fast／长上下文换算分别处理；第三方 provider 的实际计费可能不同，缺价保留未定价状态。计算方法见 [计价规则](docs/codex-pricing.md)。

总 Token 为已确认输入与输出之和，缓存已含在输入内、推理 Token 已含在输出内，不重复累加。用户请求按明确的聊天、消息及续接关系归组，可关联的子任务归入主请求；请求数与模型调用数分别统计。更多说明见 [数据口径](docs/v0.3.1/DATA_CAPABILITIES.md)。

原始 Codex 日志以只读方式采集。Mac 默认将设置、SQLite 账本、价格版本和缓存保存在 `~/Library/Application Support/Codexio`，兼容已有的 `AIQuotaWidget`／`AIQuota` 数据目录。

**手机同步默认关闭，云同步需单独启用。** 启用后，配对设备可读取用量、额度、请求预览、可用的用户正文、最终回复和图片预览；开启云同步会将这些内容存储到 Cloudflare 服务。同步内容不提供自动脱敏保证。

| 云端内容 | 保留规则 |
| --- | --- |
| **消息正文** | 最长 **7 天**；每台 Mac 最多 64 条、合计 8 MiB，单条最多 1 MiB；容量限制可能使内容更早被移除 |
| **图片预览** | 最长 **3 天**；最长边 2048 像素、每张不超过 1 MiB，动画使用静态预览 |
| **用量摘要** | 当前状态、最近记录、近 90 天日汇总及 7／30／90 日模型统计；统计汇总不因单条消息到期而清空 |

保留期按源记录时间计算，重复上传不会续期；到期后停止读取，实际删除由后台任务分批执行。内容缺失、过期或超过容量时显示相应状态。完整范围与容量见 [手机阅读与同步协议](cloudflare/README.md)。

局域网使用 TLS 并校验配对证书指纹，云端通过 HTTPS 和设备凭据鉴权。同步不上传原始日志文件、完整工具执行过程或 Codex 登录凭据，也不提供远程执行任务或任意文件下载接口。

关闭同步会停止服务，已上传副本继续按保留规则清理；“移除密钥”会停用该 Mac 的云端读写权限。手机移除设备会删除对应本地缓存，远程撤销权限不会抹除离线设备已保存的内容。

可选的 **设置 → 应用 → 上游检测** 默认关闭。开启后，Responses 请求通过本机转发以关联响应 ID 与模型名称；关闭或正常退出时恢复原服务配置，详见 [上游检测说明](docs/upstream-detection.md)。

## 开发与贡献

Mac 使用 Swift、SwiftUI、AppKit、SQLite 与 WidgetKit，iPhone 使用 SwiftUI 与 Charts，两端复用同步协议。问题反馈、功能建议和文档修正可通过 [Issues](https://github.com/Wujuhu/Codexio/issues) 交流。反馈时请附应用版本、系统版本与复现步骤，并去除个人请求内容和凭据。

修改代码前请阅读 [AGENTS.md](AGENTS.md)，了解平台范围、现有最小验证与交付约定。本地构建产物保存在 `build`，构建不会自动推送或发布。

<details>
<summary><strong>本地构建</strong></summary>

Mac 构建需要完整 Xcode（含 macOS 26+ SDK）及 Python 3.12／3.13。Python 用于组装与交付校验，交付的原生 Mac App 不依赖 Python、Qt 或 WebView。iOS 构建还需要支持 iOS 26+ 的 iPhoneOS SDK。

```bash
git clone https://github.com/Wujuhu/Codexio.git
cd Codexio

# 构建 Mac 开发包，包含现有隔离冒烟与交付校验
./build_macos.sh

# 可选：启动隔离模拟数据预览
./run_macos.sh --mock

# 构建未签名 iPhone IPA
.venv/bin/python scripts/build_ios.py
```

Mac 脚本自动创建或复用项目 `.venv`，按本机架构编译。iOS 脚本使用 `/Applications/Xcode.app`，生成 arm64 设备包；编译与归档校验不等同于真机安装验收。

`CODEXIO_DATA_DIR` 可指定应用数据目录，`CODEX_HOME` 可指定默认 Codex 根目录。

</details>

<details>
<summary><strong>源码目录与开发产物</strong></summary>

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

</details>

## 文档

- [macOS 开发说明](docs/macos.md)：原生结构、构建与稳定安装路径。
- [iOS 开发与配对记录](docs/ios/DEVELOPMENT_HANDOFF.md)：手机安装、配对与生命周期。
- [手机阅读与同步协议](cloudflare/README.md)：正文、图片、云端保留与容量约束。
- [计价规则](docs/codex-pricing.md) · [数据口径](docs/v0.3.1/DATA_CAPABILITIES.md) · [上游检测](docs/upstream-detection.md)。
- [v0.3.5 开发记录](docs/v0.3.5/DEVELOPMENT.md) · [Windows 历史平台记录](docs/v0.3.3/WINDOWS_PARITY.md)。

开发记录按时间保留历史内容；当前功能以源码及对应版本的安装包为准。
