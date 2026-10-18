// CaptureSession.swift
//
// Capture mode: the expert does the task once while talking. Frames (JPEG +
// timestamp) and narration audio are saved locally, then uploaded as one zip
// bundle at the end with progress, and the backend pipeline is started.
// Bundle layout matches commentary/guide/bundle.py.

import Foundation
import Observation
import UIKit

@Observable
@MainActor
final class CaptureSession {
    enum Phase: Equatable {
        case idle, recording, uploading(Double), processing(String), done(String), failed(String)
    }

    private(set) var phase: Phase = .idle
    private(set) var frameCount = 0
    private(set) var captureID: String?
    var title = "New procedure"
    var mic: NarrationRecorder.Mic = .glasses

    let camera: GlassesCamera
    let audio = NarrationRecorder()
    private let api = GuideAPI()
    private var dir: URL?
    private var frames: [[String: Any]] = []
    private var lastFrameAt: TimeInterval = -1
    private var recordingStartedAt: Date?

    init(camera: GlassesCamera) {
        self.camera = camera
    }

    func start() async {
        guard phase == .idle || isFinished else { return }
        do {
            let cap = try await api.json("POST", "captures/start", body: ["title": title])
            guard let id = cap["id"] as? String else { throw GuideAPIError.badResponse }
            captureID = id
            let dir = FileManager.default.temporaryDirectory.appending(path: "capture-\(id)", directoryHint: .isDirectory)
            try? FileManager.default.removeItem(at: dir)
            try FileManager.default.createDirectory(at: dir.appending(path: "frames"), withIntermediateDirectories: true)
            self.dir = dir
            frames = []
            frameCount = 0
            lastFrameAt = -1
            recordingStartedAt = Date()
            UIApplication.shared.isIdleTimerDisabled = true

            _ = audio.start(to: dir.appending(path: "audio.m4a"), mic: mic)
            camera.onFrame = { [weak self] image, _ in self?.saveFrame(image) }
            camera.start(resolution: AppConfig.captureResolution, frameRate: AppConfig.captureFrameRate)
            phase = .recording
        } catch {
            phase = .failed("Couldn't start: \(error.localizedDescription)")
        }
    }

    var isFinished: Bool {
        switch phase {
        case .done, .failed: true
        default: false
        }
    }

    private func saveFrame(_ image: UIImage) {
        guard phase == .recording, let dir, let started = recordingStartedAt else { return }
        let t = Date().timeIntervalSince(started)
        // ~2 fps regardless of what the stream delivers.
        guard t - lastFrameAt >= 1.0 / Double(AppConfig.captureFrameRate) - 0.05 else { return }
        guard let jpeg = image.jpegData(compressionQuality: AppConfig.captureJPEGQuality) else { return }
        let name = String(format: "frames/%06d.jpg", frames.count)
        do {
            try jpeg.write(to: dir.appending(path: name))
            frames.append(["file": name, "t": (t * 1000).rounded() / 1000])
            frameCount = frames.count
            lastFrameAt = t
        } catch {
            camera.lastError = "Disk: \(error.localizedDescription)"
        }
    }

    func stop() async {
        guard phase == .recording, let dir, let id = captureID, let started = recordingStartedAt else { return }
        camera.onFrame = nil
        camera.stop()
        audio.stop()
        UIApplication.shared.isIdleTimerDisabled = false

        let audioOffset = audio.startedAt.map { $0.timeIntervalSince(started) } ?? 0
        let hasAudio = FileManager.default.fileExists(atPath: dir.appending(path: "audio.m4a").path)
        let manifest: [String: Any] = [
            "version": 1,
            "title": title,
            "started_at": started.timeIntervalSince1970,
            "frames": frames,
            "audio": hasAudio ? "audio.m4a" : NSNull(),
            "audio_offset_s": audioOffset,
        ]
        do {
            guard !frames.isEmpty else { throw CaptureError.noFrames }
            let data = try JSONSerialization.data(withJSONObject: manifest)
            try data.write(to: dir.appending(path: "manifest.json"))
            let zip = try zipDirectory(dir)
            phase = .uploading(0)
            try await upload(zip, captureID: id)
            phase = .processing("starting")
            _ = try await api.json("POST", "captures/\(id)/process")
            await pollProcessing(id)
        } catch {
            phase = .failed(error.localizedDescription)
        }
    }

    enum CaptureError: Error, LocalizedError {
        case noFrames, zipFailed
        var errorDescription: String? {
            switch self {
            case .noFrames: "No frames were recorded. Was the camera streaming?"
            case .zipFailed: "Couldn't package the recording"
            }
        }
    }

    /// NSFileCoordinator's .forUploading reading option produces a zip of a
    /// directory -- the system zipper, no third-party dependency.
    private func zipDirectory(_ dir: URL) throws -> URL {
        var result: Result<URL, Error> = .failure(CaptureError.zipFailed)
        var coordError: NSError?
        NSFileCoordinator().coordinate(readingItemAt: dir, options: .forUploading, error: &coordError) { zipURL in
            let dest = FileManager.default.temporaryDirectory.appending(path: "\(dir.lastPathComponent).zip")
            try? FileManager.default.removeItem(at: dest)
            result = Result { try FileManager.default.copyItem(at: zipURL, to: dest); return dest }
        }
        if let coordError { throw coordError }
        return try result.get()
    }

    private func upload(_ zip: URL, captureID id: String) async throws {
        var req = URLRequest(url: AppConfig.guideAPI.appending(path: "captures/\(id)/bundle"))
        req.httpMethod = "POST"
        req.setValue("application/zip", forHTTPHeaderField: "Content-Type")
        req.timeoutInterval = 600
        let uploader = ProgressUploader { [weak self] p in
            Task { @MainActor [weak self] in self?.phase = .uploading(p) }
        }
        let (data, resp) = try await uploader.upload(req, fromFile: zip)
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        guard (200..<300).contains(code) else {
            throw GuideAPIError.http(code, String(decoding: data, as: UTF8.self))
        }
    }

    private func pollProcessing(_ id: String) async {
        while true {
            do {
                let st = try await api.json("GET", "captures/\(id)")
                let state = st["state"] as? String ?? ""
                let stage = st["stage"] as? String ?? ""
                if state == "done" {
                    phase = .done(st["procedure_id"] as? String ?? "")
                    return
                }
                if state == "error" {
                    phase = .failed(st["error"] as? String ?? "Processing failed")
                    return
                }
                let pct = Int(((st["progress"] as? Double) ?? 0) * 100)
                phase = .processing("\(stage) \(pct)%")
            } catch {
                phase = .processing("waiting for server…")
            }
            try? await Task.sleep(for: .seconds(4))
        }
    }

    func reset() {
        phase = .idle
        captureID = nil
        frameCount = 0
    }
}
