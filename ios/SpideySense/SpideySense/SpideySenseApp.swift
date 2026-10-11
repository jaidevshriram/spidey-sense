import SwiftUI

@main
struct SpideySenseApp: App {
    var body: some Scene {
        WindowGroup {
            ContentView()
                .onAppear { UIApplication.shared.isIdleTimerDisabled = true }
        }
    }
}
