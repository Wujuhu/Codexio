# 打包与交付

- `dist` 内只保留一个 EXE，固定为 `dist/Codexio.exe`，不创建 `update`、`refined` 或其他多版本目录。
- 先构建到 `build/release-staging`，成功后用 `scripts/publish_exe.ps1` 发布；临时产物和旧包只放在 `build` 下。
- 使用 `build_exe.ps1` 完成构建和发布。目标被运行进程占用时保留原文件及暂存新版，不为绕过占用新增 `dist` 副本，不自动结束用户进程。
- 读取文本文件显式指定 UTF-8。

# 版本管理

- 修改功能或修复问题时默认保持当前版本号。新增或递增版本号前，必须先取得用户明确确认。

# 当前开发流程

- 当前开发版本为 `0.2.4`，默认仅在本地进行。
- 每次修改完成并通过验证、打包后，提交到本地 Git。
- 未经用户新的明确授权，不执行 `git push`、GitHub 发布或其他远程变更。

# GitHub Release 发布约定

- 仅在用户明确授权发布后执行。Tag 和 Release 标题统一为 `v<版本号>`，例如 `v0.2.4`；正文留空，不添加更新说明或附件描述。
- 附件仅上传本次构建的 `dist/Codexio.exe` 和 `dist/latest.json`，确保程序版本、JSON 版本、文件大小及 SHA-256 一致。
- 先验证、打包、提交，再推送 `main`，确认远程包含本次发布的提交。新 Tag 基于远程 `main` 创建；已有 Tag 或正式 Release 不自动覆盖。
- 项目当前目录为 `C:\CodeWJH\Projects\Codexio`。PowerShell 示例（替换版本号，保留空正文）：

```powershell
"" | gh release create v0.2.4 `
  --repo Wujuhu/Codexio `
  --target main `
  --title "v0.2.4" `
  --notes-file - `
  "C:\CodeWJH\Projects\Codexio\dist\Codexio.exe" `
  "C:\CodeWJH\Projects\Codexio\dist\latest.json"
```

- 若需先核验附件，在创建命令中加 `--draft`，检查两份附件后执行 `gh release edit v0.2.4 --repo Wujuhu/Codexio --draft=false --latest`。严格空正文也可用 UTF-8 零字节文件配合 `--notes-file`，避免管道引入换行。
- 发布后核验 Tag 对应提交、标题、空正文及两份附件；使用 `git fetch origin tag v0.2.4` 将 Tag 同步到本地。
