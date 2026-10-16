# Claude 订阅、定价与本地 beta 版本

版本：0.3.10-beta。用户于 2026-10-16 明确授权实施 Mac／Windows，并指定本地测试版本；未授权推送或发布。此前 0.3.9 Claude 首轮规格的订阅、计价排除项由本规格取代。

## 已确认范围

- Claude 额度采用官方 Mods 优先、statusLine 兼容，默认静默接入；保留已有配置与状态栏输出。Desktop 本地 Code 页使用自带引擎，无需另装 CLI。普通 Chat／Cowork 不宣称已接入。
- 订阅页复用 Codex 布局：套餐与邮箱、5 小时与周额度、周额度估算；不显示套餐用量历史和主动重置区域。缺失值为 `—`。
- 定价页显示官方 Claude API Standard 基础价格及条件，分开处理 Claude Code 的规则。复用既有 model、upstream_model／returned_model、upstream_models；不增加日志模型标识字段。label 仅帮助建立价格目录别名关系，不能用当前配置改写历史请求事实。
- 当前 Claude Code Fast 的受支持 Opus 模型按实际 Fast 价格计价，订阅账号 Fast 使用 usage credits，不纳入订阅周额度估算。不添加未经官方证实的订阅长上下文倍率；缓存读取、5 分钟／1 小时写入分别计算。
- Claude 活动热图与 Codex 一致补齐最近 365 天，保留缺失计量与无活动的区别。费用贯通概览、日志、详情、趋势及聊天排行。
- 两个来源的日志均采用 Claude 紧凑布局，去掉速度筛选并消除其残留过滤。日期、模式、模型、状态、搜索、分页、显示字段及列宽各自独立。
- 来源切换扩展到订阅和定价；未特别指定的页面行为与 Codex 一致。iOS 本轮仅处理版本兼容，不新增 Claude 页面。
- 本地 beta 遵循 SemVer，beta 低于同号正式版；正式更新继续读取稳定通道，正式发布入口继续拒绝预发布版本。Apple 系统 Bundle 版本保留数字格式，完整语义版本另存应用元数据。

## 参考与复用

- 当前 Pricing.swift／pricing.go 的内容价格版本、有界派生缓存；ClaudeLedger.swift／claude_queries.go 的增量账本和分组。
- 当前 QuotaCard／Quota.svelte、WeeklyEstimator／RollingEstimator、CompactTable 和筛选控件。
- Claude 官方 Mods `$.session.usage()`、`session.measure`；公开示例 jack21/claude-mod-usage、marmottajr/habblaud。桥接只传额度及必要来源信息，不读或转存令牌，不发送模型请求。
- Claude Code 官方 fast-mode、model-config 与 Claude Platform pricing／prompt-caching 文档，2026-10-16 核对。
- Apple Foundation Bundle.executableArchitectures；既有本地提交 0f0a71f 已去掉更新器运行时的 lipo 依赖。

## 交付

只运行现有三项隔离冒烟及必要的编译、签名、版本、归档和哈希检查，不新增测试套件。Mac／iOS 本机构建，Windows 经 SSH 在指定主机交互会话构建并保留产物。开发文件在 build，最终提交本地 Git。

## 实现边界与核验

