// GuideAPI.swift
//
// Minimal client for the glasses-guide backend (commentary/guide/api.py).

import Foundation

enum GuideAPIError: Error, LocalizedError {
    case http(Int, String)
    case badResponse

    var errorDescription: String? {
        switch self {
        case let .http(code, body): "Server error \(code): \(body.prefix(160))"
        case .badResponse: "Unexpected server response"
        }
    }
}

struct GuideAPI: Sendable {
    var base: URL = AppConfig.guideAPI

    func json(_ method: String, _ path: String, body: [String: Any]? = nil) async throws -> [String: Any] {
        var req = URLRequest(url: base.appending(path: path))
        req.httpMethod = method
        if let body {
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            req.httpBody = try JSONSerialization.data(withJSONObject: body)
        }
        return try await send(req)
    }

    func raw(_ path: String, data: Data, contentType: String) async throws -> [String: Any] {
        var req = URLRequest(url: base.appending(path: path))
        req.httpMethod = "POST"
        req.setValue(contentType, forHTTPHeaderField: "Content-Type")
        req.httpBody = data
        return try await send(req)
    }

    private func send(_ req: URLRequest) async throws -> [String: Any] {
        let (data, resp) = try await URLSession.shared.data(for: req)
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        guard (200..<300).contains(code) else {
            throw GuideAPIError.http(code, String(decoding: data, as: UTF8.self))
        }
        guard let obj = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw GuideAPIError.badResponse
        }
        return obj
    }
}

/// Upload with byte-level progress (URLSession async upload has no progress callback).
final class ProgressUploader: NSObject, URLSessionTaskDelegate, @unchecked Sendable {
    private let onProgress: @Sendable (Double) -> Void

    init(onProgress: @escaping @Sendable (Double) -> Void) {
        self.onProgress = onProgress
    }

    func upload(_ request: URLRequest, fromFile file: URL) async throws -> (Data, URLResponse) {
        let session = URLSession(configuration: .default, delegate: self, delegateQueue: nil)
        defer { session.finishTasksAndInvalidate() }
        return try await session.upload(for: request, fromFile: file)
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didSendBodyData bytesSent: Int64, totalBytesSent: Int64, totalBytesExpectedToSend: Int64) {
        guard totalBytesExpectedToSend > 0 else { return }
        onProgress(Double(totalBytesSent) / Double(totalBytesExpectedToSend))
    }
}
