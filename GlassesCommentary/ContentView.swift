//
//  ContentView.swift
//  GlassesCommentary
//
//  Created by Nihaar  Shah on 8/30/26.
//

import SwiftUI
import MWDATCamera
import MWDATCore

struct ContentView: View {
    enum Mode: String, CaseIterable, Identifiable {
        case commentary = "Commentary"
        case capture = "Capture"
        case guide = "Guide"
        case cameraTest = "Camera test"
        var id: String { rawValue }
    }

    let wearables: WearablesInterface
    var wearablesViewModel: WearablesViewModel

    @State private var stream: GlassesCommentaryStream
    @State private var camera: GlassesCamera
    @State private var capture: CaptureSession
    @State private var guide: GuideSession
    @State private var diagnostics: DiagnosticsRunner
    @State private var mode: Mode = .commentary

    init(wearables: WearablesInterface, wearablesViewModel: WearablesViewModel) {
        self.wearables = wearables
        self.wearablesViewModel = wearablesViewModel
        self._stream = State(
            wrappedValue: GlassesCommentaryStream(
                wearables: wearables,
                endpoint: AppConfig.commentaryCaptionURL,
                minInterval: AppConfig.commentaryMinInterval,
                frameRate: AppConfig.commentaryFrameRate
            )
        )
        let camera = GlassesCamera(wearables: wearables)
        self._camera = State(wrappedValue: camera)
        self._capture = State(wrappedValue: CaptureSession(camera: camera))
        self._guide = State(wrappedValue: GuideSession(camera: camera))
        self._diagnostics = State(wrappedValue: DiagnosticsRunner(wearables: wearables))
    }

    var body: some View {
        if wearablesViewModel.registrationState == .registered {
            registeredView
        } else {
            VStack(spacing: 20) {
                Text("Glasses Commentary")
                    .font(.title2).bold()
                Text("You'll be redirected to the Meta AI app to confirm the connection.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                Button(
                    wearablesViewModel.registrationState == .registering
                        ? "Connecting…" : "Connect my glasses"
                ) {
                    wearablesViewModel.connectGlasses()
                }
                .buttonStyle(.borderedProminent)
                .disabled(wearablesViewModel.registrationState == .registering)
            }
            .padding()
        }
    }

    /// Only one mode may own the glasses camera at a time.
    private var cameraBusy: Bool {
        stream.isStreaming || capture.phase == .recording || guide.isActive || camera.isStreaming
            || diagnostics.isRunning
    }

    /// Unattended testing: launched with GC_AUTORUN=diagnostics (devicectl passes
    /// DEVICECTL_CHILD_GC_AUTORUN), the app runs the diagnostics matrix by itself.
    private func autorunIfRequested() {
        guard ProcessInfo.processInfo.environment["GC_AUTORUN"] == "diagnostics", !diagnostics.isRunning else { return }
        mode = .cameraTest
        GlassesLog.shared.add("autorun: diagnostics")
        Task { await diagnostics.run() }
    }

    private var registeredView: some View {
        VStack(spacing: 16) {
            Picker("Mode", selection: $mode) {
                ForEach(Mode.allCases) { Text($0.rawValue).tag($0) }
            }
            .pickerStyle(.segmented)
            .disabled(cameraBusy)

            switch mode {
            case .commentary: commentaryView
            case .capture: CaptureView(capture: capture, camera: camera)
            case .guide: GuideView(guide: guide, camera: camera)
            case .cameraTest:
                DiagnosticsView(runner: diagnostics, disabled: camera.isStreaming)
                Divider()
                CameraTestView(camera: camera)
            }
            Spacer()
            GlassesLogView()
        }
        .padding()
        .task { autorunIfRequested() }
    }

    private var commentaryView: some View {
        VStack(spacing: 24) {
            Text(stream.isStreaming ? "Streaming · \(String(format: "%.1f", stream.measuredFPS)) fps" : "Not connected")
                .foregroundStyle(.secondary)

            Text(stream.latestCommentary.isEmpty ? "Waiting for first frame…" : stream.latestCommentary)
                .multilineTextAlignment(.center)
                .padding()

            if let error = stream.lastError {
                Text(error)
                    .foregroundStyle(.red)
                    .font(.footnote)
            }

            Button(stream.isStreaming ? "Stop Streaming" : "Start Streaming") {
                if stream.isStreaming {
                    stream.stop()
                } else {
                    stream.start()
                }
            }
            .buttonStyle(.borderedProminent)
        }
    }
}

