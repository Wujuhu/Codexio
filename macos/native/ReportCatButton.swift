import AppKit
import ImageIO
import SwiftUI

struct ReportCatButton: View {
    let action: () -> Void
    var body: some View {
        Button(action:action) { ReportCatImage().frame(width:32,height:32) }
            .buttonStyle(.plain)
            .help(L("打开 AI 使用报告", "Open AI usage reports"))
            .accessibilityLabel(L("打开 AI 使用报告", "Open AI usage reports"))
    }
}

private struct ReportCatImage: NSViewRepresentable {
    func makeNSView(context: Context) -> ReportCatImageView { ReportCatImageView(frame:.zero) }
    func updateNSView(_ view: ReportCatImageView,context: Context) { view.refreshState() }
    static func dismantleNSView(_ view: ReportCatImageView,coordinator: ()) { view.invalidate() }
}

private enum ReportCatImages {
    static let queue = DispatchQueue(label:"com.wujuhu.codexio.report-cat",qos:.utility)
    private static let widths: [(String,CGFloat)] = [("head",82),("page",88),("left-paw",26),("right-paw",26),("tail",43)]
    static func load(scale: CGFloat,dark: Bool) -> [String:CGImage]? {
        // Same ImageIO path as Branding.smallTemplate: never decode the full PNG.
        var images: [String:CGImage] = [:]
        for (name,width) in widths {
            guard let url = Bundle.main.url(forResource:name,withExtension:"png",subdirectory:"ReportCat"),
                  let source = CGImageSourceCreateWithURL(url as CFURL,[kCGImageSourceShouldCache:false] as CFDictionary),
                  let properties = CGImageSourceCopyPropertiesAtIndex(source,0,nil) as? [CFString:Any],
                  let sourceWidth = properties[kCGImagePropertyPixelWidth] as? NSNumber,
                  let sourceHeight = properties[kCGImagePropertyPixelHeight] as? NSNumber, sourceWidth.doubleValue > 0 else { return nil }
            let aspect = CGFloat(sourceHeight.doubleValue/sourceWidth.doubleValue)
            let pixels = Int(ceil(width*0.2*scale*max(1,aspect)))
            let options: [CFString:Any] = [kCGImageSourceCreateThumbnailFromImageAlways:true,kCGImageSourceCreateThumbnailWithTransform:true,kCGImageSourceThumbnailMaxPixelSize:pixels,kCGImageSourceShouldCacheImmediately:true]
            guard let image = CGImageSourceCreateThumbnailAtIndex(source,0,options as CFDictionary) else { return nil }
            if dark {
                guard let themed = inverted(image) else { return nil }
                images[name] = themed
            } else { images[name] = image }
        }
        return images
    }
    private static func inverted(_ image: CGImage) -> CGImage? {
        // Preserve the PNG's distinct outline, fill and alpha. Work only on the
        // tiny thumbnail, without retaining a filter context or the source image.
        guard let space = CGColorSpace(name:CGColorSpace.sRGB),
              let context = CGContext(data:nil,width:image.width,height:image.height,bitsPerComponent:8,bytesPerRow:0,space:space,bitmapInfo:CGImageAlphaInfo.premultipliedLast.rawValue | CGBitmapInfo.byteOrder32Big.rawValue),
              let pixels = context.data?.assumingMemoryBound(to:UInt8.self) else { return nil }
        context.draw(image,in:CGRect(x:0,y:0,width:image.width,height:image.height))
        for y in 0..<image.height {
            for x in 0..<image.width {
                let offset = y*context.bytesPerRow+x*4, alpha = pixels[offset+3]
                for channel in 0..<3 { pixels[offset+channel] = alpha-min(alpha,pixels[offset+channel]) }
            }
        }
        return context.makeImage()
    }
}

