# iOS 开发前的 Mac 中间构建 · 2026-09-28

程序版本保持 **0.3.1**，Widget 构建 **19／1.18**。这是 A～G 前置修复的本地开发包，等待用户测试确认；本轮不开始 iOS，不推送、不发布。

## 修复内容

| 项目 | 本次实现 |
| --- | --- |
| A 日志初始位置 | `CompactScrollView` 在原生布局完成后按实际表头 inset 定位顶部；筛选／分页结果携带独立的重置键，普通数据刷新保留滚动位置 |
| B 弹出面板字号 | 今日费用由 22 pt 增大到 28 pt，与 Token 使用相同字重、等宽数字和基线 |
| C 菜单栏垂直位置 | 实际状态项与设置预览共用渲染组件，内容整体向下偏移 1 pt；品牌 SVG 不变 |
| D 中断后切模型续跑 | 识别结构化 `model_switch.instructions`，结合原用户消息归属与 `interrupted` 前驱建立 continuation；证据不足以合并但可确认正文时只继承预览；后续独立用户消息撤销推断关系 |
| E 导航与标题 | 导航图标／文字整体居中；六页标题移到统一固定区域，内容与工具栏共用 32 pt 横向边距，页面正文单独滚动 |
| F 数字与状态图标 | 顶部菜单栏全部数字统一 14 pt medium；运行点阵环及完成勾圈由 21 pt 改为 20 pt，猫形 Logo 仍为 18 pt；保留 8 步／1.6 秒原生动画 |
| G 折线削顶 | 共用 `TrendChart` 给四边留出描边／抗锯齿空间；网格、曲线、悬停命中和指示线使用同一绘图区 |

请求恢复复用 `v0.2.10:src/codexio/usage_collector.py` 的用户消息归属、`user_requests.py` 的 continuation 根请求与分段耗时机制。新 `RequestResume.swift` 只保存当前／前一执行段和原始用户预览，不按时间相近或文本相同合并。`Analytics.swift` 用最终续跑的状态覆盖已中断的根段状态；各次调用仍按唯一 ID 计量。

旧数据修复以已有账本中缺少预览的 session 为候选，仅在对应文件的旧索引游标上定向补读元数据一次。补读不重放计量、不重建历史账本；游标写入 `resume_metadata_version`，之后仍走文件变化检测与增量读取。

## 已完成的必要核对

- Mac 开发打包通过原有三个隔离冒烟：启动、基本数据显示、主窗口关闭与重开。APP／Widget 版本、ARM64 架构、完整签名、ZIP 解压与清单哈希核验通过。
- 使用生产 `CompactScrollView` 做一次隔离原生控件核对：顶部为 `-28 pt`，与实际 `28 pt` 表头 inset 一致；普通布局保留 `300 pt` 滚动位置，明确重置后回到真正顶部。
- 在本机日志与账本的隔离副本中核对 2026-09-27 22:43 中断、22:50 切模型续跑的案例：两段合为一条，原请求预览保留、状态 completed、5 个唯一调用、累计运行 72 秒；Token／费用总量不变，不包含中断等待时间。
- 同一次定向核对确认：前驱没有明确 interrupted 证据时只补预览、不合并；当前段有独立用户输入时不继承；文件不变的后续扫描不增加 ledger revision。
- 只查看一张隔离概览预览：`build/checks/pre-ios-ui/overview-zh.png`。未生成页面／截图矩阵，未新增维护性测试套件或修改既有三项冒烟。
- 未启动、停止或替换用户运行中的 `/Applications/Codexio.app`，没有修改真实 Codex 配置或直接修补用户账本。用户自行打开开发 App 后，索引器按上述规则处理旧预览。

## 开发交付

- APP：`build/dev/macos/Codexio.app`
- ZIP：`build/dev/macos/Codexio.app.zip`
- 合并开发清单：`build/dev/macos/latest.json`
- 构建日志：`build/logs/pre-ios-mac-build.log`
- 三项冒烟结果：`build/checks/macos-smoke/result.json`
- ZIP 大小：1,612,651 bytes
- ZIP SHA-256：`fb90d98a6066fc29d50a1ca78a4ea02160f0186dc34dc6c146205ac668b14f5b`

开发清单顶层 Windows 字段保持原样，只有 `macos` 对象对应本次 0.3.1 开发包；它不是正式跨平台发布清单。未生成 Windows EXE、iOS IPA 或正式 Release 目录。

## 用户确认重点

请在自行退出旧版并打开开发 App 后，确认日志首行完整、六页标题固定、侧栏居中、菜单栏数字及垂直位置、三处折线峰顶，以及上述历史续跑请求的预览／归组。菜单栏 1 pt 下移和 20 pt 状态图标的真实观感仍以用户环境的测试反馈为准。用户确认前不开始 iOS。
