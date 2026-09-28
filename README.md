# Codexio

当前 Mac 版本：**0.3.2**。Mac 使用 Swift + SwiftUI/AppKit。Windows 保留现有 Python/PySide6 版本；按用户约定，今后默认不再修改或发布 Windows，只有特别明确要求时才恢复该平台工作。

开发包、本地提交与正式发布分开管理。正式推送及发布需要用户开发验收后的第二次明确确认。

Mac 版本保存在 `macos/VERSION`，iOS 版本保存在 `ios/VERSION`。**以后每次 Release 默认必须附带 `Codexio.app.zip`、`Codexio.ipa` 和 `latest.json`**，缺失或过期 IPA 会阻止发布。清单保留历史 Windows 字段，并在 `ios` 中记录 IPA 的实际版本和下载信息，不生成新的 EXE。IPA 目前为未签名包，需通过 SideStore 等工具重签，见 [手机端开发交付](docs/ios/DEVELOPMENT_HANDOFF.md)。

## v0.3.1

实际数据性能与交互修复见 [性能记录](docs/v0.3.1/PERFORMANCE.md)。

- Mac 全量原生重构：概览、日志、用量、订阅、定价、设置六页，沿用旧版栏目图标，采用宽布局和克制的额度数字。
- 原生菜单栏默认显示「图标 + 周剩余百分比」，可切换 5 小时或仅图标；浮层包含 Codex 额度、今日用量和七日趋势。macOS 26+ 使用 Liquid Glass，15 使用系统材质。
- 新增三个分别注册的最小额度 Widget：单额度、双额度与分段刻度条，每个样式固定；原请求小、中、大组件保持原有 UI 和 kind。
- 日志末列“详情”悬停显示可滚动、可复制的浮层，移开关闭；保留请求／模型调用模式、日期、模型、速度和状态筛选。
- 每张主动重置独立列出截止时间与“使用重置”按钮，选择并确认后使用所选重置；不确定结果可使用同一幂等键重试。
- 用量活动完全按本机日志计算：累计与单日峰值 Token、最长有效聊天时长、当前与最长连续天数、全年热力图、Fast 与推理强度占比。
- 订阅显示官方周期和模型限额贡献；聊天排行显示官方周限额占比、实际 Credits 及模型／推理／速度构成，缺少报告时提供本机 Token 排行。
- Mac 和 Windows 均按系统首选语言显示简体中文或英文，使用各自系统字体。
- 两端移除 SSH 采集、来源管理列表与来源筛选；保留旧索引中的历史记录。本机日志目录可设置和重新扫描。
- 兼容新版 ChatGPT/Codex 客户端的 `codex-cli` 包布局，以及原版内置 CLI 和独立 CLI。

视觉要求、下载到仓库的参考图与设计草图见 [规划入口](docs/v0.3.1/PLAN.md)；实际实施与验证结果见 [实施记录](docs/v0.3.1/IMPLEMENTATION.md)。

## macOS 开发与使用

运行要求 macOS 15 或更新系统。开发构建需要完整 Xcode（支持 macOS 26+ SDK）和 Python 3.12／3.13；Python 仅用于构建工具，交付的 App 不依赖 Python、Qt 或 WebView。

```bash
./build_macos.sh                    # 构建并核验开发 APP、ZIP 和清单
./run_macos.sh --mock               # 隔离的模拟预览
./run_macos.sh                      # 启动已构建的原生 APP
```

开发产物：

```text
build/dev/macos/
├── Codexio.app
├── Codexio.app.zip
└── latest.json
```

APP 由用户自行启动。首次从开发目录或下载位置启动时，完整应用会校验后原子接管 `/Applications/Codexio.app`，从稳定宿主重新启动并维护唯一的小组件扩展注册；失败保留回滚能力。模拟与打包冒烟使用隔离数据。

支持 ⌘K 搜索、⌘, 设置、⌘R 刷新、⌘W 关闭主窗口、⌘Q 退出。关闭主窗口后继续采集，可从 Dock 或菜单栏重新打开。⌘Q 停止采集、额度查询、上游转发和自有子进程，小组件提示“打开 Codexio 主程序”；后台刷新不再独立保活。自动下载完成后由用户主动选择安装，普通退出不会因下载了更新而重开主程序。

新版客户端组件示例（已于 2026-09-27 核对本机安装）：

```text
/Applications/ChatGPT.app/Contents/Resources/codex-cli/codex-package.json
/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex
/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex
```

自动发现优先尊重用户指定的路径，支持包清单入口、正在运行／已注册的官方 App、标准应用目录、Homebrew 与常用用户 CLI 路径。手动保存的旧版路径失效时可继续找到新版。详情见 [macOS 开发说明](docs/macos.md)。

## Windows 开发与使用

以下仅保留为历史排障参考；除非用户单独明确要求，不执行 Windows 开发、构建或发布命令。

