import AppKit
import ImageIO
import UniformTypeIdentifiers

// Owned by MobileSync.queue. Images are separate bounded, expiring payloads;
// normal snapshot publication never decodes image pixels.
final class MobileImageCache {
    struct Prepared { var user: String; var final: String; var references: [MobileImageReference] }
    private struct Cached { let stamp: String; let content: String?; let image: MobileImageReference; let envelope: MobileEnvelope? }
    private let queue: DispatchQueue
    private var cached: [String:Cached] = [:]
    private(set) var envelopes: [String:MobileEnvelope] = [:]
    private var expiry: [String:Double] = [:]
    private var owners: [String:(request: String,expires: Double)] = [:]
    private var pinned: String?
    private var access: [String:Double] = [:]
    private var remote: [String:Data] = [:]
    private var localSources: [String:URL] = [:]
    private var remoteSources: [String:URL] = [:]
    private var sourceRequests: [String:String] = [:]
    private var pending = Set<String>()
    private var retry: [String:Date] = [:]
    private var generation = UUID()
    private lazy var downloader = MobileImageDownloader()
    var onReady: ((String) -> Void)?
    init(queue: DispatchQueue) { self.queue = queue }
    func stop() { generation = UUID(); downloader.cancel(); pending.removeAll(); cached.removeAll(); envelopes.removeAll(); expiry.removeAll(); owners.removeAll(); access.removeAll(); remote.removeAll(); localSources.removeAll(); remoteSources.removeAll(); sourceRequests.removeAll(); retry.removeAll() }
    func needsRefresh(_ request: String) -> Bool {
        for (key,entry) in cached where sourceRequests[key] == request && entry.image.expires > Date().timeIntervalSince1970 {
            if let file=localSources[key] {
                let stamp=FileStamp(file)
                if !entry.stamp.hasPrefix("\(file.path)|\(stamp.size)|\(stamp.modified?.timeIntervalSince1970 ?? 0)|\(stamp.inode)|") { return true }
            } else if remoteSources[key] != nil, entry.image.availability != "available", !pending.contains(key), pending.count < 2, Date() >= (retry[key] ?? .distantPast) { return true }
        }
        return false
    }
    func refreshPending() {
        let requests = Set(remoteSources.keys.compactMap { key -> String? in
            guard let entry=cached[key],entry.image.expires > Date().timeIntervalSince1970,entry.image.availability != "available",
                  !pending.contains(key),Date() >= (retry[key] ?? .distantPast) else { return nil }
            return sourceRequests[key]
        })
        for request in requests.prefix(max(0,2-pending.count)) { onReady?(request) }
    }
    func prune(now: Double = Date().timeIntervalSince1970) {
        for id in Array(envelopes.keys) where (expiry[id] ?? 0) <= now { envelopes.removeValue(forKey:id); expiry.removeValue(forKey:id); access.removeValue(forKey:id) }
        while envelopes.count > 128 || envelopes.values.reduce(0,{$0+$1.payload.utf8.count}) > 32*1_024*1_024 {
            guard let id = access.filter({$0.key != pinned}).min(by:{$0.value < $1.value})?.key else { break }
            envelopes.removeValue(forKey:id); access.removeValue(forKey:id); expiry.removeValue(forKey:id)
            cached = cached.filter {$0.value.image.id != id}
        }
        owners = owners.filter {$0.value.expires > now}
        if owners.count > 2048 { owners = Dictionary(uniqueKeysWithValues:owners.sorted {$0.value.expires > $1.value.expires}.prefix(2048).map {($0.key,$0.value)}) }
        cached = cached.filter {$0.value.image.expires > now}
        if cached.count > 512 { cached = Dictionary(uniqueKeysWithValues:cached.sorted {$0.value.image.expires > $1.value.image.expires}.prefix(512).map {($0.key,$0.value)}) }
        localSources = localSources.filter {cached[$0.key] != nil}; remoteSources = remoteSources.filter {cached[$0.key] != nil}; sourceRequests = sourceRequests.filter {cached[$0.key] != nil}
        retry = retry.filter {cached[$0.key] != nil || pending.contains($0.key)}
        if remote.values.reduce(0,{$0+$1.count}) > 16*1_024*1_024 { remote.removeAll() }
    }
    func prepare(_ raw: Object,requestID: String,started: Double,completed: Double?) -> Prepared {
        prune()
        var result = Prepared(user:raw.string("user"),final:raw.string("final"),references:[])
        var used = Set<String>()
        var canonical: [String:Cached] = [:]
        func retain(_ candidate: Cached,key: String) -> Cached {
            let content = candidate.content ?? candidate.image.id
            let chosen = canonical[content] ?? candidate
            canonical[content] = chosen
            let entry = Cached(stamp:candidate.stamp,content:candidate.content,image:chosen.image,envelope:chosen.envelope)
            cached[key] = entry
            if let envelope = entry.envelope {
                let id=entry.image.id
                envelopes[id]=envelope;expiry[id]=entry.image.expires;access[id]=Date().timeIntervalSince1970
                owners[id]=(requestID,entry.image.expires)
            }
            return entry
        }
        // Equal bytes within one message share the earliest original lifetime.
        // Including that lifetime in the ID also prevents a later source from
        // overwriting an immutable cloud image after a process restart.
        let attachments = raw.objects("attachments").prefix(32).sorted {
            let lhs=$0.number("created_at") ?? started,rhs=$1.number("created_at") ?? started
            return lhs == rhs ? $0.string("id") < $1.string("id") : lhs < rhs
        }
        for item in attachments where item.string("mime").hasPrefix("image/") {
            let placement = item.string("placement","user") == "final" ? "final" : "user"
            let path = item.string("path"), reference = item.string("reference")
            let source = path.isEmpty && ["https","http"].contains(URL(string:reference)?.scheme?.lowercased() ?? "") ? reference : path
            let name = MobileProtocol.prefix(item.string("name","图片"),bytes:240)
            let created = floor(item.number("created_at") ?? started)
            let expires = created+MobileProtocol.imageRetention
            let key = requestID+"|"+placement+"|"+(source.isEmpty ? item.string("id",name) : source)
            guard used.insert(key).inserted else { continue }
            sourceRequests[key] = requestID
            var image = MobileImageReference(id:MobileProtocol.hash(Data(key.utf8)),name:name,mime:item.string("mime","image/png"),placement:placement,width:0,height:0,expires:expires,availability:expires <= Date().timeIntervalSince1970 ? "expired" : "unavailable")
            if expires > Date().timeIntervalSince1970, !source.isEmpty {
                let url = source.hasPrefix("/") ? URL(fileURLWithPath:source) : URL(string:source)
                if let url {
                    let stamp: String
                    var bytes: Data?
                    if url.isFileURL {
                        let file = url.resolvingSymlinksInPath(), value = FileStamp(file)
                        localSources[key] = file
                        stamp = "\(file.path)|\(value.size)|\(value.modified?.timeIntervalSince1970 ?? 0)|\(value.inode)|\(expires)"
                        if let old = cached[key], old.stamp == stamp {
                            image = retain(old,key:key).image
                            append(image,item:item,to:&result); continue
                        }
                        if let attrs=try? FileManager.default.attributesOfItem(atPath:file.path),attrs[.type] as? FileAttributeType == .typeRegular,value.size > 0,value.size <= 12*1_024*1_024 { bytes=try? Data(contentsOf:file,options:.mappedIfSafe) }
                    } else {
                        stamp = source+"|\(expires)"
                        if MobileImageDownloader.allowed(url) { remoteSources[key] = url }
                        if let old=cached[key],old.stamp==stamp,old.image.availability=="available" {
                            image=retain(old,key:key).image
                            append(image,item:item,to:&result);continue
                        }
                        bytes=remote[source]
                        if bytes==nil,!pending.contains(key),Date() >= (retry[key] ?? .distantPast),pending.count < 2,MobileImageDownloader.allowed(url) {
                            pending.insert(key);let token=generation
                            downloader.fetch(url) { [weak self] data in
                                self?.queue.async {
                                    guard let self,self.generation==token else{return}
                                    self.pending.remove(key);self.retry[key]=Date().addingTimeInterval(data == nil ? 60 : 3600)
                                    if let data {self.remote[source]=data;self.cached.removeValue(forKey:key)}
                                    self.onReady?(requestID)
                                }
                            }
                        }
                    }
                    var envelope: MobileEnvelope?
                    var content: String?
                    if let bytes,let encoded=Self.raster(bytes) {
                        let key=requestID+"|"+placement+"|"+MobileProtocol.hash(encoded.data)
                        content=key
                        image.id=MobileProtocol.hash(Data((key+"|"+String(created)).utf8));image.mime=encoded.mime;image.width=encoded.width;image.height=encoded.height;image.availability="available"
                        let payload=MobileImagePayload(id:image.id,request:requestID,mime:image.mime,width:image.width,height:image.height,created:created,expires:expires,data:encoded.data.base64EncodedString())
                        if let data=try? MobileProtocol.encode(payload),data.count <= MobileProtocol.imageEnvelopeLimit {
                            envelope=envelopes[image.id] ?? MobileEnvelope(dataset:"image-"+image.id,revision:1,digest:MobileProtocol.hash(data),payload:String(decoding:data,as:UTF8.self))
                        } else {image.availability="unavailable"}
                    }
                    image=retain(Cached(stamp:stamp,content:content,image:image,envelope:envelope),key:key).image
                }
            }
            append(image,item:item,to:&result)
        }
        prune();return result
    }
    private func append(_ image: MobileImageReference,item: Object,to result: inout Prepared) {
        if !result.references.contains(where:{$0.id==image.id}) { result.references.append(image) }
        let target="codexio-image://"+image.id
        var text=image.placement=="user" ? result.user : result.final
        let sources=Set([item.string("reference"),item.string("path")]+(item["references"] as? [String] ?? [])).filter{!$0.isEmpty}
        text=RequestMedia.rewritingImages(in:text,sources:Set(sources),target:target,name:image.name)
        if !text.contains(target) { text += "\n\n![" + image.name.replacingOccurrences(of:"]",with:"") + "](" + target + ")" }
        if image.placement=="user" {result.user=text} else {result.final=text}
    }
    func deadline(_ id: String) -> Double? { expiry[id] }
    func message(_ id: String,part: Int,prepare: ((String) -> Void)? = nil) -> MobileMessage {
        pinned = id; defer { pinned = nil }
        prune()
        if envelopes[id] == nil, let owner = owners[id], owner.expires > Date().timeIntervalSince1970 { prepare?(owner.request) }
        guard let value=envelopes[id],let expires=expiry[id],expires > Date().timeIntervalSince1970 else {return MobileMessage(action:"image",error:"IMAGE_UNAVAILABLE",imageID:id)}
        let bytes=Data(value.payload.utf8),count=(bytes.count+MobileProtocol.imageChunkBytes-1)/MobileProtocol.imageChunkBytes
        guard part >= 0,part < count else{return MobileMessage(action:"image",error:"INVALID",imageID:id)}
        access[id]=Date().timeIntervalSince1970
        let start=part*MobileProtocol.imageChunkBytes,end=min(bytes.count,start+MobileProtocol.imageChunkBytes)
        return MobileMessage(action:"image",imageID:id,imagePart:part,imageManifest:MobileDetailManifest(id:id,revision:value.revision,digest:value.digest,bytes:bytes.count,parts:count),imageChunk:bytes.subdata(in:start..<end).base64EncodedString())
    }
    private static func raster(_ bytes: Data) -> (data:Data,mime:String,width:Int,height:Int)? {
        guard let source=CGImageSourceCreateWithData(bytes as CFData,[kCGImageSourceShouldCache:false] as CFDictionary) else { return vector(bytes) }
        guard let props=CGImageSourceCopyPropertiesAtIndex(source,0,nil) as? [CFString:Any],let w=props[kCGImagePropertyPixelWidth] as? NSNumber,let h=props[kCGImagePropertyPixelHeight] as? NSNumber,w.intValue>0,h.intValue>0,w.intValue<=16_384,h.intValue<=16_384,w.intValue*h.intValue<=64_000_000 else{return nil}
        for side in [2048,1536,1024] {
            guard let image=CGImageSourceCreateThumbnailAtIndex(source,0,[kCGImageSourceCreateThumbnailFromImageAlways:true,kCGImageSourceCreateThumbnailWithTransform:true,kCGImageSourceThumbnailMaxPixelSize:side,kCGImageSourceShouldCacheImmediately:true] as CFDictionary) else{return nil}
            let alpha = ![CGImageAlphaInfo.none,.noneSkipFirst,.noneSkipLast].contains(image.alphaInfo)
            let type=alpha ? UTType.png : UTType.jpeg
            for quality in alpha ? [0.82] : [0.82,0.65] {
                let data=NSMutableData()
                guard let destination=CGImageDestinationCreateWithData(data,type.identifier as CFString,1,nil) else{return nil}
                CGImageDestinationAddImage(destination,image,[kCGImageDestinationLossyCompressionQuality:quality] as CFDictionary)
                if CGImageDestinationFinalize(destination),data.length <= MobileProtocol.imageDataLimit {return(data as Data,alpha ? "image/png" : "image/jpeg",image.width,image.height)}
            }
        }
        return nil
    }
    private static func vector(_ bytes: Data) -> (data:Data,mime:String,width:Int,height:Int)? {
        guard bytes.count <= 4*1_024*1_024, let text=String(data:bytes,encoding:.utf8), text.localizedCaseInsensitiveContains("<svg"),
              text.range(of:"(?is)<!DOCTYPE|<!ENTITY|<script|(?:href|src)\\s*=\\s*[\"'](?:https?:|file:|data:)",options:.regularExpression)==nil,
              let image=NSImage(data:bytes),image.size.width>0,image.size.height>0,image.size.width.isFinite,image.size.height.isFinite else{return nil}
        let scale=min(1,2048/max(image.size.width,image.size.height)),width=max(1,Int(ceil(image.size.width*scale))),height=max(1,Int(ceil(image.size.height*scale)))
        guard let bitmap=NSBitmapImageRep(bitmapDataPlanes:nil,pixelsWide:width,pixelsHigh:height,bitsPerSample:8,samplesPerPixel:4,hasAlpha:true,isPlanar:false,colorSpaceName:.deviceRGB,bytesPerRow:0,bitsPerPixel:0),let context=NSGraphicsContext(bitmapImageRep:bitmap) else{return nil}
        NSGraphicsContext.saveGraphicsState();NSGraphicsContext.current=context;context.imageInterpolation = .high
        image.draw(in:NSRect(x:0,y:0,width:width,height:height),from:.zero,operation:.copy,fraction:1)
        NSGraphicsContext.restoreGraphicsState()
        guard let data=bitmap.representation(using:.png,properties:[:]),data.count<=MobileProtocol.imageDataLimit else{return nil}
        return(data,"image/png",width,height)
    }
}

