# Codexio for macOS

保持版本 **0.2.4**，复用 Windows 主界面与本地用量后端。macOS 的入口、菜单栏和打包与 Windows 悬浮窗、EXE 更新器分开。

## 运行与安装

安装 Python 3.12 或 3.13 后运行 `./run_macos.sh`。如果解释器不在 PATH，可运行 `CODEXIO_PYTHON="/path/to/python3.12" ./run_macos.sh`。开发依赖只安装到项目 `.venv`。

构建使用 `./build_macos.sh --dmg`。打开生成的 `build/macos/Codexio.dmg`，将 Codexio 拖入 Applications，或直接打开 `build/macos/Codexio.app`。应用内已包含 Python 与 Qt，运行应用包不需要额外安装 Python。Codex 需要已安装且登录，Codexio 本身不包含 Codex CLI。

当前包在 Apple Silicon、macOS 27 上实测；Qt 依赖和应用清单以 macOS 12 为最低目标，其他系统版本尚未实机验证。按当前 Python 架构构建，未宣称提供 universal2 包。

当前是本地 ad-hoc 签名，未做 Developer ID 签名或 Apple 公证。对外分发、公证、自动更新与 GitHub 发布均未配置；Windows 的自动下载与 EXE 安装器不会在 Mac 端加载。

## 行为与数据口径

主界面保留概览、日志、用量、订阅、定价、设置，并使用原生标题栏和 Mac 系统字体。⌘K 搜索、⌘, 设置、⌘R 刷新、⌘W 关闭窗口、⌘Q 退出。关闭窗口不会停止后台统计，可从菜单栏再次打开。单实例锁以当前数据目录为边界，重复启动激活已有实例。

菜单栏图标使用原有 Quantum X 标志的单色模板，在系统深浅菜单栏中自动着色。左右点击只切换预览，主界面由“打开主界面”按钮打开；应用激活不再自动创建窗口。点击外部区域或按 Esc 关闭。预览宽度为 380 / 440 逻辑像素，跟随应用主题；默认显示两种可用额度。隐藏预览时停止界面计时器，后台采集仍继续。

菜单栏最近请求预览只取用户消息，最多显示三行，长文本省略并可悬停查看已过滤的短预览；不回退到会话标题、系统提示或图片路径。解析器在截断前移除生成上下文与图片附件信息，旧索引会自动回填预览，不重复计量。

Dock 图标由 `src/codexio/icons/app.svg` 直接渲染，ICNS 的各尺寸独立生成，最高 1024 px；运行时也优先使用 SVG 图标引擎。所有应用内滚动区域共用 3 秒隐藏规则：滚动、键盘翻页和拖动会显示指示器，拖动期间不隐藏，停止后计时；透明状态保留原布局尺寸。

今日统计按本机当前时区的午夜至当前时间计算，复用主界面的 `confirmed_summary`；新日期的汇总尚未发布时清空昨日数值并提示等待刷新。Token 为已确认输入加输出，包含输入中的缓存部分，不重复叠加。缺少价格不会抹掉已确认的 Token；费用仅含已确认的已定价／参考估值部分，缺少价格时明确标注。

最近请求按“用户请求”分组，与日志的整轮请求一致，包含可明确关联的子代理模型调用。没有完整计量时显示“待计量”，全部未定价显示“未定价”，回复中显示持续更新的累计费用。每日合计按模型调用的实际发生时间统计，最近请求按整轮统计，因此跨午夜的单轮费用可能大于当天费用。

额度只由本机 Codex `app-server` 提供；超时、断网或接口数据缺失时显示缓存／未知，不用本机 Token 反推真实额度，不在重置时间到达时直接伪造 100%。倒计时和日期都是当地时间。金额使用项目既有 [计价规则](codex-pricing.md)，界面统一称为“费用”，表示按模型定价换算的金额，并非实际订阅账单。

默认从 `CODEX_HOME` 或 `~/.codex` 扫描 `sessions` 和 `archived_sessions`。API 请求和日志索引沿用原项目；不会主动重置额度、发送模型请求或修改原始会话日志。可选服务端周额度估算沿用现有只读凭据流程。

偏好、额度缓存、价格缓存、日志索引、运行日志位于 `~/Library/Application Support/Codexio`。`CODEXIO_DATA_DIR` 可显式覆盖存储路径，适合开发隔离；显式设置 `LOCALAPPDATA` 时保留旧的路径覆盖行为。`--mock` 默认使用该目录下的 `mock` 子目录，避免污染真实用量和偏好。

## 实现入口

- `src/codexio/macos_app.py`：单实例、主窗口生命周期、原生应用菜单、后台服务管理。
- `src/codexio/menu_bar.py`：模板图标、预览布局、额度与费用状态、多屏定位。
- `src/codexio/dashboard.py`：`desktop_platform="macos"` 启用 Mac 设置页并隐藏悬浮窗入口；默认仍保留 Windows 行为。
- `src/codexio/codex_discovery.py`：Codex / ChatGPT 的 Resources CLI、Homebrew 和常见用户安装路径；`.app` 路径只探测 CLI，避免启动桌面客户端。
- `scripts/build_macos.py`、`packaging/codexio-macos.spec`：资源、原生架构、签名、冒烟检查、应用包与可选 DMG。

## 验证

```bash
QT_QPA_PLATFORM=offscreen LOCALAPPDATA="$PWD/build/test-data" \
  .venv/bin/python -m pytest -q

./run_macos.sh --mock --smoke-test build/macos-smoke

./build_macos.sh --dmg
```

自动化覆盖数据路径和设置持久化、CLI 发现、今日汇总、跨日刷新、未知价格、整轮请求状态、缓存／重置时间、负坐标多屏定位、Esc、关闭／重新打开以及单实例。只有需要真实 Win32 进程 API 的 Windows 更新器测试在 Mac 跳过。

冒烟检查使用 Cocoa 原生平台和模拟数据，逐页打开深浅主题，保存当前应用控件截图并检查设置持久化、关闭主窗口后服务继续运行、菜单栏只切换预览、明确点击后打开主窗口、日志详情数值、3 秒滚动条隐藏、SVG 高清图标以及未创建悬浮窗。输出位于指定目录的 `result.json` 与 PNG 文件中。`--smoke-test` 必须与 `--mock` 同用。

构建流程先生成 `build/release-staging/macos/Codexio.app`，核对版本和签名，然后直接启动打包后的二进制完成同样的 Cocoa 冒烟检查。成功后移动至 `build/macos/Codexio.app`，旧包仅保存在 `build/macos-previous`。若目标或暂存应用仍在运行，构建不会覆盖或终止它。DMG 包含该次构建的应用及 Applications 快捷方式，并经 `hdiutil verify` 验证。

Qt 菜单栏行为参考 [QSystemTrayIcon 官方文档](https://doc.qt.io/qt-6/qsystemtrayicon.html)；应用包与本地签名使用 [PyInstaller 官方 macOS 打包说明](https://pyinstaller.org/en/stable/feature-notes.html#macos-binary-code-signing)。
