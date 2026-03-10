import SwiftUI
import UIKit

struct SettingsView: View {
    @EnvironmentObject var settings: AppSettings
    @EnvironmentObject var api: APIClient
    @State private var serverInput = ""
    @State private var nameInput = ""
    @State private var isTesting = false
    @State private var testResult: Bool? = nil
    @State private var isRefreshing = false
    @State private var toast: String? = nil
    @State private var showNameSaved = false

    private var appVersion: String {
        let v = Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "1.0"
        let build = Bundle.main.infoDictionary?["CFBundleVersion"] as? String ?? "1"
        return "\(v) (\(build))"
    }

    var body: some View {
        NavigationStack {
            Form {
                // Profile
                Section("Profile") {
                    HStack {
                        Text("Name")
                        Spacer()
                        HStack(spacing: 6) {
                            TextField("Your name", text: $nameInput)
                                .multilineTextAlignment(.trailing)
                                .foregroundStyle(.secondary)
                                .onChange(of: nameInput) { _, new in
                                    settings.userName = new
                                    flashNameSaved()
                                }
                            if showNameSaved {
                                Image(systemName: "checkmark.circle.fill")
                                    .foregroundStyle(Color.green)
                                    .font(.system(size: 14))
                                    .transition(.scale.combined(with: .opacity))
                            }
                        }
                    }
                }

                // Server
                Section {
                    VStack(alignment: .leading, spacing: 8) {
                        HStack(spacing: 8) {
                            TextField("http://192.168.1.x:7700", text: $serverInput)
                                .font(.system(size: 14))
                                .keyboardType(.URL)
                                .autocapitalization(.none)
                                .autocorrectionDisabled()
                                .onSubmit { saveServer() }

                            // Paste button: appears when clipboard has a URL-looking string
                            if let clip = UIPasteboard.general.string,
                               (clip.hasPrefix("http://") || clip.hasPrefix("https://")),
                               serverInput != clip {
                                Button("Paste") {
                                    serverInput = clip
                                    saveServer()
                                }
                                .font(.system(size: 13, weight: .medium))
                                .foregroundStyle(.secondary)
                            }
                        }

                        if let result = testResult {
                            Label(
                                result ? "Connected" : "Connection failed",
                                systemImage: result ? "checkmark.circle.fill" : "xmark.circle.fill"
                            )
                            .font(.system(size: 12))
                            .foregroundStyle(result ? Color.green : Color.red)
                        }
                    }

                    Button {
                        Task { await testConnection() }
                    } label: {
                        HStack {
                            if isTesting { ProgressView() }
                            else { Text("Test Connection") }
                        }
                    }
                    .disabled(isTesting)
                } header: {
                    Text("Server URL")
                } footer: {
                    Text("Your Mac's local IP (e.g. http://192.168.1.10:7700) or a hosted URL.\nFind your IP: System Settings → Wi-Fi → Details")
                }

                // Preferences
                Section("Preferences") {
                    Toggle("Haptic feedback", isOn: $settings.hapticEnabled)
                }

                // Integrations
                Section("Integrations") {
                    IntegrationRow(name: "Google Calendar & Gmail", icon: "calendar.badge.clock", serverURL: settings.serverURL)
                    IntegrationRow(name: "Canvas LMS", icon: "graduationcap", serverURL: settings.serverURL)
                    IntegrationRow(name: "Readwise", icon: "books.vertical", serverURL: settings.serverURL)
                    IntegrationRow(name: "GoodNotes", icon: "pencil.and.outline", serverURL: settings.serverURL)
                }

                // Actions
                Section {
                    Button {
                        Task { await refreshKB() }
                    } label: {
                        HStack {
                            if isRefreshing { ProgressView() }
                            else {
                                Label("Refresh Knowledge Base", systemImage: "arrow.clockwise")
                            }
                        }
                    }
                    .disabled(isRefreshing)
                }

                // Reset
                Section {
                    Button(role: .destructive) {
                        settings.isOnboarded = false
                    } label: {
                        Text("Reset & Re-onboard")
                    }
                }

                // About
                Section("About") {
                    HStack {
                        Text("Version")
                        Spacer()
                        Text(appVersion)
                            .foregroundStyle(.secondary)
                    }
                    HStack {
                        Text("App")
                        Spacer()
                        Text("Neuron")
                            .foregroundStyle(.secondary)
                    }
                }
            }
            .navigationTitle("Settings")
            .navigationBarTitleDisplayMode(.inline)
            .onAppear {
                serverInput = settings.serverURL
                nameInput = settings.userName
            }
            .overlay(alignment: .bottom) {
                if let t = toast {
                    Text(t)
                        .font(.system(size: 14, weight: .medium))
                        .foregroundStyle(.white)
                        .padding(.horizontal, 18)
                        .padding(.vertical, 10)
                        .background(Color(UIColor.label))
                        .clipShape(Capsule())
                        .padding(.bottom, 24)
                        .transition(.move(edge: .bottom).combined(with: .opacity))
                }
            }
            .animation(.easeInOut(duration: 0.3), value: toast)
            .animation(.easeInOut(duration: 0.2), value: showNameSaved)
        }
    }

    private func flashNameSaved() {
        showNameSaved = true
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.2) {
            withAnimation { showNameSaved = false }
        }
    }

    private func saveServer() {
        settings.serverURL = serverInput.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private func testConnection() async {
        saveServer()
        isTesting = true
        testResult = nil
        do {
            // Use /health for fast connection test — /status can take 20+ seconds
            _ = try await api.health()
            testResult = true
        } catch {
            testResult = false
        }
        isTesting = false
    }

    private func refreshKB() async {
        isRefreshing = true
        do {
            try await api.refresh()
            showToast("Knowledge base refreshed")
        } catch {
            showToast("Refresh failed")
        }
        isRefreshing = false
    }

    private func showToast(_ msg: String) {
        toast = msg
        DispatchQueue.main.asyncAfter(deadline: .now() + 2.5) { toast = nil }
    }
}

struct IntegrationRow: View {
    let name: String
    let icon: String
    let serverURL: String

    var body: some View {
        HStack {
            Label(name, systemImage: icon)
                .font(.system(size: 14))
            Spacer()
            Image(systemName: "chevron.right")
                .font(.system(size: 11, weight: .medium))
                .foregroundStyle(.tertiary)
        }
    }
}
