# Codexio · Mac ↔ iOS 互联与轻量同步开发设计

> **2026-09-28 实施状态更新**：用户已确认统一使用其 Cloudflare 账号与默认 workers.dev 地址，首阶段只开发主 App。现已增加原生 iOS 四页、同网扫码与 Mac 确认、固定证书 Local 连接、Worker／独立 D1 云端回退和未签名 IPA 构建。具体已实现范围、账号余量、与下述早期完整方案的差异及真机待验收项，以 [开发交付说明](DEVELOPMENT_HANDOFF.md) 为准；下文“尚未实现／等待选择”描述是早期设计阶段记录，不代表当前状态。跨公网首次配对、逐行游标协议、小组件／实时活动尚未实现，不以构建通过冒充真机完成。

> 修订：2026-09-28，第二轮架构与产品方案
>
> 当前代码基线：0.3.1 / `66a94d9`；Widget 20 / 1.19
>
> 目标版本沿用规划名 v0.3.3，**不是改版本号或发布授权**
>
> 已确认测试设备：iPhone 17 / iOS 27 / SideStore 个人签名
>
> 状态：前置方案与代码审计完成；Mac 已有开发包，用户最终验收待确认；手机、同步服务和 APNs 尚未实现／部署。

## 本轮先看这里

用户本轮要求：在正式开发 iOS 前，分析现有架构，明确互联、配对、轻量数据同步、原生 iOS 主界面，以及 Widget、灵动岛、实时活动的可行方案。iOS 要有最近使用记录，但不要详细日志。本文把这些要求转为可执行设计，未把旧文档中的建议当作新的实现授权。

本轮已完成：核对当前源代码与 Mac 前置交付记录；收紧同步字段和保留范围；查证免费额度、个人签名、后台限制；列出可直接复用的 Apple 示例与页面参考。**没有修改生产代码、重复打包、启动用户安装版、创建 iOS 工程、部署 Cloudflare、推送或发布。**

重要修订：

- 删除原稿“90 天请求摘要＋调用摘要＋Local 可看详细日志”的范围。新建议是**最近 7 天且最多 200 条请求简表**；Local 和 Cloud 都不提供完整日志／正文／调用明细。
- 小时桶收紧为最近 48 小时；日桶保留 90 个 Mac 本地自然日。模型排行、聊天排行、速度／思考维度、完整定价和订阅历史不列入首版。
- Cloudflare D1 Free 是账户总计 5 GB，**单库只有 500 MB**。不能按一个 5 GB 数据库设计；日写入次数比本方案存储体积更值得关注。
- 免费账户不能使用 APNs，但不能因此断言 Widget／App Groups 一定不可用：Apple 当前能力表及 Food Truck 的 Personal Team 示例支持把小组件列为目标；SideStore 重签后仍需实机核对扩展与共享容器。
- 用户希望“App 不划掉、放后台也能刷新”。**后台驻留不等于持续执行**：免费方案可加入系统机会式刷新，但不能承诺固定间隔；锁屏后及时更新灵动岛需要支持 APNs 的签名与服务端方案。这个取舍尚未获得用户最终确认。
- 以下数字上限、默认页面、刷新间隔是建议参数，不是已经测得的性能，也不是已确认的全部产品选择。

仓库主文档：`docs/ios/MAC_IOS_SYNC_DESIGN.md`；用户 Downloads 原文件保持同内容副本。后续修改应同时同步，避免两套相互矛盾的方案。

## 开发前置：先修复当前 Mac 问题，再开始 iOS

执行顺序固定为：**先修复下面 A～G 七项当前 Mac 问题，完成必要验证、Mac 开发打包和本地 Git 提交，交由用户测试确认后，再进入本文的 iOS／同步工程开发阶段（包括最小真机能力验证）。** 用户本轮已授权先进行架构与产品准备；准备文档不代替验收，也不作为推送、发布或修改版本号的授权。

2026-09-28 实施状态：A～G 与下述后续验收调整已完成本地中间构建，程序版本 0.3.1，最新 Widget 20／1.19（品牌图形更新），开发包位于仓库 `build/dev/macos`。原有三项隔离冒烟、版本／签名／ZIP／清单核验通过；截图中的中断／模型切换案例已在隔离副本中验证为一条请求，5 个唯一调用、72 秒有效运行，原预览保留且 Token／费用不重复。具体交付记录为仓库 `docs/v0.3.1/PRE_IOS_FIXES.md`。**当前等待用户测试确认，iOS 尚未开始。** 下文原因分析保留修复前的代码事实作为对照；F 项最终采用 20 pt 状态图标，C 项采用整体向下 1 pt。

后续验收调整：收起侧栏显示 28 pt Logo，并保持导航项的固定位置；菜单面板使用完整圆角裁切；同一账户的菜单栏额度保留上次成功值，部分窗口缺失时分别回退，并保留真实更新时间／错误提示，不延长正式额度新鲜度或跨账户复用。主 Logo 改用用户 `Codexio-Logo-Pack` 的指定图形，底板内占比约 75%，保留参考画布中心；外观页显示主图及 12 款备选图片，点击只选中，下方“确认应用”才保存并更新侧栏／运行时 Dock，取消可恢复当前选择。菜单栏尺寸保持原规则，固定使用主图模板；这些变化与 A～G 一同等待用户测试。

窗口尺寸的后续验收调整：Mac 最小窗口由 1000×700 降为 720×480 pt；窄页面采用原生紧凑筛选、纵向分组和两列指标卡，表格／热图按需横向滚动。保留标题固定对齐、导航图标位置及宽窗口原排版，不通过缩放全部界面来适配。已更新本地开发包并完成既有三项冒烟，等待用户测试。

### A. 日志表格初次进入时没有停在真正顶部

- 现象：截图实际是“日志”页。进入页面后第一条记录上半部分被固定表头遮住，必须再向上滚动才能看全。
- 已定位原因：`macos/native/CompactTable.swift` 的 `updateNSView` 在创建／重建列时执行 `scroll.contentView.scroll(to: .zero)`。原生表头由 AppKit 计入 clip view 的 `contentInsets.top`，坐标零点不是视觉顶部。
- 已做的隔离核对：32 行模拟原生表格，表头 inset 为 28 pt；正常布局顶部为 `y = -28`，执行当前归零代码后变为 `y = 0`，按实际 inset 恢复后回到 `-28`。此为原生控件机制核对，未启动用户安装版。
- 修复要求：在有效布局完成后，使用实际内容边距定位顶部，不写死 28 pt。初次进入、用户切换筛选／分页时回到首行；普通后台数据刷新保留浏览位置。区分用户操作和数据 revision，不能每次更新都强制滚到顶。
- 保留原生列宽调整、可见行复用和有界分页，不用重建整个页面解决滚动偏移。

### B. 菜单栏弹出面板中今日 Token 与费用数字大小不一致

- 现象：中间“今天”卡片左侧 `117M` 比右侧 `$179.95` 大。这一项指弹出面板，不是屏幕顶部状态项数字。
- 已定位原因：`macos/native/MenuBar.swift` 的 `MenuBarView` 中，Token 使用 28 pt medium，费用使用 22 pt medium。
- 修复要求：**费用从 22 pt 增大到与 Token 相同的 28 pt**，保持同字重、等宽数字和首文字基线对齐。Token 不缩小；标签、金额精度和统计口径保持原有规则。较长金额也应正常显示。

### C. 屏幕顶部菜单栏整体偏高，向下微调至与相邻应用平齐

- 用户描述的“降低水平位置”结合截图解释为：降低 Codexio 状态项整体的**垂直位置**，让猫形 Logo、状态图标和周额度组成的一组内容与相邻应用处在相近的视觉水平线上；不是左右平移，也不是调整弹出面板。
- 当前实现：`macos/native/MenuBar.swift` 的 `layoutReadout()` 按 `NSStatusBarButton` 的几何中心放置 `MenuBarReadoutHost`；`MenuBarSettings.swift` 内部另以 Logo 参考偏移和文字 capHeight 对齐。内部字段对齐与整组在系统菜单栏中的垂直位置需分别处理。
- 修复要求：对整组内容采用统一、最小的向下光学微调；先核对 AppKit 视图坐标方向和 backing scale，具体偏移量以实际显示确定，不根据缩放截图武断写死像素值。设置预览与实际状态项继续复用同一渲染规则。
- 不修改猫形 SVG 参考中心、不重画图标，猫形 Logo 和周额度字号保持不变；按 2026-09-28 的最新要求，其他数字统一字号、任务状态图标略微缩小，具体见 F 项。整组位置调整与 F 项一起适配，也不通过动画或反复全局刷新维持位置。

### D. “任务记录”对应模型切换后的续跑，未归回原用户请求

2026-09-27 截图中的这一条已通过本机原始日志与只读账本核对：

| 本地时间 | 已确认事件 | 当前账本表现 |
|---|---|---|
| 22:43:39 | 用户提交“分析：好像现在版本的 codexio…”；模型为 GPT-5.6 Sol | 有用户正文的原始请求段 |
| 22:43:42 | 原运行段被中断，`turn_aborted.reason = interrupted` | 原段状态为 aborted，截图约 3 秒、无计量 |
| 22:50:55 | 新 `task_started`，分配新 turn ID | 被建立为另一个运行段 |
| 22:50:56 | 出现模型切换消息；结构化 `content_item_kinds` 包含 `model_switch.instructions`，上下文模型为 GPT-6 Astra | 没有新的用户正文；续跑段的 `prompt_preview` 为空 |
| 22:52:04 | 新运行段完成并回答同一个滚动位置问题 | 截图显示“任务记录”、1.02M 输入、1.41K 输出、$2.96、约 1 分 9 秒 |

结论：**这次确实是同一次用户请求在中断／切换模型后，续跑段没有归回原请求。** “任务记录”本身只是空摘要的占位文案，不能单凭这个文案判断所有同名记录都是错误。该续跑段已有明确的 session／turn 归属，调用也已在段内聚合；缺失的是“续跑段 → 原用户请求”的关系，不能把它当成缺少归属标识的未归属调用。

技术原因：这两段各自的 `root_turn_id` 都指向自身，没有直接跨段 root 链接；现有 `UsageIndexer.swift` 没有为这类模型切换恢复生成 `continuation_of`。`Analytics.swift` 的归组只沿 `alias_of`、`continuation_of`、跨段 root 或子任务父关系处理，因此两段保留为两行，空正文段回退成“任务记录”。

修复要求：