// MARK: - Capture

struct CaptureView: View {
    @Bindable var capture: CaptureSession
    var camera: GlassesCamera

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            TextField("Procedure title", text: $capture.title)
                .textFieldStyle(.roundedBorder)
                .disabled(capture.phase != .idle)
            Picker("Narration mic", selection: $capture.mic) {
                ForEach(NarrationRecorder.Mic.allCases) { Text($0.rawValue).tag($0) }
            }
            .pickerStyle(.segmented)
            .disabled(capture.phase != .idle)

            Group {
                switch capture.phase {
                case .idle:
                    Text("Start, then do the task once while talking through each step.")
                case .recording:
                    Label("Recording · \(capture.frameCount) frames", systemImage: "record.circle.fill")
                        .foregroundStyle(.red)
                    Text("Mic: \(capture.audio.activeInput)").font(.footnote)
                case let .uploading(p):
                    ProgressView("Uploading", value: p)
                case let .processing(stage):
                    ProgressView("Processing: \(stage)")
                case let .done(procID):
                    Text("Ready for review").bold()
                    if let url = reviewURL(procID) {
                        Link("Open review page", destination: url)
                    } else {
                        Text("Open review.html?id=\(procID) on the web app").font(.footnote).textSelection(.enabled)
                    }
                case let .failed(msg):
                    Text(msg).foregroundStyle(.red)
                }
            }

            CameraStatus(camera: camera)
            if let err = capture.audio.lastError {
                Text(err).font(.footnote).foregroundStyle(.orange)
            }

            HStack {
                switch capture.phase {
                case .idle:
                    Button("Start capture") { Task { await capture.start() } }
                        .buttonStyle(.borderedProminent)
                case .recording:
                    Button("Stop & upload") { Task { await capture.stop() } }
                        .buttonStyle(.borderedProminent).tint(.red)
                    Button("Cancel") { Task { await capture.cancel() } }
                case .done, .failed:
                    Button("New capture") { capture.reset() }
                default:
                    EmptyView()
                }
            }
        }
    }

    private func reviewURL(_ procID: String) -> URL? {
        // The review page lives with the glasses web app on Vercel; open it from a laptop too.
        AppConfig.webAppBase.map { $0.appending(path: "review.html").appending(queryItems: [URLQueryItem(name: "id", value: procID)]) }
    }
}

// MARK: - Guide

struct GuideView: View {
    @Bindable var guide: GuideSession
    var camera: GlassesCamera

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            TextField("Operator name", text: $guide.operatorName)
                .textFieldStyle(.roundedBorder)
                .disabled(guide.isActive)
            if guide.isActive {
                Text(guide.runTitle.isEmpty ? "No task running" : guide.runTitle).bold()
                Text(guide.stepText)
                if !guide.lastVerdict.isEmpty { Text(guide.lastVerdict).font(.footnote) }
                Text("Frames sent: \(guide.framesSent)").font(.footnote).foregroundStyle(.secondary)
                if let frame = guide.viewfinder {
                    // Aim with this: the glasses camera sits at the frame's corner,
                    // not where your eyes are.
                    Image(uiImage: frame)
                        .resizable()
                        .scaledToFit()
                        .frame(maxHeight: 220)
                        .clipShape(RoundedRectangle(cornerRadius: 8))
                        .overlay(alignment: .topLeading) {
                            Text("Glasses camera").font(.caption2).padding(4).background(.black.opacity(0.6)).foregroundStyle(.white)
                        }
                }
            } else {
                Text("Start, then pick the task on the glasses. Keep the phone app open.")
            }
            CameraStatus(camera: camera)
            if let err = guide.lastError { Text(err).font(.footnote).foregroundStyle(.red) }
            Button(guide.isActive ? "Stop guide" : "Start guide") {
                guide.isActive ? guide.stop() : guide.start()
            }
            .buttonStyle(.borderedProminent)
        }
    }
}