private final class ReportCatImageView: NSView {
    private struct ImageKey: Equatable { let scale: CGFloat; let dark: Bool }
    private var animator: ReportCatAnimation?
    private var currentKey: ImageKey?
    private var requestedKey: ImageKey?
    private var loading = false
    private var disposed = false
    private var timer: Timer?
    private var observers: [(NotificationCenter,NSObjectProtocol)] = []
    private var windowObservers: [NSObjectProtocol] = []
    private let placeholder = NSImageView()
    override var isFlipped: Bool { false }
    override var intrinsicContentSize: NSSize { NSSize(width:32,height:32) }
    override init(frame: NSRect) {
        super.init(frame:frame)
        wantsLayer = true
        placeholder.image = NSImage(systemSymbolName:"doc.text",accessibilityDescription:nil)
        placeholder.contentTintColor = .labelColor
        placeholder.imageScaling = .scaleProportionallyDown
        addSubview(placeholder)
        for name in [NSApplication.didBecomeActiveNotification,NSApplication.didResignActiveNotification] {
            let token = NotificationCenter.default.addObserver(forName:name,object:nil,queue:.main) { [weak self] _ in self?.refreshState() }
            observers.append((.default,token))
        }
        let workspace = NSWorkspace.shared.notificationCenter
        let token = workspace.addObserver(forName:NSWorkspace.accessibilityDisplayOptionsDidChangeNotification,object:nil,queue:.main) { [weak self] _ in self?.refreshState() }
        observers.append((workspace,token))
        let quit = NotificationCenter.default.addObserver(forName:NSApplication.willTerminateNotification,object:nil,queue:.main) { [weak self] _ in self?.invalidate() }
        observers.append((.default,quit))
    }
    required init?(coder: NSCoder) { fatalError() }
    override func hitTest(_ point: NSPoint) -> NSView? { nil }
    override func layout() {
        super.layout()
        placeholder.frame = NSRect(x:bounds.midX-9,y:bounds.midY-9,width:18,height:18)
        CATransaction.begin(); CATransaction.setDisableActions(true)
        animator?.rootLayer.position = CGPoint(x:bounds.midX,y:bounds.midY)
        CATransaction.commit()
    }
    override func viewDidChangeEffectiveAppearance() { super.viewDidChangeEffectiveAppearance(); refreshState() }
    override func viewDidChangeBackingProperties() { super.viewDidChangeBackingProperties(); refreshState() }
    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        windowObservers.forEach { NotificationCenter.default.removeObserver($0) }; windowObservers.removeAll()
        if let window {
            for name in [NSWindow.didChangeOcclusionStateNotification,NSWindow.didBecomeKeyNotification,NSWindow.didResignKeyNotification,NSWindow.didMiniaturizeNotification,NSWindow.didDeminiaturizeNotification,NSWindow.willBeginSheetNotification,NSWindow.didEndSheetNotification] {
                windowObservers.append(NotificationCenter.default.addObserver(forName:name,object:window,queue:.main) { [weak self] _ in self?.refreshState() })
            }
            windowObservers.append(NotificationCenter.default.addObserver(forName:NSWindow.willCloseNotification,object:window,queue:.main) { [weak self] _ in self?.suspend() })
        }
        refreshState()
    }
    private var visible: Bool {
        !disposed && !isHiddenOrHasHiddenAncestor && window?.isVisible == true && window?.isMiniaturized == false && window?.occlusionState.contains(.visible) == true
    }
    private var canAnimate: Bool {
        visible && NSApp.isActive && window?.isKeyWindow == true && window?.attachedSheet == nil && !NSWorkspace.shared.accessibilityDisplayShouldReduceMotion
    }
    func refreshState() {
        guard visible else { suspend(); return }
        let key = ImageKey(scale:window?.backingScaleFactor ?? 2,dark:effectiveAppearance.bestMatch(from:[.darkAqua,.aqua]) == .darkAqua)
        requestedKey = key
        if currentKey != key && !loading {
            loading = true
            ReportCatImages.queue.async { [weak self] in
                let images = autoreleasepool { ReportCatImages.load(scale:key.scale,dark:key.dark) }
                DispatchQueue.main.async { [weak self] in
                    guard let self else { return }
                    self.loading = false
                    guard self.visible, self.requestedKey == key else { self.refreshState(); return }
                    self.releaseArtwork()
                    self.currentKey = key
                    if let images, let animator = try? ReportCatAnimation(images:images,backingScale:key.scale) {
                        self.animator = animator; self.layer?.addSublayer(animator.rootLayer)
                        self.placeholder.isHidden = true; self.needsLayout = true
                    }
                    self.refreshState()
                }
            }
        }
        if canAnimate, animator != nil {
            guard timer == nil else { return }
            let next = Timer(timeInterval:Double.random(in:ReportCatAnimation.suggestedInterval),repeats:false) { [weak self] _ in
                guard let self else { return }
                self.timer = nil
                if self.canAnimate { self.animator?.play() }
                self.refreshState()
            }
            next.tolerance = 5; timer = next; RunLoop.main.add(next,forMode:.common)
        } else { timer?.invalidate(); timer = nil; animator?.stop() }
    }
    private func releaseArtwork() {
        timer?.invalidate(); timer = nil
        animator?.stop(); animator?.rootLayer.removeFromSuperlayer(); animator = nil
        currentKey = nil; placeholder.isHidden = false
    }
    private func suspend() { requestedKey = nil; releaseArtwork() }
    func invalidate() {
        disposed = true; suspend()
        for (center,token) in observers { center.removeObserver(token) }; observers.removeAll()
        windowObservers.forEach { NotificationCenter.default.removeObserver($0) }; windowObservers.removeAll()
    }
    deinit {
        timer?.invalidate()
        for (center,token) in observers { center.removeObserver(token) }
        windowObservers.forEach { NotificationCenter.default.removeObserver($0) }
    }
}
