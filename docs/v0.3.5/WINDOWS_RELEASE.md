# Windows v0.3.5 补充发布

2026-10-01，用户明确授权推送仓库，并向既有 `v0.3.5` Release 增加 Windows EXE；随后确认 Windows 本体也升级为 `0.3.5`，以匹配既有更新器的版本／下载路径规则。

## 本次范围

- 合并本地 Windows Wails + Go 工作与远程 Mac/iOS 提交，保留两边历史；只解决 README 和云端文档冲突。
- Windows `windows/VERSION`、前端包版本、EXE 资源和进程清单更新为 0.3.5。Mac 0.3.5 和 iOS 0.3.1 沿用既有已发布包；旧 Python 版本保持原值。
- 复用 `build_exe.ps1`，以现有正式 Release 的 `latest.json` 为清单基线，保留其 Mac/iOS 字段。
- 新增 `--append-windows` 发布协调分支。默认先核验本机 EXE、源码指纹、三项冒烟、既有 ZIP/IPA 的 Bundle/架构/CRC/哈希，再推送 main、上传 EXE、更新共享清单。不会改写现有 Tag、标题、正文或 Apple 二进制附件，也不触发旧 CI。
- 上传完成后重新下载全部四个附件，与候选逐字节哈希比对，通过后归档；既有运行中的开发 EXE 仍按原规则保留。

## 协调命令

```powershell
.\build_exe.ps1 -Version 0.3.5 -ManifestPath build/staging/release-v0.3.5-windows-existing/latest.json
.\.venv\Scripts\python.exe -X utf8 scripts/publish_release_from_macos.py --version 0.3.5 --append-windows --prepare-only
.\.venv\Scripts\python.exe -X utf8 scripts/publish_release_from_macos.py --version 0.3.5 --append-windows --confirm-publish
```

发布记录保存到 `build/checks/release-v0.3.5-windows.json`；正式归档为 `release/0.3.5/`。Tag 仍指向原 Mac/iOS 发布提交，新增 Windows 源码以此次推送的 main 提交为准。

若远程已经完成而本地归档未完成，使用相同命令的 `--append-windows --sync-only` 只下载并校验，不重复推送/上传。清单替换删除已核验的精确资产 ID，然后以不覆盖同名文件的方式上传；并发发布者的新清单不会被按名称删除。失败暂存及原清单保留在 `build/staging`，可用于核对恢复。

## 候选包校验

Windows 0.3.5 / amd64，46,460,416 字节，SHA-256 `085d97fe521123cb5e2d09d88403f5426074215a743216f290cb8118f7f0fb47`。前端 0 errors / 0 warnings，开发构建、原有三项隔离冒烟、PE 版本/架构与源码指纹检查通过；发布协调 `--prepare-only` 已验证候选四附件。现有 Mac ZIP SHA-256 为 `4ee4472182c1cb171259ae15f2c18b751d319dffc2f7b276f82e215a980ce52d`，IPA 为 `6db464a680c4afbf78c27214f73fe35e5f1c9ee4dc52821cd509ffe775cfa5fc`，保留不替换。
