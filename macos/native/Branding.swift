import AppKit

enum Branding {
    private static let light = load("app-light")
    private static let dark = load("app-dark")
    private static let mark: NSImage = { let value = load("brand-mark"); value.isTemplate = true; return value }()
    private static func load(_ name: String) -> NSImage {
        guard let file = Bundle.main.url(forResource:name,withExtension:"png"), let image = NSImage(contentsOf:file) else { return NSImage(size:NSSize(width:512,height:512)) }
        return image
    }
    static func logo(dark: Bool) -> NSImage { dark ? self.dark : light }
    static let dockIcon: NSImage = NSImage(size:NSSize(width:1024,height:1024),flipped:false) { rect in
        light.draw(in:rect.insetBy(dx:rect.width*0.09,dy:rect.height*0.09))
        return true
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