- 优先复用 `v0.2.10`／现有 Python `user_requests.py` 的 continuation 归组机制及当前 Swift 的根请求解析；补齐本案例的结构化模型切换恢复识别，不另外建立账本。
- 关联需要同一聊天内的明确中断／恢复证据、结构化模型切换元数据、原用户消息归属，并确认中间没有新的独立用户消息。`model_switch.instructions` 本身不含前驱 ID；单个标记、空摘要、相同文本或时间相邻都不能单独作为合并依据。无法唯一确定原请求时保留独立段并明确缺少归属证据。
- 归并后“用户请求”视图只计一条请求，沿用原始用户正文；“模型调用”视图仍保留各次实际调用、模型及计量。状态由有效续跑结果决定，不能因原段 aborted 将已完成请求误标为中断。
- **用户补充的最低交付要求：能合并的合并；即使暂时不好合并，也必须保留第一次记录，同时让第二次记录显示正确的原始用户请求预览，不能继续用“任务记录”代替已经能够确认的正文。** 本案例第二条应显示 22:43 提交的“分析：好像现在版本的 codexio…”请求预览。
- 将“请求预览来源”与“计量归组关系”分开保存和处理：能够确认续跑使用的原始用户消息、但尚未完成安全归组时，先补齐第二段的预览来源；保留第一段及第二段各自的 ID、状态、模型和计量，不因补预览复制调用、Token 或费用，也不因两条预览相同便自动合并。预览缺失不能成为延后这项显示修复的理由。
- 预览优先读取当前段的真实用户正文；当前段没有新用户消息时，沿已确认的原消息／恢复上下文取得正文，复用现有 My request 提取与附件包装过滤。只有原文确实缺失或来源无法确认时才保留诚实的缺失提示，不拿聊天标题、无关的上一条消息或推测文字冒充请求正文。
- Token／费用按唯一调用 ID 汇总一次；保留多模型事实，不把所有调用改写成最终模型。时长仅累计可靠运行区间，不把 22:43 至 22:50 的等待间隔当作运行耗时。
- 对本案例已有缓存优先定向修复相关聊天／运行段的关系；若需要补读原日志，仅处理相关文件，不为补标题或关系重新扫描全部历史。
- 本案例验收允许两种展示结果：① 一条合并后的原用户请求，计量去重且状态正确；② 暂时保留两条，第一次记录仍在，第二次展示同一原始请求的正确预览，各段计量保持独立。两种结果都不得因文案修复重复计费；若采用②，跨段请求数归一仍作为后续待办明确记录，不能声称归组已完成。
- iOS 同步开发必须等待此项修复，避免把错误请求数和拆分记录同步到手机及云端。

### E. 左侧导航靠左且展开／收起图标位置一致，统一六页标题与侧栏开关

2026-09-28 用户验收后明确调整：左侧每个导航项改回靠左对齐，展开／收起时图标保持同一位置，展开仅在图标右侧显示文字。此要求取代之前的整体居中及向左光学微调建议。顶部“收起／打开侧栏”按钮与每页标题左对齐、六页标题位置固定的要求继续保留。

已确认的代码现状：

- `MainViews.swift` 的 `SidebarNavigationRow` 把尾部 `Spacer(minLength:0)` 放在图标／文字后面，并使用左 11 pt、右 26 pt 的不对称 padding，使整组偏左。右侧排序把手是另一个 overlay。
- 顶部工具栏横向 padding 为 32 pt；概览、日志、定价外层 padding 为 26 pt，订阅、设置为 32 pt，用量标题另设横向和顶部 32 pt。页标题虽共用 `PageHeading`，外围布局仍不一致。
- 概览、订阅、设置把标题放在外层 `ScrollView` 内；日志、定价使用固定 `VStack`；用量把标题放在内容滚动区外。标题固定方式和容器边距需要一起统一，不能只按截图中页面的分组逐个挪位置。

修复要求：

- 展开和收起共用固定的图标左边距，侧栏宽度拖动时也不移动图标；展开时在右侧增加文字，中英文遵守同一规则。20 pt 图标左沿固定为行外 8 pt＋行内 13 pt＝21 pt，兼容 62 pt 收起宽度；顶部占位高度一致，切换时图标也不发生纵向跳动。
- 排序把手继续在右侧独立定位，出现／隐藏时不挤动图标或文字；窄侧栏为文字截断和把手保留必要空间。保留原生导航点击命中和既有排序流程，点击与拖动分开。
- 六页共用同一套页面标题布局和边距常量，推荐以现有工具栏的 32 pt 作为统一横向基准；统一标题顶部距离、字号和基线，不再散落 26／32 pt 两套外边距。
- 顶部“收起／打开侧栏”按钮的可见图形左沿与页面标题左沿对齐。按钮命中区可以保持正常大小，不用缩小点击区域换取对齐。
- 标题区采用一致的固定布局，页面正文在标题区下方按需滚动；定价“立即同步”等操作放在同一标题行，不改变标题基线。切换页面、用量子页或设置分类，显示空状态、加载状态、错误文案时，标题都不能上下左右跳动。
- 展开／收起或拖动侧栏时，按钮与标题随内容区域一起移动，并持续共享同一左边距；不要求侧栏宽度变化后仍固定在屏幕绝对坐标。布局变化不触发业务扫描或重新聚合。

### F. 顶部菜单栏所有数字统一为周额度字号，任务状态图标略微缩小

这一项针对屏幕顶部状态项及设置中的“实时预览”。B 项针对弹出面板“今天”卡片，两项字号规则分别适用。

- 已定位原因：`MenuBarSettings.swift` 的 `MenuBarReadout` 使用 `field == "week" ? 14 : 12`，周额度为 14 pt，5 小时额度、今日／当前任务费用和 Token 为 12 pt，所以截图中的 `89%`、`$0.00`、`0` 显示大小不一致。
- **以用户认可的周额度为基准，所有数值字段统一使用 14 pt medium、等宽数字和相同 capHeight 对齐规则。** 覆盖周额度、5 小时额度、今日费用／Token、当前任务费用／Token；后续新增其他数字字段也默认使用同一共享样式，不再逐字段指定较小字号。周额度本身不放大或缩小。
- 费用／Token 字段继续仅显示数值，设置字段名称与无障碍说明保留含义。未知值占位使用相同样式；金额较长时由状态项宽度适应，不能为塞进宽度而单独缩小费用字号。
- 当前运行点阵环和完成勾圈的显示框均为 21×21 pt。两者一并略微缩小，初始建议采用 20×20 pt，最终以实际视觉核对为准；猫形 Logo 仍为 18×18 pt。使用统一尺寸常量覆盖 SwiftUI frame、原生 intrinsic size、图层 bounds 和模板绘制尺寸，避免外框缩小而内部图层仍为 21 pt。
- 设置预览与实际菜单栏继续使用同一组件。缩小后与 C 项的整体向下微调共同核对，保留数字及图标的视觉中心对齐。
- 点阵环继续使用原 SVG、8 步／1.6 秒原生合成动画，完成时仍使用 `completed.svg`，不增加可见的“加载中／已完成”文字。缩放不能改变旋转中心；减少动态效果保持静态图，完成或取消状态字段后停止动画。

### G. 菜单栏、概览和用量页折线图峰顶被裁切

- 现象：蓝色 Token 线和绿色费用线最高处有削平／缺边，三处图表均出现。
- 已定位的共用原因：三个入口复用 `Components.swift` 的 `TrendChart`。其纵坐标为 `height - amount / maximum × height`，各自最大值恰好位于 `y = 0`；曲线使用居中的 2.2 pt 描边（菜单面板紧凑版 1.8 pt），因此峰顶仍有约一半线宽落在画布外。零值位于底边、首尾点位于左右边界，也存在相同的描边越界风险。
- 在共用 `TrendChart` 内统一定义带安全内边距的绘图区，将折线、网格和交互坐标映射到该范围；根据实际线宽、圆角端点和抗锯齿留出少量余量。最大值不再贴画布顶边，零值和首尾点的完整描边也需保留。不能只给外层卡片加空白而让曲线仍画到 Canvas 边界。
- 保留真实 Token／费用数值、日期范围及当前各自归一化的绘图口径；安全留白只是坐标布局，不能为留白修改数据或把标签上的最大值人为放大。
- `UsageHoverSurface` 的时间桶命中、竖向指示线和详情浮窗锚点使用同一绘图区。左右内缩后，仍要准确命中首尾日期及各个桶，不沿用旧的全画布宽度换算。
- 菜单面板紧凑图、概览趋势和用量趋势共用一次修复，不复制三套绘图逻辑；只做可见图的轻量坐标计算，不增加计时器、数据库查询或全局状态发布。

前置阶段沿用现有最小验证与开发交付规则：只对上述实际问题做必要核对，开发包完成固定三项隔离冒烟后不重复；不新增长期测试套件或截图矩阵，不启动用户已安装的 App。需要视觉核对时遵守现有单张相关截图的限制，不按六页／三处图表批量生成截图。表格、主窗口和菜单面板修复本身不要求修改 Widget 构建号；若最终实际修改 Widget UI、注册或宿主生命周期，再按既有规则递增。

---

## 0. 范围、目标和完成边界

产品定位：**Mac 的轻量只读伴侣**。手机回答四个问题：现在在做什么、额度还剩多少、今天用了多少、最近做了哪些请求。不是缩小版日志分析器，更不是远程控制台。

已确定：

- Mac 是唯一采集、请求归组和计价权威；iOS 不解析 Codex 日志、不登录 ChatGPT、不重新计价。
- 同一可达局域网优先直连；不可达才从 Cloudflare 读取。纯局域网功能不依赖 Cloudflare 注册成功。
- iOS 立即显示上次缓存；后台失败不清空已有数据，明确区分旧值、未知、离线与撤销。
- iOS 不执行额度重置、任务取消、远程命令、修改 Mac 配置，也不复制敏感凭据。
- Mac 关闭主窗口仍可同步，⌘Q、关闭同步或进程结束后停止一切自有同步。云端不能替 Mac 继续采集。
- Windows 同步生产端不在本阶段范围；现有 Windows/Mac 正式三附件规则不变。
- iOS 主 App、Widget、Live Activity 是正式设计目标。若选定签名方式实测不能满足某目标，应报告并与用户重新确定范围，不能把静默删掉功能当成“全部完成”。

尚未确认：后台时效要求与签名路线、是否允许短请求预览进入云端、7 天／200 条是否足够、四标签导航是否采用。推荐先支持 iOS 26+ 的原生 Liquid Glass，使用本机 Xcode 27 构建，主验证设备固定为用户的 iPhone 17 / iOS 27；不未经确认再扩大老系统或设备矩阵。

## 1. 当前架构审计：有什么，缺什么

本轮审计基线为 `66a94d9`，不是笼统地把旧 Python 方案套在 Swift App 上。

