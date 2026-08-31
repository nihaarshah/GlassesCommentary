//
//  GlassesCommentaryApp.swift
//  GlassesCommentary
//
//  Created by Nihaar  Shah on 8/30/26.
//

import SwiftUI
import CoreData
import MWDATCore

@main
struct GlassesCommentaryApp: App {
    let persistenceController = PersistenceController.shared
    private let wearables: WearablesInterface
    @State private var wearablesViewModel: WearablesViewModel

    init() {
        do {
            try Wearables.configure()
        } catch {
            print("[Wearables] configure failed: \(error)")
        }

        let wearables = Wearables.shared
        self.wearables = wearables
        self._wearablesViewModel = State(wrappedValue: WearablesViewModel(wearables: wearables))
    }

    var body: some Scene {
        WindowGroup {
            ContentView(wearables: wearables, wearablesViewModel: wearablesViewModel)
                .environment(\.managedObjectContext, persistenceController.container.viewContext)
                .alert("Something went wrong", isPresented: $wearablesViewModel.showError) {
                    Button("OK") { wearablesViewModel.showError = false }
                } message: {
                    Text(wearablesViewModel.errorMessage)
                }
                // Required: Meta AI calls back into the app via glassescommentary://
                // after registration/permission approval. Without this, approvals
                // in Meta AI never reach the SDK and the flow hangs forever.
                .onOpenURL { url in
                    guard
                        let components = URLComponents(url: url, resolvingAgainstBaseURL: false),
                        components.queryItems?.contains(where: { $0.name == "metaWearablesAction" }) == true
                    else { return }
                    Task {
                        do {
                            _ = try await Wearables.shared.handleUrl(url)
                        } catch let error as RegistrationError {
                            wearablesViewModel.showError(error.description)
                        } catch {
                            wearablesViewModel.showError(error.localizedDescription)
                        }
                    }
                }
        }
    }
}
