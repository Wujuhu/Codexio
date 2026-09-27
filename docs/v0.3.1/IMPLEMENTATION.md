# v0.3.1 实施与交付记录

更新时间：2026-09-27。用户已确认设计审美并明确授权开发。当前完成本地开发与 Mac 开发包交付；正式推送和发布仍需开发验收后的第二次明确确认。

本次二次复查记录见 [REVIEW.md](REVIEW.md)；用户实际数据反馈后的性能、能耗和交互修复以 [PERFORMANCE.md](PERFORMANCE.md) 为准。

## 完成清单

- [x] Mac 运行端全量 Swift：原生设置、SQLite 兼容、只读增量扫描、请求归组、定价、本机统计和周期估值。
- [x] Swift App Server、逐张重置、官方套餐周期与聊天额度明细。
- [x] SwiftUI 六页、旧版导航图标、宽布局、较小额度数字、日志末列悬停详情。
- [x] Mac／Windows 中英文系统语言、系统字体；Widget 固定标签本地化。
- [x] 原生菜单栏图标加额度、Codex 组标题、Liquid Glass 与系统兼容材质。
- [x] 新增独立最小额度 Widget 的三种样式；旧请求小／中／大组件保留几何、颜色、字体层级和 kind。
- [x] 主进程拥有的刷新任务、唯一稳定宿主、Widget 注册、ZIP 更新与失败回滚；⌘Q 停止自有服务。
- [x] 原生上游转发及配置恢复，保留 provider 与鉴权配置。
- [x] 两端删除 SSH 采集、来源管理／筛选／列与历史归属入口；保留原始索引历史。
- [x] Windows 逐张重置、本机五项统计、活动图切换、服务端明细和双语文案。
- [x] 适配新版 ChatGPT/Codex 的 `codex-cli` 包路径，保留旧版路径、手动路径和独立 CLI。
- [x] Swift 编译、固定三项模拟冒烟、版本／签名／架构／ZIP／哈希核验、Mac 开发打包。
- [x] 更新开发说明与数据能力记录，并提交到本地 Git。

## 本轮具体交付

| 范围 | 实现 |
| --- | --- |
| Mac 主程序 | `macos/native/` 的 Swift 源码，SwiftUI/AppKit 直接运行，应用包不嵌入 Python、Qt 或 WebView |
| 界面 | 概览、日志、用量、订阅、定价、设置；日期／粒度／模型筛选；可复制的日志详情浮层与调用组成 |
| 本机统计 | 图 5 的五项指标、365 天热力图、Fast 与推理强度全部取本机已确认调用，时长仅含本机可靠运行区间 |
| 官方明细 | 图 6 按原周期边界与基点显示；图 7 用周限额和实际 Credits，展开模型、推理、速度构成 |
| 额度重置 | 完整列出逐张截止时间，用户选择后确认，固定 credit ID 和持久幂等键，超时保留重试入口 |
| 双语 | Mac String Catalog 与编译资源、Windows 集中文案；中文／英文按系统语言选择 |
| 小组件 | 请求三种尺寸保持原样，三个额度样式独立 kind；Widget 构建 16、短版本 1.15 |
| 参考资料 | 16 张参考图已在仓库本地，31 份静态 HTML 评审稿继续可查 |
| 文档 | README、Mac 开发说明、开发步骤和接口能力记录已更新到本次实现 |

### 新版客户端位置修复

本机安装为 `ChatGPT.app 26.924.22138`，其 Codex 包版本为 `0.158.0-alpha.2.1`。旧 `Contents/Resources/codex` 路径已消失。

新发现逻辑读取 `codex-package.json` 的受限相对入口，当前解析为：

```text
/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex
```

官方入口再执行 `codex-cli/CodexCLI.app/Contents/MacOS/codex`。同时兼容直接 CLI 子包、旧 App 布局、失效的旧手动路径与常见独立安装位置。仅探测 CLI，不启动桌面客户端。

只读连接确认已成功完成 `initialize`、`account/read` 和 `account/rateLimits/read`，能获得 ChatGPT 账户类型与窗口字段。能力记录：`build/checks/codex-discovery.json`。

## 验证结果与范围

1. **Mac 开发构建**：`./build_macos.sh` 生成并验证 ARM64 原生 APP、APP ZIP 和合并清单。
2. **Mac 固定三项冒烟**：程序启动、基本数据显示、主窗口关闭与重开；使用隔离模拟数据，结果 `ok: true`，运行时 `SwiftUI/AppKit`。
3. **Windows 共用 Qt 界面**：复用原有三项冒烟入口，在本机 offscreen 环境通过；此结果验证共用界面启动流程，不等同于 Windows 操作系统实机或 EXE 验证。
4. **交付校验**：APP 版本 0.3.1，Widget 构建号 16／短版本 1.15；原生架构、完整签名、ZIP 解压、清单大小与 SHA-256 通过构建校验。
5. **只读接口能力**：当前账户套餐历史返回周期和模型分组；聊天接口返回分组，样本状态为 `partial`，界面显示部分数据。
6. **源码检查**：Python 语法检查及 Git 空白检查通过。旧请求 Widget 的改动限于固定标签本地化、共用快照和新增独立 Widget 注册，原布局保持。

本轮没有执行真实“使用重置”，没有修改真实 Codex 配置，没有启动或替换用户已安装的 Codexio。安装、更新、真实重置与上游路由的生产动作保留到用户实际使用对应功能时执行。开发包的模拟入口隔离真实宿主接管、LaunchAgent 和注册操作。

按照用户要求没有新增测试套件、额外测试项、UI 遍历或截图矩阵。仅沿用三项基础冒烟，并在发现实际问题时做对应的必要检查。

## 开发产物

```text
build/dev/macos/Codexio.app
build/dev/macos/Codexio.app.zip
build/dev/macos/latest.json
```

校验结果分别在 `build/checks/macos-smoke/result.json`、`build/checks/qt-smoke/result.json`、`build/checks/codex-discovery.json`、`build/checks/account-report-capabilities.json`；构建日志在 `build/logs/build-macos-0.3.1.log`。

开发清单只更新 `macos` 对象，顶层 Windows 字段仍为原有版本。正式发布时 Windows CI 才生成同版本 EXE 并替换为最终合并清单；当前开发清单不是已经发布的 0.3.1 三平台附件证明。

## 后续关口

- 用户自行启动开发 APP，验收本机真实数据与界面；开发过程不代用户启动安装版。
- 用户第二次明确确认发布 v0.3.1 后，按 AGENTS.md 执行发布协调脚本，由 Windows x64 CI 构建正式 EXE。
- Windows 构建、同版本、签名／归档或哈希任一校验失败时保持草稿。
- 当前未推送、未创建 Tag／Release、未写入正式 `release/0.3.1` 目录。

本轮生命周期与猫形 C 品牌修改见 [LIFECYCLE_AND_BRANDING.md](LIFECYCLE_AND_BRANDING.md)。