| 层 | 当前实现与已核对机制 | 手机互联如何复用／需要补什么 |
| --- | --- | --- |
| 文件发现与增量解析 | `macos/native/UsageIndexer.swift`：目录／文件签名、60 秒有界重新发现、增量 cursor；标题读取考虑 SQLite WAL | 保留唯一采集入口；网络层不能另扫日志 |
| 本地账本 | `Database.swift`：SQLite WAL、条件更新、ledger revision 触发器 | 保留事实账本；同步元数据另建表／库，避免上传 ACK 反过来增加账本 revision |
| 计价缓存 | `UsageSnapshotCache.swift`：ledger、价格内容、上游版本、日界和时区依赖；计价缓存有条数／字节上限 | 同步复用已计价结果；抓取时间不当价格版本 |
| 请求归组 | `RequestResume.swift`、`Analytics.swift`：alias、continuation、成员调用去重和有效耗时 | 手机只接受已经归组的根请求；同标题、相邻时间不是合并依据 |
| 页面投影 | `Presentation.swift`：后台计算、筛选键、12 项缓存、日志显示 60 行／页 | 复用参数失效和过期结果丢弃；手机请求简表分页 20 行 |
| App 调度 | `AppState.swift`：data/account/report 队列，扫描默认 10 秒、额度默认 60 秒；业务 revision 与时间标签分离 | 在数据管线末端接一个轻量导出器；不复制整个 AppState |
| 额度最后成功值 | `MenuQuota.swift`：同账户分窗口回退、真实观察时间、独立 retained 状态 | 手机采用同一显示原则；旧值不代表正式额度仍新鲜 |
| Mac Widget | `publishWidget()`：内容签名＋300 秒保活；32 KiB 文件；退出写离线 | 复用业务含义，不复用含 host_pid 的本机文件作为网络协议 |
| 生命周期与构建 | `Installation.swift`、`AppMain.swift`、`scripts/build_macos.py`：稳定宿主、mock 隔离、三项冒烟 | 生产同步仅由稳定宿主启动，mock 不监听、不广播、不联网、不访问生产 Keychain |

### 1.1 必须诚实指出的两个缺口

1. **当前“界面分页”不等于“全链路有界查询”。** `Database.recordPayloads()` 读取全部记录；`UsageSnapshot` 仍持有完整 calls／requests；`LogProjection.build` 在内存里过滤后截取一页。缓存减少重复计价，但不能把现状描述成每次只查 20 条数据库记录。
2. 当前并没有 `LocalSyncService`、云端 Worker、跨设备身份、持久远程 revision、iOS 数据库、APNs 或手机配对。原稿中的这些类都是规划，不是已存在功能。

实现方式：在现有后台 Analytics 构建过程中顺带产出必要的小型 `MobileProjection`，不另起一轮全历史遍历；再写入有界移动读模型。网络请求只读这个小库／不可变缓存。后续必要的物化查询复用 `v0.2.10:src/codexio/usage_queries.py` 的 generation、物化索引和分页机制；不借同步任务顺手重写整个 Mac 数据层。

### 1.2 目标分层

```text
Mac 唯一采集／账本／Analytics
              ↓ 已计算结果
      MobileProjectionBuilder
              ↓ 隐私字段白名单
      BoundedMobileStore + SyncCoordinator
        ↙                         ↘
Bonjour + TLS + 系统消息帧      URLSession HTTPS
        ↓                         ↓
        │                  Worker + D1 小型读模型
        └──────────┬──────────────┘
                   ↓
        iOS SyncCoordinator / RevisionArbiter
                   ↓
       主 App 本地缓存与 MobileRepository
          ↙                        ↘
       四个页面              App Group 精简快照
                                   ↓
                              iOS Widget
```

Live Activity 由 iOS App 通过 ActivityKit 更新；若采用 APNs 路线，远端另经 APNs 更新 Activity 的小型内容，不让 Live Activity 自己连接 Mac 或 D1。

共享模块仅含 Foundation／Codable 值类型、有限格式化和协议常量；Mac 的 AppKit、进程发现、安装接管、账本解析不编入 iOS。当前 `Core.swift` 混合了桌面职责，不整文件跨平台搬运。

## 2. 数据范围：只有“看得见、用得上”的字段

下面是建议默认值。保留窗口在 Mac 导出、Cloud 服务端与 iOS 缓存三处执行，不能只在 UI 隐藏。

| 数据集 | 手机用途 | 保留上限 | 单次传输建议 |
| --- | --- | --- | --- |
| live | 当前／最近任务、今日总计、两档额度、套餐简称、健康状态 | 每台 Mac 1 份 | 常见 2–4 KiB，硬上限 8 KiB |
| recent | 最近使用记录，按用户请求组 | 最近 7×24 小时且最多 200 条，取更小范围 | 每页 20 条；单条目标约 700 B、硬上限 1 KiB |
| daily | 7／30／90 天趋势、简易日历 | 90 个 Mac 自然日 | 一页最多 30 桶 |
| hourly | 今日／昨日的小时趋势 | 最近 48 小时 | 一页最多 24 桶 |
| metadata | 设备别名、协议版本、数据版本、保留边界 | 每台 1 份，小型设备授权表 | 与 sync 响应合并 |
| Widget / Activity 内容 | 主屏、锁屏、灵动岛 | 当前需要的展示字段 | Widget 目标 <4 KiB；Activity 目标 <1 KiB |

最近记录保留的是“完整请求组的汇总行”，不是模型调用。并发运行请求可能有多条：live 提供 `running_count` 和一个明确选定的当前请求；recent 中保留其他运行请求的简表。正常活动中的请求不因 7 天边界被误判结束；超过有证据支持的运行有效期转为未知／过期，而非永远运行。

首页 recent 只显示 3 条，不开内部纵向滚动。记录页按天分组、20 条增量加载；最多读取 200 条，页尾说明保留边界。记录被限量裁掉不影响 daily 和今日总计，也不能把“缓存中的 200 条”当作全量请求数。

首版不发送：

- 原始 JSONL、SQLite 数据库、完整用户 prompt／AI output、附件、工具调用与参数、代码 diff、文件路径。
- 每次调用 ID、上游 response ID、全部执行段、详细上下文、调试栈、账户邮箱、auth.json、Cookie、API key、refresh token。
- 聊天排行、模型／速度／思考维度历史、逐次调用列表、完整订阅历史、完整模型定价表。
- Mac 设置镜像、原始机器名和硬件序列号。设备显示名让用户命名，默认用通用别名。

**Local 也不开放隐藏的详细日志接口。** 手机点开一条记录，只把已有的简要字段排成一张详情卡；不会再从 Mac 拉完整内容。

## 3. 字段契约与统计口径

### 3.1 通用元数据

`protocol_version / schema_version / device_id / stream_id / source_epoch / privacy_epoch / dataset / revision / content_hash / generated_at`。

- `device_id` 随机生成，不使用邮箱、MAC 地址或 Apple Account；多台 Mac 独立命名空间。
- `stream_id` 是可重建同步流的身份。普通重启不改变；重置身份需要重新配对或明确确认。
- `source_epoch` 表示来源集合的重置；账户额度另有不透明 `quota_scope`。不能因切换 ChatGPT 账户就假装旧本机历史全属于新账户。
- 计数为非负整数；金额建议以 `cost_usd_micros` 整数传输，显示时格式化为美元；不重复同步格式化字符串。超出协议整数范围拒绝，不静默损失精度。
- 缺失为 null；真实 0、无请求、未加载、价格未知分别表达。费用为本地估算，不称为官方账单；未知部分附 `cost_complete=false`。
- hash 只覆盖稳定业务内容；生成／接收／扫描时间不导致整份业务数据改版。

### 3.2 live 白名单

| 对象 | 字段 |
| --- | --- |
| host | display_name、host_state、expected_heartbeat_seconds、source_time_zone |
| task | opaque_request_id、status、preview?、preview_state、model_label／model_count、effort?、speed?、started_at、duration_base_ms、running_since?、duration_valid_until?、total_tokens?、cost_usd_micros? |
| today | bucket_start/end、input_tokens?、cached_input_tokens?、output_tokens?、total_tokens?、user_request_count、cost_usd_micros?、cost_complete |
| quota | applicable、quota_scope、five_hour / weekly：remaining_percent?、reset_at?、observed_at?、fresh_until?、retained；error_code? |
| subscription | plan_label?、period_start/end?、stats_as_of?，均为已有数据，不能为此额外高频查询 |
| health | usage_observed_at、usage_state、quota_state、running_count |
| dataset versions | recent / daily / hourly revision、retention floor、是否截断 |

运行时长继续沿用 Mac 有效执行区间，不简单用“现在减去第一次请求时间”；中断到续跑之间的空档不计费也不算运行。系统动态时间只在有效区间内展示；过期后标“等待更新”，不能无限增加一个可能已经完成的任务计时。

额度失败保留同一 `quota_scope` 的最后成功值及观察时间；重置时间到达且未读到新额度，不自动补成 100%。换账户先清空旧额度／套餐映射。历史用量属于本机数据源集合，不默认归属于当前登录账户。

### 3.3 recent 最小行

```text
opaque_request_id, row_revision, started_at, ended_at?, status,
duration_ms?, total_tokens?, cost_usd_micros?, cost_complete,
model_label, model_count, effort?, speed?,
preview?, preview_state
```

- 不需要上传 thread_id、输入／输出全文、调用列表、上游原始 ID 或上下文详情。
- 单模型显示真实请求模型；多模型显示紧凑摘要及模型数，不把整组计量伪装成最后一个模型独自完成。
- 请求数／费用／Token 取 Mac 已归组结果，iOS 不二次归组。
- 单条如果超过字节预算，先缩短可选预览／模型展示文字；不删除 ID、状态和计量，也不突破硬上限。
- `preview_state` 至少区分 available、hidden_by_user、attachment_only、unavailable。隐私关闭显示“请求预览已隐藏”；原文确实缺失时显示诚实提示，不写虚假的“任务记录”正文。

短预览建议最长 80 个字符且最多 240 UTF-8 字节，先复用 My request 提取和附件包装过滤，再移除明显的密钥、邮箱、路径样式。**过滤不是自动脱敏保证**，短文本仍可能敏感；配对时由用户独立确认“同步请求短预览”，未确认默认不上传。锁屏／Widget 默认不展示预览，可另行选择。

### 3.4 聚合

每个桶包含 `bucket_start, bucket_end, row_revision, input_tokens, cached_input_tokens, output_tokens, total_tokens, cost_usd_micros, cost_complete, user_request_count`。

命中率用合并后的 cached/input 算，不对各桶百分比求平均。输入已包含缓存输入；总 Token 不再把缓存重复相加。活动日由真实记录确定，未同步日不是 0。

今日／日桶使用 Mac 的 IANA 时区；手机处于另一时区时显示“按 Mac 时区统计”，不能在缺原始明细时伪造手机本地日聚合。小时桶以明确 UTC 起止表示；夏令时当地一天可能 23／25 小时，不沿用原稿“一天最多 24 桶”。套餐周期及统计截至仍以 UTC 标注。

## 4. 配对与设备匹配

### 4.1 四种身份不能混用

1. **SideStore 安装配对**：用户用于签名／安装的设备资料，由 SideStore 管理，Codexio 不读取或上传。
2. **Codexio 设备身份**：随机 Mac ID、同步流 ID、TLS 公钥指纹。
3. **手机访问授权**：每台手机独立 reader_id，Local reader token 与 Cloud reader token 分离。
4. **Cloud 管理／Mac 写权限**：部署者的 Cloudflare API 凭据只在管理环境；Mac 只有自身 device 的 writer token，绝不交给 iPhone。

