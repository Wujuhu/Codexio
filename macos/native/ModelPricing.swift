import Foundation

private struct StructuredPriceModel {
    let model: String
    let prefix: NSRegularExpression
}
private struct StructuredPriceFamily {
    let anchor: NSRegularExpression
    let shape: NSRegularExpression
    let reject: NSRegularExpression?
    let generationMajor: String
    let variantTokens: Set<String>
    let variantPrefixes: [String]
    let models: [StructuredPriceModel]
}

/// Immutable official prices, approved aliases and rules used by both ledgers.
/// Original request/response IDs stay intact; price_rates records the match.
final class ModelPricing {
    static let shared = ModelPricing()
    let rules: Object
    let descriptors: [Object]
    let prices: [Object]
    let policies: Object
    let version: String
    let updated: Date?
    let catalogRows: [PriceRow]
    let displayRows: [PriceRow]
    private let definitions: [String:Object]
    private let byModel: [String:[Object]]
    private var identifiers: [String:String] = [:]
    private var ambiguous = Set<String>()
    private let modelFamilies: [StructuredPriceFamily]
    private let ignoredModelTokens: Set<String>
    private let modelNormalization: [(expression:NSRegularExpression,replacement:String)]
    private static let modelTokens = try! NSRegularExpression(pattern:#"[a-z]+|[0-9]+(?:\.[0-9]+)*"#)
    private let holidays: Set<String>
    private let calendarYears: Set<Int>
    private let china: Calendar
    private let changes: [String:[(Date,Double)]]

    private init() {
        func resource(_ name: String) -> Object {Bundle.main.url(forResource:name,withExtension:"json").map(readObject) ?? [:]}
        let gpt=resource("pricing_seed"), claude=resource("claude_pricing_seed"), approved=resource("model_pricing_rules")
        let descriptions=approved.objects("models"), allPrices=gpt.objects("rows")+claude.objects("rows")+approved.objects("other_rows"), policyObject=gpt.object("billing_policies")
        rules=approved;descriptors=descriptions;prices=allPrices;policies=policyObject
        let matching=approved.object("model_matching")
        ignoredModelTokens=Set(matching["ignored_tokens"] as? [String] ?? [])
        func expression(_ pattern: String) -> NSRegularExpression? {pattern.isEmpty ? nil:try! NSRegularExpression(pattern:pattern)}
        modelNormalization=matching.objects("normalization").compactMap {row in
            guard let pattern=expression(row.string("pattern")) else {return nil}
            return (pattern,row.string("replacement"))
        }
        modelFamilies=matching.objects("families").compactMap {family in
            guard let anchor=expression(family.string("anchor")),let shape=expression(family.string("shape")) else {return nil}
            let models=family.objects("models").compactMap {model -> StructuredPriceModel? in
                guard let prefix=expression(model.string("prefix")) else {return nil}
                return StructuredPriceModel(model:model.string("model"),prefix:prefix)
            }
            return StructuredPriceFamily(anchor:anchor,shape:shape,reject:expression(family.string("reject")),generationMajor:family.string("generation_major"),variantTokens:Set(family["variant_tokens"] as? [String] ?? []),variantPrefixes:family["variant_prefixes"] as? [String] ?? [],models:models)
        }
        definitions=Dictionary(descriptions.map {($0.string("model"),$0)},uniquingKeysWith:{$1})
        byModel=Dictionary(grouping:allPrices,by:{$0.string("model")})
        holidays=Set(approved.object("calendar")["holidays"] as? [String] ?? [])
        calendarYears=Set(approved.object("calendar")["years"] as? [Int] ?? [])
        var calendar=Calendar(identifier:.gregorian);calendar.timeZone=TimeZone(secondsFromGMT:8*3600)!;china=calendar
        changes=Dictionary(uniqueKeysWithValues:allPrices.filter {!$0.objects("price_changes").isEmpty}.map { row in
            (row.string("model"),row.objects("price_changes").compactMap { change -> (Date,Double)? in
                guard let since=parsedDate(change["since"]),let multiple=change.number("multiplier") else {return nil}
                return (since,multiple)
            })
        })
        var calculationRules=approved;for key in ["verified_at","client_models","client_model_sources"] {calculationRules.removeValue(forKey:key)}
        calculationRules["models"]=descriptions.map {raw -> Object in
            var row=raw;for key in ["tooltip","tooltip_en","display_name"] {row.removeValue(forKey:key)};return row
        }
        var calendarContent=approved.object("calendar");calendarContent.removeValue(forKey:"source");calculationRules["calendar"]=calendarContent
        func content(_ raw: Object) -> Object {var row=raw;for key in ["source","sources","updated_at","verified_at","locked"] {row.removeValue(forKey:key)};return row}
        let policyContent=policyObject.mapValues {raw -> Any in
            guard var row=raw as? Object else {return raw};for key in ["sources","verified_at"] {row.removeValue(forKey:key)};return row
        }
        calculationRules["other_rows"]=approved.objects("other_rows").map(content)
        version=identity(["rules":calculationRules,"prices":allPrices.map(content),"policies":policyContent] as Object)
        updated=parsedDate(approved["verified_at"])
        let catalog=descriptions.compactMap {descriptor -> PriceRow? in
            let model=descriptor.string("model")
            guard var row=allPrices.first(where:{$0.string("model")==model && ($0.integer("threshold") ?? 0)==0}) else {return nil}
            for (key,value) in descriptor {row[key]=value}
            if descriptor.string("group")=="other" {row.removeValue(forKey:"cache_write_1h");row["cache_write_ttl"]="5m"}
            row["service_tier"]="default";row["threshold"]=0;return PriceRow(raw:row)
        }
        catalogRows=catalog
        let clientModels=approved.object("client_models")
        displayRows=catalog.filter {row in
            guard let models=clientModels[row.raw.string("group")] as? [String] else {return true}
            return models.contains(row.model)
        }
        for row in allPrices+descriptions {
            let model=row.string("model")
            for name in [model,row.string("display_name")]+(row["aliases"] as? [String] ?? []) where !name.isEmpty {
                let key=Self.key(name)
                guard !ambiguous.contains(key) else {continue}
                if let prior=identifiers[key],prior != model {identifiers.removeValue(forKey:key);ambiguous.insert(key)}
                else {identifiers[key]=model}
            }
        }
    }
    private static func key(_ value: String) -> String {
        value.folding(options:[.caseInsensitive,.widthInsensitive],locale:Locale(identifier:"en_US_POSIX")).unicodeScalars.filter {($0.value>=97 && $0.value<=122) || ($0.value>=48 && $0.value<=57)}.map(String.init).joined()
    }
    private func decoratedModels(_ value: String) -> (seen:Bool,uncertain:Bool,models:Set<String>) {
        var seen=false,uncertain=false,found=Set<String>()
        for segment in value.components(separatedBy:CharacterSet(charactersIn:"/·|:@,;")) {
            var folded=segment.folding(options:.widthInsensitive,locale:Locale(identifier:"en_US_POSIX"))
            for (pattern,replacement) in modelNormalization {
                folded=pattern.stringByReplacingMatches(in:folded,range:NSRange(location:0,length:(folded as NSString).length),withTemplate:replacement)
            }
            folded=folded.lowercased()
            let text=Self.modelTokens.matches(in:folded,range:NSRange(location:0,length:(folded as NSString).length)).map {(folded as NSString).substring(with:$0.range)}.filter {!ignoredModelTokens.contains($0)}.joined(separator:" ")
            let ns=text as NSString,range=NSRange(location:0,length:ns.length)
            var anchors: [(range:NSRange,family:StructuredPriceFamily)] = []
            for family in modelFamilies {for match in family.anchor.matches(in:text,range:range) {anchors.append((match.range,family))}}
            anchors.sort {$0.range.location<$1.range.location}
            for (index,anchor) in anchors.enumerated() {
                let start=NSMaxRange(anchor.range),end=index+1<anchors.count ? anchors[index+1].range.location:ns.length
                let body=ns.substring(with:NSRange(location:start,length:max(0,end-start))).trimmingCharacters(in:.whitespaces)
                let bodyRange=NSRange(location:0,length:(body as NSString).length),family=anchor.family
                guard family.shape.firstMatch(in:body,range:bodyRange) != nil else {continue}
                seen=true
                let tokens=body.split(separator:" ").map(String.init)
                let leadingVersion=tokens.first=="v" ? tokens.dropFirst().first:tokens.first
                let badVersion = !family.generationMajor.isEmpty && leadingVersion?.first?.isNumber != true && tokens.indices.dropLast().contains {i in
                    tokens[i]=="v" && tokens[i+1].first?.isNumber==true && tokens[i+1].split(separator:".").first.map(String.init) != family.generationMajor
                }
                if badVersion || family.reject?.firstMatch(in:body,range:bodyRange) != nil {uncertain=true;continue}
                var models=Set<String>()
                for rule in family.models {
                    guard let match=rule.prefix.firstMatch(in:body,range:bodyRange),match.range.location==0 else {continue}
                    let remainder=(body as NSString).substring(from:NSMaxRange(match.range)).split(separator:" ").map(String.init)
                    if family.variantTokens.isDisjoint(with:remainder) && !remainder.contains(where:{token in family.variantPrefixes.contains(where:{token.hasPrefix($0)})}) {models.insert(rule.model)}
                }
                if models.count==1 {found.formUnion(models)} else {uncertain=true}
            }
        }
        return (seen,uncertain,found)
    }
    func model(_ value: String) -> String? {
        guard !value.isEmpty,value.count<=512 else {return nil}
        let text=value.replacingOccurrences(of:"[1m]",with:"",options:.caseInsensitive)
        var found=Set<String>()
        for candidate in [text]+text.components(separatedBy:CharacterSet(charactersIn:"/·|:")) {
            if let exact=identifiers[Self.key(candidate)] {found.insert(exact);continue}
            let effort=candidate.replacingOccurrences(of:#"[-_\s(\[]+(?:minimal|low|medium|high|xhigh|max)[)\]]?\s*$"#,with:"",options:[.regularExpression,.caseInsensitive])
            for variant in [candidate,effort] {
                let dated=variant.replacingOccurrences(of:#"[-_](?:\d{8}|\d{4}-\d{2}-\d{2})$"#,with:"",options:.regularExpression)
                for name in [variant,dated] {if let match=identifiers[Self.key(name)] {found.insert(match)}}
            }
        }
        let decorated=decoratedModels(text)
        if decorated.seen {
            guard !decorated.uncertain else {return nil}
            found.formUnion(decorated.models)
        }
        return found.count==1 ? found.first : nil
    }
    func group(_ model: String) -> String {definitions[model]?.string("group") ?? ""}
    func resolve(_ raw: Object,returnedOnly: Bool=false) -> (String,Bool)? {
        let returned=[raw.string("upstream_model"),raw.string("returned_model")].filter {!$0.isEmpty}
        let models=Set(returned.compactMap(model))
        if models.count==1 {return (models.first!,false)}
        if models.count>1 || returnedOnly {return nil}
        guard let requested=model(raw.string("model")) else {return nil}
        return (requested,!returned.isEmpty)
    }
    private func rates(_ model: String,input: Int,raw: Object) -> (Object,Bool)? {
        let family=group(model),descriptor=definitions[model] ?? [:]
        guard let base=byModel[model]?.filter({($0.integer("threshold") ?? 0)==0 || input>($0.integer("threshold") ?? 0)}).max(by:{($0.integer("threshold") ?? 0)<($1.integer("threshold") ?? 0)}) else {return nil}
        var rates=base,estimated=false
        // v0.2.10/Python and Windows price legacy logs with no speed field at
        // the Standard reference rate. Keep the original field absent so the
        // request UI still displays its unknown speed rather than inventing it.
        let recordedTier=raw.string("service_tier").trimmingCharacters(in:.whitespacesAndNewlines).lowercased()
        let tier=recordedTier.isEmpty || recordedTier=="auto" ? "default":normalizedTier(recordedTier)
        if family=="gpt" {
            let identity=BillingIdentity.resolve(raw),mode=identity.mode=="unknown" ? "subscription" : identity.mode
            let policy=policies.object(model).object(mode)
            let threshold=policy.isEmpty ? descriptor.integer("long_context_threshold") ?? 0 : policy.integer("long_context_threshold") ?? 0
            var speed=1.0
            if !policy.isEmpty {guard let value=policy.object("speeds").number(tier) else {return nil};speed=value}
            else if tier=="priority" {guard let value=descriptor.number("fast_multiplier") else {return nil};speed=value}
            else if tier=="ultrafast" {guard let value=descriptor.number("ultrafast_multiplier") else {return nil};speed=value}
            else if tier != "default" {return nil}
            let long=threshold>0 && input>threshold
            let inMultiple=speed*(long ? 2:1),outMultiple=speed*(long ? 1.5:1)
            let writeBasis=policy.isEmpty ? "legacy" : policy.string("cache_write_basis","legacy")
            for field in ["input","cache_read","cache_write","output"] {
                let source=field=="cache_write" && (writeBasis=="input" || (writeBasis=="legacy" && (tier != "default" || long))) ? "input" : field
                rates[field]=base.number(source).map {$0*(field=="output" ? outMultiple:inMultiple)} as Any?
            }
            rates["threshold"]=long ? threshold:0;rates["long_context_threshold"]=threshold
            rates["speed_multiplier"]=speed;rates["input_multiplier"]=inMultiple;rates["output_multiplier"]=outMultiple
            rates["pricing_mode"]=mode;rates["cache_write_basis"]=writeBasis
            rates["base_rates"]=Dictionary(uniqueKeysWithValues:["input","cache_read","cache_write","output"].map {($0,base[$0] ?? NSNull())})
            rates["multipliers"]=["input":inMultiple,"cache_read":inMultiple,"cache_write":inMultiple,"output":outMultiple]
            if identity.mode=="unknown" {rates["assumed_billing_source"]="subscription"}
            else if identity.estimated {estimated=true;rates["pricing_identity_estimated"]=true}
        } else if family=="claude" {
            guard ["default","priority"].contains(tier) else {return nil}
            if tier=="priority" {
                guard let speed=base.number("fast_multiplier") else {return nil}
                for field in ["input","cache_read","cache_write","cache_write_1h","output"] {rates[field]=base.number(field).map {$0*speed} as Any?}
                rates["pricing_mode"]="credits"
            }
            estimated=raw.string("service_tier").isEmpty
        } else {
            var multiple=1.0
            if let offPeak=base.number("off_peak_multiplier") {
                guard let date=parsedDate(raw["timestamp"]) else {return nil}
                let parts=china.dateComponents([.year,.month,.day,.weekday,.hour,.minute],from:date)
                let day=String(format:"%04d-%02d-%02d",parts.year!,parts.month!,parts.day!)
                let minute=parts.hour!*60+parts.minute!,weekday=parts.weekday!
                let peak=weekday>1 && weekday<7 && ((540..<720).contains(minute) || (840..<1080).contains(minute)) && !holidays.contains(day)
                multiple=peak ? 1:offPeak;estimated=peak && !calendarYears.contains(parts.year!)
                rates["time_multiplier"]=multiple
            }
            if let scheduled=changes[model],!scheduled.isEmpty {
                guard let date=parsedDate(raw["timestamp"]) else {return nil}
                if let change=scheduled.last(where:{$0.0<=date}) {multiple *= change.1}
            }
            for field in ["input","cache_read","cache_write","cache_write_1h","output"] {rates[field]=base.number(field).map {$0*multiple} as Any?}
            rates.removeValue(forKey:"cache_write_1h");rates["cache_write_ttl"]=rules.string("other_cache_write_ttl","5m")
            rates["pricing_basis"]="official_api_reference"
        }
        rates["model"]=model;rates["service_tier"]=family=="other" ? "default":tier
        return (rates,estimated)
    }
    func price(_ input: Object,resolved: (String,Bool)?=nil,returnedOnly: Bool=false) -> Object {
        var raw=input
        raw["cost_usd"]=NSNull();raw["pricing_status"]="unpriced";raw["price_version"]=version
        for key in ["price_rates","pricing_mode","pricing_identity_estimated","assumed_billing_source"] {raw.removeValue(forKey:key)}
        guard let resolved=resolved ?? resolve(input,returnedOnly:returnedOnly),!input.flag("usage_conflict"),!input.string("quality").hasPrefix("invalid") else {return raw}
        let isClaude=input.string("client_kind")=="claude_code"
        guard let total=input.integer("input_tokens"),let output=input.integer("output_tokens"),total>=0,output>=0 else {return raw}
        let read=input.integer("cached_input_tokens") ?? (input["cached_input_tokens"]==nil ? 0:-1)
        let write=input.integer("cache_write_input_tokens") ?? (input["cache_write_input_tokens"]==nil ? 0:-1)
        guard read>=0,write>=0,read<=Int.max-write else {return raw}
        let ordinary=isClaude ? input.integer("raw_input_tokens") ?? -1:total-read-write
        guard ordinary>=0,ordinary<=Int.max-read-write else {return raw}
        let contextInput=isClaude ? ordinary+read+write:total
        let reasoning=input.integer("reasoning_output_tokens") ?? (input["reasoning_output_tokens"]==nil ? 0:-1)
        guard reasoning>=0,reasoning<=output else {return raw}
        if input["total_tokens"] != nil && !(input["total_tokens"] is NSNull) {
            guard let reported=input.integer("total_tokens"),contextInput<=Int.max-output,reported==contextInput+output else {return raw}
        }
        guard let selected=rates(resolved.0,input:contextInput,raw:input) else {return raw}
        var estimated=selected.1 || resolved.1
        var five=write,hour=0
        if group(resolved.0)=="claude",write>0,selected.0.number("cache_write_1h") != nil {
            let knownFive=input.integer("cache_write_5m_tokens"),knownHour=input.integer("cache_write_1h_tokens")
            if input["cache_write_5m_tokens"] is NSNumber && knownFive == nil || input["cache_write_1h_tokens"] is NSNumber && knownHour == nil {return raw}
            if let knownFive,let knownHour {five=knownFive;hour=knownHour}
            else if let knownFive {five=knownFive;hour=write-knownFive}
            else if let knownHour {five=write-knownHour;hour=knownHour}
            else {estimated=true}
            guard five>=0,hour>=0,five<=Int.max-hour,five+hour==write else {return raw}
        }
        let fields=["input","cache_read","cache_write","cache_write_1h","output"],counts=[ordinary,read,five,hour,output]
        guard zip(fields,counts).allSatisfy({$0.1==0 || selected.0.number($0.0) != nil}) else {return raw}
        let cost=zip(fields,counts).reduce(0.0) {$0+Double($1.1)*(selected.0.number($1.0) ?? 0)/1e6}
        guard cost.isFinite,cost>=0 else {return raw}
        let family=group(resolved.0),provider=input.string("provider").lowercased()
        if family=="gpt" && !["openai","codexio-upstream"].contains(provider) {estimated=true}
        if family=="other" || input.string("quality").hasPrefix("cumulative") {estimated=true}
        raw["cost_usd"]=cost;raw["price_rates"]=selected.0;raw["pricing_status"]=estimated ? "estimated":"priced"
        raw["pricing_basis"]=family=="gpt" ? "standard_api_x_codex":"official_api_reference"
        for key in ["pricing_mode","pricing_identity_estimated","assumed_billing_source"] {if let value=selected.0[key] {raw[key]=value}}
        return raw
    }
}
