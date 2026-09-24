# Codexio for macOS

当前开发版本 **0.2.8**，复用 Windows 主界面与本地用量后端。macOS 的入口、菜单栏和打包与 Windows 悬浮窗、EXE 更新器分开。

## 运行与安装

安装 Python 3.12 或 3.13 后运行 `./run_macos.sh`。如果解释器不在 PATH，可运行 `CODEXIO_PYTHON="/path/to/python3.12" ./run_macos.sh`。开发依赖只安装到项目 `.venv`。

构建使用 `./build_macos.sh`。开发包位于 `build/dev/macos/`，可以直接打开 `Codexio.app`；`Codexio.app.zip` 用于 GitHub 传输，解压后也是完整 APP。应用内已包含 Python 与 Qt，无需另装 Python；Codex 需要已安装且登录。日常构建不写入 `release`。

当前包在 Apple Silicon、macOS 27 上实测；主 APP 与原生小组件均以 macOS 15 为最低目标，macOS 15 实机仍需验收。按当前 Python 架构构建，未宣称提供 universal2 包。

当前是本地 ad-hoc 签名，未做 Developer ID 签名或 Apple 公证。公证和 GitHub 发布需另行处理；Mac 自动更新使用APP ZIP 原位替换流程。

## 原生桌面小组件

APP ZIP 内含 `Codexio.app/Contents/PlugIns/CodexioWidget.appex`。首次安装建议用 Finder 将完整 APP 放入 `/Applications`，启动一次后在桌面右键“编辑小组件”，搜索 Codexio，选择小、中或大尺寸。小组件优先展示进行中的主请求，否则展示最近一次主请求；金额是现有计价规则的估算费用。小尺寸显示请求预览、模型、费用、时长和额度，中尺寸增加双额度，并将四项用量数据等距排列到两个额度区域上方；大尺寸增加输入、输出、缓存读取与命中率。额度标题为“5 小时”“周”，五小时重置只显示本地时间点。WidgetKit 决定实际刷新时机，运行时长使用系统动态日期显示。

本地用量日志检查间隔在“设置 → 数据来源”选择 5 秒、10 秒、30 秒或 1 分钟，默认 10 秒。正常增量扫描先检查文件元数据，只打开新增或变化的日志；首次建索引和手动重扫会读取所需历史。发现请求或计量变化并写入新快照后，主程序立即向 WidgetKit 申请刷新；完全退出后由已安装的后台服务按相同间隔检查并申请。无数据变化时不重复申请；WidgetKit 可以合并或推迟申请。

主程序只向 `~/Library/Application Support/Codexio/widget_snapshot.json` 写入有界的精简快照，小组件扩展以沙盒的单文件只读权限读取；不读取 Codex 原始日志或凭据。普通 APP 升级复用相同的小组件 Bundle ID 和 `kind`，本轮更新扩展构建版本，已添加的小组件预期保留原位。免费临时签名方案已在构建机上验证扩展注册，另一台 macOS 15 或更新的 Mac 仍需验证首次安装与升级后保留情况。

直接启动开发包或其他位置的完整 APP 时，它会先校验并原子接管唯一宿主 `/Applications/Codexio.app`，再从稳定路径启动。接管会停止旧 Codexio 小组件后台服务与旧扩展进程、更新 LaunchAgent，并在确认当前扩展注册成功后清理其他旧路径；启动失败时保留或恢复旧 APP。这样桌面配置始终对应同一路径，不会因多个同 ID 扩展争抢而显示旧版或空白。

## 行为与数据口径

主界面保留概览、日志、用量、订阅、定价、设置，并使用原生标题栏和 Mac 系统字体。导航栏可收起为图标栏或拖动调整宽度，最大 180 px，低于 120 px 自动收起为 60 px。日志页移除右侧预览和速度列，仅按用户请求提供“详情”悬浮窗；移开鼠标自动关闭，其他日志悬浮提示已移除。导航状态和宽度自动保存。用量页顺序为每日 Token 图、汇总卡片、折线图。概览与用量页汇总卡片为费用、Total Token、用户请求数、缓存命中率。请求数按主请求发起时间统计，关联子代理不重复计数；缓存命中率按缓存读取／输入 Token 加权。日志表格的档位合并到时间说明，Standard 省略，模型调用模式不显示调用 ID；表格保留缓存命中率，不再显示 Token/s。⌘K 搜索、⌘, 设置、⌘R 刷新、⌘W 关闭窗口、⌘Q 退出。关闭窗口不会停止后台统计，可从菜单栏再次打开。单实例锁以当前数据目录为边界，重复启动激活已有实例。

