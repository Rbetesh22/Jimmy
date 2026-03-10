import SwiftUI
import UIKit

struct HomeView: View {
    @EnvironmentObject var api: APIClient
    @EnvironmentObject var settings: AppSettings

    // Single /today response provides everything
    @State private var todayData: TodayResponse? = nil
    // Digest is also sourced from /today but kept separate for refresh support
    @State private var digestRaw: String = ""
    @State private var digestSections: [(title: String, body: String)] = []
    @State private var newsArticles: [NewsArticle] = []

    @State private var isLoading = true
    @State private var isRefreshingDigest = false
    @State private var showSettings = false
    @State private var showPractice = false
    @State private var studySessionExercises: [PracticeExercise] = []
    @State private var studySessionTopics: [String] = []
    @State private var preloadedPracticeTopic: String? = nil

    @State private var contentAppeared = false
    @State private var showStreakMilestone = false
    @State private var milestoneStreak = 0
    @State private var showVoiceMemo = false
    @State private var studyPlan: StudyPlanResponse? = nil

    private let milestoneThresholds = [3, 7, 14, 30, 60, 100]

    private var greeting: String {
        let h = Calendar.current.component(.hour, from: Date())
        let name = settings.userName.isEmpty ? "" : ", \(settings.userName)"
        if h >= 23 || h < 5 { return "Late night studying?\(name)" }
        if h < 12 { return "Good morning\(name)" }
        if h < 18 { return "Good afternoon\(name)" }
        return "Good evening\(name)"
    }

    private func checkStreakMilestone() {
        let streak = settings.currentStreak
        guard milestoneThresholds.contains(streak) else { return }
        let lastShown = UserDefaults.standard.integer(forKey: "lastMilestoneShown")
        guard streak != lastShown else { return }
        milestoneStreak = streak
        showStreakMilestone = true
        UserDefaults.standard.set(streak, forKey: "lastMilestoneShown")
    }

    // Derived helpers
    private var srsDueItems: [SRSDueItem] { todayData?.srs_due ?? [] }
    private var upcomingEvents: [UpcomingEvent] { todayData?.events ?? [] }
    private var suggestions: [String] { todayData?.suggestions ?? [] }

    // Exams in next 7 days
    private var upcomingExams: [UpcomingEvent] {
        upcomingEvents.filter { ev in
            let t = ev.title.lowercased()
            return t.contains("midterm") || t.contains("exam") || t.contains("test") || t.contains("quiz") || t.contains("final")
        }
    }

