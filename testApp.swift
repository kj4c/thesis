//
//  ContentView.swift
//  testApp
//
//  Created by Khye Jac Low on 17/6/2026.
//
import SwiftUI
import CoreBluetooth
import MWDATCore
import MWDATCamera
import AVFoundation
import Speech

//struct BrainResponse: Codable {
//    let status: String
//    let agent_task_executed: String
//    let final_result: String?
//}

// MARK: - Updated Response Model
struct BrainResponse: Codable {
    let status: String?
    let id: String?
    let result: String?
    let error: String?
}

struct ContentView: View {
    // MARK: - SDK state (mirrors the sample app)
    @State private var registrationState: RegistrationState = .unavailable
    @State private var cameraPermission: PermissionStatus = .denied
    @State private var deviceIds: [DeviceIdentifier] = []
    @State private var errorMessage: String?
    
    // MARK: - UI state
    @State private var connectionStatus: String = "Disconnected"
    @State private var capturedImage: UIImage? = nil
    @State private var isProcessing: Bool = false
    
    // Keep the listeners / session / stream alive across the async capture.
    @State private var photoSubscription: Any? = nil
    @State private var streamErrorSubscription: Any? = nil
    @State private var activeSession: DeviceSession? = nil
    @State private var activeStream: MWDATCamera.Stream? = nil
    
    // Triggers the native Bluetooth permission dialog at launch.
    @State private var bluetoothManager = CBCentralManager(delegate: nil, queue: nil)
    
    // True while we've bounced out to the Meta AI app and are waiting to come back.
    @State private var awaitingMetaAI = false
    // Counts how many callback URLs Meta AI has actually delivered to us.
    @State private var callbackCount = 0
    // Whether the debug/diagnostics panel is visible.
    @State private var showDebug = false
    @State private var synthesizer = AVSpeechSynthesizer()
    @Environment(\.scenePhase) private var scenePhase
    
    private let wearables = Wearables.shared
    
