import AppKit
import SwiftUI

struct HorizontalResizeHandle: NSViewRepresentable {
    var begin: () -> Void
    var change: (CGFloat) -> Void
    var end: (CGFloat) -> Void
    func makeNSView(context: Context) -> Handle { Handle(frame:.zero) }
    func updateNSView(_ view: Handle,context: Context) { view.begin = begin; view.change = change; view.end = end }
    final class Handle: NSView {
        var begin: (() -> Void)?, change: ((CGFloat) -> Void)?, end: ((CGFloat) -> Void)?
        private var origin: CGFloat?
        override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
        override func resetCursorRects() { addCursorRect(bounds,cursor:.resizeLeftRight) }
        private func screenX(_ event: NSEvent) -> CGFloat { window?.convertPoint(toScreen:event.locationInWindow).x ?? event.locationInWindow.x }
        override func mouseDown(with event: NSEvent) { origin = screenX(event); begin?() }
        override func mouseDragged(with event: NSEvent) { if let origin { change?(screenX(event)-origin) } }
        override func mouseUp(with event: NSEvent) {
            guard let origin else { return }
            self.origin = nil; end?(screenX(event)-origin)
        }
    }
}

struct NavigationButton: NSViewRepresentable {
    let title: String
    let selected: Bool
    let action: () -> Void
    func makeNSView(context: Context) -> Control { Control(frame:.zero) }
    func updateNSView(_ view: Control,context: Context) {
        view.title = title; view.setAccessibilityLabel(title)
        view.selected = selected; view.clicked = action
    }
    final class Control: NSButton {
        var clicked: (() -> Void)?
        var selected = false { didSet { if selected != oldValue { needsDisplay = true } } }
        private var inside = false { didSet { if inside != oldValue { needsDisplay = true } } }
        private var tracking: NSTrackingArea?
        override init(frame: NSRect) {
            super.init(frame:frame)
            isBordered = false; focusRingType = .none
            setButtonType(.momentaryChange); target = self; action = #selector(activate)
            setContentHuggingPriority(.defaultLow,for:.horizontal)
        }
        required init?(coder: NSCoder) { fatalError() }
        override var intrinsicContentSize: NSSize { NSSize(width:NSView.noIntrinsicMetric,height:40) }
        override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
        override func mouseDown(with event: NSEvent) { activate() }
        @objc private func activate() { clicked?() }
        override func updateTrackingAreas() {
            super.updateTrackingAreas()
            if let tracking { removeTrackingArea(tracking) }
            let area = NSTrackingArea(rect:.zero,options:[.mouseEnteredAndExited,.activeInKeyWindow,.inVisibleRect],owner:self,userInfo:nil)
            addTrackingArea(area); tracking = area
        }
        override func mouseEntered(with event: NSEvent) { inside = true }
        override func mouseExited(with event: NSEvent) { inside = false }
        override func draw(_ dirtyRect: NSRect) {
            if selected || inside {
                NSColor.labelColor.withAlphaComponent(selected ? 0.075 : 0.04).setFill()
                NSBezierPath(roundedRect:bounds,xRadius:8,yRadius:8).fill()
            }
        }
    }
}
