// DiagnosticsRunner.swift
//
// One-tap on-glasses diagnostics (the M0 spikes, automated). Runs a fixed matrix
// of short experiments, each in a fresh DAT session, and uploads a report to the
// backend (POST /diag) so results can be read without the phone on a cable:
//
//   - camera alone at several resolutions / frame rates
//   - DAT Display alone (the phone drawing on the glasses)
//   - camera + DAT Display in the same session -- can the phone draw the guide
//     on the glasses while it streams the camera? (fallback for the web app)
//
// APIs as in samples/CameraAccess (camera) and samples/DisplayAccess
// (DisplayViewModel: addDisplay -> statePublisher -> start -> send) at 0.9.0.
// This file deliberately doesn't import SwiftUI: MWDATDisplay's Text would clash.

import Foundation
import MWDATCamera
import MWDATCore
import MWDATDisplay
import Observation
import UIKit

@Observable
@MainActor
final class DiagnosticsRunner {
    struct Experiment: Sendable {
        let name: String
        let resolution: StreamingResolution?  // nil: no camera
        let frameRate: UInt
        let display: Bool
        let seconds: Double
    }

    struct Result: Codable, Sendable {
        let name: String
        var ok = false
        var framesReceived = 0
        var fps = 0.0
        var frameSize = ""
        var firstFrameAfterS: Double?
        var displayStarted = false
        var displaySends = 0
        var displaySendErrors = 0
        var events: [String] = []
        var error: String?
    }

    static let matrix: [Experiment] = [
        Experiment(name: "camera low 2fps", resolution: .low, frameRate: 2, display: false, seconds: 25),
        Experiment(name: "display only", resolution: nil, frameRate: 0, display: true, seconds: 15),
        Experiment(name: "camera low 2fps + display", resolution: .low, frameRate: 2, display: true, seconds: 30),
        Experiment(name: "camera low 7fps", resolution: .low, frameRate: 7, display: false, seconds: 25),
        Experiment(name: "camera medium 2fps", resolution: .medium, frameRate: 2, display: false, seconds: 25),
        Experiment(name: "camera high 2fps", resolution: .high, frameRate: 2, display: false, seconds: 25),
    ]

    private(set) var isRunning = false
    private(set) var status = ""
    private(set) var results: [Result] = []

    private let wearables: WearablesInterface
    private let selector: AutoDeviceSelector
    private let api = GuideAPI()

    // Per-experiment state, touched only on the main actor.
    private var current = Result(name: "")
    private var startedAt = Date()
    private var frameTimes: [Date] = []
    private var decoder = VideoFrameDecoder()
    private var criticalError = false

    init(wearables: WearablesInterface) {
        self.wearables = wearables
        self.selector = AutoDeviceSelector(wearables: wearables)
    }

    func run() async {
        guard !isRunning else { return }
        isRunning = true
        results = []
        UIApplication.shared.isIdleTimerDisabled = true
        defer {
            UIApplication.shared.isIdleTimerDisabled = false
            isRunning = false
        }
        for (i, exp) in Self.matrix.enumerated() {
            status = "\(i + 1)/\(Self.matrix.count): \(exp.name)…"
            let result = await runOne(exp)
            results.append(result)
            GlassesLog.shared.add("diag: \(exp.name) -> \(result.ok ? "OK" : "FAIL") frames=\(result.framesReceived) \(result.error ?? "")")
            print("DIAG \(exp.name): \(result.ok ? "OK" : "FAIL") frames=\(result.framesReceived) fps=\(result.fps) size=\(result.frameSize) display=\(result.displayStarted) err=\(result.error ?? "-")")
            result.events.forEach { print("DIAG   \($0)") }
            try? await Task.sleep(for: .seconds(3))  // let the glasses settle between sessions
        }
        status = "Uploading report…"
        await upload()
        let passed = results.filter(\.ok).count
        status = "Done: \(passed)/\(results.count) passed. Report uploaded."
        print("DIAG DONE \(passed)/\(results.count)")
    }

    // MARK: - One experiment

    private func log(_ message: String) {
        let t = String(format: "%5.1f", Date().timeIntervalSince(startedAt))
        current.events.append("\(t)s \(message)")
    }