    private var isRegistered: Bool { registrationState == .registered }
    private var hasCamera: Bool { cameraPermission == .granted }
    
    
    var body: some View {
            // 👈 1. WE ADD A SCROLLVIEW HERE
            ScrollView {
                VStack(spacing: 20) {
                    
                    // Header is pushed to the top
                    header
                        .padding(.top, 20)
                    
                    // The massive image card
                    previewCard
                    
                    // Status text safely outside the image
                    Text(connectionStatus)
                        .font(.headline)
                        .foregroundColor(connectionStatus.contains("❌") ? .red : .primary)
                        .multilineTextAlignment(.center)
                        .fixedSize(horizontal: false, vertical: true)
                        .padding(.horizontal)
                    
                    statusAndError
                    
                    if showDebug {
                        debugPanel
                    }
                    
                    // 👈 2. Add minLength so it doesn't collapse in the scroll view
                    Spacer(minLength: 30)
                    
                    // Buttons anchored at the bottom
                    buttonStack
                        .padding(.bottom, 40) // Extra padding so it clears the home bar
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
    
    // MARK: - Layout pieces
    
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
                    .frame(maxWidth: .infinity, maxHeight: 450) // 👈 Takes up massive space now
                    .clipShape(RoundedRectangle(cornerRadius: 24))
            } else {
                RoundedRectangle(cornerRadius: 24)
                    .fill(Color(.secondarySystemBackground))
                    .frame(maxWidth: .infinity, minHeight: 250, maxHeight: 450)
                
                Image(systemName: isProcessing ? "camera.aperture" : "camera.fill")
                    .font(.system(size: 52))
                    .foregroundStyle(.blue)
            }
            
            // Loading spinner overlay
            if isProcessing {
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
                         disabled: isProcessing, done: isRegistered) {
                connectToMetaAI()
            }
            actionButton("Allow Camera Access", systemImage: "camera.badge.ellipsis", color: .orange,
                         disabled: isProcessing || !isRegistered, done: hasCamera) {
                requestCameraPermission()
            }
            let readyToSnap = hasCamera && !deviceIds.isEmpty
            actionButton("Snap POV Photo", systemImage: "camera.fill", color: .green,
                         disabled: isProcessing || !readyToSnap, done: false) {
                snapGlassesPhoto()
            }
            
            if capturedImage != nil {
                actionButton("Run AI Command", systemImage: "sparkles", color: .purple,
                             disabled: isProcessing, done: false) {
                    runAICommand()
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
    
    // MARK: - Speech Transcription Helper
    func transcribeAudio(url: URL) async -> String? {
        await withCheckedContinuation { continuation in
            var didResume = false // Safety flag to prevent crashes
            
            SFSpeechRecognizer.requestAuthorization { status in
                guard status == .authorized,
                      let recognizer = SFSpeechRecognizer(locale: Locale(identifier: "en-US")),
                      recognizer.isAvailable else {
                    if !didResume { didResume = true; continuation.resume(returning: nil) }
                    return
                }
                
                let request = SFSpeechURLRecognitionRequest(url: url)
                request.shouldReportPartialResults = false // 👈 Forces it to just give the final answer
                
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
    
    // MARK: - Camera permission (mirrors the sample app)
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
        
        // 🧹 1. NUKE PREVIOUS ZOMBIE SESSIONS
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
                            self.connectionStatus = "Photo success! Tap Run AI Command."
                            self.isProcessing = false
                            session.stop()
                            self.activeStream = nil
                            self.activeSession = nil
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
                try? await Task.sleep(nanoseconds: 1_500_000_000) // 1.5 second delay
                
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
                    
                    // 🧹 3. CLEANUP ON ERROR
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
    
    // MARK: - 🧠 THE BRAIN BRIDGE
    
    func runAICommand() {
            guard let image = capturedImage else { return }
            
            isProcessing = true
            connectionStatus = "🎙️ Listening for 4 secs... Yap now!"
            
            Task {
                if let audioURL = await startRecordingAudio() {
                    await MainActor.run { self.connectionStatus = "✍️ Transcribing voice..." }
                    
                    // Transcribe audio to text string for the required `prompt` field
                    let promptText = await transcribeAudio(url: audioURL) ?? "Search for items in image"
                    
                    await MainActor.run { self.connectionStatus = "🚀 Running agent..." }
                    await sendToBackend(image: image, promptText: promptText)
                } else {
                    await MainActor.run {
                        self.connectionStatus = "❌ Mic failed."
                        self.isProcessing = false
                    }
                }
            }
        }
    
    func startRecordingAudio() async -> URL? {
        let session = AVAudioSession.sharedInstance()
        do {
            try session.setCategory(.playAndRecord, mode: .default, options: [.allowBluetooth])
            try session.setActive(true)
        } catch {
            print("Audio session caught an L: \(error)")
            return nil
        }
        
        let audioFileURL = FileManager.default.temporaryDirectory.appendingPathComponent("voice_command.m4a")
        let settings: [String: Any] = [
            AVFormatIDKey: Int(kAudioFormatMPEG4AAC),
            AVSampleRateKey: 16000,
            AVNumberOfChannelsKey: 1,
            AVEncoderAudioQualityKey: AVAudioQuality.medium.rawValue
        ]
        
        do {
            let recorder = try AVAudioRecorder(url: audioFileURL, settings: settings)
            recorder.record()
            
            try await Task.sleep(nanoseconds: 4_000_000_000)
            recorder.stop()
            return audioFileURL
        } catch {
            print("Recorder failed: \(error)")
            return nil
        }
    }
    
    func speakResult(_ text: String) {
        synthesizer.stopSpeaking(at: .immediate)
        let utterance = AVSpeechUtterance(string: text)
        utterance.rate = AVSpeechUtteranceDefaultSpeechRate
        synthesizer.speak(utterance)
    }

    // MARK: - Updated JSON Network Call
    func sendToBackend(image: UIImage, promptText: String) async {
        // Adjust endpoint URL if needed
        // HOME
        let URL_ENDPOINT = "192.168.0.138"
        // EDUROM
//        let URL_ENDPOINT = "10.4.165.59"
        // HOTSPOT
//        let URL_ENDPOINT = "172.20.10.4"
        let URL_PORT = "8765"
        
        let url = URL(string: "http://\(URL_ENDPOINT):\(URL_PORT)/data")!
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.timeoutInterval = 180
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        
        guard let imageData = image.jpegData(compressionQuality: 0.8) else {
            await MainActor.run {
                self.isProcessing = false
                self.connectionStatus = "❌ Failed to compress image."
            }
            return
        }
        
        let base64Image = imageData.base64EncodedString()
        
        // Exact JSON keys expected by handle_data
        let payload: [String: Any] = [
            "prompt": promptText,
            "media_data": base64Image,
            "media_type": "image",
            "filename": "pic.jpg"
        ]
        
        do {
            let jsonData = try JSONSerialization.data(withJSONObject: payload)
            let (data, response) = try await URLSession.shared.upload(for: request, from: jsonData)

            if let http = response as? HTTPURLResponse, http.statusCode >= 400 {
                let body = String(data: data, encoding: .utf8) ?? ""
                print("HTTP \(http.statusCode): \(body)")
            }

            let brainData = try JSONDecoder().decode(BrainResponse.self, from: data)

            await MainActor.run {
                self.isProcessing = false
                if brainData.status == "error" || brainData.error != nil {
                    self.connectionStatus = "❌ Error: \(brainData.error ?? "Unknown error")"
                } else if let result = brainData.result, !result.isEmpty {
                    self.connectionStatus = result
                    self.speakResult(result)
                } else if let itemId = brainData.id {
                    self.connectionStatus = "✅ Done (ID: \(itemId.prefix(8))...)"
                } else {
                    self.connectionStatus = "✅ Task received."
                }
            }
        } catch {
            await MainActor.run {
                self.isProcessing = false
                self.connectionStatus = "❌ Network error or timeout."
                print("Network error: \(error)")
            }
        }
    }
}

#Preview {
    ContentView()
}