    // Next milestone for streak progress bar
    private var nextMilestone: Int {
        milestoneThresholds.first { $0 > settings.currentStreak } ?? (settings.currentStreak + 10)
    }
    private var prevMilestone: Int {
        milestoneThresholds.last { $0 < settings.currentStreak } ?? 0
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 0) {

                    // Greeting header
                    HStack(alignment: .top) {
                        VStack(alignment: .leading, spacing: 4) {
                            HStack(spacing: 8) {
                                Text(greeting)
                                    .font(.system(size: 28, weight: .bold))
                                    .tracking(-0.5)
                                if settings.currentStreak > 0 {
                                    HStack(spacing: 3) {
                                        Text("🔥")
                                            .font(.system(size: 20))
                                        Text("\(settings.currentStreak)")
                                            .font(.system(size: 15, weight: .bold))
                                            .foregroundStyle(.orange)
                                    }
                                    .padding(.horizontal, 8)
                                    .padding(.vertical, 3)
                                    .background(Color.orange.opacity(0.12))
                                    .clipShape(Capsule())
                                }
                            }
                            Text(Date().formatted(.dateTime.weekday(.wide).month(.wide).day()))
                                .font(.system(size: 14))
                                .foregroundStyle(.secondary)
                        }
                        Spacer()
                    }
                    .padding(.horizontal, 20)
                    .padding(.top, 16)
                    .padding(.bottom, 20)

                    if isLoading {
                        HomeSkeletonView()
                    } else if todayData == nil {
                        // Empty state
                        VStack(spacing: 24) {
                            Spacer().frame(height: 40)
                            Image(systemName: "brain")
                                .font(.system(size: 56))
                                .foregroundStyle(Color(hex: "#0071e3").opacity(0.5))
                            VStack(spacing: 8) {
                                Text("Nothing loaded yet")
                                    .font(.system(size: 20, weight: .semibold))
                                    .foregroundStyle(.primary)
                                Text("Pull down to refresh or tap below to reload your daily briefing.")
                                    .font(.system(size: 15))
                                    .foregroundStyle(.secondary)
                                    .multilineTextAlignment(.center)
                                    .padding(.horizontal, 32)
                            }
                            Button {
                                Task { await loadAll() }
                            } label: {
                                HStack(spacing: 8) {
                                    Image(systemName: "arrow.clockwise")
                                        .font(.system(size: 15, weight: .semibold))
                                    Text("Refresh")
                                        .font(.system(size: 16, weight: .semibold))
                                }
                                .foregroundStyle(.white)
                                .padding(.horizontal, 28)
                                .padding(.vertical, 14)
                                .background(Color(hex: "#0071e3"))
                                .clipShape(RoundedRectangle(cornerRadius: 14))
                            }
                            .buttonStyle(.plain)
                            Spacer().frame(height: 40)
                        }
                        .frame(maxWidth: .infinity)
                        .padding(.horizontal, 20)
                    } else {
                        // ── Quick Actions row ──────────────────────────────────
                        QuickActionsRow(
                            onAskAnything: { settings.pendingAskQuery = "What should I focus on today?" },
                            onPracticeOS: { launchPractice(topic: "Operating Systems") },
                            onPracticeNetworks: { launchPractice(topic: "Computer Networks") },
                            onReviewSRS: { Task { await startStudySession() } },
                            onAddNote: { settings.pendingAskQuery = "" }
                        )
                        .padding(.horizontal, 16)
                        .padding(.bottom, 16)
                        .opacity(contentAppeared ? 1 : 0)
                        .offset(y: contentAppeared ? 0 : 10)
                        .animation(.spring(response: 0.4, dampingFraction: 0.85).delay(0.03), value: contentAppeared)

                        // ── Exam Countdown — most urgent, shown at the top ──
                        if let exam = upcomingExams.first {
                            ExamCountdownCard(event: exam, onStudyNow: {
                                let course = extractCourse(from: exam.title)
                                launchPractice(topic: course)
                            })
                            .padding(.horizontal, 16)
                            .padding(.bottom, 14)
                            .opacity(contentAppeared ? 1 : 0)
                            .offset(y: contentAppeared ? 0 : 10)
                            .animation(.spring(response: 0.4, dampingFraction: 0.85).delay(0.05), value: contentAppeared)
                        }

                        // ── Daily Digest ──────────────────────────────────────
                        if !digestSections.isEmpty || !digestRaw.isEmpty {
                            DigestCard(
                                sections: digestSections,
                                raw: digestRaw,
                                isRefreshing: $isRefreshingDigest,
                                onRefresh: { await refreshDigest() }
                            )
                            .shadow(color: .black.opacity(0.04), radius: 8, x: 0, y: 2)
                            .padding(.horizontal, 16)
                            .padding(.bottom, 16)
                            .opacity(contentAppeared ? 1 : 0)
                            .offset(y: contentAppeared ? 0 : 12)
                            .animation(.spring(response: 0.45, dampingFraction: 0.85).delay(0.08), value: contentAppeared)
                        }

                        // ── SRS review due card ───────────────────────────────
                        if !srsDueItems.isEmpty {
                            Button {
                                Task { await startStudySession() }
                            } label: {
                                HStack(spacing: 12) {
                                    ZStack {
                                        Circle()
                                            .fill(Color.orange.opacity(0.15))
                                            .frame(width: 36, height: 36)
                                        Text("\(srsDueItems.count)")
                                            .font(.system(size: 15, weight: .bold))
                                            .foregroundStyle(.orange)
                                    }
                                    VStack(alignment: .leading, spacing: 2) {
                                        Text(srsDueItems.count == 1 ? "1 topic due — Study Today" : "\(srsDueItems.count) topics due — Study Today")
                                            .font(.system(size: 15, weight: .semibold))
                                            .foregroundStyle(.primary)
                                        Text(srsDueItems.prefix(3).map { $0.topic }.joined(separator: " · "))
                                            .font(.system(size: 12))
                                            .foregroundStyle(.secondary)
                                            .lineLimit(1)
                                    }
                                    Spacer()
                                    Image(systemName: "play.fill")
                                        .font(.system(size: 12))
                                        .foregroundStyle(.orange)
                                }
                                .padding(.horizontal, 14)
                                .padding(.vertical, 13)
                                .background(Color.orange.opacity(0.07))
                                .clipShape(RoundedRectangle(cornerRadius: 12))
                                .overlay(RoundedRectangle(cornerRadius: 12).stroke(Color.orange.opacity(0.28), lineWidth: 1))
                            }
                            .buttonStyle(.plain)
                            .padding(.horizontal, 16)
                            .padding(.bottom, 10)
                            .opacity(contentAppeared ? 1 : 0)
                            .animation(.easeInOut(duration: 0.2).delay(0.1), value: contentAppeared)
                        }

                        // ── Today's Study Plan ───────────────────────────────
                        if let plan = studyPlan, !plan.today_focus.isEmpty {
                            TodayStudyPlanCard(plan: plan, onStart: {
                                let topic = plan.today_topics.first ?? plan.today_focus
                                launchPractice(topic: topic)
                            })
                            .padding(.horizontal, 16)
                            .padding(.bottom, 14)
                            .opacity(contentAppeared ? 1 : 0)
                            .offset(y: contentAppeared ? 0 : 10)
                            .animation(.spring(response: 0.45, dampingFraction: 0.85).delay(0.11), value: contentAppeared)
                        }

                        // ── Streak Card ───────────────────────────────────────
                        if settings.currentStreak > 0 {
                            StreakCard(
                                streak: settings.currentStreak,
                                prevMilestone: prevMilestone,
                                nextMilestone: nextMilestone
                            )
                            .padding(.horizontal, 16)
                            .padding(.bottom, 14)
                            .opacity(contentAppeared ? 1 : 0)
                            .animation(.easeInOut(duration: 0.3).delay(0.12), value: contentAppeared)
                        }

                        // ── Upcoming Events (compact, max 3) ─────────────────
                        let nonExamEvents = upcomingEvents.filter { ev in
                            let t = ev.title.lowercased()
                            return !t.contains("midterm") && !t.contains("exam") && !t.contains("quiz") && !t.contains("final")
                        }
                        if !nonExamEvents.isEmpty {
                            UpcomingEventsCard(events: Array(nonExamEvents.prefix(3)))
                                .padding(.horizontal, 16)
                                .padding(.bottom, 14)
                                .opacity(contentAppeared ? 1 : 0)
                                .offset(y: contentAppeared ? 0 : 10)
                                .animation(.spring(response: 0.45, dampingFraction: 0.85).delay(0.15), value: contentAppeared)
                        }

                        // ── News carousel ─────────────────────────────────────
                        if !newsArticles.isEmpty {
                            VStack(alignment: .leading, spacing: 10) {
                                SectionHeader(title: "Today's World")
                                    .padding(.horizontal, 20)

                                ScrollView(.horizontal, showsIndicators: false) {
                                    HStack(spacing: 12) {
                                        ForEach(Array(newsArticles.prefix(8).enumerated()), id: \.element.id) { i, article in
                                            NewsCardCompact(article: article)
                                                .opacity(contentAppeared ? 1 : 0)
                                                .offset(x: contentAppeared ? 0 : 20)
                                                .animation(.spring(response: 0.45, dampingFraction: 0.85).delay(0.18 + Double(i) * 0.04), value: contentAppeared)
                                        }
                                    }
                                    .padding(.horizontal, 20)
                                    .padding(.vertical, 2)
                                }
                            }
                            .padding(.bottom, 20)
                        }

                        // ── Analogy of the Day ────────────────────────────────
                        if let analogy = todayData?.analogy {
                            Button {
                                let q = "What is the analogy between \(analogy.concept_a ?? "") and \(analogy.concept_b ?? "")? How does understanding one help understand the other?"
                                settings.pendingAskQuery = q
                            } label: {
                                SectionCard(title: "Analogy of the Day", icon: "arrow.left.arrow.right") {
                                    VStack(alignment: .leading, spacing: 8) {
                                        HStack(spacing: 8) {
                                            VStack(alignment: .leading, spacing: 2) {
                                                Text(analogy.domain_a?.uppercased() ?? "")
                                                    .font(.system(size: 9, weight: .bold))
                                                    .foregroundStyle(Color(hex: "#0071e3"))
                                                    .tracking(0.4)
                                                Text(analogy.concept_a ?? "")
                                                    .font(.system(size: 13, weight: .semibold))
                                                    .lineLimit(2)
                                            }
                                            .padding(8)
                                            .frame(maxWidth: .infinity, alignment: .leading)
                                            .background(Color(hex: "#0071e3").opacity(0.08))
                                            .clipShape(RoundedRectangle(cornerRadius: 8))

                                            Text("≈")
                                                .font(.system(size: 18, weight: .bold))
                                                .foregroundStyle(Color(hex: "#0071e3").opacity(0.5))

                                            VStack(alignment: .leading, spacing: 2) {
                                                Text(analogy.domain_b?.uppercased() ?? "")
                                                    .font(.system(size: 9, weight: .bold))
                                                    .foregroundStyle(Color.purple)
                                                    .tracking(0.4)
                                                Text(analogy.concept_b ?? "")
                                                    .font(.system(size: 13, weight: .semibold))
                                                    .lineLimit(2)
                                            }
                                            .padding(8)
                                            .frame(maxWidth: .infinity, alignment: .leading)
                                            .background(Color.purple.opacity(0.08))
                                            .clipShape(RoundedRectangle(cornerRadius: 8))
                                        }

                                        if let a = analogy.analogy, !a.isEmpty {
                                            Text(a)
                                                .font(.system(size: 13))
                                                .foregroundStyle(.secondary)
                                                .lineSpacing(3)
                                                .lineLimit(3)
                                        }

                                        Text("Tap to explore →")
                                            .font(.system(size: 12, weight: .medium))
                                            .foregroundStyle(Color(hex: "#0071e3"))
                                    }
                                }
                            }
                            .buttonStyle(.plain)
                            .padding(.horizontal, 16)
                            .padding(.bottom, 16)
                            .opacity(contentAppeared ? 1 : 0)
                            .offset(y: contentAppeared ? 0 : 12)
                            .animation(.spring(response: 0.45, dampingFraction: 0.85).delay(0.25), value: contentAppeared)
                        }

                        // ── Resurfaced Memory ─────────────────────────────────
                        if let resurface = todayData?.resurface, let text = resurface.result, !text.isEmpty {
                            Button {
                                let snippet = String(text.prefix(120))
                                settings.pendingAskQuery = "Tell me more about: \(snippet)"
                            } label: {
                                SectionCard(title: "From Your Memory", icon: "arrow.counterclockwise") {
                                    VStack(alignment: .leading, spacing: 8) {
                                        Text(cleanAIText(text))
                                            .font(.system(size: 14))
                                            .foregroundStyle(.primary)
                                            .lineSpacing(3)
                                            .lineLimit(4)

                                        HStack(spacing: 6) {
                                            if let period = resurface.period {
                                                Text(period.capitalized)
                                                    .font(.system(size: 10, weight: .semibold))
                                                    .foregroundStyle(.tertiary)
                                                    .tracking(0.3)
                                            }
                                            if let src = resurface.sources?.first?.source, !src.isEmpty {
                                                Text("· \(src.uppercased())")
                                                    .font(.system(size: 10))
                                                    .foregroundStyle(.tertiary)
                                            }
                                            Spacer()
                                            Text("Explore →")
                                                .font(.system(size: 12, weight: .medium))
                                                .foregroundStyle(Color(hex: "#0071e3"))
                                        }
                                        .padding(.top, 2)
                                    }
                                }
                            }
                            .buttonStyle(.plain)
                            .padding(.horizontal, 16)
                            .padding(.bottom, 16)
                            .opacity(contentAppeared ? 1 : 0)
                            .offset(y: contentAppeared ? 0 : 12)
                            .animation(.spring(response: 0.45, dampingFraction: 0.85).delay(0.28), value: contentAppeared)
                        }

                        // ── Daily fact + vocab ────────────────────────────────
                        if todayData?.fact != nil || todayData?.vocab != nil {
                            ViewThatFits(in: .horizontal) {
                                HStack(alignment: .top, spacing: 12) {
                                    dailyCards
                                }
                                .padding(.horizontal, 16)
                                .padding(.bottom, 16)

                                VStack(alignment: .leading, spacing: 12) {
                                    dailyCards
                                }
                                .padding(.horizontal, 16)
                                .padding(.bottom, 16)
                            }
                            .opacity(contentAppeared ? 1 : 0)
                            .offset(y: contentAppeared ? 0 : 12)
                            .animation(.spring(response: 0.45, dampingFraction: 0.85).delay(0.3), value: contentAppeared)
                        }

                        // ── Spark connection ──────────────────────────────────
                        if let spark = todayData?.spark {
                            SectionCard(title: "Connection", icon: "bolt") {
                                VStack(alignment: .leading, spacing: 6) {
                                    Text(spark.title ?? "")
                                        .font(.system(size: 15, weight: .semibold))
                                        .lineSpacing(2)
                                    if let conn = spark.connection {
                                        Text(conn.count > 200 ? String(conn.prefix(200)) + "…" : conn)
                                            .font(.system(size: 13.5))
                                            .foregroundStyle(.secondary)
                                            .lineSpacing(3)
                                    }
                                }
                            }
                            .padding(.horizontal, 16)
                            .padding(.bottom, 16)
                            .opacity(contentAppeared ? 1 : 0)
                            .offset(y: contentAppeared ? 0 : 12)
                            .animation(.spring(response: 0.45, dampingFraction: 0.85).delay(0.33), value: contentAppeared)
                        }

                        // ── Suggestion chips ──────────────────────────────────
                        if !suggestions.isEmpty {
                            VStack(alignment: .leading, spacing: 8) {
                                Text("Try asking…")
                                    .font(.system(size: 11, weight: .semibold))
                                    .foregroundStyle(.tertiary)
                                    .textCase(.uppercase)
                                    .tracking(0.6)
                                    .padding(.horizontal, 20)

                                ScrollView(.horizontal, showsIndicators: false) {
                                    HStack(spacing: 8) {
                                        ForEach(suggestions, id: \.self) { s in
                                            SuggestionChip(text: s) {
                                                settings.pendingAskQuery = s
                                            }
                                        }
                                    }
                                    .padding(.horizontal, 20)
                                    .padding(.vertical, 2)
                                }
                            }
                            .padding(.bottom, 16)
                            .opacity(contentAppeared ? 1 : 0)
                            .animation(.easeInOut(duration: 0.3).delay(0.35), value: contentAppeared)
                        }

                        // ── Learning History link ─────────────────────────────
                        NavigationLink(destination: TimelineView().environmentObject(api).environmentObject(settings)) {
                            HStack {
                                Image(systemName: "chart.bar.xaxis")
                                    .font(.system(size: 15))
                                    .foregroundStyle(Color(hex: "#0071e3"))
                                    .frame(width: 32)
                                Text("Learning History")
                                    .font(.system(size: 15))
                                    .foregroundStyle(.primary)
                                Spacer()
                                if settings.currentStreak > 0 {
                                    Text("\(settings.currentStreak) day streak")
                                        .font(.system(size: 13))
                                        .foregroundStyle(.secondary)
                                }
                                Image(systemName: "chevron.right")
                                    .font(.system(size: 12, weight: .semibold))
                                    .foregroundStyle(.tertiary)
                            }
                            .padding(14)
                            .background(Color(hex: "faf9f7"))
                            .clipShape(RoundedRectangle(cornerRadius: 12))
                        }
                        .buttonStyle(.plain)
                        .padding(.horizontal, 16)
                        .padding(.bottom, 32)
                        .opacity(contentAppeared ? 1 : 0)
                        .animation(.easeInOut(duration: 0.3).delay(0.38), value: contentAppeared)
                    }
                }
            }
            .background(Color(hex: "f5f0e8"))
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button { showPractice = true } label: {
                        Image(systemName: "graduationcap")
                            .font(.system(size: 16))
                            .foregroundStyle(Color(hex: "#0071e3"))
                    }
                }
                ToolbarItem(placement: .topBarTrailing) {
                    HStack(spacing: 16) {
                        Button { showVoiceMemo = true } label: {
                            Image(systemName: "mic")
                                .font(.system(size: 16))
                                .foregroundStyle(Color(hex: "#0071e3"))
                        }
                        Button { showSettings = true } label: {
                            Image(systemName: "gearshape")
                                .font(.system(size: 16))
                                .foregroundStyle(Color(hex: "#0071e3"))
                        }
                    }
                }
            }
            .sheet(isPresented: $showPractice, onDismiss: {
                studySessionExercises = []
                studySessionTopics = []
                preloadedPracticeTopic = nil
            }) {
                PracticeView(
                    preloadedExercises: studySessionExercises,
                    preloadedTopic: preloadedPracticeTopic ?? (studySessionTopics.isEmpty ? nil : studySessionTopics.joined(separator: ", "))
                )
                .environmentObject(api)
                .environmentObject(settings)
                .presentationDetents([.large])
                .presentationDragIndicator(.visible)
            }
            .sheet(isPresented: $showSettings) {
                SettingsView()
                    .environmentObject(AppSettings.shared)
                    .environmentObject(APIClient.shared)
                    .presentationDetents([.large])
                    .presentationDragIndicator(.visible)
            }
            .sheet(isPresented: $showVoiceMemo) {
                VoiceMemoSheet()
                    .environmentObject(api)
                    .environmentObject(settings)
                    .presentationDetents([.medium, .large])
                    .presentationDragIndicator(.visible)
            }
            .task { await loadAll() }
            .refreshable { await loadAll() }
            .onAppear { checkStreakMilestone() }
            .onChange(of: settings.currentStreak) { _, _ in checkStreakMilestone() }
            .fullScreenCover(isPresented: $showStreakMilestone) {
                StreakMilestoneView(streak: milestoneStreak, onDismiss: { showStreakMilestone = false })
            }
        }
    }

    @ViewBuilder
    private var dailyCards: some View {
        if let fact = todayData?.fact {
            DailyFactCard(fact: fact)
        }
        if let vocab = todayData?.vocab {
            DailyVocabCard(vocab: vocab)
        }
    }

    private func launchPractice(topic: String) {
        preloadedPracticeTopic = topic
        studySessionExercises = []
        studySessionTopics = [topic]
        showPractice = true
    }

    private func extractCourse(from title: String) -> String {
        let lower = title.lowercased()
        if lower.contains("operating") || lower.contains(" os ") || lower.contains("cs3281") { return "Operating Systems" }
        if lower.contains("network") || lower.contains("cs3281") { return "Computer Networks" }
        if lower.contains("algorithm") || lower.contains("algo") { return "Algorithms" }
        if lower.contains("account") { return "Accounting" }
        // Extract before midterm/exam keywords
        let keywords = ["midterm", "exam", "final", "quiz", "test"]
        for kw in keywords {
            if let range = lower.range(of: kw) {
                let prefix = String(title[..<range.lowerBound]).trimmingCharacters(in: .whitespacesAndNewlines)
                if !prefix.isEmpty { return prefix }
            }
        }
        return title
    }

    // ONE call: /today provides everything. News still loads lazily in background.
    private func loadAll() async {
        isLoading = true
        contentAppeared = false
        newsArticles = []
        todayData = nil
        digestRaw = ""
        digestSections = []
        studyPlan = nil

        let todayResult = try? await api.today()

        if let today = todayResult {
            todayData = today

            // Populate digest from /today
            if let digestText = today.digest, !digestText.isEmpty {
                digestRaw = digestText
                digestSections = parseDigestSections(digestText)
            }

            // Schedule SRS notifications from /today's srs_due field
            let dueItems = today.srs_due ?? []
            if !dueItems.isEmpty {
                let topics = dueItems.map { $0.topic }
                NotificationScheduler.shared.scheduleSRSReminder(dueTopics: topics)
            } else {
                NotificationScheduler.shared.cancelSRSReminder()
            }
        } else {
            // Fallback: separately fetch digest if /today failed
            if let digest = try? await api.digest() {
                digestRaw = digest.result
                digestSections = parseDigestSections(digest.result)
            }
            NotificationScheduler.shared.cancelSRSReminder()
        }

        isLoading = false
        withAnimation { contentAppeared = true }

        // News loads lazily — doesn't block main content
        Task {
            if let news = try? await api.news() {
                let articles = news.articles
                await MainActor.run {
                    withAnimation(.easeInOut(duration: 0.3)) {
                        newsArticles = Array(articles.prefix(8))
                    }
                }
            }
        }

        // Study plan loads lazily — cached 2h on server, fast
        Task {
            if let plan = try? await api.studyPlan() {
                await MainActor.run {
                    withAnimation(.easeInOut(duration: 0.3)) {
                        studyPlan = plan
                    }
                }
            }
        }
    }

    private func startStudySession() async {
        guard let session = try? await api.studySession(), !session.exercises.isEmpty else {
            showPractice = true
            return
        }
        studySessionExercises = session.exercises
        studySessionTopics = session.topics
        showPractice = true
    }

    private func refreshDigest() async {
        isRefreshingDigest = true
        if let result = try? await api.digest(refresh: true) {
            digestRaw = result.result
            digestSections = parseDigestSections(result.result)
            // Patch todayData with fresh digest text
            if let old = todayData {
                // We can't mutate a let struct directly, so rebuild is not possible without copy
                // The refreshed text is stored in digestRaw/digestSections which DigestCard uses directly
                _ = old
            }
        }
        isRefreshingDigest = false
    }

    private func parseDigestSections(_ text: String) -> [(title: String, body: String)] {
        let pattern = #"##\s*(.+?)\n([\s\S]*?)(?=\n##|\z)"#
        guard let regex = try? NSRegularExpression(pattern: pattern) else { return [] }
        let range = NSRange(text.startIndex..., in: text)
        let matches = regex.matches(in: text, range: range)
        return matches.compactMap { match -> (String, String)? in
            guard let titleRange = Range(match.range(at: 1), in: text),
                  let bodyRange = Range(match.range(at: 2), in: text) else { return nil }
            var rawTitle = String(text[titleRange]).trimmingCharacters(in: .whitespacesAndNewlines)
            rawTitle = rawTitle.replacingOccurrences(of: #"^#+\s*"#, with: "", options: .regularExpression)
            let title = rawTitle.trimmingCharacters(in: .whitespacesAndNewlines)
            let body = cleanForDisplay(String(text[bodyRange]))
            guard !body.isEmpty else { return nil }
            return (title, body)
        }
    }

    private func cleanForDisplay(_ text: String) -> String {
        var s = text
        s = s.replacingOccurrences(of: #"\s*\[\d+(?:,\s*\d+)*\]"#, with: "", options: .regularExpression)
        s = s.replacingOccurrences(of: #"(?m)^[\-\*]\s+"#, with: "• ", options: .regularExpression)
        s = s.replacingOccurrences(of: #"\n{3,}"#, with: "\n\n", options: .regularExpression)
        s = s.strippingEmoji()
        return s.trimmingCharacters(in: .whitespacesAndNewlines)
    }
}

// MARK: - Quick Actions Row

struct QuickActionsRow: View {
    let onAskAnything: () -> Void
    let onPracticeOS: () -> Void
    let onPracticeNetworks: () -> Void
    let onReviewSRS: () -> Void
    let onAddNote: () -> Void

    private struct ActionChip: Identifiable {
        let id = UUID()
        let label: String
        let icon: String
        let color: Color
        let action: () -> Void
    }

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                chipButton(label: "Ask anything", icon: "bubble.left", color: Color(hex: "#0071e3"), action: onAskAnything)
                chipButton(label: "Practice OS", icon: "cpu", color: Color(hex: "#5856d6"), action: onPracticeOS)
                chipButton(label: "Practice Networks", icon: "network", color: Color(hex: "#34c759"), action: onPracticeNetworks)
                chipButton(label: "Review SRS", icon: "repeat", color: Color.orange, action: onReviewSRS)
                chipButton(label: "Add Note", icon: "square.and.pencil", color: Color(hex: "#ff9500"), action: onAddNote)
            }
            .padding(.vertical, 2)
        }
    }

    @ViewBuilder
    private func chipButton(label: String, icon: String, color: Color, action: @escaping () -> Void) -> some View {
        Button {
            UIImpactFeedbackGenerator(style: .light).impactOccurred()
            action()
        } label: {
            HStack(spacing: 6) {
                Image(systemName: icon)
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(color)
                Text(label)
                    .font(.system(size: 13, weight: .medium))
                    .foregroundStyle(.primary)
            }
            .padding(.horizontal, 13)
            .padding(.vertical, 9)
            .background(color.opacity(0.1))
            .clipShape(Capsule())
            .overlay(Capsule().stroke(color.opacity(0.2), lineWidth: 1))
        }
        .buttonStyle(.plain)
    }
}

