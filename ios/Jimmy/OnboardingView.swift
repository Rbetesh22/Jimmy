import SwiftUI
import UIKit

struct OnboardingView: View {
    @EnvironmentObject var settings: AppSettings
    @State private var step = 0

    var body: some View {
        ZStack(alignment: .bottom) {
            switch step {
            case 0:
                ServerSetupStep(onNext: {
                    withAnimation(.spring(response: 0.45, dampingFraction: 0.85)) { step = 1 }
                }, onSkip: {
                    withAnimation(.spring(response: 0.45, dampingFraction: 0.85)) { step = 1 }
                })
                .transition(.asymmetric(
                    insertion: .move(edge: .trailing).combined(with: .opacity),
                    removal: .move(edge: .leading).combined(with: .opacity)
                ))
            case 1:
                WelcomeStep(onFinish: {
                    withAnimation(.easeInOut(duration: 0.4)) {
                        settings.isOnboarded = true
                    }
                })
                .transition(.asymmetric(
                    insertion: .move(edge: .trailing).combined(with: .opacity),
                    removal: .move(edge: .leading).combined(with: .opacity)
                ))
            default:
                EmptyView()
            }

            // Step dots indicator
            HStack(spacing: 7) {
                ForEach(0..<2, id: \.self) { i in
                    Capsule()
                        .fill(i == step ? Color(hex: "#0071e3") : Color(UIColor.systemGray4))
                        .frame(width: i == step ? 20 : 7, height: 7)
                        .animation(.spring(response: 0.35, dampingFraction: 0.8), value: step)
                }
            }
            .padding(.bottom, 44)
        }
        .animation(.spring(response: 0.45, dampingFraction: 0.85), value: step)
    }
}

// MARK: - Step 1: Server URL

struct ServerSetupStep: View {
    @EnvironmentObject var settings: AppSettings
    let onNext: () -> Void
    let onSkip: () -> Void

    @State private var serverInput = ""
    @State private var isTesting = false
    @State private var testResult: Bool? = nil
    @State private var appeared = false

