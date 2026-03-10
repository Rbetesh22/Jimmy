import SwiftUI
import UIKit
import UserNotifications

struct SettingsView: View {
    @EnvironmentObject var settings: AppSettings
    @EnvironmentObject var api: APIClient
    @State private var serverInput = ""
    @State private var nameInput = ""
    @State private var isTesting = false
    @State private var testResult: Bool? = nil
    @State private var isRefreshing = false
    @State private var toast: String? = nil
    @State private var toastIsError = false
    @State private var showNameSaved = false
    @State private var showClearCachesConfirm = false
    @State private var showResetStreakConfirm = false
    @State private var isPruning = false
    @State private var pruneResult: String? = nil
    @State private var isSendingTest = false
    @Environment(\.dismiss) private var dismiss

    private var isURLInvalid: Bool {
        !serverInput.isEmpty &&
        !serverInput.hasPrefix("http://") &&
        !serverInput.hasPrefix("https://")
    }

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

                // Usage stats
                if settings.totalQueriesAsked > 0 || settings.currentStreak > 0 {
                    Section("Activity") {
                        HStack(spacing: 0) {
                            ActivityStat(value: "\(settings.totalQueriesAsked)", label: "Questions")
                            Divider().frame(height: 36).padding(.horizontal, 20)
                            ActivityStat(value: "\(settings.totalNotesAdded)", label: "Saved")
                            Divider().frame(height: 36).padding(.horizontal, 20)
                            ActivityStat(value: "\(settings.currentStreak)", label: "Day streak")
                            Spacer()
                        }
                        .padding(.vertical, 4)
                    }
                }

                // Your Stats
                Section("Your Stats") {
                    HStack {
                        Text("Longest streak")
                        Spacer()
                        Text("\(settings.longestStreak) days")
                            .foregroundStyle(.secondary)
                    }
                    HStack {
                        Text("Total queries")
                        Spacer()
                        Text("\(settings.totalQueriesAsked)")
                            .foregroundStyle(.secondary)
                    }
                    HStack {
                        Text("Total notes added")
                        Spacer()
                        Text("\(settings.totalNotesAdded)")
                            .foregroundStyle(.secondary)
                    }
                }

                // Server
                Section {
                    VStack(alignment: .leading, spacing: 8) {
                        HStack(spacing: 8) {
                            TextField("http://192.168.5.6:7700", text: $serverInput)
                                .font(.system(size: 14))
                                .keyboardType(.URL)
                                .autocapitalization(.none)
                                .autocorrectionDisabled()
                                .onSubmit { saveServer() }

                            if let clip = UIPasteboard.general.string,
                               (clip.hasPrefix("http://") || clip.hasPrefix("https://")),
                               serverInput != clip {
                                Button("Paste") {
                                    serverInput = clip
                                    saveServer()
                                }
                                .font(.system(size: 13, weight: .medium))
                                .foregroundStyle(Color(hex: "#0071e3"))
                            }
                        }

                        if isURLInvalid {
                            Label("URL should start with http:// or https://", systemImage: "exclamationmark.triangle.fill")
                                .font(.system(size: 12))
                                .foregroundStyle(Color.yellow)
                                .transition(.opacity.combined(with: .move(edge: .top)))
                        } else if let result = testResult {
                            Label(
                                result ? "Connected ✓" : "Cannot reach server ✗",
                                systemImage: result ? "checkmark.circle.fill" : "xmark.circle.fill"
                            )
                            .font(.system(size: 12))
                            .foregroundStyle(result ? Color.green : Color.red)
                            .transition(.opacity)
                        }
                    }

                    Button {
                        Task { await testConnection() }
                    } label: {
                        HStack {
                            if isTesting { ProgressView() }
                            else {
                                Label("Test Connection", systemImage: "network")
                            }
                        }
                        .foregroundStyle(Color(hex: "#0071e3"))
                    }
                    .disabled(isTesting)
                } header: {
                    Text("Server URL")
                } footer: {
                    Text("Use your Mac's local IP (e.g. http://192.168.5.6:7700) or a hosted URL.\nFind your IP: System Settings \u{2192} Wi-Fi \u{2192} Details")
                }

                // Performance
                Section("Performance") {
                    VStack(alignment: .leading, spacing: 6) {
                        HStack {
                            Text("Request timeout")
                            Spacer()
                            Text("\(Int(settings.apiTimeout))s")
                                .foregroundStyle(.secondary)
                                .monospacedDigit()
                        }
                        Slider(value: $settings.apiTimeout, in: 15...120, step: 5)
                            .tint(Color(hex: "#0071e3"))
                    }
                }