运行要求 Windows 10/11 x64，开发使用 Python 3.13 和项目虚拟环境。

```powershell
.\run.ps1
.\run.ps1 --mock
```

也可手动安装：

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m codexio
```

Windows 保留悬浮窗、吸附停靠与托盘；托盘可打开主界面、切换悬浮窗、刷新和退出。正式 EXE 在确认发布后的 Windows x64 CI 构建；`.\build_exe.ps1` 保留为 Windows 本地故障排查入口，输出在 `build/dev/windows`。

发现方式覆盖设置路径、`CODEX_CLI_PATH`、PATH、常见 Codex／ChatGPT 安装目录、安装登记、MSIX 与当前组件位置。组件通过 CLI 身份和 App Server 能力检查后才用于连接。

## 数据与费用

| 内容 | 来源与语义 |
| --- | --- |
| 5 小时／周额度、重置明细 | 当前本机 Codex 的 `account/read` 与 `account/rateLimits/read`；额度属于账户，可能由多个设备共同消耗 |
| 年度活动及五项本机指标 | 本机 `sessions`、`archived_sessions` 的去重计量与运行区间；旧 SSH 历史不混入本机指标 |
| 周期模型贡献、聊天额度 | 官方 Codex 客户端使用的只读后端报告；与本机 Token 数据分别呈现 |
| Token | 已确认输入 + 输出；输入已包含缓存，输出已包含推理 Token，不重复累加 |
| 用户请求 | 按主请求发起时间统计，可明确关联的子代理归入主请求 |
| 本地费用 | 模型 Standard 基础价与既有 Codex 换算规则得出的 API 等价值，保留未知和参考估值状态 |
| 最长聊天时长 | 同一根聊天已记录的有效运行区间合并，去除轮次之间的空闲，并行区间不重复相加 |

模型价格使用 LiteLLM 标准价、models.dev 交叉核对与备用源；冲突保留已有价格。支持手工基础价覆盖，费用计算保持原精度，展示为两位美元。具体公式见 [计价规则](docs/codex-pricing.md)，接口、缺失值与账户范围见 [数据口径](docs/v0.3.1/DATA_CAPABILITIES.md)。

ChatGPT 账户模式显示官方额度；API Key／自定义 provider 模式保留本机用量与费用，官方额度显示不适用。原始 Codex 日志只读，索引保存短输入与可见输出预览；Widget 仅读取精简快照。

## 本地数据与上游检测

Mac 默认使用 `~/Library/Application Support/Codexio`，Windows 使用 `%LOCALAPPDATA%\Codexio`；已有 `AIQuotaWidget`／`AIQuota` 数据继续沿用。`CODEXIO_DATA_DIR` 可覆盖数据目录，`CODEX_HOME` 可指定 Codex 根目录。

- `settings.json`、`analytics_settings.json`：刷新、主题、路径与窗口偏好。
- `usage.sqlite`：本机增量账本、请求、会话标题、重置幂等记录及估值。
- `prices/`：自动价格、手工覆盖和历史价格版本。
- `widget_snapshot.json`：Mac Widget 所需的有界快照。

“设置 → 应用 → 上游检测”默认关闭。开启后将当前 Responses 服务地址临时改为带随机令牌的本机回环地址，仅提取 response ID 和响应模型名称，保留 provider 与认证配置；关闭和正常退出时恢复原配置。已加载旧路由的 Codex 会话需要重新打开客户端后采用新配置；恢复过程中遇到外部修改时保留恢复记录。用户确认退出后，转发服务随主程序结束。

## 最小验证与交付

固定基础冒烟只检查：**程序启动、基本数据显示、主窗口关闭与重开**，全部使用隔离模拟数据。Mac 构建已包含该入口，不重复运行；Windows 共用 Qt 入口沿用相同三项。本项目不维护 pytest、页面遍历或截图矩阵。版本、签名、归档与哈希核验属于交付校验。

中间文件、缓存、结果、日志和临时备份分别位于 `build/staging`、`build/cache`、`build/checks`、`build/logs`、`build/backups`。运行中的 App 原路径保留。

用户验收开发包并第二次明确确认发布和版本号后，才执行：

```bash
.venv/bin/python scripts/publish_release_from_macos.py --version 0.3.2 --confirm-publish
```

发布前分别运行 `./build_macos.sh` 与 `.venv/bin/python scripts/build_ios.py`，再把上例版本替换为用户新确认的版本。协调脚本核验 Mac／IPA、推送 main、创建空正文草稿，下载核验三个附件后发布并归档。默认不会触发 Windows CI；若用户以后特别要求 Windows，需先适配包含 IPA 的联合发布流程，旧分支会安全拒绝。历史发布保持原样，v0.3.2 的两附件不会因此自动补传或覆盖。
