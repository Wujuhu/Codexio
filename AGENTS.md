# 打包与交付

- 日常功能开发、验证和 Mac 打包全部留在 `build`，不因一次开发构建写入或覆盖 `release`。仅在用户第二次明确确认发布及版本号后，才允许推送，并由发布协调脚本与 Windows CI 完成正式发布；成功后将三个远程附件核验并归档到 `release/<确认的版本号>/`。
- 正式版本目录只有 `Codexio.exe`、`Codexio.app.zip` 和一份合并的 `latest.json`。ZIP 内保留完整的 `Codexio.app`，不再生成或上传 DMG。已发布的历史版本保持原样。
- `build/dev/macos` 保存最新开发 APP、APP ZIP 和清单；`build/dev/windows` 保存最新开发 EXE 和清单。中间文件使用 `build/staging`，构建缓存使用 `build/cache`，检查结果使用 `build/checks`，日志使用 `build/logs`，同版本临时备份使用 `build/backups`。
- Mac 使用 `build_macos.sh` 生成本地开发包。`build_exe.ps1` 与 `scripts/publish_exe.ps1` 保留为 Windows 本地故障排查入口；正式 Windows EXE 只在明确发布后由 `.github/workflows/release-windows.yml` 的 Windows x64 runner 构建。
- 用户确认发布后执行 `.venv/bin/python scripts/publish_release_from_macos.py --version <确认的版本号> --confirm-publish`。脚本核验 Mac 包、推送远程 `main`、创建空正文草稿并显式触发 Windows 工作流；工作流合并清单、核验三个文件并发布，脚本随后下载复核并原子归档。缺包、程序版本或哈希不匹配时不得发布；已有正式目录、正式 Tag 或正式 Release 不自动覆盖，历史版本不清理。
- 目标被运行进程占用时保留原文件及暂存新版，不自动结束用户进程。整理 `build` 时也必须保留运行中应用的原路径；当前 `build/macos` 属于待应用退出后清理的旧路径。
- 本机 Codexio APP 由用户自行启动；开发、打包和核验时不要代用户启动已安装的 APP，也不要启动后要求用户确认。既有隔离模拟数据的打包冒烟入口照常执行。
- 读取文本文件显式指定 UTF-8。

## macOS 交付

- Mac 打包先进入 `build/staging/macos`，版本、签名、原生界面冒烟检查和 ZIP 解压校验通过后，交付到 `build/dev/macos`。
- Windows 与 Mac 共用一份 `latest.json`：Windows 字段保留在顶层，Mac 信息放在 `macos` 对象中；更新本平台字段时保留另一平台字段，不生成独立的 `latest-macos.json`。
- Mac 自动更新只下载 `Codexio.app.zip`，校验后等待应用正常退出，在旧 APP 的原路径放入新版并启动。新进程确认启动成功后删除旧 APP；失败时恢复旧版，未确认成功前保留回滚能力。
- Mac 端暂不创建悬浮窗，不运行 Windows EXE 更新器。版本号与本地提交、远程发布约定继续共用。

## macOS 小组件接管规则

- `com.wujuhu.codexio.widget` 只能由唯一稳定宿主 `/Applications/Codexio.app` 提供。用户从 `build`、下载目录或其他位置启动完整 APP 时，新 APP 必须先原子复制并接管 `/Applications/Codexio.app`，从该稳定路径重新启动；不得让多个路径下的同 Bundle ID 扩展长期同时注册。
- 接管前先校验新 APP 的版本、Widget 构建号和完整签名。停止并移除旧 Codexio Widget LaunchAgent，结束旧 `/Applications/Codexio.app` 主程序、后台刷新程序和 CodexioWidget 扩展进程，注销旧扩展，再替换稳定宿主。新 APP 启动失败时恢复旧 APP；新 APP 确认运行后才清理旧备份。
- 稳定宿主启动时必须先注册并选用当前 APP 内的小组件，确认当前路径已注册后再注销其他旧路径；注册失败时保留已有可用扩展，不得先拆掉旧版造成空白小组件。接管完成后系统中只允许一个 Codexio 小组件注册路径，并与当前 APP 的 Widget 版本一致。
- 只清理 Codexio 自己创建的注册、LaunchAgent、进程、接管备份、安装标记和运行缓存，不删除系统全局 WidgetKit 数据库或其他应用缓存。小组件 UI、Bundle 版本、注册或宿主生命周期变化时递增 `WIDGET_VERSION`。
- 开发打包后除签名、ZIP 和隔离冒烟外，还要核对 APP 版本、Widget 短版本与构建号，并确认接管代码不会在 `--mock` 或打包冒烟中修改真实 `/Applications`、LaunchAgent、进程或系统注册。