// MARK: - Exam Countdown Card

struct ExamCountdownCard: View {
    let event: UpcomingEvent
    let onStudyNow: () -> Void

    private var daysRemaining: Int {
        guard let dateStr = event.date else { return 0 }
        let formats = ["yyyy-MM-dd", "MMM d, yyyy", "MMMM d, yyyy", "EEE MMM d yyyy"]
        let formatter = DateFormatter()
        for fmt in formats {
            formatter.dateFormat = fmt
            if let d = formatter.date(from: dateStr) {
                return Calendar.current.dateComponents([.day], from: Calendar.current.startOfDay(for: Date()), to: Calendar.current.startOfDay(for: d)).day ?? 0
            }
        }
        return 0
    }

    private var urgencyColor: Color {
        let days = daysRemaining
        if days <= 1 { return .red }
        if days <= 3 { return Color(hex: "#ff6b00") }
        return .orange
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 8) {
                ZStack {
                    RoundedRectangle(cornerRadius: 8)
                        .fill(urgencyColor.opacity(0.15))
                        .frame(width: 36, height: 36)
                    Image(systemName: "graduationcap.fill")
                        .font(.system(size: 16))
                        .foregroundStyle(urgencyColor)
                }

                VStack(alignment: .leading, spacing: 2) {
                    Text("EXAM COUNTDOWN")
                        .font(.system(size: 9, weight: .bold))
                        .foregroundStyle(urgencyColor)
                        .tracking(0.6)
                    Text(event.title)
                        .font(.system(size: 15, weight: .semibold))
                        .lineLimit(2)
                }

                Spacer()

                VStack(alignment: .trailing, spacing: 0) {
                    Text("\(max(0, daysRemaining))")
                        .font(.system(size: 28, weight: .bold, design: .rounded))
                        .foregroundStyle(urgencyColor)
                    Text(daysRemaining == 1 ? "day" : "days")
                        .font(.system(size: 11))
                        .foregroundStyle(urgencyColor.opacity(0.7))
                }
            }