    private func runOne(_ exp: Experiment) async -> Result {
        current = Result(name: exp.name)
        startedAt = Date()
        frameTimes = []
        decoder = VideoFrameDecoder()
        criticalError = false
        let tokens = ListenerTokenBag()
        defer { tokens.clear() }

        // Session
        let session: DeviceSession
        do throws(DeviceSessionError) {
            session = try wearables.createSession(deviceSelector: selector)
        } catch {
            current.error = "createSession: \(error.localizedDescription)"
            return current
        }
        session.statePublisher.listen { [weak self] state in
            Task { @MainActor [weak self] in self?.log("session \(state)") }
        }.store(in: tokens)
        session.errorPublisher.listen { [weak self] error in
            Task { @MainActor [weak self] in
                self?.log("session error: \(error.localizedDescription)")
                self?.current.error = self?.current.error ?? error.localizedDescription
            }
        }.store(in: tokens)
        do throws(DeviceSessionError) {
            try session.start()
        } catch {
            current.error = "session.start: \(error.localizedDescription)"
            return current
        }
        guard await waitFor(seconds: 20, { session.state == .started }) else {
            current.error = current.error ?? "session never started (state \(session.state))"
            session.stop()
            _ = await waitFor(seconds: 8) { session.state == .stopped }
            return current
        }

        // Display (optional)
        var display: Display?
        if exp.display {
            do throws(DeviceSessionError) {
                let d = try session.addDisplay()
                d.statePublisher.listen { [weak self] state in
                    Task { @MainActor [weak self] in self?.log("display \(state)") }
                }.store(in: tokens)
                d.start()
                display = d
                current.displayStarted = await waitFor(seconds: 10) { d.state == .started }
                if current.displayStarted {
                    await sendScreen(d, line: exp.resolution == nil ? "Display test" : "Starting camera…")
                }
            } catch {
                log("addDisplay error: \(error.localizedDescription)")
                current.error = current.error ?? "addDisplay: \(error.localizedDescription)"
            }
        }

        // Camera (optional)
        var camera: MWDATCamera.Camera?
        if let resolution = exp.resolution {
            let granted = (try? await wearables.checkPermissionStatus(.camera)) == .granted
            if !granted {
                current.error = current.error ?? "camera permission not granted (open Commentary once)"
            } else {
                let config = StreamConfiguration(videoCodec: .hvc1, resolution: resolution, frameRate: exp.frameRate)
                do {
                    if let cam = try session.addCamera(config: config) {
                        camera = cam
                        cam.stream.statePublisher.listen { [weak self] state in
                            Task { @MainActor [weak self] in self?.log("stream \(state)") }
                        }.store(in: tokens)
                        cam.stream.errorPublisher.listen { [weak self] error in
                            Task { @MainActor [weak self] in
                                guard let self else { return }
                                self.log("stream error: \(error.description)")
                                if error.description.lowercased().contains("critical") { self.criticalError = true }
                                self.current.error = self.current.error ?? error.description
                            }
                        }.store(in: tokens)
                        cam.stream.videoFramePublisher.listen { [weak self] frame in
                            Task { @MainActor [weak self] in self?.handleFrame(frame) }
                        }.store(in: tokens)
                        cam.stream.start()
                    } else {
                        current.error = current.error ?? "addCamera returned nil"
                    }
                } catch {
                    current.error = current.error ?? "addCamera: \(error.localizedDescription)"
                }
            }
        }

        // Observe for the experiment's duration; with a display, update it every
        // 5 s to check the phone can keep drawing while the camera streams.
        let end = Date().addingTimeInterval(exp.seconds)
        var nextSend = Date().addingTimeInterval(5)
        while Date() < end, session.state != .stopped {
            if let d = display, current.displayStarted, Date() >= nextSend {
                nextSend = Date().addingTimeInterval(5)
                await sendScreen(d, line: exp.resolution == nil
                    ? "Display test · \(Int(Date().timeIntervalSince(startedAt))) s"
                    : "Camera frames: \(current.framesReceived) · \(String(format: "%.1f", current.fps)) fps")
            }
            try? await Task.sleep(for: .milliseconds(250))
        }

        // Verdict
        if exp.resolution != nil {
            current.ok = current.framesReceived >= Int(exp.seconds) && !criticalError
                && (!exp.display || current.displayStarted)
        } else {
            current.ok = current.displayStarted && current.displaySends > 0 && current.displaySendErrors == 0
        }

        // Teardown
        camera?.stop()
        display?.stop()
        session.stop()
        _ = await waitFor(seconds: 8) { session.state == .stopped }
        log("done")
        return current
    }

    private func handleFrame(_ frame: VideoFrame) {
        let now = Date()
        if current.firstFrameAfterS == nil {
            current.firstFrameAfterS = (now.timeIntervalSince(startedAt) * 10).rounded() / 10
            log("first frame")
        }
        current.framesReceived += 1
        frameTimes.append(now)
        frameTimes.removeAll { now.timeIntervalSince($0) > 5 }
        if let first = frameTimes.first, frameTimes.count > 1 {
            current.fps = (Double(frameTimes.count - 1) / now.timeIntervalSince(first) * 10).rounded() / 10
        }
        if current.frameSize.isEmpty, let image = decoder.decode(frame.sampleBuffer) {
            current.frameSize = "\(Int(image.size.width))x\(Int(image.size.height))"
        }
    }

    private func sendScreen(_ display: Display, line: String) async {
        let view = FlexBox(direction: .column, spacing: 12, padding: EdgeInsets(all: 24)) {
            Text("GlassesCommentary diagnostics", style: .heading)
            Text(current.name, style: .body)
            Text(line, style: .body, color: .secondary)
        }
        do {
            try await display.send(view)
            current.displaySends += 1
        } catch {
            current.displaySendErrors += 1
            log("display send error: \(error.localizedDescription)")
        }
    }

    private func waitFor(seconds: Double, _ condition: @escaping () -> Bool) async -> Bool {
        let deadline = Date().addingTimeInterval(seconds)
        while Date() < deadline {
            if condition() { return true }
            try? await Task.sleep(for: .milliseconds(200))
        }
        return condition()
    }

    // MARK: - Report

    private func upload() async {
        let device = UIDevice.current
        let body: [String: Any] = [
            "app": "GlassesCommentary",
            "sdk": "DAT 0.9.0",
            "phone": "\(device.model) iOS \(device.systemVersion)",
            "at": Date().timeIntervalSince1970,
            "results": results.map { r -> [String: Any] in
                (try? JSONSerialization.jsonObject(with: JSONEncoder().encode(r))) as? [String: Any] ?? [:]
            },
            "log": GlassesLog.shared.lines,
        ]
        do {
            _ = try await api.json("POST", "diag", body: body)
        } catch {
            status = "Upload failed: \(error.localizedDescription)"
        }
    }
}