- 价格以调用的既有返回模型为首选，原请求模型或已确认的标签别名为回退。缓存写入缺少 5m／1h 拆分、缺少速度或仅能回退模型时保留估算标识；未识别型号保留 `—`。标签改变或移除时，从新观测时间开始新映射，旧记录保留之前的映射；价格目录的服务倍率始终使用当前代码规则，不被旧价格缓存覆盖。聚合先逐次调用计算，再汇总到请求／日期／线程。
- Mods 桥接经过本机 Claude Code 2.1.301 的 `plugin validate` 校验，只注册 session.start、session.measure、turn.complete；只读取已经观测到的额度，经带随机凭据的 loopback 连接发送给主 App。主 App 退出关闭监听并移除运行地址，旧 CLI 包装保留原始 statusLine 输出。
- 桥接在 Claude 下一次加载插件时生效。仅普通 Desktop Chat／Cowork、WSL、组织禁止插件以及上游尚未报告限额的场景不伪造读数。公开额度结构没有邮箱和套餐；CLI 可以读取本地非令牌账号元数据，Desktop 无可确认身份时套餐／邮箱保留 `—`，不借用 CLI 身份；有效额度仍按本机来源和周周期进入固定窗口估算。
- 日历固定展示最近 365 天；历史单日峰值与最长连续天数继续计算全部已采集历史，跨年同周的费用正常累加。
- Mac／Windows 使用既有三个隔离冒烟；iOS 仅更新完整版本读取与语义比较并跟随桌面版本。未对真实订阅账号发送额度请求或消费模型额度进行实测。
- 官方参考：[Mods 及支持范围](https://code.claude.com/docs/en/plugins/mods/overview)、[公开事件与 API](https://code.claude.com/docs/en/plugins/mods/reference)、[statusLine 字段](https://code.claude.com/docs/en/statusline)、[Claude Code Fast](https://code.claude.com/docs/en/fast-mode)、[Claude Code 长上下文](https://code.claude.com/docs/en/model-config)、[Standard API 价格](https://platform.claude.com/docs/en/about-claude/pricing)。

## 2026-10-16 固定观察窗口与日志修正

- 用户再次确认：Claude 不增加 OAuth／Cookie／携带登录凭据的接口查询。Desktop-only 不再以邮箱、套餐或 account_verified 作为估算前提；未知身份使用本机配置目录及客户端来源的内部作用域，已知账号、来源或周重置周期变化时重新建立基准。
- Codex／Claude 默认结算间隔改为 10 分钟，保留 30／60 分钟选项。旧的 30 分钟默认迁移到 10 分钟，已经选用 10／60 分钟的配置保留。
- 每条新估算只覆盖一个指定时长的完整观察区间。边界允许最多 5 秒执行／通信调度误差，记录保留真实采样起止时间与实际秒数；超出误差、缺少连续样本、明确采集错误、退出、休眠或时钟异常时，未完成窗口作废，恢复后使用新样本重建基准，不连接中断前后的读数。
- 完整窗口无消费、额度比例未变化或消费数据不可计价时不生成结果，基准仍向前推进，不累计成跨越多个周期的估算。完整窗口保留原有 120 秒日志成熟等待，这只延迟结算，不改变观察时段。旧估算历史保留原始起止时间，不改写成新窗口。
- Mods 在主 App 可接收时按分钟边界调用无网络的 $.session.usage()，通过既有 loopback 接收器传递运行中 Claude Code 已观测的状态；本地接收器停止或不可达后定时读取结束。不发送模型请求或账号接口请求。statusLine 继续采用实际回传时间，边界样本不足时跳过估算，不用 App 中缓存的读数伪造新采样。
- Windows 复用原日志工作循环的维护回调，在无账本变化时也能处理到期结算；不会为计时发布全局用量状态。Windows 用 QueryUnbiasedInterruptTime 检测休眠，Mac 使用系统休眠通知与运行时钟。
- 两种来源的日志首次默认及恢复默认字段均隐藏命中率，仍允许手动选择，来源间的字段和列宽继续独立。旧默认字段集合迁移，其他自定义集合保留。
- 请求副行及上下文列统一显示 model_context_window 容量。Mods 的 context.window 和 statusLine 的 context_window.context_window_size 通过明确的 transcript UUID／prompt ID 关联，最多保留 256 条待关联提示，新增的上下文索引在后台采集队列按需构建，不按时间相邻猜测，不把当前会话容量回填到历史记录，不新增模型标识字段。历史缺少容量显示 —。
- 参考：既有 WeeklyEstimator.swift／rolling.go 的账号及周期校验、120 秒成熟等待和本地消费查询；Claude Code 2.1.301 Mods 类型定义的 session.append UUID、clock.after 和 session.usage；[Windows 非休眠计时 API](https://learn.microsoft.com/en-us/windows/win32/api/realtimeapiset/nf-realtimeapiset-queryunbiasedinterrupttime)。验证范围仍为原有三项隔离冒烟、插件静态校验及交付校验，不新增测试套件。