// MARK: - Camera test (M0 spike 1)

struct CameraTestView: View {
    var camera: GlassesCamera
    @State private var resolution: StreamingResolution = .low
    @State private var fps: UInt = 2
    @State private var photoInfo = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Stream each resolution for ~30 s with the glasses web app open, and note the size and fps.")
                .font(.footnote)
            Picker("Resolution", selection: $resolution) {
                Text("low").tag(StreamingResolution.low)
                Text("medium").tag(StreamingResolution.medium)
                Text("high").tag(StreamingResolution.high)
            }
            .pickerStyle(.segmented)
            .disabled(camera.isStreaming)
            Picker("Frame rate", selection: $fps) {
                ForEach([UInt(2), 7, 15, 24, 30], id: \.self) { Text("\($0) fps").tag($0) }
            }
            .pickerStyle(.segmented)
            .disabled(camera.isStreaming)
            let nominal = resolution.videoFrameSize
            Text("SDK size: \(nominal.width)×\(nominal.height)").font(.footnote)
            CameraStatus(camera: camera)
            HStack {
                Button(camera.isStreaming ? "Stop" : "Stream") {
                    camera.isStreaming ? camera.stop() : camera.start(resolution: resolution, frameRate: fps)
                }
                .buttonStyle(.borderedProminent)
                Button("Photo mid-stream") {
                    Task {
                        let t0 = Date()
                        if let data = await camera.capturePhoto(), let img = UIImage(data: data) {
                            photoInfo = "Photo \(Int(img.size.width))×\(Int(img.size.height)), \(data.count / 1024) KB, \(String(format: "%.1f", Date().timeIntervalSince(t0))) s; still streaming: \(camera.isStreaming)"
                        } else {
                            photoInfo = "Photo failed or timed out"
                        }
                    }
                }
                .disabled(!camera.isStreaming)
            }
            if !photoInfo.isEmpty { Text(photoInfo).font(.footnote) }
        }
    }
}

struct CameraStatus: View {
    var camera: GlassesCamera

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            if camera.isStreaming {
                Text("Camera \(Int(camera.frameSize.width))×\(Int(camera.frameSize.height)) · \(String(format: "%.1f", camera.measuredFPS)) fps · \(String(describing: camera.streamState))")
                    .font(.footnote).foregroundStyle(.secondary)
            }
            if let err = camera.lastError {
                Text(err).font(.footnote).foregroundStyle(.red)
            }
        }
    }
}

/// One tap: runs the camera/display matrix and uploads the report to the backend.
struct DiagnosticsView: View {
    var runner: DiagnosticsRunner
    var disabled: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Glasses on and unfolded, glasses-guide closed. Keep this app open; about 4 minutes.")
                .font(.footnote)
            Button(runner.isRunning ? "Running…" : "Run diagnostics") {
                Task { await runner.run() }
            }
            .buttonStyle(.borderedProminent)
            .disabled(runner.isRunning || disabled)
            if !runner.status.isEmpty { Text(runner.status).font(.footnote).bold() }
            ForEach(Array(runner.results.enumerated()), id: \.offset) { _, r in
                Text("\(r.ok ? "✅" : "❌") \(r.name): \(r.framesReceived) frames \(r.frameSize) \(r.error ?? "")")
                    .font(.caption)
            }
        }
    }
}

/// What the glasses session/stream actually did, newest last.
struct GlassesLogView: View {
    @State private var expanded = true
    private var log = GlassesLog.shared

    var body: some View {
        DisclosureGroup("Glasses log", isExpanded: $expanded) {
            ScrollView {
                VStack(alignment: .leading, spacing: 2) {
                    ForEach(Array(log.lines.enumerated()), id: \.offset) { _, line in
                        Text(line).font(.caption2.monospaced()).textSelection(.enabled)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .frame(maxHeight: 160)
            Button("Clear") { log.clear() }.font(.caption)
        }
        .font(.footnote)
    }
}

#Preview {
    ContentView(wearables: Wearables.shared, wearablesViewModel: WearablesViewModel(wearables: Wearables.shared))
}
