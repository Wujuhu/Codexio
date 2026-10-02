// Adapted from the approved report-cat-tail-loop-source native animation.
import Foundation
import QuartzCore
import CoreGraphics

/// Native equivalent of source/animation.css.
/// Inputs are decoded CGImages keyed by: head, page, left-paw, right-paw, tail.
/// Decode bundled PNGs off the main thread, then create this object on the main actor.
/// Host this rootLayer inside a fixed 32 × 32 pt clickable control.
@MainActor
public final class ReportCatAnimation {
    public static let duration: CFTimeInterval = 2.2
    public static let tailDuration: CFTimeInterval = 3.2
    public static let playbackInterval: TimeInterval = 10
    nonisolated public static let displayUnit: CGFloat = 0.18
    public let rootLayer = CALayer()
    public private(set) var unit: CGFloat
    private var layers: [String: CALayer] = [:]
    private let animationKey = "codexio.report-cat.greeting"
    private let tailAnimationKey = "codexio.report-cat.tail-loop"

    public enum AssetError: Error { case missing(String) }

    private struct Placement {
        let name: String
        let x: CGFloat
        let y: CGFloat
        let width: CGFloat
        let aspect: CGFloat
        let anchorX: CGFloat
        let anchorY: CGFloat
        let z: CGFloat
    }
    // Positions and anchors use the top-left coordinate system from motion.json.
    private static let placements: [Placement] = [
        .init(name: "tail", x: 79, y: 83, width: 43, aspect: 280.0/300, anchorX: 0.08, anchorY: 0.90, z: 0),
        .init(name: "head", x: 9, y: 2, width: 82, aspect: 339.0/400, anchorX: 0.50, anchorY: 0.85, z: 1),
        .init(name: "page", x: 6, y: 54, width: 88, aspect: 309.0/376, anchorX: 0.50, anchorY: 0.50, z: 2),
        .init(name: "left-paw", x: 1, y: 46, width: 26, aspect: 159.0/201, anchorX: 0.65, anchorY: 0.78, z: 3),
        .init(name: "right-paw", x: 73, y: 46, width: 26, aspect: 159.0/200, anchorX: 0.35, anchorY: 0.78, z: 3)
    ]

    public init(images: [String: CGImage], unit: CGFloat = ReportCatAnimation.displayUnit, backingScale: CGFloat = 2) throws {
        for placement in Self.placements where images[placement.name] == nil {
            throw AssetError.missing(placement.name)
        }
        self.unit = unit
        rootLayer.isGeometryFlipped = false
        rootLayer.masksToBounds = false
        for placement in Self.placements {
            let layer = CALayer()
            layer.contents = images[placement.name]
            layer.contentsGravity = .resize
            layer.zPosition = placement.z
            layers[placement.name] = layer
            rootLayer.addSublayer(layer)
        }
        layout(unit: unit, backingScale: backingScale)
    }

    /// Re-layout only when display size/backing scale changes. Keeps the root's position.
    public func layout(unit: CGFloat, backingScale: CGFloat = 2) {
        guard unit > 0, backingScale > 0 else { return }
        let resumeTail = isTailAnimating
        stop()
        self.unit = unit
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        rootLayer.bounds = CGRect(x: 0, y: 0, width: 122 * unit, height: 128 * unit)
        rootLayer.contentsScale = backingScale
        for placement in Self.placements {
            guard let layer = layers[placement.name] else { continue }
            let width = placement.width * unit
            let height = width * placement.aspect // Keep source geometry after pixel downsampling.
            // Convert the CSS top-left anchor to Core Animation's bottom-left geometry.
            let anchor = CGPoint(x: placement.anchorX, y: 1 - placement.anchorY)
            let bottom = rootLayer.bounds.height - placement.y * unit - height
            layer.anchorPoint = anchor
            layer.bounds = CGRect(x: 0, y: 0, width: width, height: height)
            layer.position = CGPoint(x: placement.x * unit + width * anchor.x,
                                     y: bottom + height * anchor.y)
            layer.contentsScale = backingScale
        }
        CATransaction.commit()
        if resumeTail { startTailLoop() }
    }