            if let cal = event.calendar, !cal.isEmpty {
                Text(cal)
                    .font(.system(size: 12))
                    .foregroundStyle(.secondary)
            }

            Button(action: onStudyNow) {
                HStack(spacing: 6) {
                    Image(systemName: "play.fill")
                        .font(.system(size: 11))
                    Text("Study Now")
                        .font(.system(size: 14, weight: .semibold))
                }
                .foregroundStyle(.white)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 11)
                .background(urgencyColor)
                .clipShape(RoundedRectangle(cornerRadius: 10))
            }
            .buttonStyle(.plain)
        }
        .padding(16)
        .background(urgencyColor.opacity(0.06))
        .clipShape(RoundedRectangle(cornerRadius: 14))
        .overlay(RoundedRectangle(cornerRadius: 14).stroke(urgencyColor.opacity(0.25), lineWidth: 1))
        .shadow(color: urgencyColor.opacity(0.08), radius: 6, x: 0, y: 2)
    }
}

// MARK: - Streak Card

struct StreakCard: View {
    let streak: Int
    let prevMilestone: Int
    let nextMilestone: Int

    private var progress: Double {
        let range = Double(nextMilestone - prevMilestone)
        guard range > 0 else { return 1 }
        return Double(streak - prevMilestone) / range
    }

