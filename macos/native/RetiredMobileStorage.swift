import Foundation
import LocalAuthentication
import Security

// Remove only data owned by the retired sync service. Request bodies, media
// archives and source logs are independent of these caches.
func retireMobileStorage(_ paths: AppPaths) {
    guard !paths.mock else { return }
    let preferences = UserDefaults.standard
    for key in ["codexio.mobile.enabled", "codexio.mobile.name"] where preferences.object(forKey: key) != nil {
        preferences.removeObject(forKey: key)
    }

    let cacheNames = [
        "mobile-projection.json",
        "mobile-detail-revisions.json",
        "mobile-image-revisions.json",
        "mobile-cloud-recent.json",
        "mobile-cloud-live.json",
        "mobile-image-cache",
    ]
    let files = FileManager.default
    for name in cacheNames {
        try? files.removeItem(at: paths.data.appendingPathComponent(name))
    }

    let access = LAContext()
    access.interactionNotAllowed = true
    let credential: [String: Any] = [
        kSecClass as String: kSecClassGenericPassword,
        kSecAttrService as String: "com.wujuhu.codexio.mobile",
        kSecAttrAccount as String: "mac-host-v1",
        kSecUseAuthenticationContext as String: access,
    ]
    SecItemDelete(credential as CFDictionary)
}
