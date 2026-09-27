# Codexio — 猫形 C

当前图标按用户提供的参考图重新绘制为 SVG：猫耳、开口的 C 形主体和上扬尾巴。源图保存在 `reference-cat-c.png`。

## 居中与占比

参考图画布为 1254 × 1254，用户确认其画布中心就是所需的视觉中心。图标将原图中心 `(627, 627)` 映射到 512 × 512 图标的中心 `(256, 256)`，围绕该点等比放大；不以黑色图形的外接矩形重新居中。

SVG 明确使用 `translate(256 256) scale(.54) translate(-627 -627)`，图形宽度约占画布 77%。菜单栏单色版使用同一变换，居中的正方形 viewBox，保留相同视觉重心。

## 资源

- `codexio-icon-light.svg`／`.png`：白底黑图，App／Dock／EXE 使用这一版。
- `codexio-icon-dark.svg`／`.png`：黑底白图，深色界面使用。
- `codexio-icon.svg`／`.png`：浅色版的通用入口。
- `Codexio.ico`：16、24、32、48、64、128、256 像素的 Windows 图标。
- `codexio-lockup.svg`：图形与产品名的横向组合。

应用的源文件在 `src/codexio/icons/`。`brand-mark.svg` 为透明单色模板，菜单栏和小组件提示使用相同猫形，不另画不同图形。功能导航图标保留各自含义。

PNG 母版为 1024 × 1024；Mac 打包从浅色母版生成 ICNS。Windows EXE、窗口、托盘采用白底 App 图标，界面内品牌按主题选择黑白版。

修改 SVG 后可运行既有 `node scripts/render_brand_assets.cjs` 生成 PNG／ICO；应用运行时不依赖 Node.js。打包前核对浅／深色源保持相同路径和中心变换。

主窗口字标使用透明 C 标记接 `odexio`，Mac 图形与字母间距为 1 pt，以图形代替文字 C；App 文件名和程序名称仍为 Codexio。
