# macOS 原生开发说明 · v0.3.1

Mac 运行端已迁移到 Swift、SwiftUI、AppKit、SQLite 与 WidgetKit，最低 macOS 15。Windows 继续使用 Python/PySide6。Python 构建脚本仅负责组装与交付校验。

## 构建与启动

需要完整 Xcode、macOS 26 或更新 SDK、Python 3.12／3.13。`scripts/macos_env.sh` 创建／复用 `.venv`，默认发现 `/Applications/Xcode.app`；Mac 构建不安装 PySide6 或 PyInstaller。

```bash
./build_macos.sh
./run_macos.sh --mock
```

开发包在 `build/dev/macos`，包含完整 `Codexio.app`、`Codexio.app.zip` 和合并 `latest.json`。用户自行打开 APP。构建先使用 `build/staging/macos`，目标被运行进程占用时保留目标及暂存新版。

## 新版 Codex／ChatGPT 组件发现

2026-09-27 本机 `ChatGPT.app 26.924.22138` 的 CLI 布局已改为：

```text
Contents/Resources/codex-cli/
├── codex-package.json       # layoutVersion、entrypoint
├── bin/codex                # 官方入口脚本
└── CodexCLI.app/Contents/MacOS/codex
```

`CodexClient.discover` 读取版本 1 包清单，限定包内相对路径，并兼容上述固定布局和旧 `Contents/Resources/codex`。先检查用户设置及 `CODEX_CLI_PATH`，再查看正在运行／已注册的官方 App、`/Applications`、`~/Applications` 和常用独立 CLI 目录。失效的旧组件路径会回退到所属 App 的新包布局。

候选需能返回 `codex-cli` 版本并支持 `app-server --listen`。`Contents/MacOS` 中仅接受标识 `com.openai.codex.cli` 的 CLI 子包，不拿桌面界面程序作 CLI 探测。Windows 共用发现模块也支持新布局及 ChatGPT 安装目录。

本轮已从新入口完成只读 App Server 初始化、账户类型和额度窗口读取；只记录字段可用性，不在交付结果中保存邮箱、令牌或额度数值。

## 主窗口与数据

- 六页为概览、日志、用量、订阅、定价、设置。默认宽布局，旧版六种导航图形，系统字体；简体中文／英文跟随系统语言。
- 日志请求／调用模式均在末列提供“详情”，悬停或键盘进入后显示浮层，可滚动和复制；移出后关闭。
- 用量活动只从本机账本计算，全年图支持每日／每周／累计；趋势支持日期区间、粒度和模型筛选。
- 订阅官方周期与聊天额度通过独立只读适配器按需获取。未知值保留 `—`，账户切换丢弃旧报告，失败保留带统计时间的旧数据和错误状态。
- 重置按每张明细列出截止时间与“使用重置”；确认绑定所选账户和 credit ID，未确定结果保留幂等键以供重试。
- SSH 采集和来源管理已退出。旧索引数据保留；本机目录可以重新扫描。

默认数据目录是 `~/Library/Application Support/Codexio`。旧目录继续兼容，只有 Widget 镜像快照的新目录不阻断旧账本发现。`CODEXIO_DATA_DIR` 可覆盖；模拟模式单独创建数据目录，全部使用隔离的模拟数据。

## 菜单栏与 Widget

状态项默认显示周剩余额度，也可显示 5 小时额度或仅图标。浮层额度标题为 Codex，下方为今日汇总和七日趋势。macOS 26+ 使用真实系统 Liquid Glass，15 使用系统材质；减少透明度和增强对比度时使用实色背景。

原请求小、中、大 Widget 的 `kind = com.wujuhu.codexio.request`、内容顺序、几何和视觉保留，仅固定文案本地化。新增额度 Widget 使用 `com.wujuhu.codexio.quota`，提供单额度、双额度、分段刻度双额度三种最小尺寸样式。

Widget Bundle 为 `com.wujuhu.codexio.widget`，本次构建号 **15**、短版本 **1.14**。扩展只读取上限 32 KiB 的精简快照，主程序与原生后台服务采用相同刷新规则；系统决定实际显示时机。

唯一稳定宿主是 `/Applications/Codexio.app`。从其他路径启动完整 APP 时，校验版本、签名与 Widget 后原子接管；新进程确认启动及当前扩展注册后才清理旧备份。注册当前路径成功后，再注销 Codexio 的其他旧路径。接管失败恢复旧版。构建／模拟／冒烟不会触发真实宿主接管或系统注册。

## 更新与恢复

原生更新器读取合并清单的 `macos` 对象，校验 URL、版本、架构、大小、SHA-256、ZIP 路径与链接、APP 签名。下载就绪后等待正常退出，独立辅助程序原位安装并启动新版；新进程确认之前保留旧 APP，失败回滚。

上游检测由原生进程转发 Responses 请求，仅记录响应 ID 和模型。通过 App Server 配置版本校验写入所需地址字段，保留恢复日志；关闭和退出时先恢复配置，再让已有客户端连接完成交接。用户外部修改不被覆盖。

## 实现入口

| 文件 | 责任 |
| --- | --- |
| `macos/native/AppMain.swift`、`MainViews.swift`、`Components.swift` | 生命周期、单实例、原生菜单与六页公共界面 |
| `CodexClient.swift`、`AppState.swift` | 组件发现、App Server、账户与重置、共享状态 |
| `Database.swift`、`UsageIndexer.swift`、`Analytics.swift` | SQLite 兼容、增量扫描、请求归组、本机指标 |
| `Pricing.swift`、`WeeklyEstimator.swift` | 定价与本地周期估值 |
| `MenuBar.swift`、`macos/widget/` | 玻璃菜单栏、请求与额度 Widget |
| `Installation.swift`、`NativeUpdater.swift`、`ZipValidation.swift` | 稳定宿主、注册、原位更新和回滚 |
| `UpstreamCoordinator.swift`、`UpstreamRelay.swift` | 上游配置接管和原生转发 |
| `macos/Resources/Localizable.xcstrings` | 中文与英文系统文案 |
| `scripts/build_macos.py` | Swift 编译、AppIntent 元数据、资源、签名、冒烟、ZIP 与清单 |

## 最小验证

`./build_macos.sh` 已包含隔离模拟冒烟，固定检查启动、基本数据显示、关闭与重开，结果位于 `build/checks/macos-smoke/result.json`。签名、APP 版本、Widget 构建号、ZIP 解压与 SHA-256 校验继续保留。当前界面确需查看时只保存一张截图；不运行页面矩阵或全量回归。

开发完成后提交到本地 Git。正式推送与发布仍按 AGENTS.md 的第二次明确确认流程，正式 Windows EXE 在 Windows CI 构建；本机开发阶段不创建正式版本目录。
