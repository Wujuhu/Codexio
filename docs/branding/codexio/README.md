# Codexio — Quantum "X" Core

当前应用采用用户提供的 Quantum "X" Core 方案：深色圆角容器、白色代码括号、暖金色 X 与白色中心节点。

几何与配色沿用原稿；扩大了辉光滤镜的画布范围，避免柔光边缘被裁成方形。

- `codexio-icon.svg`：原始应用图标矢量。
- `codexio-icon.png`：512 × 512，保留容器外透明区域。
- `Codexio.ico`：16、24、32、48、64、128、256 像素的 Windows 图标。
- `codexio-lockup.svg`：用户提供的横向品牌组合。

应用使用同一套图标资源覆盖 EXE、窗口标题栏、托盘、侧栏和更新程序。导航箭头等功能图标保留各自含义。

修改矢量后，可在安装 `sharp` 的 Node.js 环境运行 `node scripts/render_brand_assets.cjs` 重新生成 PNG 和 ICO；应用运行时不需要 Node.js。
