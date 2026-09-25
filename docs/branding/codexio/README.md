# Codexio — Cobalt "X"

当前应用采用 2026-09-25 的 Cobalt "X" 方案：纯白背景、石墨色代码括号、浅钴蓝至深蓝渐变 X 与白色中心节点。应用内品牌、macOS APP／Dock、Windows EXE、窗口和托盘共用同一矢量源。

- `codexio-icon.svg`：原始应用图标矢量。
- `codexio-icon.png`：512 × 512，与矢量源一致的白色背景版本。
- `Codexio.ico`：16、24、32、48、64、128、256 像素的 Windows 图标。
- `codexio-lockup.svg`：与 Cobalt "X" 主图标一致的横向品牌组合。

应用使用同一套图标资源覆盖 EXE、窗口标题栏、托盘、侧栏和更新程序。导航箭头等功能图标保留各自含义。

修改矢量后，可在安装 `sharp` 的 Node.js 环境运行 `node scripts/render_brand_assets.cjs` 重新生成 PNG 和 ICO；应用运行时不需要 Node.js。
