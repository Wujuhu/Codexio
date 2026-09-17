# 打包与交付

- 两端产物统一按版本放入 `release/<版本号>/`（例如 `release/0.2.4/`），完整版本目录只有 `Codexio.dmg`、`Codexio.exe` 和一份合并的 `latest.json`。保留历史版本，不再向 `dist` 或独立平台目录交付安装包。
- 先构建到 `build/release-staging`，验证成功后再交付；构建中间文件、同版本旧包与验证数据留在 `build` 下。Mac 的 `.app` 仅供本地验证，位于 `build/macos/Codexio.app`。
- Windows 使用 `build_exe.ps1` 构建，并由 `scripts/publish_exe.ps1` 交付到对应版本目录；只处理当前版本，不清理其他版本。
- 目标被运行进程占用时保留原文件及暂存新版，不为绕过占用新增交付副本，不自动结束用户进程。
- 读取文本文件显式指定 UTF-8。

## macOS 交付

- macOS 使用 `build_macos.sh --dmg` 构建，先进入 `build/release-staging/macos`；版本、签名及原生界面冒烟检查通过后，将 DMG 和清单交付至 `release/<版本号>/`。
- Windows 与 Mac 共用一份 `latest.json`：Windows 字段保留在顶层，Mac 信息放在 `macos` 对象中；构建更新本平台字段时必须保留另一平台字段。不再生成独立的 `latest-macos.json`。
- 跨机器构建后，将两端同版本安装包和最后合并的清单归集到同一个版本目录；使用 `scripts/verify_release.py --version <版本号>` 核对三个文件，缺包、版本或哈希不匹配时不得发布。
- Mac 端暂不创建悬浮窗，不运行 Windows EXE 更新器。版本号与本地提交、远程发布约定继续共用。

# 版本管理

- 修改功能或修复问题时默认保持当前版本号。新增或递增版本号前，必须先取得用户明确确认。

# 当前开发流程

- 当前开发版本为 `0.2.4`，默认仅在本地进行。
- 每次修改完成并通过验证、打包后，提交到本地 Git。
- 未经用户新的明确授权，不执行 `git push`、GitHub 发布或其他远程变更。

# GitHub Release 发布约定

- 仅在用户明确授权发布后执行。Tag 和 Release 标题统一为 `v<版本号>`，例如 `v0.2.4`；正文留空，不添加更新说明或附件描述。
- 附件只使用已验证的 `release/<版本号>/Codexio.exe`、`Codexio.dmg` 和 `latest.json`，共三个文件。分别核对各平台程序与对应清单中的版本、文件大小及 SHA-256，不重新生成 Windows 独占清单。
- 先验证、打包、提交，再推送 `main`，确认远程包含本次发布的提交。新 Tag 基于远程 `main` 创建；已有 Tag 或正式 Release 不自动覆盖。
- 项目当前目录为 `C:\CodeWJH\Projects\Codexio`。PowerShell 示例（替换版本号，保留空正文）：

```powershell
"" | gh release create v0.2.4 `
  --repo Wujuhu/Codexio `
  --target main `
  --title "v0.2.4" `
  --notes-file - `
  "C:\CodeWJH\Projects\Codexio\release\0.2.4\Codexio.exe" `
  "C:\CodeWJH\Projects\Codexio\release\0.2.4\Codexio.dmg" `
  "C:\CodeWJH\Projects\Codexio\release\0.2.4\latest.json"
```

- 若需先核验附件，在创建命令中加 `--draft`，检查本次附件后执行 `gh release edit v0.2.4 --repo Wujuhu/Codexio --draft=false --latest`。严格空正文也可用 UTF-8 零字节文件配合 `--notes-file`，避免管道引入换行。
- 发布后核验 Tag 对应提交、标题、空正文及本次附件；使用 `git fetch origin tag v0.2.4` 将 Tag 同步到本地。
