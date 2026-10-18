// NarrationRecorder.swift
//
// Records the expert's narration to AAC (.m4a). Mic choice is the M0 audio
// spike: the glasses' Bluetooth HFP mic vs the phone's built-in mic. Session
// setup follows samples/CameraAccess (Media/AudioInputHandler.swift):
// .playAndRecord with .allowBluetoothHFP, and the HFP input is detected by
// port type.

import AVFoundation
import Foundation
import Observation

@Observable
@MainActor
final class NarrationRecorder {
    enum Mic: String, CaseIterable, Identifiable {
        case glasses = "Glasses mic"
        case phone = "Phone mic"
        var id: String { rawValue }
    }

    private(set) var isRecording = false
    private(set) var activeInput = ""
    var lastError: String?

    private var recorder: AVAudioRecorder?
    private(set) var startedAt: Date?

    func start(to url: URL, mic: Mic) -> Bool {
        let session = AVAudioSession.sharedInstance()
        do {
            try session.setCategory(.playAndRecord, mode: .default, options: [.allowBluetoothHFP, .mixWithOthers])
            try session.setActive(true)
            let inputs = session.availableInputs ?? []
            let wanted: AVAudioSession.Port = mic == .glasses ? .bluetoothHFP : .builtInMic
            if let port = inputs.first(where: { $0.portType == wanted }) {
                try session.setPreferredInput(port)
            } else if mic == .glasses {
                lastError = "Glasses mic not available; using \(session.currentRoute.inputs.first?.portName ?? "default mic")"
            }
            let settings: [String: Any] = [
                AVFormatIDKey: kAudioFormatMPEG4AAC,
                AVSampleRateKey: 16_000,
                AVNumberOfChannelsKey: 1,
                AVEncoderAudioQualityKey: AVAudioQuality.high.rawValue,
            ]
            let rec = try AVAudioRecorder(url: url, settings: settings)
            guard rec.record() else {
                lastError = "Couldn't start audio recording"
                return false
            }
            recorder = rec
            startedAt = Date()
            isRecording = true
            activeInput = session.currentRoute.inputs.first.map { "\($0.portName) (\($0.portType.rawValue))" } ?? "unknown"
            return true
        } catch {
            lastError = "Audio: \(error.localizedDescription)"
            return false
        }
    }

    func stop() {
        recorder?.stop()
        recorder = nil
        isRecording = false
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
    }
}
