import Foundation

// Display-only last-success values. Never used to authorize resets or renew
// the official quota freshness. Mirrors the Python window-merge policy.
struct MenuQuota {
    struct Sample {
        let window: QuotaWindow
        let observed: Date
        var json: Object { ["minutes":window.minutes,"used":window.used as Any? ?? NSNull(),"reset":window.reset?.timeIntervalSince1970 as Any? ?? NSNull(),"observed":observed.timeIntervalSince1970] }
    }
    private(set) var accountKey = ""
    private var samples: [Int:Sample] = [:]
    private(set) var retained = false
    init() {}
    init(saved: Object) {
        guard saved.integer("schema") == 1 else { return }
        accountKey = saved.string("account")
        for row in saved.objects("windows").prefix(2) {
            guard let minutes = row.integer("minutes"), [300,10080].contains(minutes),
                  let used = row.number("used"), used >= 0, let observed = parsedDate(row["observed"]) else { continue }
            samples[minutes] = Sample(window:QuotaWindow(id:String(minutes),minutes:minutes,used:used,reset:parsedDate(row["reset"])),observed:observed)
        }
    }
    mutating func receive(_ quota: QuotaState, now: Date = Date()) {
        let owner = quota.account.string("identityKey")
        guard quota.applicable, !owner.isEmpty else { self = MenuQuota(); return }
        if accountKey != owner { self = MenuQuota(); accountKey = owner }
        if quota.error == nil, let observed = quota.updated {
            for window in quota.windows where [300,10080].contains(window.minutes) && window.used != nil {
                samples[window.minutes] = Sample(window:window,observed:observed)
            }
        }
        retained = samples.values.contains { quota.error != nil || $0.observed != quota.updated || now < $0.observed || now.timeIntervalSince($0.observed) >= 900 }
    }
    var five: QuotaWindow? { samples[300]?.window }
    var week: QuotaWindow? { samples[10080]?.window }
    var updated: Date? { samples.values.map(\.observed).min() }
    var json: Object { ["schema":1,"account":accountKey,"windows":samples.keys.sorted().compactMap {samples[$0]?.json}] }
    var contentKey: String {
        identity(["account":accountKey,"retained":retained,"windows":samples.keys.sorted().compactMap { key -> Object? in
            guard var value = samples[key]?.json else { return nil }; value.removeValue(forKey:"observed"); return value
        }])
    }
}
