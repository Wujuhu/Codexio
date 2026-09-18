# 打包与交付

- 日常功能开发、验证和打包全部留在 `build`，不因一次开发构建写入或覆盖 `release`。仅在用户明确确认发布及版本号后，才将完整正式版本归档到 `release/<确认的版本号>/`。
- 正式版本目录只有 `Codexio.exe`、`Codexio.app.zip` 和一份合并的 `latest.json`。ZIP 内保留完整的 `Codexio.app`，不再生成或上传 DMG。已发布的历史版本保持原样。
- `build/dev/macos` 保存最新开发 APP、APP ZIP 和清单；`build/dev/windows` 保存最新开发 EXE 和清单。中间文件使用 `build/staging`，构建缓存使用 `build/cache`，检查结果使用 `build/checks`，日志使用 `build/logs`，同版本临时备份使用 `build/backups`。
- Mac 使用 `build_macos.sh`，Windows 使用 `build_exe.ps1`；两者默认只生成开发包。Windows 的 `scripts/publish_exe.ps1` 仅将通过构建的 EXE 移入 `build/dev/windows`。
- 用户确认发布后执行 `scripts/prepare_release.py --version <确认的版本号>`，合并两端清单并核验三个文件；缺包、程序版本或哈希不匹配时不得归档或发布。已有正式目录不自动覆盖，历史版本不清理。
- 目标被运行进程占用时保留原文件及暂存新版，不自动结束用户进程。整理 `build` 时也必须保留运行中应用的原路径；当前 `build/macos` 属于待应用退出后清理的旧路径。
- 读取文本文件显式指定 UTF-8。

## macOS 交付

- Mac 打包先进入 `build/staging/macos`，版本、签名、原生界面冒烟检查和 ZIP 解压校验通过后，交付到 `build/dev/macos`。
- Windows 与 Mac 共用一份 `latest.json`：Windows 字段保留在顶层，Mac 信息放在 `macos` 对象中；更新本平台字段时保留另一平台字段，不生成独立的 `latest-macos.json`。
- Mac 自动更新只下载 `Codexio.app.zip`，校验后等待应用正常退出，在旧 APP 的原路径放入新版并启动。新进程确认启动成功后删除旧 APP；失败时恢复旧版，未确认成功前保留回滚能力。
- Mac 端暂不创建悬浮窗，不运行 Windows EXE 更新器。版本号与本地提交、远程发布约定继续共用。

# 版本管理

- 修改功能或修复问题时默认保持当前版本号。新增或递增版本号前，必须先取得用户明确确认。

# 当前开发流程

- 当前开发版本为 `0.2.4`，默认仅在本地进行。
- 每次修改完成并通过验证、开发打包后，提交到本地 Git；开发打包不等于确认正式发布。
- 未经用户新的明确授权，不执行 `git push`、GitHub 发布或其他远程变更。

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