                // Preferences
                Section("Preferences") {
                    HStack(spacing: 12) {
                        Image(systemName: "hand.tap")
                            .font(.system(size: 14))
                            .foregroundStyle(.white)
                            .frame(width: 28, height: 28)
                            .background(Color.gray)
                            .clipShape(RoundedRectangle(cornerRadius: 7))
                        Toggle("Haptic feedback", isOn: $settings.hapticEnabled)
                    }
                }

                // Notifications
                Section {
                    // Master toggle
                    HStack(spacing: 12) {
                        Image(systemName: "bell.badge")
                            .font(.system(size: 14))
                            .foregroundStyle(.white)
                            .frame(width: 28, height: 28)
                            .background(Color.red)
                            .clipShape(RoundedRectangle(cornerRadius: 7))
                        Toggle("Notifications", isOn: $settings.notificationsEnabled)
                            .onChange(of: settings.notificationsEnabled) { _, enabled in
                                if enabled {
                                    Task {
                                        let granted = await NotificationScheduler.shared.requestPermission()
                                        if granted {
                                            NotificationScheduler.shared.scheduleAll(
                                                userName: settings.userName,
                                                streak: settings.currentStreak,
                                                settings: settings
                                            )
                                        } else {
                                            await MainActor.run { settings.notificationsEnabled = false }
                                        }
                                    }
                                } else {
                                    NotificationScheduler.shared.cancelAll()
                                }
                            }
                    }

                    if settings.notificationsEnabled {
                        // Morning Briefing toggle + time picker
                        VStack(alignment: .leading, spacing: 0) {
                            HStack(spacing: 12) {
                                Image(systemName: "sun.horizon")
                                    .font(.system(size: 14))
                                    .foregroundStyle(.white)
                                    .frame(width: 28, height: 28)
                                    .background(Color.orange)
                                    .clipShape(RoundedRectangle(cornerRadius: 7))
                                Toggle("Morning Briefing", isOn: $settings.notifyMorningBriefing)
                                    .onChange(of: settings.notifyMorningBriefing) { _, _ in rescheduleAll() }
                            }
                            if settings.notifyMorningBriefing {
                                HStack {
                                    Text("Time")
                                        .font(.system(size: 14))
                                        .foregroundStyle(.secondary)
                                        .padding(.leading, 40)
                                    Spacer()
                                    // Build a binding to a Date whose hour matches morningBriefingHour
                                    let hourBinding = Binding<Date>(
                                        get: {
                                            var comps = Calendar.current.dateComponents([.year, .month, .day], from: Date())
                                            comps.hour   = settings.morningBriefingHour
                                            comps.minute = 0
                                            return Calendar.current.date(from: comps) ?? Date()
                                        },
                                        set: { newDate in
                                            settings.morningBriefingHour = Calendar.current.component(.hour, from: newDate)
                                            rescheduleAll()
                                        }
                                    )
                                    DatePicker("", selection: hourBinding, displayedComponents: .hourAndMinute)
                                        .labelsHidden()
                                }
                                .padding(.top, 8)
                            }
                        }
                        .padding(.vertical, 4)

                        // Study Reminders
                        HStack(spacing: 12) {
                            Image(systemName: "calendar.badge.exclamationmark")
                                .font(.system(size: 14))
                                .foregroundStyle(.white)
                                .frame(width: 28, height: 28)
                                .background(Color.blue)
                                .clipShape(RoundedRectangle(cornerRadius: 7))
                            Toggle("Study Reminders", isOn: $settings.notifyStudyReminder)
                                .onChange(of: settings.notifyStudyReminder) { _, _ in rescheduleAll() }
                        }

                        // Streak Reminders
                        HStack(spacing: 12) {
                            Image(systemName: "flame")
                                .font(.system(size: 14))
                                .foregroundStyle(.white)
                                .frame(width: 28, height: 28)
                                .background(Color(hex: "#e05e00"))
                                .clipShape(RoundedRectangle(cornerRadius: 7))
                            Toggle("Streak Reminders", isOn: $settings.notifyStreakReminder)
                                .onChange(of: settings.notifyStreakReminder) { _, _ in rescheduleAll() }
                        }

                        // SRS Reminders
                        HStack(spacing: 12) {
                            Image(systemName: "brain")
                                .font(.system(size: 14))
                                .foregroundStyle(.white)
                                .frame(width: 28, height: 28)
                                .background(Color.purple)
                                .clipShape(RoundedRectangle(cornerRadius: 7))
                            Toggle("SRS Card Reminders", isOn: $settings.notifySRSReminder)
                                .onChange(of: settings.notifySRSReminder) { _, _ in rescheduleAll() }
                        }

                        // Random Resurface
                        HStack(spacing: 12) {
                            Image(systemName: "sparkles")
                                .font(.system(size: 14))
                                .foregroundStyle(.white)
                                .frame(width: 28, height: 28)
                                .background(Color.teal)
                                .clipShape(RoundedRectangle(cornerRadius: 7))
                            Toggle("Random Resurface (2–4 PM)", isOn: $settings.notifyRandomResurface)
                                .onChange(of: settings.notifyRandomResurface) { _, _ in rescheduleAll() }
                        }

                        // Test notification button
                        Button {
                            isSendingTest = true
                            NotificationScheduler.shared.sendTestNotification()
                            DispatchQueue.main.asyncAfter(deadline: .now() + 0.8) {
                                isSendingTest = false
                                showToast("Test notification sent — check in 5 seconds", isError: false)
                            }
                        } label: {
                            HStack {
                                if isSendingTest { ProgressView() }
                                else { Label("Send test notification", systemImage: "paperplane") }
                            }
                            .foregroundStyle(Color(hex: "#0071e3"))
                        }
                        .disabled(isSendingTest)
                    }
                } header: {
                    Text("Notifications")
                } footer: {
                    if settings.notificationsEnabled {
                        Text("Streak reminders only fire if you haven't opened the app by 8 PM.")
                    }
                }

