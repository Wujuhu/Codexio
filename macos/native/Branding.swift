import AppKit

enum Branding {
    static let iconIDs = ["main","01-teal-blue-gradient","02-violet-gradient-tile","03-graphite-relief","04-honey-orange","05-mint-ceramic","06-deep-ocean-aurora","07-ice-blue-glass","08-champagne-metal","09-cream-deboss","10-burgundy-enamel","11-obsidian-copper","12-moonlight-pearl"]
    struct IconPreview: Identifiable { let id: String; let image: NSImage }
    private static let light = load("app-light")
    private static let dark = load("app-dark")
    private static let mark: NSImage = { let value = load("brand-mark"); value.isTemplate = true; return value }()
    private static func load(_ name: String) -> NSImage {
        guard let file = Bundle.main.url(forResource:name,withExtension:"png"), let image = NSImage(contentsOf:file) else { return NSImage(size:NSSize(width:512,height:512)) }
        return image
    }
    static func logo(dark: Bool) -> NSImage { dark ? self.dark : light }
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
    static func dockIcon(_ image: NSImage) -> NSImage {
        NSImage(size:NSSize(width:1024,height:1024),flipped:false) { rect in
            image.draw(in:rect.insetBy(dx:rect.width*0.09,dy:rect.height*0.09))
            return true
        }
    }
    static func menuIcon() -> NSImage {
        let image = mark.copy() as! NSImage; image.size = NSSize(width:18,height:18); image.isTemplate = true; return image
    }
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
