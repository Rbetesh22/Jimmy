import SwiftUI
import UserNotifications

@main
struct JimmyApp: App {
    @StateObject private var api = APIClient.shared
    @StateObject private var settings = AppSettings.shared
    @State private var serverReachable = true
    @State private var reachabilityError = ""
    @State private var reachabilityTimer: Timer? = nil
    @Environment(\.scenePhase) private var scenePhase

    // Streak milestone celebration triggered from foreground events
    @State private var showMilestone = false
    @State private var milestoneStreak = 0

    var body: some Scene {
        WindowGroup {
            if settings.isOnboarded {
                ZStack(alignment: .top) {
                    ContentView()
                        .environmentObject(api)
                        .environmentObject(settings)
                        .preferredColorScheme(.light)
                        .onAppear {
                            if settings.notificationsEnabled {
                                Task {
                                    await NotificationScheduler.shared.requestPermission()
                                    NotificationScheduler.shared.scheduleAll(
                                        userName: settings.userName,
                                        streak: settings.currentStreak,
                                        settings: settings
                                    )
                                    // User opened the app — streak is safe for today
                                    NotificationScheduler.shared.cancelStreakReminder()
                                }
                            }
                            // Start repeating reachability timer (every 30s)
                            reachabilityTimer = Timer.scheduledTimer(withTimeInterval: 30, repeats: true) { _ in
                                Task { await checkReachability() }
                            }
                            // Check for an unshown milestone (e.g. after a crash or cold start)
                            checkMilestone()
                        }
                        .onChange(of: settings.notificationsEnabled) { _, enabled in
                            if enabled {
                                Task {
                                    await NotificationScheduler.shared.requestPermission()
                                    NotificationScheduler.shared.scheduleAll(
                                        userName: settings.userName,
                                        streak: settings.currentStreak,
                                        settings: settings
                                    )
                                }
                            } else {
                                UNUserNotificationCenter.current().removeAllPendingNotificationRequests()
                            }
                        }
                        .onChange(of: scenePhase) { _, phase in
                            if phase == .active {
                                // App came to foreground — check streak milestone
                                checkMilestone()
                                // Refresh SRS due count for Practice tab badge
                                settings.syncSRSDue(api: api)
                            }
                        }
                        .fullScreenCover(isPresented: $showMilestone) {
                            // Reuse the same StreakMilestoneView defined in HomeView
                            StreakMilestoneView(streak: milestoneStreak) {
                                showMilestone = false
                            }
                        }

                    if !serverReachable {
                        HStack(spacing: 8) {
                            Image(systemName: "wifi.slash")
                                .font(.system(size: 12))
                            VStack(alignment: .leading, spacing: 2) {
                                Text(settings.serverURL)
                                    .font(.system(size: 10, weight: .medium, design: .monospaced))
                                if !reachabilityError.isEmpty {
                                    Text(reachabilityError)
                                        .font(.system(size: 10))
                                        .lineLimit(1)
                                }
                            }
                            Button("Retry") { Task { await checkReachability() } }
                                .font(.system(size: 13, weight: .semibold))
                        }
                        .foregroundStyle(.white)
                        .padding(.horizontal, 16)
                        .padding(.vertical, 8)
                        .background(Color.red.opacity(0.9))
                        .clipShape(Capsule())
                        .padding(.top, 8)
                        .transition(.move(edge: .top).combined(with: .opacity))
                        .animation(.spring(response: 0.4), value: serverReachable)
                    }
                }
                .task {
                    // Non-blocking background reachability check
                    Task {
                        await checkReachability()
                    }
                }
            } else {
                OnboardingView()
                    .environmentObject(api)
                    .environmentObject(settings)
                    .preferredColorScheme(.light)
            }
        }
    }

    @MainActor
    private func checkReachability() async {
        do {
            _ = try await api.health()
            withAnimation(.spring(response: 0.4)) {
                serverReachable = true
                reachabilityError = ""
            }
        } catch {
            withAnimation(.spring(response: 0.4)) {
                serverReachable = false
                reachabilityError = error.localizedDescription
            }
        }
    }

    /// Check if the current streak just reached a milestone that hasn't been shown yet.
    /// Shows the full-screen celebration overlay if so.
    @MainActor
    private func checkMilestone() {
        guard !showMilestone else { return }  // already showing
        if let milestone = settings.detectMilestone() {
            milestoneStreak = milestone
            showMilestone = true
        }
    }
}