    var body: some View {
        HStack(spacing: 14) {
            ZStack {
                Circle()
                    .fill(Color.orange.opacity(0.15))
                    .frame(width: 44, height: 44)
                Image(systemName: "flame.fill")
                    .font(.system(size: 20))
                    .foregroundStyle(.orange)
            }

            VStack(alignment: .leading, spacing: 6) {
                HStack(spacing: 6) {
                    Text("\(streak) day streak")
                        .font(.system(size: 15, weight: .bold))
                        .foregroundStyle(.primary)
                    Spacer()
                    Text("\(nextMilestone - streak) to go")
                        .font(.system(size: 12))
                        .foregroundStyle(.secondary)
                }

                // Progress bar to next milestone
                GeometryReader { geo in
                    ZStack(alignment: .leading) {
                        RoundedRectangle(cornerRadius: 3)
                            .fill(Color.orange.opacity(0.15))
                            .frame(height: 5)
                        RoundedRectangle(cornerRadius: 3)
                            .fill(Color.orange)
                            .frame(width: geo.size.width * min(1, max(0, progress)), height: 5)
                    }
                }
                .frame(height: 5)

                Text("Next milestone: \(nextMilestone) days")
                    .font(.system(size: 11))
                    .foregroundStyle(.tertiary)
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 13)
        .background(Color.orange.opacity(0.06))
        .clipShape(RoundedRectangle(cornerRadius: 14))
        .overlay(RoundedRectangle(cornerRadius: 14).stroke(Color.orange.opacity(0.22), lineWidth: 1))
    }
}

// MARK: - Upcoming Events Card

struct UpcomingEventsCard: View {
    let events: [UpcomingEvent]

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Label("Upcoming", systemImage: "calendar")
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(.tertiary)
                    .textCase(.uppercase)
                    .tracking(0.6)
                Spacer()
            }
            .padding(.horizontal, 14)
            .padding(.top, 14)
            .padding(.bottom, 10)

            ForEach(Array(events.enumerated()), id: \.offset) { idx, event in
                HStack(spacing: 10) {
                    RoundedRectangle(cornerRadius: 2)
                        .fill(Color(hex: "#0071e3"))
                        .frame(width: 3, height: 32)

                    VStack(alignment: .leading, spacing: 1) {
                        Text(event.title)
                            .font(.system(size: 13, weight: .medium))
                            .lineLimit(1)
                        if let date = event.date {
                            Text(date)
                                .font(.system(size: 11))
                                .foregroundStyle(.secondary)
                        }
                    }
                    Spacer()
                    if let cal = event.calendar {
                        Text(cal)
                            .font(.system(size: 10))
                            .foregroundStyle(.tertiary)
                            .lineLimit(1)
                    }
                }
                .padding(.horizontal, 14)
                .padding(.vertical, 8)

                if idx < events.count - 1 {
                    Divider().padding(.leading, 14)
                }
            }

            Spacer().frame(height: 10)
        }
        .background(Color(hex: "faf9f7"))
        .clipShape(RoundedRectangle(cornerRadius: 14))
        .shadow(color: .black.opacity(0.04), radius: 8, x: 0, y: 2)
    }
}


// MARK: - Streak Milestone

struct StreakMilestoneView: View {
    let streak: Int
    let onDismiss: () -> Void
    @State private var appeared = false

    private var milestoneTitle: String {
        switch streak {
        case 3: return "3 days in a row!"
        case 7: return "One week streak!"
        case 14: return "Two weeks strong!"
        case 30: return "30 day milestone!"
        case 60: return "Two months!"
        case 100: return "100 days!"
        default: return "\(streak) day streak!"
        }
    }

    var body: some View {
        ZStack {
            // Dark gradient background
            LinearGradient(colors: [Color(hex: "#0d0d1a"), Color(hex: "#0d0d2a")], startPoint: .top, endPoint: .bottom)
                .ignoresSafeArea()

            VStack(spacing: 32) {
                Spacer()

                // Flame icon
                Image(systemName: "flame.fill")
                    .font(.system(size: 72))
                    .foregroundStyle(
                        LinearGradient(colors: [.orange, .red], startPoint: .top, endPoint: .bottom)
                    )
                    .scaleEffect(appeared ? 1.0 : 0.3)
                    .animation(.spring(response: 0.5, dampingFraction: 0.6).delay(0.1), value: appeared)

                // Streak number
                Text("\(streak)")
                    .font(.system(size: 96, weight: .bold, design: .rounded))
                    .foregroundStyle(.white)
                    .contentTransition(.numericText())
                    .opacity(appeared ? 1 : 0)
                    .animation(.easeInOut(duration: 0.4).delay(0.25), value: appeared)

                VStack(spacing: 8) {
                    Text(milestoneTitle)
                        .font(.system(size: 28, weight: .bold))
                        .foregroundStyle(.white)
                    Text("Keep building your second brain")
                        .font(.system(size: 16))
                        .foregroundStyle(.white.opacity(0.6))
                }
                .opacity(appeared ? 1 : 0)
                .offset(y: appeared ? 0 : 20)
                .animation(.easeInOut(duration: 0.4).delay(0.35), value: appeared)

                Spacer()

                // Share + Continue buttons
                VStack(spacing: 12) {
                    ShareLink(item: "I'm on a \(streak)-day learning streak with Neuron — my AI second brain. 🔥") {
                        HStack(spacing: 8) {
                            Image(systemName: "square.and.arrow.up")
                            Text("Share milestone")
                        }
                        .font(.system(size: 16, weight: .semibold))
                        .foregroundStyle(.white)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 16)
                        .background(Color(hex: "#0071e3"))
                        .clipShape(RoundedRectangle(cornerRadius: 14))
                    }

                    Button("Continue") {
                        onDismiss()
                    }
                    .font(.system(size: 16))
                    .foregroundStyle(.white.opacity(0.6))
                    .padding(.vertical, 8)
                }
                .padding(.horizontal, 32)
                .padding(.bottom, 48)
                .opacity(appeared ? 1 : 0)
                .animation(.easeInOut(duration: 0.4).delay(0.5), value: appeared)
            }
        }
        .onAppear {
            appeared = true
            UINotificationFeedbackGenerator().notificationOccurred(.success)
        }
    }
}

