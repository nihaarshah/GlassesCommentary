// AppConfig.swift
//
// The one place the iOS app gets its endpoints and capture settings.
// No secrets: these are public, unauthenticated Modal URLs.

import Foundation
import MWDATCamera

enum AppConfig {
    /// Live Moondream commentary (commentary/modal_app.py), unchanged.
    static let commentaryCaptionURL = URL(string: "https://nihaarshah--glasses-commentary-commentary-caption.modal.run")!

    /// glasses-guide FastAPI app (commentary/guide/modal_app.py).
    static let guideAPI = URL(string: "https://nihaarshah--glasses-guide-api.us-east.modal.run")!

    /// The deployed glasses web app (Vercel), which also serves review.html.
    /// The project's stable production alias -- not a per-deployment URL, which
    /// changes on every `vercel --prod`.
    static let webAppBase: URL? = URL(string: "https://webapp-flame-chi.vercel.app")

    /// Capture: the highest resolution that holds ~2 fps. Defaults to .high until
    /// the M0 measurements (Camera test mode) say otherwise.
    static let captureResolution: StreamingResolution = .high
    static let captureFrameRate: UInt = 2
    static let captureJPEGQuality: CGFloat = 0.7

    /// Guide: frames go up ~1 Hz; Check picks the sharpest recent one server-side.
    static let guideResolution: StreamingResolution = .high
    static let guideFrameRate: UInt = 2
    static let guideUploadInterval: TimeInterval = 1.0
    static let guideJPEGQuality: CGFloat = 0.75
    static let guideMaxSide: CGFloat = 1280
}
