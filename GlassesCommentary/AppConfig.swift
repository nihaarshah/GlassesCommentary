// AppConfig.swift
//
// The one place the iOS app gets its endpoints and capture settings.
// No secrets: these are public, unauthenticated Modal URLs.

import Foundation
import MWDATCamera

enum AppConfig {
    /// Live Moondream commentary (commentary/modal_app.py), unchanged.
    static let commentaryCaptionURL = URL(string: "https://nihaarshah--glasses-commentary-commentary-caption.modal.run")!
    /// Same app's GET endpoint; the phone pings it to wake the caption server.
    static let commentaryLatestURL = URL(string: "https://nihaarshah--glasses-commentary-commentary-latest.modal.run")

    /// Live commentary stream. Valid DAT frame rates: 2, 7, 15, 24, 30, but on this
    /// glasses firmware (V131) with DAT 0.9.0, 15 fps ended in "Critical error, the
    /// stream should end" while 2 fps streams fine. Captions don't need more: every
    /// frame is decoded, so 2 fps always gives a frame <= 0.5 s old. A caption is
    /// requested as soon as the last one returns, at most once per
    /// `commentaryMinInterval`. Try higher rates in Camera test first.
    static let commentaryFrameRate: UInt = 2
    static let commentaryMinInterval: TimeInterval = 1.0

    /// glasses-guide FastAPI app (commentary/guide/modal_app.py).
    static let guideAPI = URL(string: "https://nihaarshah--glasses-guide-api.us-east.modal.run")!

    /// The deployed glasses web app (Vercel), which also serves review.html.
    /// The project's stable production alias -- not a per-deployment URL, which
    /// changes on every `vercel --prod`.
    static let webAppBase: URL? = URL(string: "https://webapp-flame-chi.vercel.app")

    /// Capture: the highest resolution that holds ~2 fps. On V131 firmware with the
    /// glasses web app open, .high and .medium both turned the display off (.high
    /// also ended in "Critical error, the stream should end"); .low keeps the
    /// display on, as live commentary does.
    static let captureResolution: StreamingResolution = .low
    static let captureFrameRate: UInt = 2
    static let captureJPEGQuality: CGFloat = 0.7

    /// Guide: frames go up ~1 Hz; Check picks the sharpest recent one server-side.
    static let guideResolution: StreamingResolution = .low
    static let guideFrameRate: UInt = 2
    static let guideUploadInterval: TimeInterval = 1.0
    static let guideJPEGQuality: CGFloat = 0.75
    static let guideMaxSide: CGFloat = 1280
}
