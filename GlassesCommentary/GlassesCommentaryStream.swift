// GlassesCommentaryStream.swift
//
// Streams Ray-Ban Display camera frames (via Meta's Device Access Toolkit)
// to the same Modal commentary endpoint used by commentary/client.py --
// identical request contract ({"image_b64", "prompt"} -> {"commentary"}),
// so commentary/modal_app.py needs zero changes to serve either source.
//
// API verified against facebook/meta-wearables-dat-ios samples/CameraAccess
// (ViewModels/CameraViewModel.swift). An earlier draft of this file was based
// on a summarized doc and used the wrong session type name and the wrong
// observability pattern (Combine's ObservableObject instead of the real
// SDK's @Observable convention) -- this version mirrors the actual working
// sample's session/camera/stream lifecycle.

import Foundation
import MWDATCamera
import MWDATCore
import Observation
import UIKit

@Observable
@MainActor
final class GlassesCommentaryStream {
    var latestCommentary: String = ""
    var lastError: String?
    private(set) var isStreaming = false
    /// Decoded frames per second over the last ~5 s, for the on-phone readout.
    private(set) var measuredFPS: Double = 0

    private let endpoint: URL
    private let prompt: String
    private let minInterval: TimeInterval
    private let frameRate: UInt
    private var lastSentAt: Date = .distantPast
    private var requestInFlight = false
    private var serverWarm = false
    private var frameTimes: [Date] = []

    private let wearables: WearablesInterface
    private let deviceSelector: AutoDeviceSelector
    private var deviceSession: DeviceSession?
    private var camera: MWDATCamera.Camera?
    private let videoFrameDecoder = VideoFrameDecoder()
    private let sessionTokenBag = ListenerTokenBag()
    private let streamTokenBag = ListenerTokenBag()

    init(
        wearables: WearablesInterface,
        endpoint: URL,
        prompt: String = "Describe what you see in one short sentence.",
        minInterval: TimeInterval,
        frameRate: UInt
    ) {
        self.wearables = wearables
        self.deviceSelector = AutoDeviceSelector(wearables: wearables)
        self.endpoint = endpoint
        self.prompt = prompt
        self.minInterval = minInterval
        self.frameRate = frameRate
    }

    /// Creates and starts a device session; the camera stream is started once
    /// the session reports `.started` (see `observeSession`).
    func start() {
        guard deviceSession == nil else { return }
        warmUpServer()
        GlassesLog.shared.add("commentary: creating session")
        do throws(DeviceSessionError) {
            let session = try wearables.createSession(deviceSelector: deviceSelector)
            deviceSession = session
            observeSession(session)
            try session.start()
        } catch {
            lastError = error.localizedDescription
            GlassesLog.shared.add("commentary: session error: \(error.localizedDescription)")
            deviceSession = nil
        }
    }

    func stop() {
        camera?.stop()
        camera = nil
        streamTokenBag.clear()
        isStreaming = false
        deviceSession?.stop()
    }