不以相同 Wi-Fi 名称、设备名字、IP 地址或相同 Apple Account 证明是同一台 Mac。Bonjour 用于发现候选，证书 pin＋配对 reader 权限才用于确认身份。

### 4.2 推荐的首次同网配对

1. Mac 设置 → iPhone 同步（放在“菜单栏”设置之前），用户明确开启同步；可选“仅局域网”或“局域网＋云端”。
2. 点“添加 iPhone”，生成 5 分钟、单次有效的二维码。使用至少 128 bit、建议 256 bit 随机加入 secret，不用可猜的六位数字当唯一认证。
3. QR 含协议、随机设备 ID、TLS 指纹、pair_id、加入 secret、过期时间；云模式附已允许的 HTTPS origin。没有 writer token、长期 reader token、真实账户凭据。
4. iPhone 用原生相机扫描组件读取，解释后请求 Local Network／相机权限；发现对应服务，先完成 TLS 身份校验再提交加入请求。
5. Mac 显示这台手机的请求并让用户确认；确认后签发仅可读取的 Local 权限。第二次消费同一票据失败。
6. 若启用了 Cloud，由 Mac 使用 writer 权限登记该手机的独立 cloud reader；在已经认证的局域网连接中交付云访问资料。Cloud 暂时失败不回滚本地配对，UI 显示“局域网已连接，云端待完成”。
7. 手机保存 pin 与凭据到 Keychain，先拿 live 和最近 20 条，再补必要趋势。设备变 IP、切换网络无需重配。

不要求安装全局根证书或允许任意不可信 TLS。不能为“扫码方便”关闭证书检查。

### 4.3 异网配对与后续启用 Local

Cloud 是可选配对中介：

- Mac 开着配对窗口，向 Worker 登记一次性高熵票据（服务器只存摘要／HMAC和期限）。
- 手机扫码访问经验证的 HTTPS origin，原子 claim；reader 先 pending，Mac 明确确认后才可读取。配对状态轮询只在这 5 分钟流程存在。
- 手机 reader secret 可由手机生成、仅将其 hash 登记；成功后客户端不依赖服务器长期保存明文 token。重试要绑定同一 claim attempt，避免响应丢失就生成两个 reader。
- 手机保留 QR 中的 TLS pin。以后发现该 Mac 时，在 pin 校验后的 TLS 连接中提交 Cloud reader 的持有证明；Mac 通过专用 writer verify 接口核对 secret 与当前授权，再登记独立 Local 权限。不能仅凭 reader_id、设备名字或“Cloud 曾配过”就签发 Local token，也不直接把 Cloud token 当成本地 token。
- Mac 无法访问云端核验时，不冒险接受新 reader；可重新走同网 QR 授权。已经建立的 Local 授权仍能离线使用。
- Mac 不在运行、票据过期、用户拒绝或证书不匹配时明确说明原因，不自动匹配同名设备。

仅局域网模式无需 enroll／邀请码／Cloud ticket。部署共享免费服务时，Mac enroll 需要部署者一次性邀请码与设备数上限，不能开放无限匿名注册。

### 4.4 撤销、续签和多设备

- 默认撤销某手机同时撤销两条通道；Local 立即断开，Cloud 写入撤销状态。Mac 断网时云撤销显示“待同步”，不能宣称已经全网生效。
- iPhone 收到 401/403 停止轮询，清除该授权和相应本地数据；主动移除设备清除缓存。无法抹除已经被对方复制的数据，不承诺远程擦除离线手机。
- 最后一个 reader 移除后可删除云数据。Cloud 关闭／预览隐私关闭需要持久的清除待办与结果，不是只停止上传。
- SideStore 续签尽量保留同一 Apple Account、Bundle ID、扩展 Bundle ID／App Group 映射，覆盖安装，不先删除 App。映射变化或凭据不可访问时提示重配，不拿别的缓存猜身份。
- 一部 iPhone 可配多台 Mac，一台 Mac 可授权多部手机；首版建议上限 3 台 Mac／每 Mac 3 个 reader。
- 默认只主动同步当前选中的 Mac；Widget 所选设备可另取小型快照。各设备缓存完全隔离，切换设备不混合旧回调；不直接相加账户周额度和可能重复来源的用量。

## 5. Local 优先与 Cloud 回退

### 5.1 使用现成网络能力

Mac 保留 macOS 15 基线，用 `Network.framework` 的 `NWListener / NWBrowser / NWConnection`；复用 Apple “Building a custom peer-to-peer protocol”的 Bonjour＋TLS生命周期。系统 WebSocket framing（`NWProtocolWebSocket`）作为默认消息边界，业务消息为有大小上限的 Codable JSON；不自己实现 TCP 拆包／HTTP Upgrade／重连线程。

Bonjour 服务 `_codexio._tcp`，TXT 仅广播随机服务 ID、协议版本和可选通用别名，不广播任务标题、额度或 token。App 信息文件按平台要求声明本地网络用途与服务类型，权限被拒绝时保留 Cloud；`NWPathMonitor` 的 satisfied 不能单独证明能连 Mac。

TLS 本地证书生成不是只调用一次 Keychain 就自动具备的能力。优先使用 Apple 的身份管理示例和 `apple/swift-certificates`，锁定可兼容工具链的 release／license；私钥放 Keychain，客户端固定 QR 中的公钥并核对证书用途／有效期。轮换有明确确认流程，不采用所有安装共用私钥或 verify-always-true。

