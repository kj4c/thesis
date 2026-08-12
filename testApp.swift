//
//  ContentView.swift
//  testApp
//
//  Created by Khye Jac Low on 17/6/2026.
//
import SwiftUI
import Combine
import CoreBluetooth
import MWDATCore
import MWDATCamera
import AVFoundation
import Speech

// MARK: - Backend + speech (shared with Siri intents in GlassesAgentIntent.swift)

struct BrainResponse: Codable {
    let status: String?
    let id: String?
    let result: String?
    let error: String?
}

extension Notification.Name {
    static let glassesAgentRun = Notification.Name("glassesAgentRun")
    static let glassesAgentFollowUp = Notification.Name("glassesAgentFollowUp")
}

enum GlassesAgentKeys {
    static let prompt = "prompt"
    static let converse = "startConversation"
}

@MainActor
final class AgentService: ObservableObject {
    static let shared = AgentService()

    // HOME — update when your Mac IP changes
    var host = "192.168.0.138"
    // EDUROM: "10.4.165.59"
    // HOTSPOT: "172.20.10.4"
    var port = "8765"

    @Published var status = ""
    @Published var isProcessing = false
    @Published var conversationActive = false

    private let synthesizer = AVSpeechSynthesizer()

    func runWithPhoto(image: UIImage, prompt: String) async -> String {
        isProcessing = true
        status = "Running agent…"
        defer { isProcessing = false }

        let reply = await postToBackend(prompt: prompt, image: image)
        status = reply
        speak(reply)
        return reply
    }

    func sendFollowUp(_ prompt: String) async -> String {
        isProcessing = true
        status = "Thinking…"
        defer { isProcessing = false }

        let reply = await postToBackend(prompt: prompt, image: nil)
        status = reply
        speak(reply)
        return reply
    }

    func runConversationLoop(onTranscribe: @escaping (URL) async -> String?) async {
        conversationActive = true
        defer { conversationActive = false }

        speak("Anything else? Say stop when you're done.")

        while conversationActive {
            guard let audioURL = await recordAudio(seconds: 5) else {
                status = "Mic unavailable."
                break
            }
            guard let text = await onTranscribe(audioURL), !text.isEmpty else { continue }

            let lower = text.lowercased()
            if lower.contains("stop") || lower.contains("goodbye") || lower.contains("that's all") {
                speak("Okay, talk later.")
                status = "Conversation ended."
                break
            }

            _ = await sendFollowUp(text)
        }
    }

    func stopConversation() {
        conversationActive = false
        synthesizer.stopSpeaking(at: .immediate)
    }

    func speak(_ text: String) {
        synthesizer.stopSpeaking(at: .immediate)
        let utterance = AVSpeechUtterance(string: text)
        utterance.rate = AVSpeechUtteranceDefaultSpeechRate
        synthesizer.speak(utterance)
    }

    func recordAudio(seconds: UInt64 = 4) async -> URL? {
        let session = AVAudioSession.sharedInstance()
        do {
            try session.setCategory(.playAndRecord, mode: .default, options: [.allowBluetooth, .defaultToSpeaker])
            try session.setActive(true)
        } catch {
            print("Audio session error: \(error)")
            return nil
        }

        let url = FileManager.default.temporaryDirectory.appendingPathComponent("voice_\(UUID().uuidString).m4a")
        let settings: [String: Any] = [
            AVFormatIDKey: Int(kAudioFormatMPEG4AAC),
            AVSampleRateKey: 16000,
            AVNumberOfChannelsKey: 1,
            AVEncoderAudioQualityKey: AVAudioQuality.medium.rawValue,
        ]

        do {
            let recorder = try AVAudioRecorder(url: url, settings: settings)
            recorder.record()
            try await Task.sleep(nanoseconds: seconds * 1_000_000_000)
            recorder.stop()
            return url
        } catch {
            print("Recorder error: \(error)")
            return nil
        }
    }