    private func observeSession(_ session: DeviceSession) {
        session.statePublisher.listen { [weak self] state in
            Task { @MainActor in
                guard let self else { return }
                GlassesLog.shared.add("commentary: session \(state)")
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
            Task { @MainActor [weak self] in
                self?.lastError = error.localizedDescription
                GlassesLog.shared.add("commentary: session error: \(error.localizedDescription)")
            }
        }.store(in: sessionTokenBag)
    }

    /// Camera permission check/request, then start a low-bandwidth stream.
    /// `.hvc1` (compressed HEVC) is what the SDK actually streams -- there is
    /// no raw/uncompressed option -- so frames need `VideoFrameDecoder` below.
    private func beginStreamingIfNeeded(on session: DeviceSession) async {
        guard camera == nil else { return }

        do {
            var granted = try await wearables.checkPermissionStatus(.camera) == .granted
            if !granted {
                granted = try await wearables.requestPermission(.camera) == .granted
            }
            GlassesLog.shared.add("commentary: camera permission \(granted ? "granted" : "denied")")
            guard granted else {
                lastError = "Camera permission denied"
                return
            }
        } catch {
            lastError = error.localizedDescription
            GlassesLog.shared.add("commentary: permission error: \(error.localizedDescription)")
            return
        }

        let config = StreamConfiguration(
            videoCodec: VideoCodec.hvc1,
            resolution: StreamingResolution.low,
            frameRate: frameRate
        )
        do {
            guard let newCamera = try session.addCamera(config: config) else {
                lastError = "Couldn't create the camera"
                return
            }
            camera = newCamera
            newCamera.stream.statePublisher.listen { [weak self] state in
                Task { @MainActor [weak self] in
                    GlassesLog.shared.add("commentary: stream \(state)")
                    // The SDK can end the stream on its own (e.g. the DAT app on the
                    // glasses became unreachable). Tear the session down too, or the
                    // next session for this device fails with "already exists".
                    if state == .stopped { self?.stop() }
                }
            }.store(in: streamTokenBag)
            newCamera.stream.errorPublisher.listen { [weak self] error in
                Task { @MainActor [weak self] in
                    self?.lastError = error.localizedDescription
                    GlassesLog.shared.add("commentary: stream error: \(error.localizedDescription)")
                }
            }.store(in: streamTokenBag)
            newCamera.stream.videoFramePublisher.listen { [weak self] frame in
                self?.handleFrame(frame)
            }.store(in: streamTokenBag)
            newCamera.stream.start()
            isStreaming = true
            GlassesLog.shared.add("commentary: camera added, stream starting")
        } catch {
            lastError = error.localizedDescription
            GlassesLog.shared.add("commentary: addCamera error: \(error.localizedDescription)")
        }
    }

    /// Every frame is decoded -- HEVC frames depend on the ones before them, so
    /// decoding only the occasional frame fails and the decoder falls back to its
    /// last good (stale) image. A frame is sent as soon as the previous caption
    /// comes back, at most once per `minInterval`, so requests never pile up.
    private nonisolated func handleFrame(_ frame: VideoFrame) {
        Task { @MainActor [weak self] in
            guard let self, let image = self.videoFrameDecoder.decode(frame.sampleBuffer) else { return }
            let now = Date()
            self.frameTimes.append(now)
            self.frameTimes.removeAll { now.timeIntervalSince($0) > 5 }
            if let first = self.frameTimes.first, self.frameTimes.count > 1 {
                self.measuredFPS = Double(self.frameTimes.count - 1) / now.timeIntervalSince(first)
            }

            guard !self.requestInFlight, now.timeIntervalSince(self.lastSentAt) >= self.minInterval,
                  let jpegData = image.jpegData(compressionQuality: 0.7) else { return }
            self.lastSentAt = now
            self.requestInFlight = true
            await self.sendCommentaryRequest(imageData: jpegData)
            self.requestInFlight = false
        }
    }

    /// The caption server scales to zero; a cold start (GPU + Moondream load) can
    /// take longer than URLSession's default 60 s. Its GET `latest` endpoint lives on
    /// the same GPU class, so hitting it boots a container while the glasses camera
    /// is still starting.
    private func warmUpServer() {
        guard let latest = AppConfig.commentaryLatestURL else { return }
        serverWarm = false
        latestCommentary = "Warming up the commentary server…"
        Task { [weak self] in
            var req = URLRequest(url: latest)
            req.timeoutInterval = 150
            let ok = (try? await URLSession.shared.data(for: req)) != nil
            await MainActor.run { [weak self] in
                guard let self else { return }
                self.serverWarm = ok
                GlassesLog.shared.add("commentary: server \(ok ? "warm" : "warm-up failed")")
            }
        }
    }

    private func sendCommentaryRequest(imageData: Data) async {
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        // The first request may land on a cold container.
        request.timeoutInterval = serverWarm ? 30 : 150
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")

        let body: [String: Any] = [
            "image_b64": imageData.base64EncodedString(),
            "prompt": prompt,
        ]
        request.httpBody = try? JSONSerialization.data(withJSONObject: body)

        do {
            let (data, _) = try await URLSession.shared.data(for: request)
            let json = try JSONSerialization.jsonObject(with: data) as? [String: Any]
            latestCommentary = json?["commentary"] as? String ?? ""
            serverWarm = true
            lastError = nil
        } catch {
            lastError = "commentary request failed: \(error.localizedDescription)"
        }
    }
}
