import Foundation

struct AppVersion: Comparable {
    let core: [Int]
    let prerelease: [String]

    init?(_ value: String) {
        guard value.utf8.count <= 128,
              value.range(of:#"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(-[0-9A-Za-z-]+(\.[0-9A-Za-z-]+)*)?(\+[0-9A-Za-z-]+(\.[0-9A-Za-z-]+)*)?$"#,options:.regularExpression) != nil else { return nil }
        let precedence = value.split(separator:"+",maxSplits:1)[0]
        let components = precedence.split(separator:"-",maxSplits:1)
        let numbers = components[0].split(separator:".").compactMap {Int($0)}
        guard numbers.count == 3, numbers.allSatisfy({$0 <= 65535}) else { return nil }
        let pre = components.count == 2 ? components[1].split(separator:".").map(String.init) : []
        guard !pre.contains(where:{$0.utf8.allSatisfy {48...57 ~= $0} && $0.count > 1 && $0.first == "0"}) else { return nil }
        core = numbers; prerelease = pre
    }

    static func < (lhs: Self, rhs: Self) -> Bool {
        if lhs.core != rhs.core { return lhs.core.lexicographicallyPrecedes(rhs.core) }
        if lhs.prerelease.isEmpty || rhs.prerelease.isEmpty { return !lhs.prerelease.isEmpty && rhs.prerelease.isEmpty }
        for (left,right) in zip(lhs.prerelease,rhs.prerelease) where left != right {
            let l = left.utf8.allSatisfy {48...57 ~= $0}, r = right.utf8.allSatisfy {48...57 ~= $0}
            if l && r { return left.count == right.count ? left < right : left.count < right.count }
            if l != r { return l }
            return left < right
        }
        return lhs.prerelease.count < rhs.prerelease.count
    }
}
