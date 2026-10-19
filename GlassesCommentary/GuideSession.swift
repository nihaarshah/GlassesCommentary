// GuideSession.swift
//
// Guide mode: the phone is the camera for a novice being guided on the glasses.
// It polls GET /display (~1 Hz) to learn the active run, uploads a frame ~1 Hz
// to that run, and answers still-photo requests (when the backend's
// GUIDE_CHECK_SOURCE is "photo") with stream.capturePhoto(.jpeg).

import Foundation
import Observation
import UIKit

@Observable
@MainActor
final class GuideSession {
    private(set) var isActive = false
    private(set) var runTitle = ""
    private(set) var stepText = ""
    private(set) var lastVerdict = ""
    private(set) var framesSent = 0
    /// What the glasses camera sees right now (~1 Hz), so the wearer can aim.
    private(set) var viewfinder: UIImage?
    var operatorName = "Operator"
    var lastError: String?

    let camera: GlassesCamera
    private let api = GuideAPI()
    private var latest: UIImage?
    private var runID: String?
    private var handledPhotoRequest: String?
    private var loop: Task<Void, Never>?

    init(camera: GlassesCamera) {
        self.camera = camera
    }

    func start() {
        guard !isActive else { return }
        isActive = true
        framesSent = 0
        UIApplication.shared.isIdleTimerDisabled = true
        camera.onFrame = { [weak self] image, _ in self?.latest = image }
        camera.start(resolution: AppConfig.guideResolution, frameRate: AppConfig.guideFrameRate)
        loop = Task { [weak self] in
            if let name = self?.operatorName {
                _ = try? await self?.api.json("PUT", "operator", body: ["operator": name])
            }
            while !Task.isCancelled {
                await self?.tick()
                try? await Task.sleep(for: .seconds(AppConfig.guideUploadInterval))
            }
        }
    }

    func stop() {
        loop?.cancel()
        loop = nil
        camera.onFrame = nil
        camera.stop()
        isActive = false
        UIApplication.shared.isIdleTimerDisabled = false
    }

    private func tick() async {
        do {
            let display = try await api.json("GET", "display")
            guard display["mode"] as? String == "run", let run = display["run"] as? [String: Any],
                  let id = run["id"] as? String
            else {
                runID = nil
                runTitle = ""
                stepText = "Waiting for a task to start on the glasses"
                return
            }
            runID = id
            runTitle = run["procedure_title"] as? String ?? ""
            let screen = run["screen"] as? String ?? ""
            if screen == "done" {
                stepText = "Done"
                return
            }
            let n = (run["current_step"] as? Int ?? 0) + 1
            let total = run["total_steps"] as? Int ?? 0
            let title = (run["step"] as? [String: Any])?["title"] as? String ?? ""
            stepText = "\(n) of \(total): \(title)"
            if let a = run["last_attempt"] as? [String: Any] {
                lastVerdict = "\(a["verdict"] as? String ?? ""): \(a["reason"] as? String ?? "")"
            } else {
                lastVerdict = ""
            }

            if let req = display["photo_request"] as? String, req != handledPhotoRequest {
                handledPhotoRequest = req
                if let photo = await camera.capturePhoto() {
                    _ = try await api.raw("runs/\(id)/photo", data: photo, contentType: "image/jpeg")
                }
            }
            if let image = latest { viewfinder = image }
            if let image = latest, let jpeg = resized(image).jpegData(compressionQuality: AppConfig.guideJPEGQuality) {
                _ = try await api.raw("runs/\(id)/frames", data: jpeg, contentType: "image/jpeg")
                framesSent += 1
            }
            lastError = nil
        } catch {
            lastError = error.localizedDescription
        }
    }

    private func resized(_ image: UIImage) -> UIImage {
        let side = max(image.size.width, image.size.height)
        guard side > AppConfig.guideMaxSide else { return image }
        let scale = AppConfig.guideMaxSide / side
        let size = CGSize(width: (image.size.width * scale).rounded(), height: (image.size.height * scale).rounded())
        let format = UIGraphicsImageRendererFormat()
        format.scale = 1
        return UIGraphicsImageRenderer(size: size, format: format).image { _ in
            image.draw(in: CGRect(origin: .zero, size: size))
        }
    }
}
