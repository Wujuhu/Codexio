import AppKit
import SwiftUI
import Combine

enum NativeUpdatePhase {
    case offer, downloading, validating, ready, installing, failed(String)
    var isBusy: Bool { switch self { case .downloading,.validating,.installing: return true; default: return false } }
    var isFailure: Bool { if case .failed = self { return true }; return false }
}

final class NativeUpdatePresentation: ObservableObject {
    let version: String
    let notes: String
    let size: Int
    @Published var phase = NativeUpdatePhase.offer
    @Published var received: Int64 = 0
    init(version: String, notes: String, size: Int) { self.version = version; self.notes = notes; self.size = size }
}

enum NativeUpdateSheet {
    static func make(model: NativeUpdatePresentation, update: @escaping () -> Void, later: @escaping () -> Void, cancel: @escaping () -> Void) -> NSWindow {
        NativeUpdateWindow(model:model,update:update,later:later,cancel:cancel)
    }
}

private final class NativeUpdateWindow: NSWindow {
    private var phaseObservation: AnyCancellable?
    init(model: NativeUpdatePresentation, update: @escaping () -> Void, later: @escaping () -> Void, cancel: @escaping () -> Void) {
        super.init(contentRect:NSRect(x:0,y:0,width:440,height:250),styleMask:[.titled],backing:.buffered,defer:false)
        title = L("Codexio 更新", "Codexio Update"); isReleasedWhenClosed = false
        // An informational update sheet must not veto the normal quit handshake.
        preventsApplicationTerminationWhenModal = false
        let hosting = NSHostingView(rootView:NativeUpdateView(model:model,update:update,later:later,cancel:cancel))
        hosting.sizingOptions = [.intrinsicContentSize]
        contentView = hosting
        fitContent()
        phaseObservation = model.$phase.dropFirst().sink { [weak self] _ in
            DispatchQueue.main.async { self?.fitContent() }
        }
    }
    private func fitContent() {
        guard let contentView else { return }
        contentView.layoutSubtreeIfNeeded()
        setContentSize(NSSize(width:440,height:max(170,min(540,contentView.fittingSize.height))))
    }
}

private struct NativeUpdateView: View {
    @ObservedObject var model: NativeUpdatePresentation
    let update: () -> Void
    let later: () -> Void
    let cancel: () -> Void

    var body: some View {
        VStack(alignment:.leading,spacing:18) {
            HStack(alignment:.top,spacing:13) {
                Image(systemName:model.phase.isFailure ? "exclamationmark.triangle" : "arrow.down.circle")
                    .font(.system(size:31,weight:.regular)).foregroundStyle(model.phase.isFailure ? Color.orange : Color.accentColor)
                    .frame(width:38,height:40).accessibilityHidden(true)
                VStack(alignment:.leading,spacing:6) {
                    Text(model.phase.isFailure ? L("更新未完成", "Update not completed") : L("Codexio 有新版本", "A new Codexio is available"))
                        .font(.system(size:17,weight:.semibold))
                    HStack(spacing:8) {
                        Text(L("当前版本", "Current")+" "+BuildInfo.version)
                        Image(systemName:"arrow.right").font(.system(size:10))
                        Text(L("目标版本", "Target")+" "+model.version).foregroundStyle(.primary)
                    }.font(.system(size:12)).foregroundStyle(.secondary)
                }
            }
            content
            actions
        }
        .padding(22).frame(width:440).fixedSize(horizontal:false,vertical:true)
    }

    @ViewBuilder private var content: some View {
        switch model.phase {
        case .offer:
            if !model.notes.isEmpty {
                ScrollView {
                    Text(model.notes).font(.system(size:12)).textSelection(.enabled)
                        .frame(maxWidth:.infinity,alignment:.leading).padding(12)
                }
                .frame(height:160)
                .background(Color(nsColor:.textBackgroundColor),in:RoundedRectangle(cornerRadius:8))
            }
            Text(L("更新后将重新打开 Codexio。", "Codexio will reopen after the update."))
                .font(.system(size:12)).foregroundStyle(.secondary)
        case .downloading:
            VStack(alignment:.leading,spacing:8) {
                ProgressView(value:Double(model.received),total:Double(max(1,model.size)))
                HStack {
                    Text(L("正在下载", "Downloading"))
                    Spacer()
                    Text(ByteCountFormatter.string(fromByteCount:model.received,countStyle:.file)+" / "+ByteCountFormatter.string(fromByteCount:Int64(model.size),countStyle:.file)).monospacedDigit()
                }.font(.system(size:12)).foregroundStyle(.secondary)
            }
        case .validating:
            progress(L("正在校验安装包与应用签名…", "Verifying the package and app signature…"))
        case .installing:
            progress(L("正在准备安装，Codexio 将正常退出并重新打开…", "Preparing to install. Codexio will quit normally and reopen…"))
        case .ready:
            Text(L("更新已校验。点击“更新”后继续正常退出并安装。", "The update is verified. Choose Update to quit normally and install."))
                .font(.system(size:12)).foregroundStyle(.secondary)
        case .failed(let message):
            VStack(alignment:.leading,spacing:12) {
                ScrollView {
                    Text(message).font(.system(size:12)).textSelection(.enabled)
                        .frame(maxWidth:.infinity,alignment:.leading)
                }.frame(height:min(150,max(42,CGFloat(message.count/52+message.split(separator:"\n").count)*17)))
                Text(L("可下载此版本的 Mac ZIP，或打开对应 Release 页面。", "Download the Mac ZIP for this version or open its release page."))
                    .font(.system(size:12)).foregroundStyle(.secondary)
            }
        }
    }
    private func progress(_ text: String) -> some View {
        HStack(spacing:10) { ProgressView().controlSize(.small); Text(text).font(.system(size:12)).foregroundStyle(.secondary) }
    }
    @ViewBuilder private var actions: some View {
        switch model.phase {
        case .offer,.ready:
            HStack { Spacer(); Button(L("稍后", "Later"),action:later).keyboardShortcut(.cancelAction); Button(L("更新", "Update"),action:update).keyboardShortcut(.defaultAction) }
        case .downloading,.validating:
            HStack { Spacer(); Button(L("取消", "Cancel"),action:cancel).keyboardShortcut(.cancelAction) }
        case .installing:
            EmptyView()
        case .failed:
            HStack(spacing:10) {
                Button(L("Release 页面", "Release page")) { NSWorkspace.shared.open(NativeUpdater.releaseURL(model.version)) }.buttonStyle(.link)
                Spacer()
                Button(L("稍后", "Later"),action:later).keyboardShortcut(.cancelAction)
                Button(L("下载 Mac ZIP", "Download Mac ZIP")) { NSWorkspace.shared.open(NativeUpdater.downloadURL(model.version)) }.keyboardShortcut(.defaultAction)
            }
        }
    }
}