    var body: some View {
        ZStack(alignment: .topTrailing) {
            // Skip button
            Button {
                onSkip()
            } label: {
                Text("Skip")
                    .font(.system(size: 15))
                    .foregroundStyle(.secondary)
                    .padding(.horizontal, 16)
                    .padding(.vertical, 10)
            }
            .padding(.top, 8)
            .zIndex(1)

        VStack(spacing: 0) {
            Spacer()

            VStack(spacing: 36) {
                // Logo with animation
                VStack(spacing: 16) {
                    JimmyLogoView(size: 80)
                        .scaleEffect(appeared ? 1.0 : 0.5)
                        .opacity(appeared ? 1.0 : 0)
                        .animation(.spring(response: 0.6, dampingFraction: 0.7).delay(0.1), value: appeared)

                    VStack(spacing: 8) {
                        Text("Welcome to Jimmy")
                            .font(.system(size: 28, weight: .bold))
                            .tracking(-0.5)
                            .opacity(appeared ? 1 : 0)
                            .offset(y: appeared ? 0 : 10)
                            .animation(.spring(response: 0.5, dampingFraction: 0.8).delay(0.2), value: appeared)

                        Text("Your personal second brain.")
                            .font(.system(size: 16))
                            .foregroundStyle(.secondary)
                            .opacity(appeared ? 1 : 0)
                            .offset(y: appeared ? 0 : 10)
                            .animation(.spring(response: 0.5, dampingFraction: 0.8).delay(0.28), value: appeared)
                    }
                }

                // Server URL input
                VStack(alignment: .leading, spacing: 8) {
                    Text("Server URL")
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundStyle(.secondary)

                    HStack(spacing: 10) {
                        Image(systemName: "server.rack")
                            .font(.system(size: 14))
                            .foregroundStyle(testResult == true ? Color.green : Color(UIColor.tertiaryLabel))

                        TextField("https://spine-multi-subsidiaries-projection.trycloudflare.com", text: $serverInput)
                            .font(.system(size: 15))
                            .keyboardType(.URL)
                            .autocapitalization(.none)
                            .autocorrectionDisabled()

                        if testResult == true {
                            Image(systemName: "checkmark.circle.fill")
                                .foregroundStyle(Color.green)
                                .font(.system(size: 18))
                                .transition(.scale.combined(with: .opacity))
                        }
                    }
                    .padding(14)
                    .background(Color(UIColor.secondarySystemGroupedBackground))
                    .clipShape(RoundedRectangle(cornerRadius: 12))
                    .overlay(
                        RoundedRectangle(cornerRadius: 12)
                            .stroke(borderColor, lineWidth: 1.5)
                    )
                    .animation(.spring(response: 0.3, dampingFraction: 0.8), value: testResult)

                    if let result = testResult, !result {
                        Label("Could not connect — check the address and make sure your server is running.", systemImage: "xmark.circle.fill")
                            .font(.system(size: 12))
                            .foregroundStyle(Color.red.opacity(0.9))
                            .lineSpacing(2)
                            .transition(.opacity.combined(with: .move(edge: .top)))
                    } else if testResult == nil {
                        VStack(alignment: .leading, spacing: 3) {
                            Text("Enter your Mac's local IP (e.g. https://spine-multi-subsidiaries-projection.trycloudflare.com), or a hosted URL (Railway, Fly.io, etc.)")
                                .font(.system(size: 12))
                                .foregroundStyle(.tertiary)
                            Text("Find your IP: System Settings \u{2192} Wi-Fi \u{2192} Details")
                                .font(.system(size: 11.5))
                                .foregroundStyle(.quaternary)
                        }
                    }
                }
                .padding(.horizontal, 24)
                .opacity(appeared ? 1 : 0)
                .offset(y: appeared ? 0 : 16)
                .animation(.spring(response: 0.5, dampingFraction: 0.8).delay(0.35), value: appeared)

                // Actions
                VStack(spacing: 10) {
                    Button {
                        Task { await testConnection() }
                    } label: {
                        HStack(spacing: 8) {
                            if isTesting {
                                ProgressView().tint(.white)
                            } else {
                                Image(systemName: "network")
                                    .font(.system(size: 14))
                                Text("Test Connection")
                                    .font(.system(size: 16, weight: .semibold))
                            }
                        }
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 15)
                        .background(serverInput.isEmpty ? Color(UIColor.systemGray4) : Color(UIColor.label))
                        .foregroundStyle(.white)
                        .clipShape(RoundedRectangle(cornerRadius: 14))
                    }
                    .disabled(serverInput.isEmpty || isTesting)
                    .animation(.easeInOut(duration: 0.2), value: serverInput.isEmpty)

                    if testResult == true {
                        Button {
                            onNext()
                        } label: {
                            HStack(spacing: 8) {
                                Text("Continue")
                                    .font(.system(size: 16, weight: .semibold))
                                Image(systemName: "arrow.right")
                                    .font(.system(size: 14, weight: .semibold))
                            }
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 15)
                            .background(Color(hex: "#0071e3"))
                            .foregroundStyle(.white)
                            .clipShape(RoundedRectangle(cornerRadius: 16))
                        }
                        .transition(.scale.combined(with: .opacity))
                    }
                }
                .padding(.horizontal, 24)
                .opacity(appeared ? 1 : 0)
                .animation(.spring(response: 0.5, dampingFraction: 0.8).delay(0.42), value: appeared)
            }

            Spacer()
            Spacer()
        }
        .background(Color(UIColor.systemGroupedBackground))
        .onAppear {
            serverInput = settings.serverURL
            appeared = true
        }
        .animation(.spring(response: 0.3, dampingFraction: 0.8), value: testResult)
        } // end ZStack
    }

    private var borderColor: Color {
        if let result = testResult {
            return result ? Color.green : Color.red.opacity(0.6)
        }
        return Color(UIColor.separator)
    }

    private func testConnection() async {
        let url = serverInput.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !url.isEmpty else { return }
        isTesting = true
        testResult = nil
        settings.serverURL = url
        do {
            _ = try await APIClient.shared.health()
            testResult = true
            UINotificationFeedbackGenerator().notificationOccurred(.success)
        } catch {
            testResult = false
            UINotificationFeedbackGenerator().notificationOccurred(.error)
        }
        isTesting = false
    }
}

