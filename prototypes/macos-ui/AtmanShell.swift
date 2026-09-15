import Cocoa
import WebKit

/// T-1027 throwaway native chrome: a real window + dock icon around `atm ui`.
/// It does not reimplement Objective / Work / Team. Those screens stay in the
/// existing local app so the honesty rules have one home.
final class App: NSObject, NSApplicationDelegate, WKNavigationDelegate {
    var window: NSWindow!
    var web: WKWebView!

    func applicationDidFinishLaunching(_ notification: Notification) {
        let url = URL(string: ProcessInfo.processInfo.environment["ATMAN_UI_URL"]
                      ?? CommandLine.arguments.dropFirst().first
                      ?? "http://127.0.0.1:8765")!
        let screen = NSScreen.main?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1280, height: 800)
        let frame = NSRect(x: screen.midX - 640, y: screen.midY - 400, width: 1280, height: 800)
        window = NSWindow(
            contentRect: frame,
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered,
            defer: false
        )
        window.title = "Atman"
        window.makeKeyAndOrderFront(nil)

        web = WKWebView(frame: window.contentView!.bounds)
        web.autoresizingMask = [.width, .height]
        web.navigationDelegate = self
        window.contentView?.addSubview(web)
        web.load(URLRequest(url: url))

        NSApp.activate(ignoringOtherApps: true)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
}

let app = NSApplication.shared
app.setActivationPolicy(.regular)
let delegate = App()
app.delegate = delegate
app.run()
