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

    private let endpoint: URL
    private let prompt: String
    private let minInterval: TimeInterval
    private var lastSentAt: Date = .distantPast

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
        minInterval: TimeInterval = 4.0
    ) {
        self.wearables = wearables
        self.deviceSelector = AutoDeviceSelector(wearables: wearables)
        self.endpoint = endpoint
        self.prompt = prompt
        self.minInterval = minInterval
    }

    /// Creates and starts a device session; the camera stream is started once
    /// the session reports `.started` (see `observeSession`).
    func start() {
        guard deviceSession == nil else { return }
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
        deviceSession?.stop()
    }

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
            Task { @MainActor in self?.lastError = error.localizedDescription }
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
            guard granted else {
                lastError = "Camera permission denied"
                return
            }
        } catch {
            lastError = error.localizedDescription
            return
        }

        let config = StreamConfiguration(
            videoCodec: VideoCodec.hvc1,
            resolution: StreamingResolution.low,
            frameRate: 2
        )
        do {
            guard let newCamera = try session.addCamera(config: config) else {
                lastError = "Couldn't create the camera"
                return
            }
            camera = newCamera
            newCamera.stream.videoFramePublisher.listen { [weak self] frame in
                self?.handleFrame(frame)
            }.store(in: streamTokenBag)
            newCamera.stream.start()
            isStreaming = true
        } catch {
            lastError = error.localizedDescription
        }
    }

    /// `VideoFrameDecoder` is thread-safe and documented as callable from any
    /// thread, so this stays nonisolated and only hops to the main actor to
    /// touch `lastSentAt`/`latestCommentary`.
    private nonisolated func handleFrame(_ frame: VideoFrame) {
        Task { @MainActor [weak self] in
            guard let self else { return }
            let now = Date()
            guard now.timeIntervalSince(self.lastSentAt) >= self.minInterval else { return }
            self.lastSentAt = now

            guard let image = self.videoFrameDecoder.decode(frame.sampleBuffer),
                  let jpegData = image.jpegData(compressionQuality: 0.7) else { return }

            await self.sendCommentaryRequest(imageData: jpegData)
        }
    }

    private func sendCommentaryRequest(imageData: Data) async {
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
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
        } catch {
            lastError = "commentary request failed: \(error.localizedDescription)"
        }
    }
}