// MARK: - Text cleaning

/// Strips AI citation markers and converts markdown list syntax for clean SwiftUI display.
func cleanAIText(_ text: String) -> String {
    var s = text
    s = s.replacingOccurrences(of: #"\s*\[\d+(?:,\s*\d+)*\]"#, with: "", options: .regularExpression)
    s = s.replacingOccurrences(of: #"(?m)^[\-\*]\s+"#, with: "\u{2022} ", options: .regularExpression)
    s = s.replacingOccurrences(of: #"\n{3,}"#, with: "\n\n", options: .regularExpression)
    s = s.strippingEmoji()
    return s.trimmingCharacters(in: .whitespacesAndNewlines)
}

func renderMarkdown(_ text: String) -> AttributedString {
    let cleaned = cleanAIText(text)
    return (try? AttributedString(markdown: cleaned,
        options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace))) ?? AttributedString(cleaned)
}

// MARK: - Digest Card

struct DigestCard: View {
    let sections: [(title: String, body: String)]
    let raw: String
    @Binding var isRefreshing: Bool
    let onRefresh: () async -> Void
    @State private var expanded = false

    private let sectionIcons: [String: String] = [
        "What You're Studying": "book.closed",
        "Ideas Worth Sitting With": "lightbulb",
        "Connections": "link",
        "One Thread to Pull": "arrow.right.circle",
        "What Needs Attention": "exclamationmark.circle",
        "On Your Plate": "doc.text",
        "Your World": "globe",
        "Worth Exploring": "book",
        "One Thread Worth Pulling": "arrow.right.circle",
    ]

    /// Extract up to N bullet/sentence insights from a section body for the collapsed preview
    private func extractBullets(from body: String, max: Int = 2) -> [String] {
        let lines = body.components(separatedBy: "\n")
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
        // Lines starting with bullet marker
        let bullets = lines.filter { $0.hasPrefix("•") || $0.hasPrefix("-") || $0.hasPrefix("*") }
        if !bullets.isEmpty {
            return Array(bullets.prefix(max)).map {
                $0.replacingOccurrences(of: #"^[•\-\*]\s*"#, with: "", options: .regularExpression)
                    .trimmingCharacters(in: .whitespacesAndNewlines)
            }
        }
        // Fall back to first sentences
        return Array(lines.prefix(max))
    }

    /// 3-4 total bullet insights drawn from the first sections
    private var collapsedInsights: [(icon: String, title: String, bullet: String)] {
        var results: [(icon: String, title: String, bullet: String)] = []
        for sec in sections.prefix(4) {
            let bullets = extractBullets(from: sec.body, max: 1)
            if let b = bullets.first, !b.isEmpty {
                results.append((
                    icon: sectionIcons[sec.title] ?? "circle.fill",
                    title: sec.title,
                    bullet: b
                ))
            }
            if results.count >= 4 { break }
        }
        return results
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            // Header
            HStack {
                HStack(spacing: 6) {
                    Image(systemName: "text.alignleft")
                        .font(.system(size: 10))
                        .foregroundStyle(Color(hex: "#0071e3").opacity(0.8))
                    Text("Daily Briefing")
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundStyle(.tertiary)
                        .textCase(.uppercase)
                        .tracking(0.6)
                }
                Spacer()
                if isRefreshing {
                    ProgressView()
                        .scaleEffect(0.75)
                        .padding(.trailing, 4)
                } else {
                    Button {
                        Task { await onRefresh() }
                    } label: {
                        Image(systemName: "arrow.clockwise")
                            .font(.system(size: 13, weight: .medium))
                            .foregroundStyle(Color(hex: "#0071e3").opacity(0.7))
                    }
                    .buttonStyle(.plain)
                    .padding(.trailing, 4)
                }
                Text(Date().formatted(.dateTime.month(.abbreviated).day()))
                    .font(.system(size: 11))
                    .foregroundStyle(.tertiary)
            }
            .padding(.horizontal, 16)
            .padding(.top, 16)
            .padding(.bottom, 12)

            Divider()

            if sections.isEmpty {
                // No sections parsed — render raw with markdown
                Text(renderMarkdown(raw))
                    .font(.system(size: 15))
                    .foregroundStyle(.secondary)
                    .lineSpacing(4)
                    .padding(16)
            } else if !expanded {
                // Collapsed: show 3-4 bullet insights with "Read More" button
                VStack(alignment: .leading, spacing: 0) {
                    VStack(alignment: .leading, spacing: 10) {
                        ForEach(Array(collapsedInsights.enumerated()), id: \.offset) { _, item in
                            HStack(alignment: .top, spacing: 10) {
                                Image(systemName: item.icon)
                                    .font(.system(size: 11))
                                    .foregroundStyle(Color(hex: "#0071e3").opacity(0.6))
                                    .frame(width: 16, alignment: .center)
                                    .padding(.top, 1)
                                VStack(alignment: .leading, spacing: 1) {
                                    Text(item.title.uppercased())
                                        .font(.system(size: 9, weight: .bold))
                                        .foregroundStyle(.tertiary)
                                        .tracking(0.5)
                                    Text(item.bullet)
                                        .font(.system(size: 14))
                                        .foregroundStyle(.primary)
                                        .lineSpacing(2)
                                        .lineLimit(2)
                                }
                            }
                        }
                        // Fallback if no bullets parsed
                        if collapsedInsights.isEmpty && !raw.isEmpty {
                            Text(renderMarkdown(String(raw.prefix(300))))
                                .font(.system(size: 14))
                                .foregroundStyle(.secondary)
                                .lineSpacing(3)
                                .lineLimit(4)
                        }
                    }
                    .padding(.horizontal, 16)
                    .padding(.vertical, 14)

                    Button {
                        withAnimation(.spring(response: 0.4, dampingFraction: 0.85)) {
                            expanded.toggle()
                        }
                    } label: {
                        HStack(spacing: 5) {
                            Text("Read More")
                                .font(.system(size: 13, weight: .medium))
                            Image(systemName: "chevron.down")
                                .font(.system(size: 10, weight: .medium))
                        }
                        .foregroundStyle(Color(hex: "#0071e3"))
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 12)
                    }
                    .buttonStyle(.plain)
                    .overlay(alignment: .top) { Divider() }
                }
            } else {
                // Expanded: show all sections with body
                ForEach(Array(sections.enumerated()), id: \.offset) { idx, sec in
                    DigestSection(
                        title: sec.title,
                        content: sec.body,
                        icon: sectionIcons[sec.title] ?? "circle.fill",
                        isLast: idx == sections.count - 1
                    )
                }

                Button {
                    withAnimation(.spring(response: 0.4, dampingFraction: 0.85)) {
                        expanded.toggle()
                    }
                } label: {
                    HStack(spacing: 5) {
                        Text("Show less")
                            .font(.system(size: 13, weight: .medium))
                        Image(systemName: "chevron.up")
                            .font(.system(size: 10, weight: .medium))
                    }
                    .foregroundStyle(Color(hex: "#0071e3"))
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 12)
                }
                .buttonStyle(.plain)
                .overlay(alignment: .top) { Divider() }
            }
        }
        .background(Color(hex: "faf9f7"))
        .clipShape(RoundedRectangle(cornerRadius: 14))
    }
}