菜单栏图标使用原有 Quantum X 标志的单色模板，在系统深浅菜单栏中自动着色。左右点击只切换预览，主窗口由预览最顶部的“主界面”按钮打开；应用激活不再自动创建窗口。点击外部区域或按 Esc 关闭。预览宽度为 253 / 293 逻辑像素，费用和 Token 卡片保持左右并排，使用 macOS 默认系统字体与较小字号，卡片之间保留 12 px 间距，跟随应用主题；默认显示两种可用额度。隐藏预览时停止界面计时器，后台采集仍继续。

菜单栏顶部只保留“主界面”按钮。今日费用与 Token 卡片只显示标题和汇总数值，完整 Token 数与费用说明可悬停查看；底部只显示更新时间数值，读取状态可悬停查看。最近请求的说明行只保留时间和模型调用次数。数据继续自动刷新，也可通过主界面或 ⌘R 手动刷新。

菜单栏最近请求预览只取用户消息，最多显示三行，长文本省略并可悬停查看已过滤的短预览；不回退到会话标题、系统提示或图片路径。解析器在截断前移除生成上下文与图片附件信息，旧索引会自动回填预览，不重复计量。

Dock 图标由 `src/codexio/icons/app.svg` 直接渲染，ICNS 的各尺寸独立生成，最高 1024 px；运行时也优先使用 SVG 图标引擎。日志表格的水平滚动条常驻，垂直滚动条固定保留位置。其他应用内滚动指示器共用 3 秒隐藏规则：滚动、键盘翻页和拖动会显示指示器，拖动期间不隐藏，停止后计时；透明状态保留原布局尺寸。

今日统计按本机当前时区的午夜至当前时间计算，复用主界面的 `confirmed_summary`；新日期的汇总尚未发布时清空昨日数值并提示等待刷新。Token 为已确认输入加输出，包含输入中的缓存部分，不重复叠加。缺少价格不会抹掉已确认的 Token；费用仅含已确认的已定价／参考估值部分，缺少价格时明确标注。

最近请求按“用户请求”分组，与日志的整轮请求一致，包含可明确关联的子代理模型调用。没有完整计量时显示“待计量”，全部未定价显示“未定价”，回复中显示持续更新的累计费用。每日合计按模型调用的实际发生时间统计，最近请求按整轮统计，因此跨午夜的单轮费用可能大于当天费用。

结构化问答回执不会生成独立的用户请求，也不会覆盖下一条真实请求的预览；若回执启动了独立的后续处理轮次，按提问调用 ID 关联原请求。原始调用账本不改动，展示索引在采集器版本升级后自动重建。

额度只由本机 Codex `app-server` 提供；超时、断网或接口数据缺失时显示缓存／未知，不用本机 Token 反推真实额度，不在重置时间到达时直接伪造 100%。倒计时和日期都是当地时间。金额使用项目既有 [计价规则](codex-pricing.md)，界面统一称为“费用”，表示按模型定价换算的金额，并非实际订阅账单。

默认从 `CODEX_HOME` 或 `~/.codex` 扫描 `sessions` 和 `archived_sessions`。API 请求和日志索引沿用原项目；不会主动重置额度、发送模型请求或修改原始会话日志。周额度等价美元仅依据本地日志和本地额度观测估算。

偏好、额度缓存、价格缓存、日志索引、运行日志位于 `~/Library/Application Support/Codexio`。`CODEXIO_DATA_DIR` 可显式覆盖存储路径，适合开发隔离；显式设置 `LOCALAPPDATA` 时保留旧的路径覆盖行为。`--mock` 默认使用该目录下的 `mock` 子目录，避免污染真实用量和偏好。

## GitHub 自动更新

“设置 → 应用”提供“自动下载更新”和“检查并更新”。打包应用默认在启动 5 秒后检查，运行期间每 6 小时检查一次；源码、模拟和冒烟检查模式不安装更新。Mac 使用独立偏好，升级到支持自动更新的版本后默认开启，用户关闭后会保留选择。

定价页开启“自动同步”时，每次启动进程都在后台联网核对价格，成功后每 24 小时再次检查；联网失败一小时后重试，期间沿用已缓存的价格。关闭自动同步后，仍可使用“立即同步”。

