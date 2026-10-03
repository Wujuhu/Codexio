# Python 工具与历史规则参考

当前 Codexio 的桌面界面由 macOS Swift／SwiftUI 和 Windows Go／Wails 实现。此目录不再提供 Python／PySide6 桌面程序，不能整目录删除：当前启动、构建、发布和资源生成仍有依赖。

## 正在使用的工具

| 文件 | 用途 |
| --- | --- |
| `__main__.py`、`native_launcher.py` | `run_macos.sh` 的原生 Mac 开发包启动转交，不创建 Qt 界面 |
| `app_archive.py`、`macos_updater.py` | Mac APP ZIP 安全解包、版本、签名和架构校验 |
| `updates.py`、`i18n.py` | 清单、版本、SHA-256 与交付错误信息等共享工具 |
| `update_installer.py`、`settings.py` | 上述 Mac 校验模块的传递依赖，暂时保留 |
| `__init__.py` | 历史版本常量，按仓库规则保持冻结；不能据此判断当前 Mac／Windows 版本 |

这些工具没有必须安装的第三方 Python 依赖。当前构建不需要旧版 PySide6 运行时。Windows 应用使用项目根目录的 `run.ps1` 与 `build_exe.ps1`。

## 保留的参考文件

按 `AGENTS.md` 的复用要求，原位保留以下文件：

- `usage_collector.py`：目录、文件和 WAL 变化检测。
- `usage_worker.py`：dirty、发布签名和时间边界。
- `usage_queries.py`：增量计价、物化索引及有界分页。
- `pricing.py`：计价规则与稳定价格版本。
- `user_requests.py`：请求身份、别名和续接归属。
- `desktop_widgets.py`：`LedgerDelegate` 等原有展示机制。

这些文件用于阅读和机制核对，已不构成可运行的旧应用；不为它们保留整套 UI、采集线程和第三方依赖。当前文件包含部分后续修正，不能与 v0.2.10 混为同一版本。需要运行完整历史实现时，应从相应 Git 版本恢复独立工作副本。

例如，可只读查阅原始案例：

```bash
git show v0.2.10:src/codexio/usage_collector.py
git show v0.2.10:src/codexio/desktop_widgets.py
```

## 共享资源

`icons/`、`pricing_seed.json` 与 `translations.json` 仍由当前平台构建使用。不要随旧 Python 界面一起删除、移动或重命名。各平台实际版本分别读取 `macos/VERSION`、`windows/VERSION` 和 `ios/VERSION`。
