import SwiftUI

@main
struct SpideySenseApp: App {
    var body: some Scene {
        WindowGroup {
            ContentView()
                .preferredColorScheme(.light)   // light theme everywhere (also UIUserInterfaceStyle in Info.plist)
                .onAppear { UIApplication.shared.isIdleTimerDisabled = true }
        }
    }
}