更新源为同一仓库 `Wujuhu/Codexio` 的正式 GitHub Release。Mac 读取与 Windows 共用的 `latest.json` 中的 `macos` 部分，只下载该版本的 `Codexio.app.zip`，检查芯片架构、文件大小和 SHA-256，再验证应用标识、版本、macOS 签名及可执行架构。准备完成后等待应用正常退出，在原来的路径替换完整 `.app` 并启动新版；收到该新进程的启动确认后删除旧 APP，启动失败恢复旧版；无写入权限或应用未正常退出时保留当前版本。发布信任仍依赖 GitHub 仓库及 HTTPS，本地 ad-hoc 签名用于完整性检查。

`./build_macos.sh` 只向 `build/dev/macos` 输出开发 APP、`Codexio.app.zip` 与 `latest.json`。顶层 Windows 字段保持原格式，Mac 版本与 ZIP 下载信息放在 `macos` 对象中。构建默认离线，优先使用另一平台的同版本开发清单，其次使用本地正式版清单；可用 `--manifest /path/to/latest.json` 指定另一台机器的清单。只有 Mac 开发包时也可以构建，正式归档时必须补齐 Windows 包。

用户明确确认发布及版本号后，运行 `.venv/bin/python scripts/prepare_release.py --version <确认的版本号>`，从 `build/dev/windows` 和 `build/dev/macos` 合并清单、检查两端版本与哈希，成功后才创建 `release/<版本号>/`。正式目录与 GitHub 附件均为 **`Codexio.exe`、`Codexio.app.zip`、`latest.json`**。已有正式目录不自动覆盖，历史 DMG 版本不改写。

ZIP 构建后会走一遍与更新器相同的解压、签名及架构检查。解压保留可执行权限和应用包内部链接，拒绝越界路径、重复文件和外部链接；下载仍检查大小及 SHA-256。旧版 DMG 更新器无法读取 ZIP 更新地址，需手动换装一次支持 ZIP 的 APP，之后自动更新使用新流程。相同或更低版本不会触发更新。

最新附件下载地址使用 [GitHub 官方的 Release 附件链接格式](https://docs.github.com/en/repositories/releasing-projects-on-github/linking-to-releases)。本次开发仅生成本地产物，不自动发布。

## 实现入口

- `src/codexio/macos_app.py`：单实例、主窗口生命周期、原生应用菜单、后台服务管理。
- `src/codexio/menu_bar.py`：模板图标、预览布局、额度与费用状态、多屏定位。
- `src/codexio/dashboard.py`：`desktop_platform="macos"` 启用 Mac 设置页并隐藏悬浮窗入口；默认仍保留 Windows 行为。
- `src/codexio/codex_discovery.py`：Codex / ChatGPT 的 Resources CLI、Homebrew 和常见用户安装路径；`.app` 路径只探测 CLI，避免启动桌面客户端。
- `src/codexio/macos_updater.py`：复用更新检查、下载与退出协调，验证 APP ZIP、替换应用包并回滚失败的启动。
- `scripts/build_macos.py`、`packaging/codexio-macos.spec`：资源、原生架构、签名、冒烟检查、开发应用包与 APP ZIP。

## 最小运行检查

```bash
./build_macos.sh
```

仅保留三个基本冒烟检查：程序能启动、基本数据显示正常、主窗口能关闭并重新打开。构建时使用 Cocoa 和隔离的模拟数据执行一次，不运行 pytest、专项回归、主题／页面遍历或截图矩阵。结果写入 `build/checks/macos-smoke/result.json`；后续不扩充测试文件或检查项。

确有当前设置 UI 改动需要查看时，可用 `CODEXIO_CAPTURE_SETTINGS=1 ./build_macos.sh` 额外保存一张设置截图；默认不截图。不构建时可单独执行 `./run_macos.sh --mock --smoke-test build/checks/macos-smoke`，无需与构建重复执行。

构建流程先生成 `build/staging/macos/Codexio.app`，核对版本与签名，启动打包后二进制完成 Cocoa 冒烟检查，再验证 APP ZIP，成功后移入 `build/dev/macos`。构建缓存在 `build/cache`，测试与截图在 `build/checks`，日志在 `build/logs`，被替换的开发 APP 暂存在 `build/backups/macos`。目标开发 APP 正在运行时，仍完成构建和验证，将新版 APP、ZIP 与清单留在 `build/staging/macos`，不覆盖或结束运行中的应用；暂存应用正在运行时拒绝在其位置重新构建。

Qt 菜单栏行为参考 [QSystemTrayIcon 官方文档](https://doc.qt.io/qt-6/qsystemtrayicon.html)；应用包与本地签名使用 [PyInstaller 官方 macOS 打包说明](https://pyinstaller.org/en/stable/feature-notes.html#macos-binary-code-signing)。
