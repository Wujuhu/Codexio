import Foundation

struct PriceRow: Identifiable {
    var raw: Object
    var id: String {raw.string("model")+":"+raw.string("service_tier")+":"+String(raw.integer("threshold") ?? 0)}
    var model: String {raw.string("model")}
}

final class PricingCatalog {
    let directory: URL
    let reference = ModelPricing.shared
    var rows: [PriceRow] {reference.displayRows}
    var bases: [Object] {rows.map(\.raw)}
    var version: String {reference.version}
    var updated: Date? {reference.updated}
    var warning: String? {nil}
    init(_ directory: URL) {self.directory=directory}
    func price(_ raw: Object) -> Object {reference.price(raw)}
    func setOverride(model: String,rates: Object?) throws {throw AppFailure(L("官方价格目录为只读", "The official price catalog is read-only"))}
    func sync(force: Bool = false) throws {
        let destination=directory.appendingPathComponent("versions/\(version).json")
        if !FileManager.default.fileExists(atPath:destination.path) {
            try atomicJSON(["price_version":version,"rows":reference.prices,"rules":reference.rules,"billing_policies":reference.policies,"unit":"USD per million tokens"],to:destination)
        }
    }
}
