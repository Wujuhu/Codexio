import Foundation

// Presentation only: PricingCatalog keeps its established rule order and hash.
enum PricingDisplayOrder {
    static func sorted(_ rows: [PriceRow]) -> [PriceRow] {
        let groups = Dictionary(grouping:rows,by: \.model)
        return groups.keys.sorted(by:before).flatMap { groups[$0] ?? [] }
    }
    private static func before(_ lhs: String,_ rhs: String) -> Bool {
        let aName = lhs.lowercased().replacingOccurrences(of:"openai/",with:"")
        let bName = rhs.lowercased().replacingOccurrences(of:"openai/",with:"")
        if aName == "gpt-6-astra" && bName != "gpt-6-astra" { return true }
        if bName == "gpt-6-astra" && aName != "gpt-6-astra" { return false }
        let a = key(lhs), b = key(rhs)
        for index in 0..<max(a.0.count,b.0.count) {
            let x = index < a.0.count ? a.0[index] : 0, y = index < b.0.count ? b.0[index] : 0
            if x != y { return x > y }
        }
        if a.1 != b.1 { return a.1 < b.1 }
        return lhs.localizedStandardCompare(rhs) == .orderedAscending
    }
    private static func key(_ model: String) -> ([Int],Int) {
        let normalized = model.lowercased().replacingOccurrences(of:"openai/",with:"")
        guard normalized.hasPrefix("gpt-") else { return ([],5) }
        let suffix = normalized.dropFirst(4), numeric = suffix.prefix { $0.isNumber || $0 == "." }
        let family = numeric.split(separator:".").compactMap {Int($0)}
        let label = suffix.dropFirst(numeric.count).split(separator:"-").first.map(String.init) ?? ""
        return (family,["astra":0,"sol":1,"terra":2,"luna":3][label] ?? 4)
    }
}