    public var isTailAnimating: Bool { layers["tail"]?.animation(forKey: tailAnimationKey) != nil }
    public var isAnimating: Bool { layers["head"]?.animation(forKey: animationKey) != nil }

    /// Plays the greeting once without restarting the independent tail loop.
    /// Gate calls on visible/active window state and Reduce Motion.
    public func play() {
        startTailLoop()
        guard !isAnimating else { return }
        let start = CACurrentMediaTime()
        add("head", times: [0, 0.18, 0.32, 0.50, 0.66, 0.79, 1],
            y: [0, -10, -10, -10, -10, -7, 0], degrees: [0, 0, -7, -7, 3, 0, 0],
            curve: CAMediaTimingFunction(controlPoints: 0.4, 0, 0.2, 1), start: start)
        add("left-paw", times: [0, 0.18, 0.66, 0.82, 1],
            y: [0, 0, 0, 0, 0], degrees: [0, -3, -3, 0, 0], start: start)
        add("right-paw", times: [0, 0.22, 0.34, 0.46, 0.58, 0.68, 0.82, 1],
            y: [0, 0, -10, -10, -10, -4, 0, 0], degrees: [0, 0, -24, 10, -16, -6, 0, 0], start: start)
    }

    public func startTailLoop() {
        guard let tail = layers["tail"], !isTailAnimating else { return }
        let animation = CAKeyframeAnimation(keyPath: "transform.rotation.z")
        animation.values = [0.0, 10.0, 0.0, -10.0, 0.0].map { NSNumber(value: $0 * .pi / 180) }
        animation.keyTimes = [0, 0.25, 0.5, 0.75, 1]
        let outward = CAMediaTimingFunction(controlPoints: 0.33, 0.55, 0.66, 1)
        let inward = CAMediaTimingFunction(controlPoints: 0.33, 0, 0.66, 0.45)
        animation.timingFunctions = [outward, inward, outward, inward]
        animation.calculationMode = .linear
        animation.duration = Self.tailDuration
        animation.repeatCount = .infinity
        tail.add(animation, forKey: tailAnimationKey)
    }

    /// Stops both the greeting and the continuous tail loop.
    public func stop() {
        for name in ["head", "left-paw", "right-paw"] {
            layers[name]?.removeAnimation(forKey: animationKey)
        }
        layers["tail"]?.removeAnimation(forKey: tailAnimationKey)
    }

    private func add(_ name: String, times: [Double], y: [Double], degrees: [Double],
                     curve: CAMediaTimingFunction = CAMediaTimingFunction(name: .easeInEaseOut),
                     start: CFTimeInterval) {
        guard let layer = layers[name] else { return }
        let position = keyframes("transform.translation.y", times: times,
                                 values: y.map { -$0 * Double(unit) }, curve: curve)
        let rotation = keyframes("transform.rotation.z", times: times,
                                 values: degrees.map { -$0 * .pi / 180 }, curve: curve)
        let group = CAAnimationGroup()
        group.animations = [position, rotation]
        group.duration = Self.duration
        group.beginTime = layer.convertTime(start, from: nil)
        group.isRemovedOnCompletion = true
        layer.add(group, forKey: animationKey)
    }

    private func keyframes(_ path: String, times: [Double], values: [Double],
                           curve: CAMediaTimingFunction) -> CAKeyframeAnimation {
        let animation = CAKeyframeAnimation(keyPath: path)
        animation.values = values.map { NSNumber(value: $0) }
        animation.keyTimes = times.map { NSNumber(value: $0) }
        animation.timingFunctions = Array(repeating: curve, count: max(0, times.count - 1))
        animation.calculationMode = .linear
        animation.duration = Self.duration
        return animation
    }
}
