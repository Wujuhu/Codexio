# 打包与交付

- `dist` 内只保留一个 EXE，固定为 `dist/Codexio.exe`，不创建 `update`、`refined` 或其他多版本目录。
- 先构建到 `build/release-staging`，成功后用 `scripts/publish_exe.ps1` 发布；临时产物和旧包只放在 `build` 下。
- 使用 `build_exe.ps1` 完成构建和发布。目标被运行进程占用时保留原文件及暂存新版，不为绕过占用新增 `dist` 副本，不自动结束用户进程。
- 读取文本文件显式指定 UTF-8。

# 版本管理

- 修改功能或修复问题时默认保持当前版本号。新增或递增版本号前，必须先取得用户明确确认。
