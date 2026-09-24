# 打包与交付

- 日常功能开发、验证和打包全部留在 `build`，不因一次开发构建写入或覆盖 `release`。仅在用户明确确认发布及版本号后，才将完整正式版本归档到 `release/<确认的版本号>/`。
- 正式版本目录只有 `Codexio.exe`、`Codexio.app.zip` 和一份合并的 `latest.json`。ZIP 内保留完整的 `Codexio.app`，不再生成或上传 DMG。已发布的历史版本保持原样。
- `build/dev/macos` 保存最新开发 APP、APP ZIP 和清单；`build/dev/windows` 保存最新开发 EXE 和清单。中间文件使用 `build/staging`，构建缓存使用 `build/cache`，检查结果使用 `build/checks`，日志使用 `build/logs`，同版本临时备份使用 `build/backups`。
- Mac 使用 `build_macos.sh`，Windows 使用 `build_exe.ps1`；两者默认只生成开发包。Windows 的 `scripts/publish_exe.ps1` 仅将通过构建的 EXE 移入 `build/dev/windows`。
- 用户确认发布后执行 `scripts/prepare_release.py --version <确认的版本号>`，合并两端清单并核验三个文件；缺包、程序版本或哈希不匹配时不得归档或发布。已有正式目录不自动覆盖，历史版本不清理。
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

- 当前开发版本为 `0.2.8`，Mac 与 Windows 同步开发；Windows EXE 由用户自行编译，本轮 Release 只发布 Mac。
- 本轮开发已获确认；推送和发布仍必须等待用户开发验收后的第二次明确确认。正式 Mac 附件为 APP ZIP 与合并清单，保留已发布的 Windows 下载信息。
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
- 附件只使用已验证的 `release/<版本号>/Codexio.exe`、`Codexio.app.zip` 和 `latest.json`，共三个文件。分别核对各平台程序与对应清单中的版本、文件大小及 SHA-256，不重新生成 Windows 独占清单。
- 先验证、打包、提交，再推送 `main`，确认远程包含本次发布的提交。新 Tag 基于远程 `main` 创建；已有 Tag 或正式 Release 不自动覆盖。
- 项目当前目录为 `C:\CodeWJH\Projects\Codexio`。PowerShell 示例（替换版本号，保留空正文）：

```powershell
"" | gh release create v0.2.4 `
  --repo Wujuhu/Codexio `
  --target main `
  --title "v0.2.4" `
  --notes-file - `
  "C:\CodeWJH\Projects\Codexio\release\0.2.4\Codexio.exe" `
  "C:\CodeWJH\Projects\Codexio\release\0.2.4\Codexio.app.zip" `
  "C:\CodeWJH\Projects\Codexio\release\0.2.4\latest.json"
```

- 若需先核验附件，在创建命令中加 `--draft`，检查本次附件后执行 `gh release edit v0.2.4 --repo Wujuhu/Codexio --draft=false --latest`。严格空正文也可用 UTF-8 零字节文件配合 `--notes-file`，避免管道引入换行。
- 发布后核验 Tag 对应提交、标题、空正文及本次附件；使用 `git fetch origin tag v0.2.4` 将 Tag 同步到本地。