struct DigestSection: View {
    let title: String
    let content: String
    let icon: String
    let isLast: Bool

    private var renderedContent: AttributedString {
        renderMarkdown(content)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 7) {
                Image(systemName: icon)
                    .font(.system(size: 11))
                    .foregroundStyle(.tertiary)
                Text(title.uppercased())
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(.tertiary)
                    .tracking(0.6)
            }
            .padding(.horizontal, 16)
            .padding(.top, 14)
            .padding(.bottom, 8)

            Text(renderedContent)
                .font(.system(size: 15))
                .foregroundStyle(.primary)
                .lineSpacing(4)
                .padding(.horizontal, 16)
                .padding(.bottom, 14)

            if !isLast { Divider() }
        }
    }
}

// MARK: - Daily Cards

struct DailyFactCard: View {
    let fact: DailyFact

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Label("Today's Fact", systemImage: "sparkles")
                .font(.system(size: 11, weight: .semibold))
                .foregroundStyle(.tertiary)
                .textCase(.uppercase)
                .tracking(0.6)

            Text(renderMarkdown(fact.text))
                .font(.system(size: 15))
                .foregroundStyle(.secondary)
                .lineSpacing(3)

            if let source = fact.source, !source.isEmpty {
                Text("from \(source)")
                    .font(.system(size: 11))
                    .foregroundStyle(.tertiary)
                    .italic()
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color(hex: "faf9f7"))
        .clipShape(RoundedRectangle(cornerRadius: 12))
    }
}

struct DailyVocabCard: View {
    let vocab: DailyVocab

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Label("Word of the Day", systemImage: "text.quote")
                .font(.system(size: 11, weight: .semibold))
                .foregroundStyle(.tertiary)
                .textCase(.uppercase)
                .tracking(0.6)

            Text(vocab.word)
                .font(.system(size: 16, weight: .bold))
                .foregroundStyle(.primary)

            Text(vocab.definition)
                .font(.system(size: 15))
                .foregroundStyle(.secondary)
                .lineSpacing(2)

            if let context = vocab.context, !context.isEmpty {
                Text(context)
                    .font(.system(size: 11.5))
                    .foregroundStyle(.secondary)
                    .lineSpacing(2)
                    .padding(.leading, 8)
                    .overlay(alignment: .leading) {
                        Rectangle()
                            .fill(Color(hex: "#0071e3").opacity(0.4))
                            .frame(width: 2)
                    }
            }

            if let source = vocab.source, !source.isEmpty {
                Text("from \(source)")
                    .font(.system(size: 11))
                    .foregroundStyle(.tertiary)
                    .italic()
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color(hex: "faf9f7"))
        .clipShape(RoundedRectangle(cornerRadius: 12))
    }
}

// MARK: - Skeleton

struct ShimmerView: View {
    @State private var phase: CGFloat = -1

    var body: some View {
        GeometryReader { geo in
            let width = geo.size.width
            Rectangle()
                .fill(
                    LinearGradient(
                        gradient: Gradient(colors: [
                            Color(hex: "faf9f7"),
                            Color(hex: "ede8df").opacity(0.8),
                            Color(hex: "faf9f7")
                        ]),
                        startPoint: UnitPoint(x: phase, y: 0.5),
                        endPoint: UnitPoint(x: phase + 0.6, y: 0.5)
                    )
                )
                .onAppear {
                    withAnimation(.linear(duration: 1.4).repeatForever(autoreverses: false)) {
                        phase = 1.2
                    }
                }
                .frame(width: width)
        }
    }
}

struct HomeSkeletonView: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            // Digest skeleton
            RoundedRectangle(cornerRadius: 14)
                .fill(Color(hex: "faf9f7"))
                .frame(maxWidth: .infinity)
                .frame(height: 160)
                .overlay(ShimmerView().clipShape(RoundedRectangle(cornerRadius: 14)))
                .padding(.horizontal, 16)

            // Fact/vocab skeleton
            HStack(spacing: 12) {
                ForEach(0..<2, id: \.self) { _ in
                    RoundedRectangle(cornerRadius: 12)
                        .fill(Color(hex: "faf9f7"))
                        .frame(maxWidth: .infinity)
                        .frame(height: 90)
                        .overlay(ShimmerView().clipShape(RoundedRectangle(cornerRadius: 12)))
                }
            }
            .padding(.horizontal, 16)

            // Spark skeleton
            RoundedRectangle(cornerRadius: 14)
                .fill(Color(hex: "faf9f7"))
                .frame(maxWidth: .infinity)
                .frame(height: 80)
                .overlay(ShimmerView().clipShape(RoundedRectangle(cornerRadius: 14)))
                .padding(.horizontal, 16)
        }
    }
}

// MARK: - Sub-components

struct SectionCard<Content: View>: View {
    let title: String
    let icon: String
    @ViewBuilder let content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Label(title, systemImage: icon)
                .font(.system(size: 11, weight: .semibold))
                .foregroundStyle(.tertiary)
                .textCase(.uppercase)
                .tracking(0.6)
            content
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color(hex: "faf9f7"))
        .clipShape(RoundedRectangle(cornerRadius: 14))
        .shadow(color: .black.opacity(0.04), radius: 8, x: 0, y: 2)
    }
}

struct SuggestionChip: View {
    let text: String
    let action: () -> Void
    @State private var isPressed = false

    var body: some View {
        Button(action: action) {
            Text(text)
                .font(.system(size: 13))
                .foregroundStyle(isPressed ? Color(hex: "#0071e3") : Color.primary)
                .padding(.horizontal, 14)
                .padding(.vertical, 9)
                .background(isPressed
                    ? Color(hex: "#0071e3").opacity(0.08)
                    : Color(hex: "faf9f7"))
                .clipShape(Capsule())
                .overlay(Capsule().stroke(
                    isPressed ? Color(hex: "#0071e3").opacity(0.3) : Color(UIColor.separator).opacity(0.4),
                    lineWidth: 0.5))
                .animation(.easeInOut(duration: 0.12), value: isPressed)
        }
        .buttonStyle(.plain)
        .simultaneousGesture(
            DragGesture(minimumDistance: 0)
                .onChanged { _ in isPressed = true }
                .onEnded { _ in isPressed = false }
        )
    }
}

struct SectionHeader: View {
    let title: String

    var body: some View {
        Text(title)
            .font(.system(size: 11, weight: .semibold))
            .foregroundStyle(.tertiary)
            .textCase(.uppercase)
            .tracking(0.6)
            .frame(maxWidth: .infinity, alignment: .leading)
    }
}

struct NewsCardCompact: View {
    let article: NewsArticle
    @State private var showSafari = false

    var body: some View {
        Button { showSafari = true } label: {
            VStack(alignment: .leading, spacing: 6) {
                if let imgStr = article.image, let imgURL = URL(string: imgStr) {
                    AsyncImage(url: imgURL) { phase in
                        switch phase {
                        case .success(let image):
                            image.resizable()
                                .aspectRatio(contentMode: .fill)
                                .transition(.opacity.animation(.easeInOut(duration: 0.3)))
                        case .failure:
                            categoryPlaceholder
                        default:
                            Color(hex: "ede8df")
                        }
                    }
                    .frame(width: 148, height: 90)
                    .clipShape(RoundedRectangle(cornerRadius: 8))
                } else {
                    categoryPlaceholder
                        .frame(width: 148, height: 90)
                        .clipShape(RoundedRectangle(cornerRadius: 8))
                }

                Text(article.source.uppercased())
                    .font(.system(size: 9.5, weight: .semibold))
                    .foregroundStyle(.tertiary)
                    .tracking(0.4)

                Text(article.title)
                    .font(.system(size: 12.5, weight: .semibold))
                    .foregroundStyle(.primary)
                    .lineLimit(3)
                    .lineSpacing(1.5)
            }
            .frame(width: 148)
        }
        .buttonStyle(.plain)
        .fullScreenCover(isPresented: $showSafari) {
            if let url = URL(string: article.url) {
                SafariView(url: url).ignoresSafeArea()
            }
        }
    }

