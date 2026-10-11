import Foundation
import Network
import Observation

/// Zero-config engine URL. `@AppStorage("serverURL")` holds "" for automatic, else a custom URL.
///
/// Resolution order: custom URL -> the engine's Bonjour service `_spidey._tcp` (first result, ~2 s;
/// host + port from the service) -> device: the Mac's LAN IP baked into Info.plist (`SpideyMacHost`)
/// at build time by the "Stamp Mac IP" build phase -> simulator: ws://127.0.0.1:8765.
/// After a disconnect the app browses again before reconnecting (the Mac's IP may have changed).
enum ServerConfig {
    static let defaultPort = 8765
    /// The placeholder older builds stored as a "default". Treated as automatic.
    static let legacyPlaceholder = "ws://192.168.1.10:8765"
    static let bonjourType = "_spidey._tcp"

    static var isSimulator: Bool {
        #if targetEnvironment(simulator)
        true
        #else
        false
        #endif
    }

    /// Mac LAN IP stamped into Info.plist at build time (127.0.0.1 if the Mac had none).
    static var buildHost: String? {
        let h = (Bundle.main.object(forInfoDictionaryKey: "SpideyMacHost") as? String)?
            .trimmingCharacters(in: .whitespaces)
        return (h?.isEmpty ?? true) ? nil : h
    }

    /// True if the stored value means "automatic".
    static func isAutomatic(_ stored: String) -> Bool {
        let s = stored.trimmingCharacters(in: .whitespaces)
        return s.isEmpty || s == legacyPlaceholder
    }

    /// One-time migration for this version: stale saved URLs from old installs must not win.
    /// The old placeholder, and 127.0.0.1/localhost on a real device (never right there), become automatic.
    static func migrateStoredURL(_ d: UserDefaults = .standard) {
        let key = "serverURLMigrated.v2"
        guard !d.bool(forKey: key) else { return }
        d.set(true, forKey: key)
        // Look only at the app's own persistent domain: launch arguments (-serverURL …) aren't stored.
        guard let stored = d.persistentDomain(forName: Bundle.main.bundleIdentifier ?? "")?["serverURL"] as? String
        else { return }
        let loopback = stored.contains("127.0.0.1") || stored.contains("localhost")
        if stored == legacyPlaceholder || (!isSimulator && loopback) {
            d.set("", forKey: "serverURL")
        }
    }
}

/// Where the effective URL came from, for Settings.
enum ServerSource: Equatable {
    case custom, simulator, bonjour(name: String, host: String), buildIP(String), fallback

    var label: String {
        switch self {
        case .custom: "custom"
        case .simulator: "auto: simulator"
        case .bonjour(let name, _): "auto: found Mac on network (\(name))"
        case .buildIP(let ip): "auto: Mac IP at build time \(ip)"
        case .fallback: "auto: no Mac IP at build time (127.0.0.1)"
        }
    }
}

/// Finds the engine via Bonjour (simulator and device); resolves the service to an IPv4 host:port.
@MainActor @Observable
final class ServerResolver {
    private(set) var bonjourName: String?      // e.g. "Spidey Sense on MacBook-Pro-2"
    private(set) var bonjourHost: String?      // e.g. 10.0.0.5
    private(set) var bonjourPort: Int?
    /// True once Bonjour answered or the ~2 s grace period passed.
    private(set) var settled = false

    private var browser: NWBrowser?
    private var resolving: NWConnection?

    func effective(stored: String) -> (url: String, source: ServerSource) {
        if !ServerConfig.isAutomatic(stored) { return (stored.trimmingCharacters(in: .whitespaces), .custom) }
        if let h = bonjourHost {
            return ("ws://\(h):\(bonjourPort ?? ServerConfig.defaultPort)", .bonjour(name: bonjourName ?? h, host: h))
        }
        if ServerConfig.isSimulator { return ("ws://127.0.0.1:\(ServerConfig.defaultPort)", .simulator) }
        if let ip = ServerConfig.buildHost, ip != "127.0.0.1" {
            return ("ws://\(ip):\(ServerConfig.defaultPort)", .buildIP(ip))
        }
        return ("ws://127.0.0.1:\(ServerConfig.defaultPort)", .fallback)
    }

    /// Starts browsing and returns after a Bonjour answer or `timeout`, whichever is first.
    func start(timeout: Double = 2) async {
        if browser == nil { startBrowser() }
        let deadline = Date().addingTimeInterval(timeout)
        while bonjourHost == nil && Date() < deadline {
            try? await Task.sleep(for: .milliseconds(100))
        }
        settled = true
    }

    /// Forget the last answer and browse again (after a disconnect: the Mac's IP may have changed).
    func rebrowse(timeout: Double = 2) async {
        browser?.cancel(); browser = nil
        resolving?.cancel(); resolving = nil
        settled = false   // ContentView ignores URL changes until this browse settles
        bonjourName = nil; bonjourHost = nil; bonjourPort = nil
        await start(timeout: timeout)
    }

    private func startBrowser() {
        let b = NWBrowser(for: .bonjour(type: ServerConfig.bonjourType, domain: nil), using: .tcp)
        b.browseResultsChangedHandler = { [weak self] results, _ in
            guard let first = results.first?.endpoint else { return }
            Task { @MainActor in
                guard let self, self.bonjourHost == nil, self.resolving == nil else { return }
                if case .service(let name, _, _, _) = first { self.bonjourName = name }
                self.resolve(first)
            }
        }
        b.stateUpdateHandler = { state in
            if case .failed = state { b.cancel() }   // e.g. Local Network permission denied: build IP is used
        }
        b.start(queue: .main)
        browser = b
    }

    /// Opens a throwaway TCP connection to the service to learn its IPv4 address and port.
    private func resolve(_ endpoint: NWEndpoint) {
        resolving?.cancel()
        let params = NWParameters.tcp
        (params.defaultProtocolStack.internetProtocol as? NWProtocolIP.Options)?.version = .v4
        let c = NWConnection(to: endpoint, using: params)
        c.stateUpdateHandler = { [weak self] state in
            switch state {
            case .ready:
                if case .hostPort(let host, let port)? = c.currentPath?.remoteEndpoint {
                    var h = "\(host)"
                    if let pct = h.firstIndex(of: "%") { h = String(h[..<pct]) }   // drop interface scope
                    let p = Int(port.rawValue)
                    Task { @MainActor in self?.bonjourHost = h; self?.bonjourPort = p; self?.resolving = nil }
                }
                c.cancel()
            case .failed, .waiting:
                c.cancel()
                Task { @MainActor in self?.resolving = nil }
            default: break
            }
        }
        c.start(queue: .main)
        resolving = c
    }
}
