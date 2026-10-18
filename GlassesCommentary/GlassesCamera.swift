// GlassesCamera.swift
//
// Device session + camera stream for Capture and Guide modes, with decoded
// frames delivered to a callback and an optional still photo.
//
// Lifecycle mirrors facebook/meta-wearables-dat-ios samples/CameraAccess
// (ViewModels/CameraViewModel.swift) at the 0.9.0 release: createSession ->
// session .started -> camera permission -> addCamera(config:) -> stream
// listeners -> stream.start(). Still photos use stream.capturePhoto(format: .jpeg)
// with the result on stream.photoDataPublisher, exactly as the sample does
// mid-stream. Live commentary keeps its own GlassesCommentaryStream untouched.

import Foundation
import MWDATCamera
import MWDATCore
import Observation
import UIKit

@Observable
@MainActor
final class GlassesCamera {
    private(set) var streamState: StreamState = .stopped
    private(set) var isStreaming = false
    var lastError: String?

    /// M0 measurements: decoded frame size and frames per second over ~5 s.
    private(set) var frameSize: CGSize = .zero
    private(set) var measuredFPS: Double = 0
    private(set) var configuredResolution: StreamingResolution = .low

    /// Decoded frame + seconds since the stream started. Called on the main actor.
    @ObservationIgnored var onFrame: ((UIImage, TimeInterval) -> Void)?

    private let wearables: WearablesInterface
    private let deviceSelector: AutoDeviceSelector
    private var deviceSession: DeviceSession?
    private var camera: MWDATCamera.Camera?
    private var config = StreamConfiguration(videoCodec: .hvc1, resolution: .low, frameRate: 2)
    private let videoFrameDecoder = VideoFrameDecoder()
    private let sessionTokenBag = ListenerTokenBag()
    private let streamTokenBag = ListenerTokenBag()
    private var startedAt = Date()
    private var fpsWindow: [Date] = []
    private var photoContinuation: CheckedContinuation<Data?, Never>?

    init(wearables: WearablesInterface) {
        self.wearables = wearables
        self.deviceSelector = AutoDeviceSelector(wearables: wearables)
    }

    func start(resolution: StreamingResolution, frameRate: UInt) {
        guard deviceSession == nil else { return }
        config = StreamConfiguration(videoCodec: .hvc1, resolution: resolution, frameRate: frameRate)
        configuredResolution = resolution
        lastError = nil
        do throws(DeviceSessionError) {
            let session = try wearables.createSession(deviceSelector: deviceSelector)
            deviceSession = session
            observeSession(session)
            try session.start()
        } catch {
            lastError = error.localizedDescription
            deviceSession = nil
        }
    }

    func stop() {
        camera?.stop()
        camera = nil
        streamTokenBag.clear()
        isStreaming = false
        streamState = .stopped
        deviceSession?.stop()
        finishPhoto(nil)
    }

    /// Still photo while streaming. Returns JPEG data, or nil on failure/timeout.
    func capturePhoto(timeout: TimeInterval = 4) async -> Data? {
        guard let camera, isStreaming, photoContinuation == nil else { return nil }
        guard camera.stream.capturePhoto(format: .jpeg) else { return nil }
        let timeoutTask = Task { @MainActor [weak self] in
            try? await Task.sleep(for: .seconds(timeout))
            self?.finishPhoto(nil)
        }
        defer { timeoutTask.cancel() }
        return await withCheckedContinuation { continuation in
            photoContinuation = continuation
        }
    }

    private func finishPhoto(_ data: Data?) {
        photoContinuation?.resume(returning: data)
        photoContinuation = nil
    }

    // MARK: - Session / stream

    private func observeSession(_ session: DeviceSession) {
        session.statePublisher.listen { [weak self] state in
            Task { @MainActor in
                guard let self else { return }
                if state == .started {
                    await self.beginStreamingIfNeeded(on: session)
                } else if state == .stopped {
                    self.sessionTokenBag.clear()
                    self.deviceSession = nil
                    self.isStreaming = false
                }
            }
        }.store(in: sessionTokenBag)

        session.errorPublisher.listen { [weak self] error in
            Task { @MainActor [weak self] in self?.lastError = error.localizedDescription }
        }.store(in: sessionTokenBag)
    }

    private func beginStreamingIfNeeded(on session: DeviceSession) async {
        guard camera == nil else { return }
        do {
            var granted = try await wearables.checkPermissionStatus(.camera) == .granted
            if !granted {
                granted = try await wearables.requestPermission(.camera) == .granted
            }
            guard granted else {
                lastError = "Camera permission denied"
                return
            }
        } catch {
            lastError = error.localizedDescription
            return
        }

        do {
            guard let newCamera = try session.addCamera(config: config) else {
                lastError = "Couldn't create the camera"
                return
            }
            camera = newCamera
            // Subscribe before start() so no initial state transitions are missed.
            newCamera.stream.statePublisher.listen { [weak self] state in
                Task { @MainActor [weak self] in self?.handleStreamState(state) }
            }.store(in: streamTokenBag)
            newCamera.stream.errorPublisher.listen { [weak self] error in
                Task { @MainActor [weak self] in self?.handleStreamError(error) }
            }.store(in: streamTokenBag)
            newCamera.stream.videoFramePublisher.listen { [weak self] frame in
                self?.handleFrame(frame)
            }.store(in: streamTokenBag)
            newCamera.stream.photoDataPublisher.listen { [weak self] photo in
                Task { @MainActor [weak self] in self?.finishPhoto(photo.data) }
            }.store(in: streamTokenBag)
            startedAt = Date()
            fpsWindow = []
            newCamera.stream.start()
            isStreaming = true
        } catch {
            lastError = error.localizedDescription
        }
    }

    private func handleStreamState(_ state: StreamState) {
        streamState = state
        if state == .stopped {
            streamTokenBag.clear()
            camera = nil
            isStreaming = false
        }
    }

    /// Thermal / battery / doff errors pause or stop streaming; say so plainly.
    private func handleStreamError(_ error: StreamError) {
        switch error {
        case .thermalCritical, .thermalEmergency:
            lastError = "Glasses are too hot. Streaming paused until they cool down."
        case .batteryCritical:
            lastError = "Glasses battery critical."
        case .hingesClosed:
            lastError = "Glasses folded or taken off."
        case .photoCaptureFailed:
            finishPhoto(nil)
        default:
            lastError = error.description
        }
    }

    /// Same hop as GlassesCommentaryStream.handleFrame: decode on the main actor
    /// (the project's default isolation); at <= 2 fps that's cheap.
    private nonisolated func handleFrame(_ frame: VideoFrame) {
        Task { @MainActor [weak self] in
            guard let self, let image = self.videoFrameDecoder.decode(frame.sampleBuffer) else { return }
            let now = Date()
            self.frameSize = image.size
            self.fpsWindow.append(now)
            self.fpsWindow.removeAll { now.timeIntervalSince($0) > 5 }
            if let first = self.fpsWindow.first, self.fpsWindow.count > 1 {
                self.measuredFPS = Double(self.fpsWindow.count - 1) / now.timeIntervalSince(first)
            }
            self.onFrame?(image, now.timeIntervalSince(self.startedAt))
        }
    }
}