# 版本管理

- 修改功能或修复问题时默认保持当前版本号。新增或递增版本号前，必须先取得用户明确确认。

# 当前开发流程

- 当前开发版本为 `0.2.10`，Mac 与 Windows 共享功能代码；Mac 保留本地开发打包，Windows 正式 EXE 在确认发布后的 CI 阶段构建，Windows 不包含 macOS WidgetKit 小组件。
- 本轮开发已获确认；推送和发布仍必须等待用户开发验收后的第二次明确确认。0.2.10 正式附件固定为同版本的 Windows EXE、Mac APP ZIP 与合并清单；Windows 构建或任一跨平台校验失败时保持草稿，不发布残缺版本。
- 每次修改完成并通过验证、开发打包后，提交到本地 Git；开发打包不等于确认正式发布。
- 未经用户新的明确授权，不执行 `git push`、GitHub 发布或其他远程变更。

# 最小测试要求

- 删除原有测试套件、测试数据和专项测试脚本，不再维护或运行 pytest、全量回归测试、界面遍历或截图矩阵。
- 仅保留现有的最小冒烟入口，固定检查三个基本事项：程序能启动、基本数据显示正常、主窗口能关闭并重新打开。使用隔离的模拟数据，不修改真实 Codex 配置、不重启用户正在使用的客户端。
- 后续不得增加测试文件、测试项或扩大测试范围；除非用户新的明确要求，不为日常功能、文案或 UI 修改编写额外测试。
- 每次修改只做必要且尽量少的验证；开发打包中已完成最小运行检查时不重复执行。发现实际问题时只检查与问题直接相关的部分。
- 同版本的小范围 UI 或文案修改优先使用最小代码改动和最少工具调用；直接定位问题，不做无关调查、重复测试或额外截图，已有开发打包检查通过后即可提交。
- 版本、签名、归档完整性和发布文件哈希核验属于交付校验，继续保留。截图仅按当前 UI 修改的实际需要查看一张，不批量生成历史测试截图。

# GitHub Release 发布约定

- 仅在用户明确授权发布后执行。Tag 和 Release 标题统一为 `v<版本号>`，例如 `v0.2.4`；正文留空，不添加更新说明或附件描述。
- 附件只使用已验证的 `Codexio.exe`、`Codexio.app.zip` 和 `latest.json`，共三个文件。Windows 工作流在草稿中直接加入 EXE 并替换最终合并清单；发布后 Mac 协调脚本下载同一组附件到 `release/<版本号>/` 再次核验。分别核对各平台程序与对应清单中的版本、文件大小及 SHA-256，不生成 Windows 独占清单。
- 先验证 Mac 开发包并提交，再在用户明确发布后由协调脚本推送 `main`，确认远程精确包含本次提交。新 Tag 基于远程 `main` 创建；已有 Tag 或正式 Release 不自动覆盖。
- 发布协调命令（替换版本号）为 `.venv/bin/python scripts/publish_release_from_macos.py --version 0.2.10 --confirm-publish`。需要继续同一提交的失败草稿时必须显式加 `--resume-draft`；远程已成功而本地归档缺失时只可用 `--sync-only` 补齐。
- 协调脚本先用 UTF-8 零字节正文创建草稿并上传 Mac ZIP 与开发清单，再触发 Windows 工作流。工作流只有在 Windows 版本、PE 头、Mac ZIP、两端大小、SHA-256、Tag、标题、空正文和三个附件全部正确时才发布并标记 latest。
- 发布后协调脚本核验远程 Tag、标题、空正文与附件，下载三个附件到临时目录，通过相同校验后创建本地正式目录，并执行 `git fetch origin tag v<版本号>`。
