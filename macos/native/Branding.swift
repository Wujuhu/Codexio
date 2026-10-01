import AppKit
import ImageIO

enum Branding {
    static let iconIDs = ["main","01-teal-blue-gradient","02-violet-gradient-tile","03-graphite-relief","04-honey-orange","05-mint-ceramic","06-deep-ocean-aurora","07-ice-blue-glass","08-champagne-metal","09-cream-deboss","10-burgundy-enamel","11-obsidian-copper","12-moonlight-pearl"]
    struct IconPreview: Identifiable { let id: String; let image: NSImage }
    private static let light = load("app-light")
    private static let dark = load("app-dark")
    private static let smallImageLock = NSLock()
    private static var smallImages: [String:NSImage] = [:]
    private static var smallImagesPrepared = false
    private static let emptyMark: NSImage = { let image = NSImage(size:NSSize(width:18,height:18)); image.isTemplate = true; return image }()
    private static let emptyWordmark: NSImage = { let image = NSImage(size:NSSize(width:170,height:33)); image.isTemplate = true; return image }()

    static var smallImagesReady: Bool {
        smallImageLock.lock(); defer { smallImageLock.unlock() }; return smallImagesPrepared
    }
    // Called once from AppState's branding queue, before publishing the selected
    // icon. Reuse ReportArtworkResources' ImageIO downsampling, retaining only
    // small display representations rather than the original decoded PNGs.
    static func prepareSmallImages() {
        guard !smallImagesReady else { return }
        var images: [String:NSImage] = [:]
        for (name,width,extraWidths) in [("brand-mark",CGFloat(18),[CGFloat(28)]),("wordmark",CGFloat(170),[]),("c-dot-ring-static",MenuBarMetrics.taskSize,[]),("completed",MenuBarMetrics.taskSize,[])] {
            images[name] = smallTemplate(name,width:width,extraWidths:extraWidths)
        }
        smallImageLock.lock(); smallImages = images; smallImagesPrepared = true; smallImageLock.unlock()
    }
    private static func smallTemplate(_ name: String,width: CGFloat,extraWidths: [CGFloat]) -> NSImage? {
        guard let file = Bundle.main.url(forResource:name,withExtension:"png"),
              let source = CGImageSourceCreateWithURL(file as CFURL,[kCGImageSourceShouldCache:false] as CFDictionary),
              let properties = CGImageSourceCopyPropertiesAtIndex(source,0,nil) as? [CFString:Any],
              let sourceWidth = properties[kCGImagePropertyPixelWidth] as? NSNumber,
              let sourceHeight = properties[kCGImagePropertyPixelHeight] as? NSNumber, sourceWidth.doubleValue > 0 else { return nil }
        let aspect = CGFloat(sourceHeight.doubleValue/sourceWidth.doubleValue)
        let size = NSSize(width:width,height:width*aspect)
        let image = NSImage(size:size)
        // Include the collapsed sidebar's 28-point use of the same mark. Keep
        // the full source canvas and alpha; never recenter on opaque bounds.
        let pixelSizes = Set(([width]+extraWidths).flatMap { pointWidth in [1,2,3].map {Int(ceil(pointWidth*max(1,aspect)*CGFloat($0)))} })
        for pixels in pixelSizes.sorted() {
            let options: [CFString:Any] = [kCGImageSourceCreateThumbnailFromImageAlways:true,kCGImageSourceCreateThumbnailWithTransform:true,kCGImageSourceThumbnailMaxPixelSize:pixels,kCGImageSourceShouldCacheImmediately:true]
            guard let thumbnail = CGImageSourceCreateThumbnailAtIndex(source,0,options as CFDictionary) else { continue }
            let bitmap = NSBitmapImageRep(cgImage:thumbnail); bitmap.size = size; image.addRepresentation(bitmap)
        }
        guard !image.representations.isEmpty else { return nil }
        image.isTemplate = true; return image
    }
    private static func smallImage(_ name: String) -> NSImage? {
        smallImageLock.lock(); defer { smallImageLock.unlock() }; return smallImages[name]
    }
    private static func load(_ name: String) -> NSImage {
        guard let file = Bundle.main.url(forResource:name,withExtension:"png"), let image = NSImage(contentsOf:file) else { return NSImage(size:NSSize(width:512,height:512)) }
        return image
    }
    static func logo(dark: Bool) -> NSImage { dark ? self.dark : light }
    static func sidebarWordmark() -> NSImage { smallImage("wordmark") ?? emptyWordmark }
    static func iconID(_ value: String) -> String { iconIDs.contains(value) ? value : "main" }
    // Call from the branding queue. Only the selected full-size artwork is loaded.
    static func appIcon(_ id: String) throws -> NSImage {
        let id = iconID(id)
        guard let file = Bundle.main.url(forResource:id == "main" ? "app-light" : id,withExtension:"png",subdirectory:id == "main" ? nil : "app-icons"),
              let image = NSImage(data:try Data(contentsOf:file)) else { throw AppFailure(L("无法读取图标", "The icon could not be loaded")) }
        return image
    }
    static func iconPreviews() -> [IconPreview] {
        iconIDs.compactMap { id in
            guard let file = Bundle.main.url(forResource:id+"-preview",withExtension:"png",subdirectory:"app-icons"),
                  let data = try? Data(contentsOf:file), let image = NSImage(data:data) else { return nil }
            return IconPreview(id:id,image:image)
        }
    }
    static func dockIcon(_ image: NSImage,id: String) -> NSImage? {
        // nil restores the bundle's native multi-resolution ICNS, generated by
        // renderIconset with this same source and 9% inset, including 1024px.
        if iconID(id) == "main" { return nil }
        // 512 points retain the source's 1024 pixels on a 2x display without
        // creating oversized original-image and application-icon snapshots.
        return NSImage(size:NSSize(width:512,height:512),flipped:false) { rect in
            image.draw(in:rect.insetBy(dx:rect.width*0.09,dy:rect.height*0.09))
            return true
        }
    }
    static func menuIcon() -> NSImage {
        smallImage("brand-mark") ?? emptyMark
    }
    static func taskStatusIcon(running: Bool) -> NSImage? { smallImage(running ? "c-dot-ring-static" : "completed") ?? smallImage("brand-mark") }
    static func renderIconset(to directory: URL) throws {
        guard let source = Bundle.main.url(forResource:"app-light",withExtension:"png"), let image = NSImage(contentsOf:source) else { throw AppFailure("The light SVG raster is missing") }
        try FileManager.default.createDirectory(at:directory,withIntermediateDirectories:true)
        for size in [16,32,128,256,512] {
            for scale in [1,2] {
                let pixels = size*scale
                guard let bitmap = NSBitmapImageRep(bitmapDataPlanes:nil,pixelsWide:pixels,pixelsHigh:pixels,bitsPerSample:8,samplesPerPixel:4,hasAlpha:true,isPlanar:false,colorSpaceName:.deviceRGB,bytesPerRow:0,bitsPerPixel:0), let context = NSGraphicsContext(bitmapImageRep:bitmap) else { throw AppFailure("Cannot render the app icon") }
                NSGraphicsContext.saveGraphicsState(); NSGraphicsContext.current = context; context.imageInterpolation = .high
                image.draw(in:NSRect(x:Double(pixels)*0.09,y:Double(pixels)*0.09,width:Double(pixels)*0.82,height:Double(pixels)*0.82),from:.zero,operation:.copy,fraction:1)
                NSGraphicsContext.restoreGraphicsState()
                guard let data = bitmap.representation(using:.png,properties:[:]) else { throw AppFailure("Cannot encode the app icon") }
                try data.write(to:directory.appendingPathComponent("icon_\(size)x\(size)"+(scale == 2 ? "@2x" : "")+".png"))
            }
        }
    }
}