    private func postToBackend(prompt: String, image: UIImage?) async -> String {
        let url = URL(string: "http://\(host):\(port)/data")!
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.timeoutInterval = 180
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")

        var payload: [String: Any] = ["prompt": prompt]
        if let image, let data = image.jpegData(compressionQuality: 0.8) {
            payload["media_data"] = data.base64EncodedString()
            payload["media_type"] = "image"
            payload["filename"] = "pic.jpg"
        }

        do {
            let json = try JSONSerialization.data(withJSONObject: payload)
            let (data, response) = try await URLSession.shared.upload(for: request, from: json)

            if let http = response as? HTTPURLResponse, http.statusCode >= 400 {
                let body = String(data: data, encoding: .utf8) ?? ""
                print("HTTP \(http.statusCode): \(body)")
            }

            let brain = try JSONDecoder().decode(BrainResponse.self, from: data)
            if brain.status == "error" || brain.error != nil {
                return "Error: \(brain.error ?? "Unknown error")"
            }
            if let result = brain.result, !result.isEmpty { return result }
            return "Done."
        } catch {
            print("Network error: \(error)")
            return "Network error or timeout."
        }
    }
}

// MARK: - ContentView

struct ContentView: View {
    @ObservedObject private var agent = AgentService.shared

    // MARK: - SDK state
    @State private var registrationState: RegistrationState = .unavailable
    @State private var cameraPermission: PermissionStatus = .denied
    @State private var deviceIds: [DeviceIdentifier] = []
    @State private var errorMessage: String?

    // MARK: - UI state
    @State private var connectionStatus: String = "Disconnected"
    @State private var capturedImage: UIImage? = nil
    @State private var isProcessing: Bool = false

    @State private var photoSubscription: Any? = nil
    @State private var streamErrorSubscription: Any? = nil
    @State private var activeSession: DeviceSession? = nil
    @State private var activeStream: MWDATCamera.Stream? = nil

    @State private var bluetoothManager = CBCentralManager(delegate: nil, queue: nil)
    @State private var awaitingMetaAI = false
    @State private var callbackCount = 0
    @State private var showDebug = false
    @Environment(\.scenePhase) private var scenePhase

    // Siri queue — set when Hey Siri launches the app
    @State private var siriPendingPrompt: String?
    @State private var siriStartConversation = false

    private let wearables = Wearables.shared
    private let defaultSiriPrompt = "Find this product and tell me the price"

    private var isRegistered: Bool { registrationState == .registered }
    private var hasCamera: Bool { cameraPermission == .granted }
    private var busy: Bool { isProcessing || agent.isProcessing }
    private var displayStatus: String {
        if !agent.status.isEmpty && (agent.isProcessing || agent.conversationActive) {
            return agent.status
        }
        return connectionStatus
    }

