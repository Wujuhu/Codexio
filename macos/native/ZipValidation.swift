import Foundation

enum ZipValidation {
    static func validate(_ url: URL) throws {
        let data = try Data(contentsOf:url,options:.mappedIfSafe)
        func uint(_ offset: Int,_ length: Int) throws -> UInt64 {
            guard offset >= 0, offset+length <= data.count else { throw AppFailure("Truncated ZIP directory") }
            return (0..<length).reduce(0) { $0 | UInt64(data[offset+$1]) << (8*$1) }
        }
        guard data.count >= 22, let ending = data.range(of:Data([0x50,0x4b,0x05,0x06]),options:.backwards,in:max(0,data.count-65557)..<data.count) else { throw AppFailure(L("更新文件不是有效 ZIP", "The update is not a valid ZIP")) }
        let end = ending.lowerBound
        guard try uint(end+4,2) == 0, try uint(end+6,2) == 0 else { throw AppFailure("Multi-volume ZIP is unsupported") }
        let count = Int(try uint(end+10,2)), size = Int(try uint(end+12,4)), start = Int(try uint(end+16,4))
        guard count > 0, count < 20000, start >= 0, start+size <= end else { throw AppFailure("Invalid ZIP directory bounds") }
        var cursor = start, total: UInt64 = 0, names = Set<String>()
        let root = URL(fileURLWithPath:"/Codexio.app")
        for _ in 0..<count {
            guard try uint(cursor,4) == 0x02014b50 else { throw AppFailure("Invalid ZIP entry") }
            let flags = try uint(cursor+8,2), unpacked = try uint(cursor+24,4), length = Int(try uint(cursor+28,2)), extra = Int(try uint(cursor+30,2)), comment = Int(try uint(cursor+32,2)), attributes = try uint(cursor+38,4)
            let nameStart = cursor+46, next = nameStart+length+extra+comment
            guard next <= start+size, flags & 1 == 0, length > 0, unpacked != 0xffffffff else { throw AppFailure("Unsupported ZIP entry") }
            guard let name = String(data:data.subdata(in:nameStart..<nameStart+length),encoding:.utf8), !name.contains("\\"), !name.contains("\0"), !name.contains("\n"), !name.contains("\r"), !name.hasPrefix("/"), name.split(separator:"/").allSatisfy({$0 != ".." && $0 != "."}), name == "Codexio.app/" || name.hasPrefix("Codexio.app/"), names.insert(name).inserted else { throw AppFailure(L("ZIP 包含不安全的文件路径", "The ZIP contains an unsafe path")) }
            total += unpacked
            guard total <= 1_073_741_824 else { throw AppFailure(L("ZIP 解压大小超过限制", "The ZIP exceeds the extraction size limit")) }
            if (attributes >> 16) & 0xf000 == 0xa000 {
                guard unpacked <= 4096 else { throw AppFailure("Invalid ZIP symbolic link") }
                let result = try execute("/usr/bin/unzip",["-p",url.path,name])
                guard result.code == 0, result.output.count <= 4096, let destination = String(data:result.output,encoding:.utf8), !destination.hasPrefix("/"), !destination.contains("\0"), !destination.contains("\\") else { throw AppFailure("Invalid ZIP symbolic link target") }
                let resolved = URL(fileURLWithPath:"/"+name).deletingLastPathComponent().appendingPathComponent(destination).standardizedFileURL
                guard resolved.path == root.path || resolved.path.hasPrefix(root.path+"/") else { throw AppFailure("ZIP symbolic link escapes the app") }
            }
            cursor = next
        }
        guard names.contains("Codexio.app/Contents/Info.plist"), names.contains("Codexio.app/Contents/MacOS/Codexio") else { throw AppFailure(L("ZIP 缺少完整的 Codexio.app", "The ZIP does not contain a complete Codexio.app")) }
    }
}
