import SwiftUI
import UIKit

struct ContentView: View {
    @EnvironmentObject var api: APIClient
    @EnvironmentObject var settings: AppSettings
    @State private var selectedTab: Tab = .home

    enum Tab {
        case home, ask, library, search, sparks, practice, news, timeline
    }

    var body: some View {
        TabView(selection: $selectedTab) {
            HomeView()
                .tabItem { Label("Home", systemImage: selectedTab == .home ? "house.fill" : "house") }
                .tag(Tab.home)

            AskView()
                .tabItem { Label("Ask", systemImage: selectedTab == .ask ? "sparkles.square.filled.on.square" : "sparkles") }
                .tag(Tab.ask)

            LibraryView()
                .tabItem { Label("Library", systemImage: selectedTab == .library ? "books.vertical.fill" : "books.vertical") }
                .tag(Tab.library)

            SearchView()
                .tabItem { Label("Search", systemImage: selectedTab == .search ? "magnifyingglass.circle.fill" : "magnifyingglass") }
                .tag(Tab.search)

            SparksView()
                .tabItem { Label("Sparks", systemImage: selectedTab == .sparks ? "bolt.fill" : "bolt") }
                .tag(Tab.sparks)

            PracticeView()
                .tabItem { Label("Practice", systemImage: selectedTab == .practice ? "brain.fill" : "brain") }
                .tag(Tab.practice)
                .badge(settings.srsDueCount > 0 ? settings.srsDueCount : 0)

            NewsView()
                .tabItem { Label("News", systemImage: selectedTab == .news ? "newspaper.fill" : "newspaper") }
                .tag(Tab.news)

            TimelineView()
                .tabItem { Label("Timeline", systemImage: selectedTab == .timeline ? "clock.fill" : "clock") }
                .tag(Tab.timeline)
        }
        .tint(Color(hex: "#c1440e"))
        .onChange(of: selectedTab) { _, _ in
            if settings.hapticEnabled {
                UISelectionFeedbackGenerator().selectionChanged()
            }
        }
        .onChange(of: settings.pendingAskQuery) { _, newVal in
            if newVal != nil {
                withAnimation { selectedTab = .ask }
            }
        }
    }
}

// MARK: - String Extensions

extension String {
    /// Remove emoji and replacement characters that render as ? on some systems.
    func strippingEmoji() -> String {
        unicodeScalars.filter { scalar in
            // Keep basic ASCII, Latin, punctuation, math operators
            scalar.value < 0x2000 ||
            // Allow smart quotes and common typographic characters
            (scalar.value >= 0x2018 && scalar.value <= 0x201F) ||
            (scalar.value >= 0x2026 && scalar.value <= 0x2027)
        }.reduce(into: "") { $0.append(Character($1)) }
    }
}

// MARK: - Color Hex Extension

extension Color {
    init(hex: String) {
        let hex = hex.trimmingCharacters(in: CharacterSet.alphanumerics.inverted)
        var int: UInt64 = 0
        Scanner(string: hex).scanHexInt64(&int)
        let a, r, g, b: UInt64
        switch hex.count {
        case 3: (a, r, g, b) = (255, (int >> 8) * 17, (int >> 4 & 0xF) * 17, (int & 0xF) * 17)
        case 6: (a, r, g, b) = (255, int >> 16, int >> 8 & 0xFF, int & 0xFF)
        case 8: (a, r, g, b) = (int >> 24, int >> 16 & 0xFF, int >> 8 & 0xFF, int & 0xFF)
        default: (a, r, g, b) = (255, 0, 0, 0)
        }
        self.init(.sRGB, red: Double(r) / 255, green: Double(g) / 255, blue: Double(b) / 255, opacity: Double(a) / 255)
    }
}