    private var categoryPlaceholder: some View {
        ZStack {
            Color(hex: "ede8df")
            Text(categoryEmoji)
                .font(.system(size: 28))
        }
    }

    private var categoryEmoji: String {
        switch article.category.lowercased() {
        case "israel": return "🇮🇱"
        case "world": return "🌍"
        case "politics": return "🏛️"
        case "ai": return "🤖"
        case "tech": return "💻"
        case "finance": return "📈"
        case "sports": return "⚽"
        case "torah": return "📖"
        default: return "📰"
        }
    }
}

struct RecRow: View {
    let rec: Recommendation

    private var icon: String {
        switch rec.type {
        case "youtube": return "play.rectangle.fill"
        case "book":    return "book.closed.fill"
        default:        return "headphones"
        }
    }

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: icon)
                .font(.system(size: 15))
                .foregroundStyle(.secondary)
                .frame(width: 28, height: 28)
                .background(Color(UIColor.tertiarySystemFill))
                .clipShape(RoundedRectangle(cornerRadius: 7))

            VStack(alignment: .leading, spacing: 4) {
                Text(rec.title)
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundStyle(.primary)

                if let show = rec.author_or_show {
                    Text(show)
                        .font(.system(size: 12))
                        .foregroundStyle(.tertiary)
                }

                if let why = rec.why {
                    Text(why)
                        .font(.system(size: 13))
                        .foregroundStyle(.secondary)
                        .lineSpacing(2)
                        .padding(.top, 2)
                }

                HStack(spacing: 8) {
                    if let link = rec.link, let url = URL(string: link) {
                        Link(rec.link_label ?? "Open", destination: url)
                            .font(.system(size: 12, weight: .medium))
                            .foregroundStyle(.primary)
                            .padding(.horizontal, 10)
                            .padding(.vertical, 4)
                            .background(Color(UIColor.tertiarySystemFill))
                            .clipShape(Capsule())
                    }
                    if let link2 = rec.link2, let url2 = URL(string: link2) {
                        Link(rec.link2_label ?? "", destination: url2)
                            .font(.system(size: 12, weight: .medium))
                            .foregroundStyle(.secondary)
                            .padding(.horizontal, 10)
                            .padding(.vertical, 4)
                            .background(Color(UIColor.tertiarySystemFill))
                            .clipShape(Capsule())
                    }
                }
                .padding(.top, 4)
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color(hex: "faf9f7"))
        .clipShape(RoundedRectangle(cornerRadius: 14))
        .padding(.bottom, 6)
    }
}


// MARK: - Today Study Plan Card

struct TodayStudyPlanCard: View {
    let plan: StudyPlanResponse
    let onStart: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(alignment: .top, spacing: 12) {
                ZStack {
                    RoundedRectangle(cornerRadius: 10)
                        .fill(Color(hex: "#0071e3").opacity(0.1))
                        .frame(width: 40, height: 40)
                    Image(systemName: "calendar.badge.clock")
                        .font(.system(size: 18))
                        .foregroundStyle(Color(hex: "#0071e3"))
                }
                VStack(alignment: .leading, spacing: 4) {
                    Text("Today's Focus")
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundStyle(.secondary)
                        .textCase(.uppercase)
                        .tracking(0.4)
                    Text(plan.today_focus)
                        .font(.system(size: 15, weight: .semibold))
                        .foregroundStyle(.primary)
                        .lineLimit(2)
                    if !plan.today_topics.isEmpty {
                        HStack(spacing: 5) {
                            ForEach(plan.today_topics.prefix(3), id: \.self) { topic in
                                Text(topic)
                                    .font(.system(size: 10, weight: .medium))
                                    .foregroundStyle(Color(hex: "#0071e3"))
                                    .padding(.horizontal, 7)
                                    .padding(.vertical, 2)
                                    .background(Color(hex: "#0071e3").opacity(0.08))
                                    .clipShape(Capsule())
                                    .lineLimit(1)
                            }
                        }
                        .padding(.top, 2)
                    }
                }
                Spacer()
                VStack(alignment: .trailing, spacing: 1) {
                    Text("\(plan.today_duration_min)")
                        .font(.system(size: 22, weight: .bold))
                        .foregroundStyle(Color(hex: "#0071e3"))
                    Text("min")
                        .font(.system(size: 10))
                        .foregroundStyle(.secondary)
                }
            }
            .padding(.horizontal, 14)
            .padding(.top, 14)
            .padding(.bottom, 12)

            if plan.srs_due > 0 {
                HStack(spacing: 6) {
                    Image(systemName: "clock.badge.exclamationmark")
                        .font(.system(size: 11))
                        .foregroundStyle(.orange)
                    Text("\(plan.srs_due) SRS topic\(plan.srs_due == 1 ? "" : "s") due today")
                        .font(.system(size: 11.5))
                        .foregroundStyle(.orange)
                }
                .padding(.horizontal, 14)
                .padding(.bottom, 10)
            }

            Button(action: onStart) {
                HStack {
                    Spacer()
                    Text("Start studying")
                        .font(.system(size: 14, weight: .semibold))
                        .foregroundStyle(.white)
                    Image(systemName: "arrow.right")
                        .font(.system(size: 12, weight: .semibold))
                        .foregroundStyle(.white)
                    Spacer()
                }
                .padding(.vertical, 10)
                .background(Color(hex: "#0071e3"))
                .clipShape(RoundedRectangle(cornerRadius: 10))
            }
            .buttonStyle(.plain)
            .padding(.horizontal, 14)
            .padding(.bottom, 14)
        }
        .background(Color.white)
        .clipShape(RoundedRectangle(cornerRadius: 14))
        .overlay(RoundedRectangle(cornerRadius: 14).stroke(Color(hex: "#e8e5e0"), lineWidth: 1))
        .shadow(color: .black.opacity(0.04), radius: 6, x: 0, y: 2)
    }
}

// MARK: - Streak Share Card (for ImageRenderer)

struct StreakShareCard: View {
    let streak: Int
    let milestone: String

    var body: some View {
        ZStack {
            LinearGradient(
                colors: [Color(hex: "#1a1a2e"), Color(hex: "#16213e")],
                startPoint: .topLeading, endPoint: .bottomTrailing
            )

            VStack(spacing: 20) {
                Image(systemName: "flame.fill")
                    .font(.system(size: 56))
                    .foregroundStyle(Color.orange)

                Text("\(streak)")
                    .font(.system(size: 80, weight: .black, design: .rounded))
                    .foregroundStyle(.white)

                Text("day streak")
                    .font(.system(size: 24, weight: .medium))
                    .foregroundStyle(.white.opacity(0.8))

                Text(milestone)
                    .font(.system(size: 16))
                    .foregroundStyle(.white.opacity(0.6))
                    .multilineTextAlignment(.center)

                Spacer().frame(height: 8)

                Text("Neuron · your second brain")
                    .font(.system(size: 13))
                    .foregroundStyle(.white.opacity(0.4))
            }
            .padding(40)
        }
        .frame(width: 400, height: 500)
        .clipShape(RoundedRectangle(cornerRadius: 24))
    }
}
