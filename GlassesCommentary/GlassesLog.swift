// GlassesLog.swift
//
// A short on-screen log of DAT session/stream state changes and errors, so a
// stream that starts and immediately stops says why on the phone itself.
// Also goes to the unified log (subsystem nrs.GlassesCommentary, category DAT).

import Foundation
import Observation
import os

@Observable
@MainActor
final class GlassesLog {
    static let shared = GlassesLog()

    private(set) var lines: [String] = []
    @ObservationIgnored private let logger = Logger(subsystem: "nrs.GlassesCommentary", category: "DAT")
    @ObservationIgnored private let clock: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "HH:mm:ss"
        return f
    }()

    func add(_ message: String) {
        logger.notice("\(message, privacy: .public)")
        lines.append("\(clock.string(from: Date()))  \(message)")
        if lines.count > 40 { lines.removeFirst(lines.count - 40) }
    }

    func clear() {
        lines.removeAll()
    }
}
