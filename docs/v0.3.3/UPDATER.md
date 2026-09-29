# macOS 更新流程

实现范围为 `macos/native/NativeUpdater.swift` 与 `macos/native/UpdateViews.swift`，主窗口和退出流程由 AppMain 集成。开发包不触发正式发布。

## 采用的机制

- 复用当前 `NativeUpdater.extract` 的 ZIP 结构检查、ditto 解包、签名、Bundle 版本及架构检查；保留固定 latest.json 地址、严格版本格式、Mac ZIP 下载地址、大小上限与 SHA-256。
- 复用当前 `Installation.swift` 的唯一稳定宿主、`.Codexio-update.lock`、Widget 接管、正常退出等待与 `launchChecked` 启动确认。只有该确认成功后才删除旧版备份。
- 参考 `v0.2.10:src/codexio/macos_updater.py` 的状态记录、准备／提交分离和失败后保留旧版本机制。新增状态恢复在后台读取自己的更新目录；清理只涉及明确归属的包、暂存和已确认完成的任务。
- 使用 Foundation `URLSessionDownloadDelegate` 获取真实累计下载字节；分母来自经过严格检查的清单大小。限频发布到独立 ObservableObject，不触发业务扫描或全局用量状态更新。
- 使用 AppKit 主窗口 sheet + SwiftUI 原生控件。弹窗从不调用 activate 或打开主窗口；报告与更新由宿主统一排序。

## 行为

- 初始化时恢复上次安装状态并检查更新；`CODEXIO_SKIP_UPDATE_ONCE` 只跳过首次网络检查，不跳过恢复。既有小时定时检查保留，增加 60 秒容差；`macos_auto_update` 现在控制自动检查。
- 检查仅下载清单和对应 `v<version>` 的 GitHub Release 元数据。仅采用返回 tag_name 完全一致、非草稿的正文；空白正文不占用界面区域。未点击“更新”不会下载 Mac ZIP。
- “稍后”按版本写入 `updates/reminders.json`，默认 24 小时；最多保留 32 个仍有效版本。手动“更新…”／“检查更新”可再次展示。该文件使用独立串行队列写入，避免被下载校验延迟，正常退出前等待已经排队的提醒写入。
- 点击更新后依次下载、校验、请求正常退出和原路径安装。普通 Command-Q 取消下载或未获本次安装授权的已准备数据，不运行安装器或重新拉起主程序。
- 下载、清单／包校验和安装失败均显示目标版本的 Mac ZIP 与 Release 入口。安装器退出前失败与异常中断写入状态，供下次主窗口可见时恢复提示；失败包及时清理。
- 备份只有经过启动确认才可删除。恢复中遇到未确认备份时保留它；已确认状态还需核对当前 App 可执行文件哈希，才能补做之前未完成的备份清理。旧版无确认哈希的残留备份保守保留。
- `--mock` 下初始化、检查、下载、退出准备全部隔离，不调用安装、接管、真实网络或真实系统注册。

## AppMain 集成

```swift
updater = NativeUpdater(state: state)
updater?.onPresentationNeeded = { [weak self] in self?.presentWindowContentIfEligible() }
updater?.onPresentationFinished = { [weak self] in self?.presentWindowContentIfEligible() }
updater?.onInstallRequested = { NSApp.terminate(nil) }
state.onCheckUpdate = { [weak self] in self?.updater?.check(manual: true) }
state.onInstallUpdate = { [weak self] in self?.updater?.requestUpdate() }
```

从主窗口 showWindow、didBecomeKey / applicationDidBecomeActive 以及报告关闭回调调用同一协调方法。初始化的旧 20 秒检查延时块应删除。

```swift
guard let window, !isTerminating, window.isVisible, NSApp.isActive,
      window.attachedSheet == nil, updater?.isResolvingStartup != true else { return }
if updater?.presentIfNeeded(on: window) == true { return }
// 接下来展示符合条件的报告。
```

进入 applicationShouldTerminate 时调用 `updater?.beginTermination()`；取消退出时调用 `updater?.cancelTermination()`，不从外部写 installOnQuit。保留原有先停止上游、再准备安装器、再 finishQuit 的顺序，安装器准备改为异步：

```swift
updater?.prepareInstallerIfNeeded { [weak self] error in
    guard let self, self.isTerminating else { return }
    if error != nil {
        self.isTerminating = false
        self.updater?.cancelTermination()
        self.upstream?.cancelQuit()
        NSApp.reply(toApplicationShouldTerminate: false)
        // 更新器已经提供失败弹窗及下载入口；不重复创建另一张错误 sheet。
        return
    }
    self.state.finishQuit { _ in NSApp.reply(toApplicationShouldTerminate: true) }
}
```

设置可用按钮改为“更新…”／“Update…”，开关改为“自动检查更新”／“Check for updates automatically”；保留现有偏好键。

## 验证边界

本子任务只做语法、类型检查和静态生命周期审阅，不新增测试，不运行安装器，不启动用户已安装的 App。最终开发构建、签名／ZIP 校验及既有三项隔离冒烟由主任务统一执行。真实网络下载和实际 /Applications 替换不会作为开发验证运行。
