// Add this file separately to your Xcode target (requires App Intents).
// testApp.swift already defines AgentService + notification names.

import AppIntents

struct SnapAndSearchIntent: AppIntent {
    static var title: LocalizedStringResource = "Snap and Search"
    static var description = IntentDescription("Take a glasses photo and search with your voice command.")
    static var openAppWhenRun: Bool = true

    @Parameter(title: "Command", default: "Find this product and tell me the price")
    var command: String

    @Parameter(title: "Keep Chatting", default: true)
    var keepChatting: Bool

    func perform() async throws -> some IntentResult & ProvidesDialog {
        NotificationCenter.default.post(
            name: .glassesAgentRun,
            object: nil,
            userInfo: [
                GlassesAgentKeys.prompt: command,
                GlassesAgentKeys.converse: keepChatting,
            ]
        )
        return .result(dialog: "Opening Glasses Agent. Hold still for the photo.")
    }
}

struct AskGlassesAgentIntent: AppIntent {
    static var title: LocalizedStringResource = "Ask Glasses Agent"
    static var description = IntentDescription("Send a follow-up question to the browser agent.")
    static var openAppWhenRun: Bool = true

    @Parameter(title: "Question", requestValueDialog: IntentDialog("What should I ask your agent?"))
    var question: String

    func perform() async throws -> some IntentResult & ProvidesDialog {
        // ContentView handles the request via notification (updates UI + speaks).
        NotificationCenter.default.post(
            name: .glassesAgentFollowUp,
            object: nil,
            userInfo: [GlassesAgentKeys.prompt: question]
        )
        return .result(dialog: "Asking your agent…")
    }
}

struct GlassesAgentShortcuts: AppShortcutsProvider {
    static var appShortcuts: [AppShortcut] {
        AppShortcut(
            intent: SnapAndSearchIntent(),
            phrases: [
                "Snap and search with \(.applicationName)",
                "Search with my \(.applicationName) glasses",
                "\(.applicationName) look at this",
            ],
            shortTitle: "Snap & Search",
            systemImageName: "camera.viewfinder"
        )
        AppShortcut(
            intent: AskGlassesAgentIntent(),
            phrases: [
                "Ask \(.applicationName)",
                "Ask my \(.applicationName) agent",
            ],
            shortTitle: "Ask Agent",
            systemImageName: "sparkles"
        )
    }
}