// MARK: - Step 2: Welcome / integrations

struct WelcomeStep: View {
    let onFinish: () -> Void
    @EnvironmentObject var settings: AppSettings
    @State private var appeared = false
    @State private var notificationsGranted = false

    private let integrations: [(String, String, String, Color)] = [
        ("Google Calendar & Gmail", "calendar.badge.clock", "Your schedule, emails, and context", .blue),
        ("Canvas LMS", "graduationcap", "Courses, assignments, and deadlines", .orange),
        ("Readwise", "books.vertical", "Highlights and articles you've saved", .purple),
        ("GoodNotes", "pencil.and.outline", "Handwritten notes from iCloud", .yellow),
    ]

    var body: some View {
        VStack(spacing: 0) {
            Spacer()

            VStack(spacing: 32) {
                VStack(spacing: 8) {
                    // Success checkmark
                    ZStack {
                        Circle()
                            .fill(Color.green.opacity(0.12))
                            .frame(width: 72, height: 72)
                        Image(systemName: "checkmark.circle.fill")
                            .font(.system(size: 40))
                            .foregroundStyle(Color.green)
                    }
                    .scaleEffect(appeared ? 1.0 : 0.3)
                    .opacity(appeared ? 1.0 : 0)
                    .animation(.spring(response: 0.5, dampingFraction: 0.65).delay(0.1), value: appeared)

                    VStack(spacing: 6) {
                        Text("You're connected!")
                            .font(.system(size: 26, weight: .bold))
                            .tracking(-0.4)
                            .opacity(appeared ? 1 : 0)
                            .offset(y: appeared ? 0 : 10)
                            .animation(.spring(response: 0.4, dampingFraction: 0.8).delay(0.2), value: appeared)

                        Text("Jimmy works best with integrations.\nConnect them anytime in the Library tab.")
                            .font(.system(size: 14))
                            .foregroundStyle(.secondary)
                            .multilineTextAlignment(.center)
                            .lineSpacing(3)
                            .opacity(appeared ? 1 : 0)
                            .offset(y: appeared ? 0 : 8)
                            .animation(.spring(response: 0.4, dampingFraction: 0.8).delay(0.28), value: appeared)
                    }
                }

                VStack(spacing: 8) {
                    ForEach(Array(integrations.enumerated()), id: \.element.0) { i, integration in
                        let (name, icon, desc, color) = integration
                        HStack(spacing: 14) {
                            Image(systemName: icon)
                                .font(.system(size: 16))
                                .foregroundStyle(color)
                                .frame(width: 36, height: 36)
                                .background(color.opacity(0.12))
                                .clipShape(RoundedRectangle(cornerRadius: 9))

                            VStack(alignment: .leading, spacing: 2) {
                                Text(name)
                                    .font(.system(size: 14, weight: .semibold))
                                Text(desc)
                                    .font(.system(size: 12))
                                    .foregroundStyle(.secondary)
                            }
                            Spacer()
                        }
                        .padding(14)
                        .background(Color(UIColor.secondarySystemGroupedBackground))
                        .clipShape(RoundedRectangle(cornerRadius: 12))
                        .opacity(appeared ? 1 : 0)
                        .offset(y: appeared ? 0 : 16)
                        .animation(.spring(response: 0.4, dampingFraction: 0.85).delay(0.3 + Double(i) * 0.07), value: appeared)
                    }
                }
                .padding(.horizontal, 24)

                VStack(spacing: 12) {
                    // Notification permission card
                    if !notificationsGranted {
                        VStack(spacing: 14) {
                            HStack(spacing: 14) {
                                Image(systemName: "bell.fill")
                                    .font(.system(size: 18))
                                    .foregroundStyle(Color(hex: "#0071e3"))
                                    .frame(width: 44, height: 44)
                                    .background(Color(hex: "#0071e3").opacity(0.1))
                                    .clipShape(RoundedRectangle(cornerRadius: 11))

                                VStack(alignment: .leading, spacing: 2) {
                                    Text("Get your daily briefing")
                                        .font(.system(size: 14, weight: .semibold))
                                    Text("Every morning at 8 AM — what's on your calendar, what to review, and what you've been learning.")
                                        .font(.system(size: 12))
                                        .foregroundStyle(.secondary)
                                        .lineSpacing(2)
                                }
                                Spacer()
                            }

                            Button {
                                Task {
                                    let granted = await NotificationScheduler.shared.requestPermission()
                                    if granted {
                                        notificationsGranted = true
                                        settings.notificationsEnabled = true
                                        NotificationScheduler.shared.scheduleAll(
                                            userName: settings.userName,
                                            streak: settings.currentStreak
                                        )
                                    }
                                }
                            } label: {
                                HStack(spacing: 8) {
                                    Image(systemName: "sun.horizon.fill")
                                        .font(.system(size: 15))
                                    Text("Enable Daily Briefing →")
                                        .font(.system(size: 15, weight: .semibold))
                                }
                                .frame(maxWidth: .infinity)
                                .padding(.vertical, 12)
                                .background(Color(hex: "#0071e3"))
                                .foregroundStyle(.white)
                                .clipShape(RoundedRectangle(cornerRadius: 16))
                            }

                            Button {
                                notificationsGranted = true   // skip — treat as dismissed
                            } label: {
                                Text("Maybe later")
                                    .font(.system(size: 13))
                                    .foregroundStyle(.tertiary)
                            }
                            .buttonStyle(.plain)
                        }
                        .padding(16)
                        .background(Color(UIColor.secondarySystemGroupedBackground))
                        .clipShape(RoundedRectangle(cornerRadius: 16))
                        .transition(.opacity.combined(with: .scale))
                    } else if settings.notificationsEnabled {
                        HStack(spacing: 8) {
                            Image(systemName: "checkmark.circle.fill")
                                .foregroundStyle(Color.green)
                            Text("Notifications enabled")
                                .font(.system(size: 15))
                                .foregroundStyle(.secondary)
                        }
                        .transition(.opacity.combined(with: .scale))
                    }

                    Button {
                        UIImpactFeedbackGenerator(style: .medium).impactOccurred()
                        onFinish()
                    } label: {
                        HStack(spacing: 8) {
                            Text("Start using Jimmy")
                                .font(.system(size: 16, weight: .semibold))
                            Image(systemName: "arrow.right")
                                .font(.system(size: 14, weight: .semibold))
                        }
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 15)
                        .background(Color(hex: "#0071e3"))
                        .foregroundStyle(.white)
                        .clipShape(RoundedRectangle(cornerRadius: 16))
                    }
                }
                .padding(.horizontal, 24)
                .opacity(appeared ? 1 : 0)
                .offset(y: appeared ? 0 : 16)
                .animation(.spring(response: 0.4, dampingFraction: 0.85).delay(0.6), value: appeared)
            }

            Spacer()
            Spacer()
        }
        .background(Color(UIColor.systemGroupedBackground))
        .onAppear { appeared = true }
    }
}

// MARK: - Jimmy Logo

struct JimmyLogoView: View {
    let size: CGFloat
    @State private var rotationAngle: Double = 0

    var body: some View {
        ZStack {
            // Outer glow
            Circle()
                .fill(Color(hex: "#0071e3").opacity(0.12))
                .frame(width: size * 1.2, height: size * 1.2)

            // Main logo
            RoundedRectangle(cornerRadius: size * 0.22)
                .fill(
                    LinearGradient(
                        colors: [Color(hex: "#5856d6"), Color(hex: "#0071e3")],
                        startPoint: .topLeading,
                        endPoint: .bottomTrailing
                    )
                )
                .frame(width: size, height: size)
                .shadow(color: Color(hex: "#5856d6").opacity(0.35), radius: 12, x: 0, y: 6)
                .overlay(
                    Image(systemName: "brain")
                        .font(.system(size: size * 0.4, weight: .medium))
                        .foregroundStyle(.white.opacity(0.95))
                )
        }
    }
}