    var body: some View {
        ScrollView {
            VStack(spacing: 20) {
                header
                    .padding(.top, 20)

                previewCard

                Text(displayStatus)
                    .font(.headline)
                    .foregroundColor(displayStatus.contains("❌") || displayStatus.contains("Error:") ? .red : .primary)
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.horizontal)

                if agent.conversationActive {
                    Text("Listening for follow-ups — say \"stop\" to end")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }

                statusAndError

                if showDebug {
                    debugPanel
                }

                Spacer(minLength: 30)

                buttonStack
                    .padding(.bottom, 40)
            }
            .padding(.horizontal, 20)
        }
        .background(Color(.systemBackground).ignoresSafeArea())
        .onOpenURL { url in
            handleMetaCallback(url: url)
        }
        .onChange(of: scenePhase) { _, phase in
            guard phase == .active else { return }
            if awaitingMetaAI {
                awaitingMetaAI = false
                Task { await refreshAfterMetaAI() }
            } else {
                deviceIds = wearables.devices
            }
        }
        .onReceive(NotificationCenter.default.publisher(for: .glassesAgentRun)) { note in
            let prompt = note.userInfo?[GlassesAgentKeys.prompt] as? String ?? defaultSiriPrompt
            let converse = note.userInfo?[GlassesAgentKeys.converse] as? Bool ?? true
            siriPendingPrompt = prompt
            siriStartConversation = converse
            connectionStatus = "Siri request — preparing…"
            if capturedImage != nil {
                Task { await runSiriCommand(prompt: prompt, converse: converse) }
            } else if hasCamera && !deviceIds.isEmpty {
                snapGlassesPhoto()
            } else {
                connectionStatus = "Connect glasses and allow camera first, then try Siri again."
            }
        }
        .onReceive(NotificationCenter.default.publisher(for: .glassesAgentFollowUp)) { note in
            guard let prompt = note.userInfo?[GlassesAgentKeys.prompt] as? String else { return }
            Task {
                let reply = await agent.sendFollowUp(prompt)
                connectionStatus = reply
            }
        }
        .task {
            registrationState = wearables.registrationState
            deviceIds = wearables.devices
            await checkCameraPermission()

            await withTaskGroup(of: Void.self) { group in
                group.addTask {
                    for await ids in wearables.devicesStream() {
                        await MainActor.run { self.deviceIds = ids }
                    }
                }
                group.addTask {
                    for await state in wearables.registrationStateStream() {
                        await MainActor.run { self.registrationState = state }
                    }
                }
            }
        }
    }

    // MARK: - Layout

    private var header: some View {
        HStack(alignment: .firstTextBaseline) {
            VStack(alignment: .leading, spacing: 2) {
                Text("POV Camera").font(.largeTitle.bold())
                Text("Ray-Ban Meta").font(.subheadline).foregroundStyle(.secondary)
            }
            Spacer()
            Button {
                withAnimation(.easeInOut(duration: 0.2)) { showDebug.toggle() }
            } label: {
                Image(systemName: showDebug ? "ladybug.fill" : "ladybug")
                    .font(.title2)
                    .foregroundStyle(showDebug ? Color.accentColor : Color.secondary)
            }
            .accessibilityLabel("Toggle debug info")
        }
    }

    @ViewBuilder
    private var previewCard: some View {
        ZStack {
            if let image = capturedImage {
                Image(uiImage: image)
                    .resizable()
                    .scaledToFit()
                    .frame(maxWidth: .infinity, maxHeight: 450)
                    .clipShape(RoundedRectangle(cornerRadius: 24))
            } else {
                RoundedRectangle(cornerRadius: 24)
                    .fill(Color(.secondarySystemBackground))
                    .frame(maxWidth: .infinity, minHeight: 250, maxHeight: 450)

                Image(systemName: busy ? "camera.aperture" : "camera.fill")
                    .font(.system(size: 52))
                    .foregroundStyle(.blue)
            }

            if busy {
                ZStack {
                    RoundedRectangle(cornerRadius: 24).fill(Color.black.opacity(0.3))
                    ProgressView().controlSize(.large).tint(.white)
                }
                .frame(maxWidth: .infinity, maxHeight: 450)
            }
        }
    }

    @ViewBuilder
    private var statusAndError: some View {
        if let errorMessage {
            Text(errorMessage)
                .font(.caption)
                .foregroundStyle(.red)
                .multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)
                .textSelection(.enabled)
                .padding(.horizontal, 4)
        }
    }

    private var debugPanel: some View {
        VStack(spacing: 4) {
            debugRow("Registration", registrationState.description)
            debugRow("Camera", hasCamera ? "granted" : "not granted")
            debugRow("Devices", "\(deviceIds.count)")
            debugRow("Meta AI callbacks", "\(callbackCount)")
            debugRow("Conversation", agent.conversationActive ? "active" : "off")
            debugRow("Backend", "\(agent.host):\(agent.port)")
        }
        .font(.caption.monospaced())
        .padding(12)
        .frame(maxWidth: .infinity)
        .background(RoundedRectangle(cornerRadius: 12).fill(Color(.secondarySystemBackground)))
        .transition(.opacity.combined(with: .move(edge: .bottom)))
    }

    private var buttonStack: some View {
        VStack(spacing: 12) {
            actionButton("Connect to Glasses", systemImage: "link", color: .blue,
                         disabled: busy, done: isRegistered) {
                connectToMetaAI()
            }
            actionButton("Allow Camera Access", systemImage: "camera.badge.ellipsis", color: .orange,
                         disabled: busy || !isRegistered, done: hasCamera) {
                requestCameraPermission()
            }
            let readyToSnap = hasCamera && !deviceIds.isEmpty
            actionButton("Snap POV Photo", systemImage: "camera.fill", color: .green,
                         disabled: busy || !readyToSnap, done: false) {
                snapGlassesPhoto()
            }

            if capturedImage != nil {
                actionButton("Run AI Command", systemImage: "sparkles", color: .purple,
                             disabled: busy, done: false) {
                    runAICommand(startConversation: true)
                }
            }

            if agent.conversationActive {
                actionButton("Stop Conversation", systemImage: "stop.circle.fill", color: .red,
                             disabled: false, done: false) {
                    agent.stopConversation()
                    connectionStatus = "Conversation stopped."
                }
            }
        }
    }

    @ViewBuilder
    private func actionButton(_ title: String, systemImage: String, color: Color,
                              disabled: Bool, done: Bool,
                              action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack(spacing: 8) {
                Image(systemName: done ? "checkmark.circle.fill" : systemImage)
                Text(title).fontWeight(.semibold)
            }
            .frame(maxWidth: .infinity)
            .padding(.vertical, 16)
            .foregroundStyle(.white)
            .background(disabled ? Color.gray.opacity(0.4) : color, in: RoundedRectangle(cornerRadius: 16))
        }
        .buttonStyle(.plain)
        .disabled(disabled)
    }

    @ViewBuilder
    private func debugRow(_ label: String, _ value: String) -> some View {
        HStack {
            Text(label).foregroundStyle(.secondary)
            Spacer()
            Text(value).foregroundStyle(.primary)
        }
    }

    // MARK: - Speech

    func transcribeAudio(url: URL) async -> String? {
        await withCheckedContinuation { continuation in
            var didResume = false

            SFSpeechRecognizer.requestAuthorization { status in
                guard status == .authorized,
                      let recognizer = SFSpeechRecognizer(locale: Locale(identifier: "en-US")),
                      recognizer.isAvailable else {
                    if !didResume { didResume = true; continuation.resume(returning: nil) }
                    return
                }

                let request = SFSpeechURLRecognitionRequest(url: url)
                request.shouldReportPartialResults = false

                recognizer.recognitionTask(with: request) { result, error in
                    if let error = error {
                        print("Speech error: \(error)")
                        if !didResume { didResume = true; continuation.resume(returning: nil) }
                        return
                    }

                    if let result = result, result.isFinal {
                        if !didResume {
                            didResume = true
                            continuation.resume(returning: result.bestTranscription.formattedString)
                        }
                    }
                }
            }
        }
    }

    // MARK: - Registration

    func connectToMetaAI() {
        isProcessing = true
        awaitingMetaAI = true
        errorMessage = nil
        connectionStatus = "Opening Meta AI..."

        Task {
            do {
                try await wearables.startRegistration()
            } catch RegistrationError.alreadyRegistered {
                await MainActor.run {
                    self.isProcessing = false
                    self.connectionStatus = "Already registered — allow camera next."
                }
            } catch {
                await MainActor.run {
                    self.isProcessing = false
                    self.connectionStatus = "Registration failed."
                    self.errorMessage = error.localizedDescription
                }
            }
        }
    }

    func handleMetaCallback(url: URL) {
        awaitingMetaAI = false
        callbackCount += 1
        print("📲 Meta AI callback URL received (#\(callbackCount)): \(url)")
        connectionStatus = "Verifying link..."

        let query = url.query ?? ""
        let isPermissionCallback = query.contains("metaWearablesAction=requestPermission")
        let permissionGranted = query.contains("permission_granted=true")

        Task {
            _ = try? await wearables.handleUrl(url)

            if isPermissionCallback {
                await MainActor.run {
                    self.cameraPermission = permissionGranted ? .granted : .denied
                    self.deviceIds = wearables.devices
                    self.isProcessing = false
                    self.connectionStatus = permissionGranted
                        ? "Camera granted — finding glasses..."
                        : "Camera not granted in Meta AI."
                }
                if permissionGranted {
                    await waitAndRefreshDevices()
                }
            } else {
                await refreshAfterMetaAI()
            }
        }
    }

    func refreshAfterMetaAI() async {
        if !hasCamera { await checkCameraPermission() }
        await MainActor.run {
            self.registrationState = wearables.registrationState
            self.deviceIds = wearables.devices
            self.isProcessing = false
            if self.hasCamera {
                self.connectionStatus = "Camera granted — tap Snap POV Photo."
            } else if self.isRegistered {
                self.connectionStatus = "Registered. Tap Allow Camera Access."
            } else {
                self.connectionStatus = "Back from Meta AI."
            }
        }
    }

    func checkCameraPermission() async {
        do {
            let status = try await wearables.checkPermissionStatus(.camera)
            await MainActor.run { self.cameraPermission = status }
        } catch {
            await MainActor.run { self.cameraPermission = .denied }
        }
    }

    func requestCameraPermission() {
        isProcessing = true
        awaitingMetaAI = true
        errorMessage = nil
        connectionStatus = "Requesting camera permission in Meta AI..."

        Task {
            _ = try? await wearables.requestPermission(.camera)
        }
    }

    // MARK: - Capture

    func snapGlassesPhoto() {
        isProcessing = true
        errorMessage = nil
        connectionStatus = "Looking for glasses..."

        photoSubscription = nil
        streamErrorSubscription = nil
        activeStream = nil
        if let oldSession = activeSession {
            oldSession.stop()
            activeSession = nil
        }

        Task {
            do {
                guard hasCamera else {
                    await MainActor.run {
                        self.isProcessing = false
                        self.connectionStatus = "Allow camera access first (step 2)."
                    }
                    return
                }

                if wearables.devices.isEmpty {
                    _ = await waitForDevice(timeout: 30)
                }

                guard let deviceId = wearables.devices.first else {
                    await MainActor.run {
                        self.isProcessing = false
                        self.connectionStatus = "No glasses found. Make sure they're powered on, "
                            + "worn, and connected in the Meta AI app."
                    }
                    return
                }

                await MainActor.run { self.connectionStatus = "Starting stream..." }

                let selector = SpecificDeviceSelector(device: deviceId)
                let session = try wearables.createSession(deviceSelector: selector)
                self.activeSession = session
                try session.start()

                await MainActor.run { self.connectionStatus = "Connecting to glasses camera..." }
                guard await waitForSessionStarted(session, timeout: 15) else {
                    await MainActor.run {
                        self.isProcessing = false
                        self.connectionStatus = "Glasses camera session didn't start "
                            + "(state: \(session.state.description)). Make sure they're worn and try again."
                    }
                    return
                }

                let config = StreamConfiguration()
                guard let stream = try session.addStream(config: config) else {
                    await MainActor.run {
                        self.isProcessing = false
                        self.connectionStatus = "Failed to allocate camera stream."
                    }
                    return
                }
                self.activeStream = stream

                photoSubscription = stream.photoDataPublisher.listen { photoData in
                    if let uiImage = UIImage(data: photoData.data) {
                        Task { @MainActor in
                            self.capturedImage = uiImage
                            self.isProcessing = false
                            session.stop()
                            self.activeStream = nil
                            self.activeSession = nil

                            // Siri: auto-run after snap
                            if let prompt = self.siriPendingPrompt {
                                let converse = self.siriStartConversation
                                self.siriPendingPrompt = nil
                                Task { await self.runSiriCommand(prompt: prompt, converse: converse) }
                            } else {
                                self.connectionStatus = "Photo success! Tap Run AI Command."
                            }
                        }
                    }
                }

                streamErrorSubscription = stream.errorPublisher.listen { streamError in
                    Task { @MainActor in
                        self.isProcessing = false
                        self.connectionStatus = "Stream error."
                        self.errorMessage = streamError.localizedDescription
                        print("Stream Error: \(streamError)")
                    }
                }

                await stream.start()
                await MainActor.run { self.connectionStatus = "Connecting camera feed..." }
                guard await waitForStreaming(stream, timeout: 15) else {
                    await MainActor.run {
                        self.isProcessing = false
                        self.connectionStatus = "Camera feed didn't start "
                            + "(state: \(stream.state)). Make sure the glasses are worn."
                    }
                    return
                }

                await MainActor.run { self.connectionStatus = "Waking up sensor..." }
                try? await Task.sleep(nanoseconds: 1_500_000_000)

                await MainActor.run { self.connectionStatus = "Capturing..." }
                let didRequest = stream.capturePhoto(format: .jpeg)
                if !didRequest {
                    await MainActor.run {
                        self.isProcessing = false
                        self.connectionStatus = "capturePhoto was rejected by the SDK."
                    }
                    return
                }

                Task {
                    try? await Task.sleep(nanoseconds: 12_000_000_000)
                    await MainActor.run {
                        if self.isProcessing && self.capturedImage == nil {
                            self.isProcessing = false
                            self.connectionStatus = "No photo received after 12s "
                                + "(stream state: \(stream.state))."
                        }
                    }
                }

            } catch {
                await MainActor.run {
                    self.isProcessing = false
                    self.connectionStatus = "Camera error."
                    self.errorMessage = error.localizedDescription
                    print("Pipeline Error Trace: \(error)")
                    self.activeSession?.stop()
                    self.activeSession = nil
                    self.activeStream = nil
                }
            }
        }
    }

    private func waitForDevice(timeout: TimeInterval) async -> Bool {
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            if !wearables.devices.isEmpty { return true }
            try? await Task.sleep(nanoseconds: 300_000_000)
        }
        return !wearables.devices.isEmpty
    }

    private func waitAndRefreshDevices() async {
        let found = await waitForDevice(timeout: 20)
        await MainActor.run {
            self.deviceIds = wearables.devices
            if found {
                self.connectionStatus = "Glasses ready — tap Snap POV Photo."
            } else {
                self.connectionStatus = "Camera linked! Fully close and reopen the app "
                    + "once to finish connecting the glasses (first-time only)."
            }
        }
    }

    private func waitForSessionStarted(_ session: DeviceSession, timeout: TimeInterval) async -> Bool {
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            switch session.state {
            case .started: return true
            case .stopped, .stopping: return false
            default: break
            }
            try? await Task.sleep(nanoseconds: 200_000_000)
        }
        return session.state == .started
    }

    private func waitForStreaming(_ stream: MWDATCamera.Stream, timeout: TimeInterval) async -> Bool {
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            switch stream.state {
            case .streaming: return true
            case .stopped, .stopping: return false
            default: break
            }
            try? await Task.sleep(nanoseconds: 200_000_000)
        }
        return stream.state == .streaming
    }

    // MARK: - Agent + Siri

    func runAICommand(startConversation: Bool = true) {
        guard let image = capturedImage else { return }
        Task {
            connectionStatus = "🎙️ Listening for 4 secs…"
            if let audioURL = await agent.recordAudio(seconds: 4) {
                connectionStatus = "✍️ Transcribing…"
                let promptText = await transcribeAudio(url: audioURL) ?? "Search for items in image"
                connectionStatus = "🚀 Running agent…"
                let reply = await agent.runWithPhoto(image: image, prompt: promptText)
                connectionStatus = reply
                if startConversation {
                    await agent.runConversationLoop(onTranscribe: transcribeAudio)
                    connectionStatus = agent.status
                }
            } else {
                connectionStatus = "❌ Mic failed."
            }
        }
    }

    func runSiriCommand(prompt: String, converse: Bool) async {
        guard let image = capturedImage else { return }

        connectionStatus = "🎙️ Listening…"
        let voicePrompt: String
        if prompt.isEmpty || prompt == defaultSiriPrompt {
            if let audio = await agent.recordAudio(seconds: 4),
               let text = await transcribeAudio(url: audio), !text.isEmpty {
                voicePrompt = text
            } else {
                voicePrompt = prompt.isEmpty ? defaultSiriPrompt : prompt
            }
        } else {
            voicePrompt = prompt
        }

        connectionStatus = "🚀 Running agent…"
        let reply = await agent.runWithPhoto(image: image, prompt: voicePrompt)
        connectionStatus = reply

        if converse {
            await agent.runConversationLoop(onTranscribe: transcribeAudio)
            connectionStatus = agent.status
        }
    }
}

#Preview {
    ContentView()
}