// Public image URLs use an ephemeral unauthenticated session with bounded bytes.
private final class MobileImageDownloader: NSObject, URLSessionDataDelegate, @unchecked Sendable {
    private final class Transfer { var data=Data(); let done:(Data?)->Void; init(done:@escaping(Data?)->Void) { self.done=done } }
    private let lock=NSLock()
    private var transfers:[Int:Transfer]=[:]
    private lazy var session:URLSession = {let c=URLSessionConfiguration.ephemeral;c.timeoutIntervalForRequest=12;c.timeoutIntervalForResource=20;return URLSession(configuration:c,delegate:self,delegateQueue:nil)}()
    static func allowed(_ url:URL)->Bool {
        guard ["https","http"].contains(url.scheme?.lowercased() ?? ""),url.user==nil,url.password==nil,let host=url.host?.lowercased(),!host.isEmpty else{return false}
        return host != "localhost" && !host.hasSuffix(".local") && !host.hasPrefix("127.") && !host.hasPrefix("10.") && !host.hasPrefix("192.168.") && !host.hasPrefix("169.254.") && host != "::1" && !host.hasPrefix("[::1]") && !(host.hasPrefix("172.") && host.split(separator:".").dropFirst().first.flatMap({Int($0)}).map{(16...31).contains($0)} == true)
    }
    func fetch(_ url:URL,done:@escaping(Data?)->Void) {
        guard Self.allowed(url) else{done(nil);return};let task=session.dataTask(with:url)
        lock.lock();transfers[task.taskIdentifier]=Transfer(done:done);lock.unlock();task.resume()
    }
    func cancel() {session.getAllTasks{$0.forEach{$0.cancel()}}}
    func urlSession(_ session:URLSession,task:URLSessionTask,willPerformHTTPRedirection response:HTTPURLResponse,newRequest request:URLRequest,completionHandler:@escaping(URLRequest?)->Void) {completionHandler(request.url.flatMap{Self.allowed($0) ? request : nil})}
    func urlSession(_ session:URLSession,dataTask:URLSessionDataTask,didReceive response:URLResponse,completionHandler:@escaping(URLSession.ResponseDisposition)->Void) {
        guard let http=response as? HTTPURLResponse,(200..<300).contains(http.statusCode),response.expectedContentLength<=12*1_024*1_024,(response.mimeType ?? "").hasPrefix("image/") else{completionHandler(.cancel);return};completionHandler(.allow)
    }
    func urlSession(_ session:URLSession,dataTask:URLSessionDataTask,didReceive data:Data) {
        lock.lock();defer{lock.unlock()};guard let transfer=transfers[dataTask.taskIdentifier] else{return}
        guard transfer.data.count+data.count<=12*1_024*1_024 else{dataTask.cancel();return};transfer.data.append(data)
    }
    func urlSession(_ session:URLSession,task:URLSessionTask,didCompleteWithError error:Error?) {
        lock.lock();let transfer=transfers.removeValue(forKey:task.taskIdentifier);lock.unlock()
        transfer?.done(error==nil ? transfer?.data : nil)
    }
}