                // Integrations
                Section("Integrations") {
                    IntegrationRow(name: "Google Calendar & Gmail", icon: "calendar.badge.clock", color: .blue, serverURL: settings.serverURL)
                    IntegrationRow(name: "Canvas LMS", icon: "graduationcap", color: .orange, serverURL: settings.serverURL)
                    IntegrationRow(name: "Readwise", icon: "books.vertical", color: .purple, serverURL: settings.serverURL)
                    IntegrationRow(name: "GoodNotes", icon: "pencil.and.outline", color: .yellow, serverURL: settings.serverURL)
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
                        .foregroundStyle(Color(hex: "#0071e3"))
                    }
                    .disabled(isRefreshing)

                    Button {
                        Task { await pruneNoise() }
                    } label: {
                        HStack {
                            if isPruning { ProgressView() }
                            else { Label("Prune Noise", systemImage: "scissors") }
                        }
                        .foregroundStyle(Color.orange)
                    }
                    .disabled(isPruning)

                    if let result = pruneResult {
                        Text(result)
                            .font(.system(size: 12))
                            .foregroundStyle(.secondary)
                    }

                    Button {
                        showClearCachesConfirm = true
                    } label: {
                        Label("Clear all caches", systemImage: "trash")
                            .foregroundStyle(Color.orange)
                    }
                    .alert("Clear Caches?", isPresented: $showClearCachesConfirm) {
                        Button("Clear", role: .destructive) {
                            Task { await clearCaches() }
                        }
                        Button("Cancel", role: .cancel) {}
                    } message: {
                        Text("This will delete cached digest, sparks, and news data. Fresh data will load on next use.")
                    }
                }

                // Danger zone
                Section {
                    Button(role: .destructive) {
                        showResetStreakConfirm = true
                    } label: {
                        Label("Reset Streak", systemImage: "flame.slash")
                    }
                    .alert("Reset Streak?", isPresented: $showResetStreakConfirm) {
                        Button("Reset", role: .destructive) {
                            settings.currentStreak = 0
                            settings.longestStreak = 0
                            settings.lastActiveDate = nil
                        }
                        Button("Cancel", role: .cancel) {}
                    } message: {
                        Text("This will reset your current and longest streak to zero. This cannot be undone.")
                    }

                    Button(role: .destructive) {
                        settings.isOnboarded = false
                    } label: {
                        Label("Reset & Re-onboard", systemImage: "arrow.counterclockwise")
                    }
                } footer: {
                    Text("This will clear your onboarding state. Your library data is preserved on the server.")
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
                        Text("Built with")
                        Spacer()
                        HStack(spacing: 4) {
                            Image(systemName: "brain")
                                .font(.system(size: 12))
                                .foregroundStyle(Color(hex: "#0071e3"))
                            Text("Claude")
                                .foregroundStyle(.secondary)
                        }
                    }
                    Link(destination: URL(string: "mailto:feedback@neuron.app")!) {
                        HStack {
                            Label("Contact / Feedback", systemImage: "envelope")
                            Spacer()
                            Image(systemName: "arrow.up.right")
                                .font(.system(size: 11))
                                .foregroundStyle(.tertiary)
                        }
                    }
                    .foregroundStyle(.primary)
                }

                Section {
                    HStack {
                        Spacer()
                        Text("Neuron · v1.0")
                            .font(.system(size: 12))
                            .foregroundStyle(.tertiary)
                        Spacer()
                    }
                }
                .listRowBackground(Color.clear)
            }
            .navigationTitle("Settings")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }
                        .foregroundStyle(Color(hex: "#0071e3"))
                }
            }
            .onAppear {
                serverInput = settings.serverURL
                nameInput = settings.userName
            }
            .overlay(alignment: .bottom) {
                if let t = toast {
                    HStack(spacing: 8) {
                        Image(systemName: toastIsError ? "exclamationmark.circle.fill" : "checkmark.circle.fill")
                            .foregroundStyle(toastIsError ? Color.red : Color.green)
                            .font(.system(size: 14))
                        Text(t)
                            .font(.system(size: 14, weight: .medium))
                            .foregroundStyle(.white)
                    }
                    .padding(.horizontal, 18)
                    .padding(.vertical, 10)
                    .background(Color(UIColor.label))
                    .clipShape(Capsule())
                    .padding(.bottom, 24)
                    .transition(.move(edge: .bottom).combined(with: .opacity))
                    .shadow(color: Color.black.opacity(0.15), radius: 8, x: 0, y: 4)
                }
            }
            .animation(.spring(response: 0.35, dampingFraction: 0.85), value: toast)
            .animation(.easeInOut(duration: 0.2), value: showNameSaved)
            .animation(.easeInOut(duration: 0.2), value: testResult)
            .animation(.easeInOut(duration: 0.2), value: isURLInvalid)
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
            _ = try await api.health()
            testResult = true
            UINotificationFeedbackGenerator().notificationOccurred(.success)
        } catch {
            testResult = false
            UINotificationFeedbackGenerator().notificationOccurred(.error)
        }
        isTesting = false
    }

    private func refreshKB() async {
        isRefreshing = true
        do {
            try await api.refresh()
            showToast("Knowledge base refreshed", isError: false)
        } catch {
            showToast("Refresh failed", isError: true)
        }
        isRefreshing = false
    }

    private func pruneNoise() async {
        isPruning = true
        pruneResult = nil
        do {
            let result = try await api.pruneNoise()
            let removed = result.removed ?? 0
            pruneResult = removed > 0 ? "Pruned \(removed) low-value items" : "Nothing to prune"
            showToast(pruneResult!, isError: false)
        } catch {
            showToast("Prune failed", isError: true)
        }
        isPruning = false
    }

    private func clearCaches() async {
        let fm = FileManager.default
        let urls = fm.urls(for: .cachesDirectory, in: .userDomainMask)
        guard let cacheBase = urls.first else { return }
        let cacheDir = cacheBase.appendingPathComponent("neuron")
        do {
            let files = try fm.contentsOfDirectory(at: cacheDir, includingPropertiesForKeys: nil)
            let jsonFiles = files.filter { $0.pathExtension == "json" }
            for file in jsonFiles {
                try? fm.removeItem(at: file)
            }
            showToast("Caches cleared", isError: false)
        } catch {
            showToast("Could not clear caches", isError: true)
        }
    }

    private func rescheduleAll() {
        guard settings.notificationsEnabled else { return }
        Task {
            let granted = await NotificationScheduler.shared.requestPermission()
            guard granted else { return }
            NotificationScheduler.shared.scheduleAll(
                userName: settings.userName,
                streak: settings.currentStreak,
                settings: settings
            )
        }
    }

    private func showToast(_ msg: String, isError: Bool) {
        toastIsError = isError
        toast = msg
        DispatchQueue.main.asyncAfter(deadline: .now() + 2.5) { toast = nil }
    }
}

// MARK: - Activity Stat

struct ActivityStat: View {
    let value: String
    let label: String

    var body: some View {
        VStack(alignment: .leading, spacing: 1) {
            Text(value)
                .font(.system(size: 22, weight: .bold, design: .rounded))
                .foregroundStyle(Color(hex: "#0071e3"))
            Text(label)
                .font(.system(size: 11))
                .foregroundStyle(.secondary)
        }
        .contentTransition(.numericText())
    }
}

// MARK: - Integration Row

struct IntegrationRow: View {
    let name: String
    let icon: String
    let color: Color
    let serverURL: String

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: icon)
                .font(.system(size: 14))
                .foregroundStyle(color)
                .frame(width: 28, height: 28)
                .background(color.opacity(0.12))
                .clipShape(RoundedRectangle(cornerRadius: 7))

            Text(name)
                .font(.system(size: 14))
            Spacer()
            Image(systemName: "chevron.right")
                .font(.system(size: 11, weight: .medium))
                .foregroundStyle(.tertiary)
        }
    }
}
