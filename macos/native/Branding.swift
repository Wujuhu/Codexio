import AppKit

enum Branding {
    static func renderIconset(to directory: URL) throws {
        try FileManager.default.createDirectory(at:directory,withIntermediateDirectories:true)
        for size in [16,32,128,256,512] {
            for scale in [1,2] {
                let pixels = size*scale
                guard let bitmap = NSBitmapImageRep(bitmapDataPlanes:nil,pixelsWide:pixels,pixelsHigh:pixels,bitsPerSample:8,samplesPerPixel:4,hasAlpha:true,isPlanar:false,colorSpaceName:.deviceRGB,bytesPerRow:0,bitsPerPixel:0), let context = NSGraphicsContext(bitmapImageRep:bitmap) else { throw AppFailure("Cannot render the app icon") }
                NSGraphicsContext.saveGraphicsState(); NSGraphicsContext.current = context
                let cg = context.cgContext; cg.scaleBy(x:CGFloat(pixels)/512,y:CGFloat(pixels)/512)
                NSColor.white.setFill(); NSBezierPath(roundedRect:NSRect(x:0,y:0,width:512,height:512),xRadius:112,yRadius:112).fill()
                cg.translateBy(x:256,y:256); cg.scaleBy(x:1.2,y:1.2)
                NSColor(red:0.2,green:0.216,blue:0.267,alpha:1).setStroke()
                let brackets = NSBezierPath(); brackets.lineWidth = 38; brackets.lineCapStyle = .round; brackets.lineJoinStyle = .round
                brackets.move(to:NSPoint(x:-60,y:120)); brackets.line(to:NSPoint(x:-136,y:0)); brackets.line(to:NSPoint(x:-60,y:-120)); brackets.move(to:NSPoint(x:60,y:120)); brackets.line(to:NSPoint(x:136,y:0)); brackets.line(to:NSPoint(x:60,y:-120)); brackets.stroke()
                cg.saveGState(); cg.setLineWidth(14); cg.setLineCap(.round)
                cg.move(to:CGPoint(x:-32,y:32)); cg.addLine(to:CGPoint(x:32,y:-32)); cg.move(to:CGPoint(x:-32,y:-32)); cg.addLine(to:CGPoint(x:32,y:32)); cg.replacePathWithStrokedPath(); cg.clip()
                let colors = [NSColor(red:0.145,green:0.388,blue:0.922,alpha:1).cgColor,NSColor(red:0.188,green:0.212,blue:0.620,alpha:1).cgColor] as CFArray
                if let gradient = CGGradient(colorsSpace:CGColorSpaceCreateDeviceRGB(),colors:colors,locations:[0,1]) { cg.drawLinearGradient(gradient,start:CGPoint(x:-32,y:32),end:CGPoint(x:32,y:-32),options:[.drawsBeforeStartLocation,.drawsAfterEndLocation]) }
                cg.restoreGState(); NSColor.white.setFill(); NSBezierPath(ovalIn:NSRect(x:-6.5,y:-6.5,width:13,height:13)).fill()
                NSGraphicsContext.restoreGraphicsState()
                guard let data = bitmap.representation(using:.png,properties:[:]) else { throw AppFailure("Cannot encode the app icon") }
                try data.write(to:directory.appendingPathComponent("icon_\(size)x\(size)"+(scale == 2 ? "@2x" : "")+".png"))
            }
        }
    }
}