参考：[Apple 网络示例](https://developer.apple.com/documentation/network/building-a-custom-peer-to-peer-protocol)、[系统 WebSocket framing](https://developer.apple.com/documentation/network/nwprotocolwebsocket)、[swift-certificates](https://github.com/apple/swift-certificates)。不引入 Python 网络守护程序、第三方全栈 Web 框架或 Multipeer Connectivity 新依赖。

### 5.2 连接状态机

`显示缓存 → 发现/认证 Local → Local 已连接`；Local 超时／不可达且 Cloud 开启时转 `Cloud`；都不可用则 `离线缓存`；认证失败单列 `需重新配对`。

- 冷启动先读本地缓存，给 Local 约 1–1.5 秒优先窗口；没有完成合法握手才发一次 Cloud GET。无局域网条件时直接 Cloud，不盲等。
- Local 就绪后停止前台 Cloud 轮询；保持一个有背压的连接，业务变化推送，约 30–60 秒低频 ping。页面切换不重建连接。
- 已连接后可按连接失败／约 3 秒有效超时回退；不能声称 Mac 睡眠后一律 3 秒内发现，实际检测还取决于 ping 和系统网络事件。
- 恢复网络时先尝试一次 Local；只有通过认证并收到不旧于本地缓存的数据后，才把来源标成“局域网直连”。加退避，不能网络抖动就反复切源。
- Cloud 旧数据不得覆盖 Local 新数据。即便 Cloud 暂未追上，也继续显示新缓存及同步状态。
- 同一 Wi-Fi 不保证互通：访客隔离、企业防火墙、VPN、禁止 Bonjour、Mac 睡眠都可能失败；不根据 SSID 直接判定 Local 成功。
- 不做 UPnP、路由器端口映射、公网暴露 Mac 或 Cloudflare Tunnel。Cloud 是小型数据缓存，不是到 Mac 的隧道。
- SideStore 的本机 VPN 只用于其签名／安装工作；不作为 Codexio 数据通道，也不替用户修改 VPN 配置。

### 5.3 本地消息与限额

仅 `hello/auth/subscribe/live/sync/query/page/ping/error`，无远程命令。认证前单消息 ≤4 KiB、握手限时；认证后单消息 ≤32 KiB，每页 ≤20 条，收到长度越界立即拒绝。慢读者只保留最新未发送 live；分页按需拉取，不累计整段变化历史。所有请求有 request_id、设备／epoch、取消和截止时间。

首版只传相同轻量 DTO，Local 不暗中放宽字段或保留范围。

## 6. 同步一致性：不倒退、不重复、不丢最终状态

### 6.1 持久版本

`stream_id + source_epoch + dataset_revision` 决定新旧，不用手机接收时间排序。Mac 在 SQLite 事务中保存新 revision、内容 hash 和待发送内容后才能发布，重启不归零。revision 限制在协议可精确表达的整数范围。

- 同流较新：接受；相同 revision＋相同 hash：幂等；相同 revision＋不同 hash：协议冲突，拒绝。
- 同流较旧：保留本地较新内容，不闪回。
- 新 stream／来源重置：只在已确认的身份操作后接受，不能任意用服务器返回值替换可信设备身份。
- 启动本地状态缺失时不能离线猜 revision；先读取服务端和本地持久记录对齐；两者无法恢复则显式新建 stream、让客户端确认。
- 跨请求回调带设备、流、来源、隐私 epoch 和本次连接 generation；切换设备／撤销后的晚到结果丢弃。
- live、recent、daily、hourly 独立版本，live 更新不能让所有历史图表重新下载。

序列化采用明确、稳定的字段顺序与数值规则；复用成熟 canonical JSON 机制或保存并哈希 Mac 生成的原始规范化 UTF-8 字节，不临时发明跨 Swift／JavaScript 的浮点 canonicalizer。hash 比较针对业务内容，不含网络来源和观察时刻。

### 6.2 增量与删除

Mac 维护有界 `outbox[dataset,row_id]`，同一行多次变化合并成最终 UPSERT／DELETE。**latest-only 只适用于 live 和同一行**，不能只留“最后一次 batch”而丢掉其他行的修订。

- 请求开始／终止写 recent，运行中的高频 Token 优先只改 live；recent 行内的活动计量可按较低频率补齐，已选请求用 live 合并显示。
- 已合并请求 ID 变化时，在一个 dataset generation 内删除旧行、加入正确归组行；不能双计请求数。
- 聚合只更新受影响桶。价格规则重算、来源变化会失效相关桶；扫描时间／相同价格抓取时间不会。
- 分页游标使用稳定键（时间＋ID），增量游标使用 change sequence，二者不混用；迟到修正不能只靠“开始时间比上次新”发现。
- 每批最多 20 行、32 KiB 解码体积，manifest／row revision 同事务提交。iOS 也在事务内应用完整一批，再更新游标。
- tombstone 至少保留 7 天，另设最多 512 个的上限；若裁掉旧 tombstone，提高 `min_valid_cursor`，旧客户端收到 `RESYNC_REQUIRED`。
- 初次导入／游标过期只重取当前有限窗口（最多 200 条、90 日、48 小时），保留旧数据显示，完整取得新窗口后原子替换。分页中 generation 变化则重试该数据集，不能拼接两个不一致快照。
- 客户端同时执行本地保留期限，离线也不会无限保存旧条目。

D1 超额或故障时，Mac 的 outbox 仍留在本机；恢复后补最新值和必要删除，不上传中间变化流水。没有 iPhone 在线也不影响 Mac 自身功能。

### 6.3 隐私关闭和来源变化

预览开关关闭：增加 privacy_epoch，立即清除本地导出标题、安排云端 scrub，iOS 下一次合法同步先清除旧标题再接受新内容。Cloud 未完成清除显示“云端清除待完成”。旧 epoch 在途上传不得重新写回标题。

完整数据集无需无限保留以追踪删除：同步游标过期后用有限窗口整体重建。彻底关闭云同步时区分“停止上传”和“删除云数据”，默认给出明确的删除选择与完成结果；不静默删除 Mac 原始账本。

## 7. Cloudflare 实现与安全边界

### 7.1 选型

首版使用 Worker（TypeScript、原生 Fetch/Web Crypto）＋D1；Mac 用 URLSession，iOS 用 URLSession。Worker 不扫描历史、不计价、不调用 OpenAI、不持有用户 Codex 凭据。

不用 Workers KV 承担高频强一致状态，也不引入 R2 存日志、Durable Objects 长连接、Tunnel 或排队系统。后续只有真实数据证明需要，才讨论增加服务。Worker 的免费 CPU 预算不适合大型依赖、巨大 JSON／压缩任务或每次全表聚合。

云服务可由用户自己的 Cloudflare 账户部署，或部署者为少量朋友共享；配额是**账户共享**，不是每台设备各享一份。本轮没有登录、创建数据库或部署，正式部署域名和账户由用户确认。

### 7.2 最小表与查询

| 表 | 用途／关键约束 |
| --- | --- |
| devices | 每 Mac 一行身份、writer secret hash、来源 epoch、状态；device_id 主键 |
| readers | 每手机每 Mac 一行授权，token_id 唯一定位、scope、revoked_at |
| pair_tickets | 单次票据、expires_at、claim_attempt、批准状态；定时小批清理 |
| current_state | 每 Mac 一行 live、版本、hash、last_seen／观察元信息；覆盖而非历史 INSERT |
| mobile_manifest | 每 Mac 一行各数据集版本、保留边界、privacy_epoch |
| recent_requests | device_id＋opaque_request_id 主键；稳定分页索引；7 天／200 条约束 |
| usage_buckets | device_id＋粒度＋bucket_start 主键；只有日／小时小桶 |
| mobile_changes | 有界变更键／删除标记与 sequence，不保存完整历史日志 |

不默认启用读副本；若后续启用，身份、撤销、配对、写后读必须使用合适的主库／Sessions 一致性策略。所有 SELECT 必须约束授权 device；LIMIT 不足以避免无索引扫描，需核对查询计划和 D1 返回的 rows_read。

### 7.3 HTTP 接口

```text
POST   /v1/devices/enroll                邀请码 -> 单设备 writer
POST   /v1/pairs                         writer -> 一次性票据
POST   /v1/pairs/{id}/claim              高熵加入 secret -> pending reader
POST   /v1/pairs/{id}/approve            writer -> 批准
GET    /v1/pairs/{id}/result             本次 claim 证明 -> 授权结果
PUT    /v1/devices/{id}/live             writer -> latest-only 条件更新
POST   /v1/devices/{id}/heartbeat        writer -> 存活／观察元信息
POST   /v1/devices/{id}/datasets/batch   writer -> 有界 UPSERT/DELETE
GET    /v1/devices/{id}/sync             reader -> live/health/manifest 和必要增量
GET    /v1/devices/{id}/recent           reader -> 20 条简表
GET    /v1/devices/{id}/buckets          reader -> 有界日／小时桶
POST   /v1/devices/{id}/readers/verify   writer -> 核对 Cloud reader 持有证明与当前权限
DELETE /v1/devices/{id}/readers/{reader} writer -> 单手机撤销
DELETE /v1/devices/{id}/cloud-data      writer -> 删除云副本，保留本机账本
```

GET sync 首包可附 recent 前 20 条和首页 7 日趋势，避免手机首次打开连续串行请求五六个接口。初始 API 无 calls、full-log、prompt、file 或 command 路由。

D1 原子性使用官方 prepared statements、条件 SQL 和 `batch()` 事务语义，不把本地 SQLite 的交互式 `BEGIN → 跨 await → COMMIT` 直接套进 Worker。仅“先 SELECT 再 UPDATE”不能防并发 claim；消费票据、插入授权与版本提交必须使用条件约束确保同一票据最多一个有效 reader。批量失败回滚，检查实际受影响行数。参考：[D1 binding / batch](https://developers.cloudflare.com/d1/worker-api/d1-database/)。

### 7.4 认证、重试、缓存

- writer、Cloud reader、Local reader 均使用独立 32 字节随机 secret；服务端只保存 hash。凭据形如 token_id.secret，索引定位再恒定时间验证。
- 每个写请求在执行 SQL 时确认 writer 没被撤销；每个读请求确认 reader 当前权限，不以长期内存缓存绕过撤销。
- API 权限按服务器授权表确定，不能相信 URL 的 device_id 或手机传来的 user_id。
- pair secret 和邀请码高熵、短时、单次；若以后增加短手输码，须 PAKE 或独立强限速／确认设计，不能直接代替高熵二维码。
- 二维码中的云 origin 必须 HTTPS 且在允许列表或用户明确确认的自托管列表；拒绝跨主机转发 Authorization、HTTPS 降级及任意重定向。非必要 CORS 不开放。
- 请求和响应大小按解码后体积限制；认证／配对失败限速，重试 5→15→30→60→120→300 秒＋jitter。401/403 停止；429 尊重 Retry-After；409 读取版本处理，不循环覆盖。
- 304 只省 payload／客户端解码，不免除 Workers 请求和 D1 授权读取。可在响应头携带服务器 last_seen 等小型健康信息，让业务相同但新鲜度变化仍可正确显示。
- Cache-Control 为 private/no-store；不将带授权的用量放进公共 CDN Cache。
- 日志只记操作类别、状态码、延迟、字节数和短版本信息；不打印二维码、token、预览、整份 snapshot、账户邮箱。
- TLS 是传输加密，**不是端到端加密**。本版可信云后端能读到已授权上传的白名单字段；预览可能敏感，必须独立确认。若需要“Cloudflare／部署者也不能读”，增加成熟 AEAD 端到端加密和按手机密钥封装为单独安全工作包，不能把 TLS 包装成已经实现 E2EE。

## 8. 只在必要时更新

### 8.1 建议节奏

| 通道／事件 | 行为 |
| --- | --- |
| Mac 原始采集 | 继续现有变化检测，不因手机连接增加扫描器；默认采集约 10 秒 |
| Local 新业务数据 | 获取结果后约 250–500 ms 合并发送；开始／结束优先，不等普通数值合并 |
| Cloud 普通运行数值 | 合并约 30 秒上传一次 latest-only；只在内容有变化时发送 |
| Cloud 开始／结束／错误恢复／隐私关闭 | 尽快发送最终关键状态；短时间批量事件合并，不逐 token 写入 |
| Cloud 日／小时桶 | 一般 60 秒合并；请求结束、跨日或明确修正可随下一批提交 |
| Cloud recent | 开始、结束和实质修订更新；不跟随每个 live token 重写 |
| Mac Heartbeat | running 60 秒、idle 300 秒；已有成功上传可替代当次心跳 |
| iOS Cloud 前台 | 当前任务页约 15 秒；idle／非实时页面约 60 秒；本地直连成功即停止 |
| iOS 切回前台／下拉刷新 | 一个合并的立即同步请求；不触发 Mac 全量扫描 |
| iOS 后台 | 停止常驻连接和前台 timer；可申请 BGAppRefresh，获调度时只做一次有界 Cloud GET |
| Widget | 展示内容变化／必要时间边界才写共享快照和请求对应 kind 刷新 |

Local 的“快”是指新增传输延迟小，不代表跳过现有 10 秒采集间隔。Cloud 普通数值还叠加上传合并、手机轮询和网络延迟；不能承诺所有数值秒级到达。开始／结束仍受 Mac 检测时间和网络可用性约束。这些是目标参数，需真机验证后调优。

先用 30 秒 Cloud 数值节奏。若用户后续要求 10 秒模式，先从实际 rows_written 推算全账户预算；不能仅凭 payload 小就开启。任务状态与时间动画分开，运行计时由系统显示，不每秒联网。

### 8.2 不变时不做什么

不重读 JSONL，不重新解码全库，不重新计价，不重新构造所有历史桶，不全量 JSON 序列化／哈希，不发 Cloud PUT，不改全局 AppState，不重建列表，不写相同 App Group 文件，不无条件 reload Widget。

远程导出依赖：账本 generation、实际价格版本、源集合、当前任务、额度 scope／业务值、隐私 epoch、时区与必要时间边界。手机依赖各数据集 revision 和页面条件。健康时间戳只更新健康区域，不使整页数据失效。

成功读到同一个额度值可以续期它的真实观察时间；失败和纯主机 heartbeat 不能续期额度。observed_at／fresh_until／last_seen 等元信息走独立 health 版本，可通过低频 heartbeat 合并传输，不增加历史业务 revision；fresh/retained/error 等实际展示状态变化仍需通知对应视图。跨日、重置期限、运行有效期、时钟回退、未来记录生效均需明确失效。

### 8.3 背压和生命周期

一个 Cloud 上传任务、一个最新 pending live、有界逐行 outbox；旧回调带 generation 丢弃。后台文件、Keychain、JSON、DB、网络放独立队列／actor，主线程只应用小型 UI 结果。

仅稳定宿主启动生产同步；关闭窗口不断同步。睡眠停止连接与保活、唤醒事件合并一次恢复。⌘Q 停止 listener、browse、URLSession、计时器和子进程，最多约 1–2 秒尽力发送 offline，不为发送成功阻止退出；没收到 offline 的手机按 last_seen 超时显示“无法确认在线”，不伪装成当前数据。

mock／冒烟完全禁用真实网络、Keychain 身份、Bonjour、端口和权限弹框。新增同步生命周期接入按现有规则复核是否涉及 Mac Widget 宿主并递增 WIDGET_VERSION；只修改本文不递增版本。

## 9. Cloudflare 免费容量与算账

核对日期：2026-09-28。官方额度会变化，实施和发布前再次核对，不以本文作为永久免费保证。

| 资源 | 当前 Free 限制 |
| --- | --- |
| Workers | 100,000 次请求／日；10 ms CPU／调用；128 MB 内存 |
| D1 读取 | 5,000,000 行／日 |
| D1 写入 | 100,000 行／日；INSERT/UPDATE/DELETE 及相关索引写入均要考虑 |
| D1 存储 | 单数据库 500 MB；账户总计 5 GB；Free 最多 10 个数据库 |
| D1 查询约束 | Free 每次 Worker 调用最多 50 次查询；每条 SQL 最多 100 个绑定参数 |

依据：[Workers limits](https://developers.cloudflare.com/workers/platform/limits/)、[D1 pricing](https://developers.cloudflare.com/d1/platform/pricing/)、[D1 limits](https://developers.cloudflare.com/d1/platform/limits/)。D1 日读写额度按 UTC 0 点重置，超额查询会报错；D1 数据传出没有额外 egress 费用。这不代表 Worker 请求、CPU、数据库大小或所有其他 Cloudflare 产品都无限。

### 9.1 存储估算，不是实测

以每台 Mac 的推荐硬边界估算：

```text
recent：200 × 1 KiB                 ≈ 200 KiB
daily：90 × 256 B                  ≈ 22.5 KiB
hourly：48 × 256 B                 ≈ 12 KiB
live / 身份 / manifest             约几十 KiB
有界 change keys / tombstones      另留 100–200 KiB
```

加 SQLite 页面、主键、索引与余量，**每台先按约 1–2 MiB 规划，10 台约 10–20 MiB**。实际必须以数据库 `size_after` 核对；不称为准确占用。这里按条数硬上限，因此一天 1,000 请求的设备也只保留最近 200 条；界面会如实显示“已达条数上限”，并不保证这种设备能回看完整 7 天。

单库接近 350 MB 就应预警和停止扩张；不能等到总账户 4 GB 才处理单库超限。首版不为用满 5 GB 刻意分库。

### 9.2 请求／写入预算

定义：P 是实际 live 上传次数、H 是未被上传替代的 heartbeat、B 是数据批次、G 是 iOS GET；总请求约为所有设备的 P+H+B+G，再加配对、重试与管理。**批量合并减少 HTTP 次数，不免除行写入。**

一个示意工作日：Mac 在线 8 小时，其中有数据变化的运行时间 2 小时、idle 6 小时；100 个请求；iPhone 异网前台 30 分钟。

- 普通 live 最多约 2×3600/30＝240 次；开始／结束最多再预留 200 次（实际常与普通上传合并）。
- idle heartbeat 最多 6×3600/300＝72 次；离线 16 小时无 heartbeat。
- 聚合每分钟合并，约 120 批上限；recent 尽量同批提交。
- 手机按 15 秒读取 30 分钟约 120 次，另留分页、配对、重试余量。
- 因此先按**约 1,000 次 HTTP／Mac＋手机组合／日**留预算，不把它当测速结果。
- 基于 live/健康、请求／变更键、两类桶、manifest 和索引，暂给每组合 **4,000–6,000 行写入／日**预算；这是保守容量预留，不是从请求数直接换算出的账单。上线后替换为 D1 meta 实测值。

10 组按这个负载约 10,000 请求和 40,000–60,000 行写入／日。可作为少量自用设备的起始目标，不能推导“20 台全天重度运行也一定免费”。

反例：持续 8 小时每秒发生变化，按每秒直接上传就是 28,800 次／Mac；4 台仅 live 请求就超过 Workers 日额度。即使 30 秒合并，连续运行时长、索引写入和设备数仍必须统计。数据很小不等于写入很少。

### 9.3 降级与监控

- 目标软阈值：账户 Workers 70,000 次／日、D1 写入 70,000 行／日、读取 3,500,000 行／日、单库 350 MB；同时考虑同账户其他项目。
- 从 D1 `meta.rows_read / rows_written / size_after` 和 Cloudflare dashboard／Analytics 查看实际值；不为每个 GET 额外写一次统计行。
- 优先延长普通数值上传／idle 保活，减少非可见读取，暂停低优先级桶更新；关键状态与最新缓存优先，Local 不受云预算影响。
- 70% 时提示，90% 时保守降级；不自动开通付费、不通过创建新账户绕额度。
- 清理、撤销和重试也需余量。已经触及硬上限时删除查询也可能受限，不能依赖“超限后再清理”救场。
- 离线设备数据可按 30 天无 writer 活动到期清理，设备身份另行管理；手机回来收到 RESYNC_REQUIRED，由 Mac 重建小窗口。
- 首次开启 Cloud 检查真实使用网络的 HTTPS 可达性。workers.dev／自定义域名在不同地区、运营商和 VPN 下可能表现不同，不承诺处处可达，也不自动购买域名。

## 10. iOS 主界面与交互设计（待讨论）

### 10.1 导航结构

建议四个原生 `TabView` 标签，每个标签各自保留 `NavigationStack` 与浏览位置：

| 标签 | 第一层内容 | 进入第二层 |
| --- | --- | --- |
| 概览 | 当前任务、额度、今日四指标、最近 3 条、7 日小趋势 | 当前任务简卡、额度／套餐说明、全部最近记录 |
| 用量 | 今日／7 日／30 日／90 日，费用／Token／请求／命中率切换 | 某日或某小时的聚合卡，无日志下钻 |
| 记录 | 按天分组的最近请求简表，显示实际保留边界 | 简要字段详情，不出现调用列表 |
| 设置 | Mac 设备、同步／隐私说明、外观、Widget／实时活动、关于 | 扫码配对、设备管理、能力状态 |

当前 Mac 选择器放导航栏，名字较长自动截断；全 App 使用同一个选定设备上下文，切换时取消旧请求。多设备列表可在选择器的 sheet 与设置中进入，不单独占据第五个标签。每个 Widget 可选择设备，不强迫它随 App 切换。

不把 Mac 的侧栏、可拖列宽表格和六页布局等比缩小到手机；也不做一屏几十个数字的桌面密度。若用户更偏好三标签，可把记录归入概览“全部记录”，但当前建议独立记录页以便经常查看。

### 10.2 概览页从上到下

1. **原生导航栏**：Codexio／概览标题，当前 Mac 选择器；一条小型“局域网直连／云同步／离线缓存”状态和固定“上次更新：M.d HH:mm”。更新时间指成功获得的数据，不把本次打开时间冒充更新。
2. **当前任务卡**：运行时放首位，显示状态图标、模型、有效耗时、Token、费用；获授权时加两行以内短预览。提供“在灵动岛跟踪”操作。无运行任务时变成紧凑的最近任务卡，不占一大块空白。并发显示“另有 N 个任务”，可进入记录列表选择关注对象。
3. **额度卡**：5 小时与周额度，同一张卡的两行进度或并排小区块；突出剩余百分比，下面是重置时间。曾成功但本轮失败仍显示旧值并标“上次获取”，无历史才显示未知。点卡片打开额度／当前套餐 sheet，没有重置券操作。
4. **今日四指标**：费用、总 Token、用户请求、命中率，2×2 布局；使用同一数值排版等级和等宽数字，不能费用小、Token 大。大字体模式自然变单列，不强行缩字。
5. **最近使用**：最多 3 条，短预览／隐私占位、模型、时间、状态、Token 与费用，右上“全部”。不再做内部纵向滚动。
6. **最近 7 天趋势**：默认费用，允许切 Token；点击去用量页。单次只显示一种量纲，不把独立归一化曲线误导成同一数值轴。

所有数据都有用途，连接模式不是一个占满首页的“网络仪表盘”。错误尽量局部表达：例如额度失败不遮住正常的今日用量；空状态解释“尚无使用记录”而不是所有值都显示 0。

### 10.3 用量页

参考原生股市的“区间选择＋图表触摸查看”，采用 Swift Charts，不复制现有 Mac Canvas 绘图坐标：

- 上方范围：今日、7 日、30 日、90 日；今日用小时，其他用日。
- 中部四项指标与原生分段选择，图表单位随选择明确改变。
- 拖动／点选只命中已在本机的桶；浮动数值放在图表上方稳定区域，不盖住峰值，也不触发 SQL／网络。
- Y 轴与 plot padding 留完整线宽／点标记余量；zero／single point／all null 分别处理；缺数断开或提示，不连成 0。
- 下方可有 90 日简易活动日历，但不是首屏的必需负担，直接使用已同步的 daily 数据，不新增按分钟活动记录。
- 不提供首版没有数据支持的按模型／速度／聊天排行按钮。保留扩展空间，不展示无法使用的假控件。
- 统计范围、保留范围、Mac 时区和费用估算的语义可在 info sheet 查看。

### 10.4 最近记录页

原生 `List / Section` 按日期分组，一条记录建议两行主体＋一行轻量计量，不复制横向大表格。

第一行：短预览（最多两行）或明确隐私占位；第二行：时间、模型、High 等英文思考强度与速度；末行：Token、费用、有效耗时与状态。小屏／大字按优先级自然换行。

- 原生 searchable 仅搜索已经同步的最多 200 条；输入防抖约 250 ms。提示“搜索最近记录”，不让用户误以为搜索全部 Mac 历史。
- 筛选首版仅状态；只有确有需求且不增加数据集时再加模型。
- 点击进入 sheet／详情页面，只放已有字段，**不叫“详细日志”**。
- 不允许滑动删除真实请求；可以移除设备缓存，但不改 Mac 账本。
- 自动刷新保留阅读位置；新记录到达可显示轻量“有新记录”入口，不强制把用户拉回顶部。
- 如果请求后续被正确归一，收到旧 ID tombstone 后替换为正确请求，仍按 Mac 归组规则显示。

### 10.5 设置与配对体验

使用 iOS 原生 Form／分组 List。主入口是“添加 Mac”，扫描后给出清楚的设备名和权限范围；失败页面区分本地网络权限、Mac 未运行、票据过期、云端不可达和身份变化。

配对确认页单列“同步短请求预览”的选择，说明它也可能被云端读到；锁屏预览另行选择，默认隐藏。手机主题、图标、当前设备等只作为手机本地偏好，不回写 Mac。

图标延续已经确认的猫耳 C 和 13 款风格。iOS 资源按系统 App Icon／alternate icons 的要求导出，不拿带桌面透明外边距的 ICNS 当 iOS 图标，也不自行重画视觉中心；运行时换图标用原生 API和确认流程，是否首版包含全部备选可在排期中讨论。

## 11. 直接参考和复用什么

本轮遵循 Product Design 的 brief 与现有设计系统优先流程；用户要先讨论设计，因此输出页面与交互规格，不先生成三套概念图，也没有启动网页原型。以下是明确来源和采用范围，不是宣称已经把示例代码接入生产。

| 来源／版本 | 直接采用的机制 | 不照搬的内容 |
| --- | --- | --- |
| [Apple 健康 App 摘要](https://support.apple.com/en-sg/104997) | 摘要页的重要指标层级、卡片进入分类详情 | 不复制健康数据语义或疾病配色 |
| [Apple 股市](https://support.apple.com/en-asia/guide/iphone/iph1ac0b1bc/ios) | 时间范围与单指查看图表的交互；数值／单位清楚分层 | 不加入新闻、买卖功能或多余金融信息 |
| [Apple 查找](https://support.apple.com/en-ae/guide/iphone/iph09b087eda/ios) | 设备列表、当前／最后已知状态与设备详情 sheet 的信息层级 | 不加入定位、地图、蓝牙查找、远程控制 |
| [Landmarks: Building an app with Liquid Glass](https://developer.apple.com/documentation/swiftui/landmarks-building-an-app-with-liquid-glass)；WWDC25 | 原生导航／toolbar、系统玻璃、必要时 GlassEffectContainer，适配 Dynamic Type | 不照搬图片主视觉／地标内容，不自制玻璃 shader |
| [Visualizing your app’s data](https://developer.apple.com/documentation/charts/visualizing-your-app-s-data)；WWDC23 | Swift Charts 原生 selection、坐标和可访问性 | 不重新发明图表命中坐标系统 |
| [Apple Food Truck](https://github.com/apple/sample-food-truck/tree/3954a769e99f3cc53297d94f2b960ceb2665b3d6)；核对提交 `3954a769` | Personal Team 的 App＋Widget target，ActivityAttributes、ActivityConfiguration、DynamicIsland、deep link | 不复制餐车业务、WeatherKit、Passkeys 或无关付费 capability |
| [Apple 本地网络示例](https://developer.apple.com/documentation/network/building-a-custom-peer-to-peer-protocol)；WWDC22 | Bonjour 发现、TLS、连接与生命周期 | 不引入示例游戏协议或配对弱口令 |
| [Cloudflare D1 binding](https://developers.cloudflare.com/d1/worker-api/d1-database/) | prepare/bind、条件 SQL、batch 原子提交 | 不自行拼接 SQL、不自制跨请求事务 |
| 仓库 v0.2.10 与当前 Swift | dirty/内容签名、物化读模型、请求 continuation、价格版本、时间边界 | 不把 Python 常驻进程重新加入 Mac 或手机 |

Food Truck 已核对的具体文件：`Widgets/TruckActivityWidget.swift`、`Widgets/TruckActivityAttributes.swift`、`App/Orders/OrderDetailView.swift`、`Widgets/Widgets.entitlements`。正式采用代码时保留各样例 LICENSE／版权头，记录选定提交与改动；本轮仅研究，没有复制代码。新 API 以目标 SDK 文档为准，不原封照搬旧版 ActivityKit 调用。

### 11.1 液态玻璃与可访问性

使用系统 TabView、toolbar、Menu、sheet 的原生材质，少量浮动的“跟踪当前任务”控制可以采用系统 glassEffect。**数字卡片、记录列表、图表本体不是整块透明玻璃**；用系统分组背景／标准材质保证可读性，避免多层玻璃叠加。依据：[Apple Materials 指南](https://developer.apple.com/design/human-interface-guidelines/materials)。

跟随深浅色、减少透明度、增加对比度与减少动态效果；使用系统语义色、SF Symbols 和既有品牌矢量。数字采用同级排版，目标交互热区至少 44 pt；状态不仅靠红绿区别，VoiceOver 能读出数据时间、来源、未知原因。iOS 标题使用原生导航行为，不把 Mac 的固定标题像素位置硬搬到手机。

## 12. Widget、灵动岛、Live Activity

### 12.1 小组件目标

只用一个 iOS Widget Extension target，内部注册独立 kind，减少扩展 Bundle ID 数量：

| kind／尺寸 | 推荐展示 |
| --- | --- |
| 额度小／中 | 周额度、5 小时额度、重置时间；中号加今日费用／Token |
| 今日小／中 | 今日四指标；中号加 7 日单指标趋势 |
| 当前任务小／中 | 状态、模型、运行时间、Token、费用；预览默认隐藏 |
| 锁屏 accessory | 一个关键额度或紧凑任务状态，空间不足省略更新时间而非缩到难读 |

本阶段不改已有四种 Mac Widget 的 kind 与布局。iOS App 通过 App Group 原子写精简 snapshot，Widget 不读取整个 MobileDatabase、不读取认证 token、不连接 Mac，也不执行原始数据采集。

普通 Widget 第一版只读共享缓存；BGAppRefresh 有机会拿到新数据时同步缓存并请求对应 kind 刷新。未使用的 kind 不做无意义 reload。可预测的 reset／stale 期限生成 timeline，时间显示交给系统；**reload 请求不等于系统保证立刻刷新**。常见频繁查看的 Widget 每日预算约 40–70 次，这只是 Apple 的说明范围，不是固定配额或 10 秒更新承诺。[WidgetKit 更新机制](https://developer.apple.com/documentation/widgetkit/keeping-a-widget-up-to-date)

App Group 不可读时显式显示能力问题；不得无声展示空白并宣称 Widget 已完成。需要更独立的后台 Widget 云拉取时，另讨论受系统预算约束的精简只读 endpoint／Keychain 共享；本版不同时维护两套同步引擎。

### 12.2 灵动岛是一种 Live Activity 展示，不是另一条数据通道

用户在 App 中主动跟踪一个请求，首版每台手机默认一个关注任务：

- compact：主 Logo／状态＋短耗时，避免塞三个不断变化的数值。
- minimal：一个状态图形。
- expanded：模型与状态、Token／费用、有效耗时、可选短预览；点按进入对应设备的请求简卡。
- 锁屏：同一数据的横向卡片，默认隐藏请求文字。
- 点击“停止跟踪”只结束手机 Activity，不取消 Mac 请求。

复用 Food Truck 的 ActivityAttributes／ActivityConfiguration／DynamicIsland 结构。静态＋动态 Activity 数据合计小于 Apple 的 4 KB 限制，内部目标 <1 KiB，不把 recent 列表塞进 Activity。Activity 不能自己联网，使用 App 的 ActivityKit 更新或 APNs；普通 Activity 最长活动时间及锁屏保留受系统限制，不能用它做永久菜单栏。[Live Activity 限制](https://developer.apple.com/documentation/activitykit/displaying-live-data-with-live-activities)

每次更新携带 revision 与 staleDate，数据旧时明确“等待更新”，不能一直保持绿色 running。任务完成时收到真实结果才显示完成并结束；无进度分母时不画假进度百分比。用户许可、系统禁用、已结束状态均需处理，不能无限重建。

## 13. SideStore 与后台刷新：本轮必须讲清的关口

### 13.1 用户已经确认的信息

- iPhone 17、iOS 27。
- 使用 SideStore，用户自行完成约 7 天个人签名。
- 用户不要求手动划掉 App 后仍刷新；希望 App 在后台时能刷新。
- 用户问“APNs 必须要支持吗”，**尚未选择付费签名，也未同意用不定时刷新替代及时更新**。

答案取决于“刷新”的时效要求：**仅系统有机会时偶尔刷新，不一定需要 APNs；切到别的 App／锁屏后仍及时跟随 Mac 任务，正常可靠方案需要 APNs。**

### 13.2 运行状态与实际行为

| iOS 状态 | 免费个人签名、无 APNs | 有 APNs 的方案 |
| --- | --- | --- |
| App 前台可见 | Local push 或 Cloud 轮询，更新 UI／Widget 缓存／Activity | 相同 |
| 刚切后台、仍获运行时间 | 尽快完成当前有限同步并保存；不把这段宽限时间当永久许可 | 相同 |
| 没划掉、但进程已挂起 | timer／socket 不能持续执行；BGAppRefresh 由系统决定机会 | ActivityKit 专用推送可让系统更新 Live Activity，不要求 App 常驻执行 |
| 系统调度到后台刷新 | 一次有界 Cloud 请求、版本合并、缓存／Activity 更新，随后结束 | 同样可用，静默推送也不能当高频保证 |
| 用户手动划掉 | 尊重用户意图，不绕过，不承诺继续后台采集／刷新 | 本产品同样不把绕过划掉作为目标 |
| 签名过期／App 不能运行 | 不承诺继续服务，用户通过 SideStore 续签 | 依所选分发签名管理，不混同 Cloudflare 免费额度 |

后台切换器保留卡片不证明进程在执行。Apple 明确不存在普通 App 任意持续运行或固定间隔唤醒的通用机制；SideStore 重签／VPN 不改变 Codexio 的系统调度。参考：[Apple DTS 背景执行说明](https://developer.apple.com/forums/thread/685525)、[选择后台策略](https://developer.apple.com/documentation/backgroundtasks/choosing-background-strategies-for-your-app)。

不能拿静音音频、VoIP、定位、VPN 或反复 background task 延期伪装常驻。BGContinuedProcessingTask 用于用户发起的有限持续工作，不作为无限远程监视的替代方案。真机行为必须脱离 Xcode 调试器观察，调试附着可能掩盖挂起限制。

### 13.3 免费路线 A

保留 SideStore 个人签名：

- 主 App＋Widget＋前台启动的本地 Live Activity。
- BGAppRefresh 作为“尽力刷新”，获调度后可更新缓存和已有 Activity；低电量、后台刷新关闭、不常使用、网络问题等都会影响机会。
- 不承诺每 15 分钟一定刷新，更不承诺 10 秒；App 重新进入前台立即补齐。
- Activity 的时间部分可由系统推算，但真实任务是否结束、实际 Token／费用都必须等数据到来。
- 只有用户明确接受这个后台体验，才把它作为免费版本完成标准。

### 13.4 及时后台路线 B

保留同一轻量同步协议，加 APNs 专用 Live Activity 推送：

`Mac 最新关键状态 → Worker 验证／限频 → APNs liveactivity → iOS 系统更新锁屏／灵动岛`。

需要 Push Notifications entitlement、合法 provisioning profile、对应 Team／Bundle ID 的 APNs key、activity token 生命周期。使用官方 `pushTokenUpdates`，区分 sandbox／production、topic，撤销旧 token，结束后清理。服务端 APNs 私钥仅在部署者 secret store；不得写进 IPA、二维码或 Mac reader 凭据。

Live Activity 专用推送不同于“先静默唤醒 App 再拉数据”；前者直接面向 Activity，后者强依赖后台调度，不能用静默推送证明秒级可靠。APNs 也受网络、系统预算和用户设置影响，不作硬实时 SLA。参考：[ActivityKit 推送](https://developer.apple.com/documentation/activitykit/starting-and-updating-live-activities-with-activitykit-push-notifications)。

签名取舍：

- Apple 当前 iOS capability 表在免费 Apple Developer 列支持 App Groups／Keychain sharing，但不支持 Push Notifications。不能给未授权 profile 手工加一个 aps-environment 就认为能推送。[官方能力表](https://developer.apple.com/help/account/reference/supported-capabilities-ios/)
- Apple [Food Truck Personal Team 示例](https://developer.apple.com/documentation/swiftui/food-truck-building-a-swiftui-multiplatform-app) 说明免费团队能够开发带 Widget 的基础 App，因此小组件目标合理；SideStore 的具体重签结果仍需真机核对。
- 如果必须及时后台更新，优先讨论开发者统一有效签名的 TestFlight／合规测试分发。用户无需每人购买开发者资格；但不能把这种构建再用免费 Personal Team 重签后，期待原 APNs 权限仍保留。
- 若坚持每位用户自己付费 Team 签名，还要处理每个 Team 的 Bundle ID／APNs 凭据映射，运营复杂度远高于统一分发，不作为首选。
- 本轮不购买会员、不申请证书、不替用户签名、不创建推送资源。Cloudflare 免费预算与 Apple 签名权限是两件独立的事。

### 13.5 SideStore 落地注意

Apple 当前 Personal Team 限制为 10 个 App ID、3 台设备、每台 3 个安装 App，相关 profile 约 7 天到期；扩展也有独立 App ID。SideStore 自身占安装 App 名额之一，应在目标设备核对现有占用；不要声称一个 Widget 就一定占一个完整 App 名额。[Apple Personal Team](https://developer.apple.com/help/account/basics/about-your-developer-account/)

开发产物是可重签 IPA，不能未签名直接安装。SideStore 文档说明其刷新是续签，不是为被安装应用提供常驻执行；其初次刷新与 app group ID 映射有关。[SideStore FAQ](https://docs.sidestore.io/docs/faq)、[安装说明](https://docs.sidestore.io/docs/installation/install)

实际关口：

1. 一个主 App＋一个扩展，全部 kind 和 Activity 放同扩展；核对 SideStore 当前版本、有效 Bundle ID 和 entitlements。
2. 同时签入主 App 与扩展，保持两者 App Group 映射一致；不能只验证主 App 能启动。
3. 使用稳定的同一 Apple Account 与覆盖安装，完成一次续签；不要求用户把 Apple Account 密码、设备配对文件或证书私钥交给 Codexio。
4. Keychain 凭据选适当的 ThisDeviceOnly 访问级别；如需后台锁屏获取数据，允许设备首次解锁后使用，同时不给 Widget 暴露凭据。缓存使用适当 Data Protection，锁屏预览默认隐藏。
5. 重签工具若重命名 Bundle ID／Group 导致共享容器失效，报告实际原因并修复映射；不能只用“免费所以不支持”解释。
6. SideStore 在 iOS 27、目标扩展组合上的实测尚未完成，文档和 iPhone 型号支持不等于这份 IPA 已兼容。

## 14. 正式写 iOS 前的工作拆解与状态

“完成开发前工作”分为可在本轮完成的分析／规格、需要用户选择的产品关口、以及下一轮实际工程任务。不能把计划文档写完等同于互联功能已经做完。

| 项目 | 本轮状态 | 后续关口 |
| --- | --- | --- |
| A～G 及侧栏、Logo、菜单、窗口尺寸修复 | 已有本地提交及 Mac 开发包 | 用户最终验收；不由助手代启动安装版 |
| 当前 Swift 架构／源码审计 | 已完成 | 真实数据规模与能耗未测，不作性能倍数结论 |
| 手机字段白名单、保留上限、同步语义 | 本文已给出完整建议 | 用户确认最近记录／短预览范围 |
| 原生 UI／示例复用方案 | 已完成参考映射与页面说明 | 确认四标签和首页优先级，再进入视觉／代码实施 |
| Cloudflare 免费容量与限流模型 | 官方限制已核对，容量为估算 | 部署账户、域名和实际 rows 指标尚未验证 |
| iPhone／iOS／工具 | 已确认 17／27／SideStore | 签名与扩展真机实测未进行 |
| 后台实时性／APNs | 硬约束和两条路线已说明 | 用户确认“机会式”还是“及时更新” |
| MobileProjection／Local／Cloud 代码 | 未实现 | 先确认方案与生命周期边界 |
| iOS App／Widget／Activity／IPA | 未实现 | 能力样例通过后再进入完整 UI |
| 发布 | 未授权、未进行 | 开发验收后第二次确认，不能推送或新增正式附件 |

### 14.1 Mac／共享层工程工作包

- 新增 `apple/shared` 的 Codable DTO、枚举、边界校验；保持 Framework 依赖小，不导入 AppKit 或原始账本解析器。
- 在现有后台构建处产出 MobileProjection，并用有界 MobileStore 保存 recent／bucket／sync 元信息；复用 v0.2.10 的物化与增量查询思路。
- 新增 Mac SyncCoordinator、IdentityStore、PairingStore、LocalSyncService、CloudSyncClient；一个 actor 负责发送队列与生命周期，AppState 只提供已经计算的业务输入。
- 先做纯局域网配对，确保 Cloud 不可用时仍可工作；再接 Worker/D1 回退。
- Mac 设置新增“iPhone 同步”，明确开关、云端授权、设备撤销、预览开关及待清除状态。
- 构建系统目前用 swiftc 直接编译；共享源文件需显式纳入，若使用 swift-certificates 再引入一个锁定版本的 SwiftPM 依赖构建步骤，不能只加 import 却漏打包。
- 不改变默认程序版本；同步默认关闭，既有用户不因升级就自动上传数据。
- Mac 当前最小 720×480、已有导航与品牌规则继续保持；不因新增设置页恢复较大窗口硬下限。

### 14.2 计划目录（尚未创建工程）

```text
apple/shared/                 协议／值类型／轻量格式化
macos/native/                 现有 App＋新增同步适配层
cloudflare/                   Worker、D1 migration、wrangler 配置（无密钥）
ios/
  CodexioIOS.xcodeproj
  App/                        四标签与配对界面
  Sync/                       Local/Cloud、仲裁、后台机会刷新
  Storage/                    MobileRepository、Keychain、共享快照
  WidgetExtension/            多个独立 kind＋ActivityConfiguration
  Resources/                  品牌资源、String Catalog
```

开发输出沿用 build 分类：

```text
build/dev/ios/Codexio.ipa     可重签包；未签名不能直接安装
build/dev/ios/build-info.json
build/staging/ios/
build/cache/ios/
build/checks/ios/
build/logs/
```

Mac 仍由 build_macos.sh 打包；不往正式 release 写文件。现有合并 latest.json 的 Windows 顶层与 macos 对象不为本轮文档改动，IPA 不擅自成为第四个正式附件。

本机已发现完整 Xcode 27.0（27A266a）；全局 xcode-select 仍指 CommandLineTools。后续 iOS 构建用单次进程的 DEVELOPER_DIR 指向完整 Xcode，不擅自改变全局开发工具配置。没有据此声称已拥有可用签名证书／真机连接。

## 15. 推荐实施顺序

1. **方案确认与 Mac 验收**：先确认后台目标、字段范围和页面结构；Mac 开发包继续由用户测试。发现现有问题先处理。
2. **最小真机能力验证**：以 Apple Food Truck 的 Personal Team 路径为参考，在 iPhone 17／iOS 27＋SideStore 核对主 App、扩展、App Group、Live Activity、续签与后台行为。这里只是能力闭环，不先做全部页面。
3. **共享 DTO＋Mac 有界投影**：验证 unchanged 不增版本、续跑不双计、额度回退不串账户；先不上传真实预览。
4. **纯 Local 配对和读取**：网络不可用仍能同网授权、合法 TLS 读取小型快照；服务随主 App 退出。
5. **Cloud 最小链路**：经用户确认部署账户后建 Worker/D1；读取 latest-only 和有界数据，确认条件写入、撤销和实际行计数。
6. **iOS 缓存和四页原生 UI**：先概览／记录，再趋势／设置；所有数据先缓存后同步，Local／Cloud切换不回退。
7. **Widget＋本地 Activity**：复用同一手机投影，不增采集服务；验证真实 stale／offline 表达。
8. **仅在路线 B 获确认后增加 APNs**：保持统一合法签名；按任务开始／结束和节流后的状态推送，不逐 token。
9. **中间交付与用户验收**：本地开发包、签名与归档核验、本地 Git；不推送、不发布。版本调整和正式附件规则另请确认。

这些是实施顺序，不是时间／人天承诺。签名与后台能力先验证，是为了避免先做完漂亮页面后才发现产品目标无法满足。

## 16. 验证、失败状态与完成定义

### 16.1 最小验证边界

本轮只有文档和源码审计，没有生产代码变化，所以不重复跑 Mac 打包／冒烟，也不生成 UI 截图矩阵。沿用上一开发包已完成的三项隔离冒烟记录。

以后实现时：

- Mac 仍固定原有三项隔离冒烟及版本、签名、Widget、ZIP／哈希核验，不增加长期测试文件或测试套件。
- 新同步能力只做与当次实现直接相关的必要核对；不自动升级为全面回归或设备矩阵。
- 真机验证由用户授权／操作配合，不代启动或重启其真实 Mac 安装版；测试不能依赖调试器维持后台运行。
- 以下为人工验收条件与实现不变量，不是新增自动测试项清单。

### 16.2 必须满足的实际体验

- 冷启动先显示缓存，同网优先 Local，切蜂窝用 Cloud；不存在旧响应把新数据覆盖。
- recent 只保留规定简表；手机／云端均无禁传日志或凭据；开启短预览需要明确同意。
- 请求恢复和模型切换沿 Mac 归组，摘要不冒充，计量不重复，源账户／设备不串数据。
- 断网保留最后已知数据且标旧；错误恢复、隐私清除、删除与游标过期最终能收敛。
- Mac 关闭窗口仍同步，⌘Q 不残留自有常驻服务；mock 不触及生产网络与身份。
- 相同业务结果不反复上传；分页／图表／Widget 不因无关 revision 整体重建。
- SideStore 的 App、Widget、共享快照与 Activity 在目标 iPhone 上实际工作；7 天续签路径记录清楚，不用模拟器截图代替签名验证。
- 按用户最终选定路线验收后台：A 明确接受系统机会式；B 必须有可用 APNs 与合法签名，且不称为硬实时。
- Cloud 额度估算有一次实际请求／行读写／体积证据替换，并说明数据规模、前台时长与网络条件。

### 16.3 需要用户讨论的最终选择

1. **后台时效**：保留免费 SideStore、接受不保证间隔的后台刷新；还是锁屏／其他 App 中仍需及时跟进任务，并愿意讨论保留 APNs 的统一签名分发？
2. **短预览隐私**：记录显示 80 字／240 字节以内的用户请求短预览（明确同意后上传），还是只显示模型、状态和计量？
3. **保留范围**：最近 7 天且最多 200 条、90 日日汇总、48 小时小时桶是否足够？
4. **首页与导航**：四标签，“当前任务 → 额度 → 今日 → 最近记录 → 趋势”的顺序是否符合最常用场景？

在这些选项确定前，当前正确的交付状态是“开发准备方案已完成，等待决策”，不是“iOS 已开发完成”或“所有前置验收已经通过”。

## 17. 资料与交付记录

技术和设计来源已在对应章节链接，全部优先采用 Apple／Cloudflare／SideStore 官方资料与本仓库已验证实现。关键在线资料核对日为 2026-09-28；源码示例正式采用时应固定版本和许可证，不把网页的未来变化自动当成本仓库行为。

当前 Mac 交付未因本文而更改：

- `build/dev/macos/Codexio.app` 与 `Codexio.app.zip`
- 程序 0.3.1；Widget 20／1.19；基线提交 `66a94d9`
- ZIP 大小 4,775,949 bytes
- SHA-256：`0d13690a69f7397cfad4febca2fcaefa7aaa240df2160c136684d5ca82e5b070`
- 验证记录：`docs/v0.3.1/PRE_IOS_FIXES.md`、`build/checks/macos-smoke/result.json`
- 本轮不重新构建二进制、不更改版本、不变更 Windows 清单、不操作 release。

原 Downloads 文档的上一稿存档于仓库 `build/backups/ios-preparation-20260928/Codexio_Mac_iOS_Sync_Design.before.md`，用于回看被收紧的旧方案，不是第二套生效规格。
