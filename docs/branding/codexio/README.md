# Codexio — 猫形 C

2026-09-28 当前主图形取自用户提供的 `build/design/logo/Codexio-Logo-Pack/main/codexio-main.svg`，使用包内指定的黑白圆润版路径，保留猫耳、开口 C 与尾巴。原先的 `reference-cat-c.png` 和横向锁定字标文件作为历史参考保留。

## 居中与占比

参考图画布为 1254 × 1254，用户确认其画布中心就是所需的视觉中心。图标将原图中心 `(627, 627)` 映射到 512 × 512 图标的中心 `(256, 256)`，围绕该点等比放大；不以黑色图形的外接矩形重新居中。

APP／Dock 默认主图使用 `translate(256 256) scale(512/976) translate(-627 -627)`，图形宽度约占图标底板 75%，比旧版约 77% 略小；1254 参考图外围的展示背景不计入底板比例。菜单栏单色版保留 `.54` 变换、原 viewBox 与 18 pt 显示框，只换新主图形，保留同一参考中心。

## 资源

- `codexio-icon-light.svg`／`.png`：白底黑图，App／Dock／EXE 使用这一版。
- `codexio-icon-dark.svg`／`.png`：黑底白图，深色界面使用。
- `codexio-icon.svg`／`.png`：浅色版的通用入口。
- `Codexio.ico`：16、24、32、48、64、128、256 像素的 Windows 图标。
- `codexio-lockup.svg`：历史横向组合参考；当前 Mac 字标使用下述最终用户素材。

应用的源文件在 `src/codexio/icons/`。`brand-mark.svg` 为透明单色模板，菜单栏和小组件提示使用相同猫形，不另画不同图形。功能导航图标保留各自含义。

PNG 母版为 1024 × 1024；Mac 打包从浅色母版生成 ICNS。Windows EXE、窗口、托盘采用白底 App 图标，界面内品牌按主题选择黑白版。

备选图位于 `src/codexio/icons/app-icons`，包含包内 12 款 SVG 的裁切包装源、1024 px PNG 和 160 px 预览。仅裁去图标底板外的展示画布，保留提供的渐变／材质矢量内容，不重新绘制；材质 SVG 为用户素材包的描摹版。构建只打包 PNG，运行时选择页加载小预览，确认时后台加载所选大图。

macOS 外观页用 13 张图片（含主图）选择，点击只更新局部待选状态；下方“确认应用”才保存设置并切换侧栏品牌与运行时 Dock 图标，下次启动恢复选择。使用系统 [NSApplication.applicationIconImage](https://developer.apple.com/documentation/appkit/nsapplication/applicationiconimage)，不修改已签名 App 内部的图标资源；Finder 中应用包的默认图标仍为主图。菜单栏和 Widget 品牌使用固定主图模板。

修改 SVG 后可运行既有 `node scripts/render_brand_assets.cjs` 生成 PNG／ICO；应用运行时不依赖 Node.js。打包前核对浅／深色源保持相同路径和中心变换。

重新导入原素材包时使用 `node scripts/render_brand_assets.cjs --logo-pack build/design/logo/Codexio-Logo-Pack`；导入主图时不改任务状态动画资源。材质原 SVG 已随本仓库的包装源保留，后续重新导出不依赖被忽略的 `build/design` 目录。

主窗口展开侧栏使用 `src/codexio/icons/wordmark.svg`，原样取自用户最终 `build/design/logo/Codexio-Wordmark-Final/codexio-wordmark.svg`。`render_brand_assets.cjs --wordmark-only` 仅生成对应透明 PNG，对称移除多余透明边距而不改变原画布视觉中心；运行时使用缓存的模板图跟随主题。高度框 28 pt，宽度随侧栏收缩且不超过 138 pt，保留搜索按钮及原导航位置。收起侧栏继续显示外观页选中的图形，Dock 与菜单栏主图形不因字标替换而改变。
