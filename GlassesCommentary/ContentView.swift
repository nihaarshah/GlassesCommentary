//
//  ContentView.swift
//  GlassesCommentary
//
//  Created by Nihaar  Shah on 8/30/26.
//

import SwiftUI
import MWDATCore

struct ContentView: View {
    let wearables: WearablesInterface
    var wearablesViewModel: WearablesViewModel

    @State private var stream: GlassesCommentaryStream

    init(wearables: WearablesInterface, wearablesViewModel: WearablesViewModel) {
        self.wearables = wearables
        self.wearablesViewModel = wearablesViewModel
        self._stream = State(
            wrappedValue: GlassesCommentaryStream(
                wearables: wearables,
                endpoint: URL(string: "https://nihaarshah--glasses-commentary-commentary-caption.modal.run")!
            )
        )
    }

    var body: some View {
        if wearablesViewModel.registrationState == .registered {
            streamingView
        } else {
            VStack(spacing: 20) {
                Text("Glasses Commentary")
                    .font(.title2).bold()
                Text("You'll be redirected to the Meta AI app to confirm the connection.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                Button(
                    wearablesViewModel.registrationState == .registering
                        ? "Connecting…" : "Connect my glasses"
                ) {
                    wearablesViewModel.connectGlasses()
                }
                .buttonStyle(.borderedProminent)
                .disabled(wearablesViewModel.registrationState == .registering)
            }
            .padding()
        }
    }

    private var streamingView: some View {
        VStack(spacing: 24) {
            Text("Glasses Commentary")
                .font(.title2).bold()

            Text(stream.isStreaming ? "Streaming" : "Not connected")
                .foregroundStyle(.secondary)

            Text(stream.latestCommentary.isEmpty ? "Waiting for first frame…" : stream.latestCommentary)
                .multilineTextAlignment(.center)
                .padding()

            if let error = stream.lastError {
                Text(error)
                    .foregroundStyle(.red)
                    .font(.footnote)
            }

            Button(stream.isStreaming ? "Stop Streaming" : "Start Streaming") {
                if stream.isStreaming {
                    stream.stop()
                } else {
                    stream.start()
                }
            }
            .buttonStyle(.borderedProminent)
        }
        .padding()
    }
}

#Preview {
    ContentView(wearables: Wearables.shared, wearablesViewModel: WearablesViewModel(wearables: Wearables.shared))
}
