// WearablesViewModel.swift
//
// Tracks DAT registration state ("connect my glasses" -- linking this app to
// the glasses via Meta AI, a one-time step separate from camera permission).
// Trimmed from facebook/meta-wearables-dat-ios samples/CameraAccess
// (ViewModels/WearablesViewModel.swift) -- device-compatibility/firmware-update
// monitoring removed since this app doesn't need it.

import Foundation
import MWDATCore
import Observation

@Observable
@MainActor
final class WearablesViewModel {
    var registrationState: RegistrationState
    var showError = false
    var errorMessage = ""

    @ObservationIgnored private var registrationTask: Task<Void, Never>?
    private let wearables: WearablesInterface

    init(wearables: WearablesInterface) {
        self.wearables = wearables
        self.registrationState = wearables.registrationState

        registrationTask = Task {
            for await state in wearables.registrationStateStream() {
                self.registrationState = state
            }
        }
    }

    isolated deinit {
        registrationTask?.cancel()
    }

    func connectGlasses() {
        guard registrationState != .registering else { return }
        Task { @MainActor in
            do {
                try await wearables.startRegistration()
            } catch let error as RegistrationError {
                showError(error.description)
            } catch {
                showError(error.localizedDescription)
            }
        }
    }

    func showError(_ error: String) {
        errorMessage = error
        showError = true
    }
}
