import SwiftUI

// MARK: - Warm Design Tokens

private extension Color {
    static let warmBg    = Color(red: 0.98, green: 0.96, blue: 0.92)
    static let warmCard  = Color(red: 1.00, green: 0.98, blue: 0.95)
    static let warmAmber = Color(hex: "#d97706")
    static let warmGold  = Color(hex: "#f59e0b")
    static let warmGreen = Color(red: 0.18, green: 0.65, blue: 0.35)
    static let warmRed   = Color(red: 0.85, green: 0.22, blue: 0.18)
    static let warmYellow = Color(red: 0.96, green: 0.72, blue: 0.11)
    static let accentBlue = Color(hex: "#0071e3")
}

// MARK: - State

private enum PracticeState {
    case topic
    case loading
    case srsReview
    case question(index: Int)
    case result(index: Int, response: EvaluateResponse)
    case feynman(index: Int, response: EvaluateResponse)
    case summary
}

private enum PracticeMode: String, CaseIterable, Identifiable {
    case quickReview  = "Quick Review"
    case fullSession  = "Full Session"
    case deepDive     = "Deep Dive"
    var id: String { rawValue }
    var icon: String {
        switch self {
        case .quickReview: return "bolt.fill"
        case .fullSession: return "list.bullet.rectangle"
        case .deepDive:    return "brain.head.profile"
        }
    }
    var subtitle: String {
        switch self {
        case .quickReview: return "5 cards"
        case .fullSession: return "All due"
        case .deepDive:    return "Feynman mode"
        }
    }
    /// Maximum number of SRS cards to pull into the queue (nil = unlimited)
    var cardLimit: Int? {
        switch self {
        case .quickReview: return 5
        case .fullSession: return nil
        case .deepDive:    return nil
        }
    }
}

// MARK: - Main View

struct PracticeView: View {
    @EnvironmentObject var api: APIClient
    @EnvironmentObject var settings: AppSettings
    @Environment(\.dismiss) private var dismiss

    var preloadedExercises: [PracticeExercise] = []
    var preloadedTopic: String? = nil

    @State private var state: PracticeState = .topic
    @State private var topic = ""
    @State private var exercises: [PracticeExercise] = []
    @State private var resolvedTopic = ""
    @State private var answers: [Int: String] = [:]
    @State private var evaluations: [Int: EvaluateResponse] = [:]
    @State private var userAnswer = ""
    @State private var isEvaluating = false
    @State private var errorMessage: String? = nil
    @State private var suggestions: [String] = [
        "Operating Systems", "Computer Networks", "Algorithms", "Financial Accounting",
        "Apache Arrow", "Trino", "ClickHouse", "Distributed Systems"
    ]
    @State private var srsNextReview: String? = nil
    @State private var srsDueItems: [SRSDueItem] = []
    @State private var srsStatsData: SRSStatsResponse? = nil
    @State private var srsReviewQueue: [SRSDueItem] = []
    @State private var srsReviewIndex: Int = 0
    @State private var srsReviewedCount: Int = 0
    @State private var practiceMode: PracticeMode = .fullSession

    var body: some View {
        NavigationStack {
            stateView
                .navigationTitle("Practice")
                .navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .topBarTrailing) {
                        Button("Done") { dismiss() }.foregroundStyle(Color.warmAmber)
                    }
                }
                .animation(.spring(response: 0.4, dampingFraction: 0.85), value: stateKey)
        }
        .task { await loadAll() }
        .onAppear {
            if !preloadedExercises.isEmpty {
                exercises = preloadedExercises
                resolvedTopic = preloadedTopic ?? "Today's Review"
                userAnswer = ""
                state = .question(index: 0)
            }
        }
    }

    /// A simple string key used to drive state-change animations.
    private var stateKey: String {
        switch state {
        case .topic:             return "topic"
        case .loading:           return "loading"
        case .srsReview:         return "srsReview"
        case .question(let i):   return "question-\(i)"
        case .result(let i, _):  return "result-\(i)"
        case .feynman(let i, _): return "feynman-\(i)"
        case .summary:           return "summary"
        }
    }

    @ViewBuilder
    private var stateView: some View {
        switch state {
        case .topic:
            TopicInputView(
                topic: $topic,
                practiceMode: $practiceMode,
                suggestions: suggestions,
                errorMessage: errorMessage,
                srsDueItems: srsDueItems,
                srsStats: srsStatsData,
                onGenerate: { await generateQuestions() },
                onStudySession: { await startSmartStudySession() },
                onReviewSRS: {
                    let cards = srsDueItems.filter { $0.type == "flashcard" }
                    guard !cards.isEmpty else { return }
                    let limit = practiceMode.cardLimit
                    srsReviewQueue = limit.map { Array(cards.prefix($0)) } ?? cards
                    srsReviewIndex = 0
                    srsReviewedCount = 0
                    state = .srsReview
                },
                onStudyDueTopic: { dueTopic in
                    topic = dueTopic
                    Task { await generateQuestions() }
                }
            )
            .transition(.asymmetric(insertion: .move(edge: .leading), removal: .move(edge: .leading)))
        case .loading:
            PracticeLoadingView()
            .transition(.opacity)
        case .srsReview:
            srsReviewView
            .transition(.asymmetric(insertion: .move(edge: .trailing), removal: .move(edge: .leading)))
        case .question(let index):
            QuestionCardView(
                exercise: exercises[index], index: index, total: exercises.count,
                userAnswer: $userAnswer, isEvaluating: isEvaluating,
                onCheck: { await checkAnswer(index: index) }
            )
            .transition(.asymmetric(insertion: .move(edge: .trailing), removal: .move(edge: .leading)))
        case .result(let index, let evalResponse):
            ResultView(
                exercise: exercises[index], evalResponse: evalResponse,
                isLast: index == exercises.count - 1,
                onNext: {
                    let next = index + 1
                    if evalResponse.score != "correct" && !exercises[index].isMultipleChoice {
                        userAnswer = ""
                        withAnimation(.spring(response: 0.4, dampingFraction: 0.85)) {
                            state = .feynman(index: index, response: evalResponse)
                        }
                    } else if next < exercises.count {
                        userAnswer = ""
                        withAnimation(.spring(response: 0.4, dampingFraction: 0.85)) {
                            state = .question(index: next)
                        }
                    } else {
                        withAnimation(.spring(response: 0.4, dampingFraction: 0.85)) {
                            state = .summary
                        }
                    }
                },
                onSeeScore: {
                    withAnimation(.spring(response: 0.4, dampingFraction: 0.85)) { state = .summary }
                }
            )
            .transition(.asymmetric(insertion: .move(edge: .trailing), removal: .move(edge: .leading)))
        case .feynman(let index, let evalResponse):
            FeynmanView(
                exercise: exercises[index], evalResponse: evalResponse,
                onContinue: {
                    let next = index + 1
                    userAnswer = ""
                    withAnimation(.spring(response: 0.4, dampingFraction: 0.85)) {
                        state = next < exercises.count ? .question(index: next) : .summary
                    }
                }
            )
            .transition(.asymmetric(insertion: .move(edge: .trailing), removal: .move(edge: .leading)))
        case .summary:
            SummaryView(
                exercises: exercises, evaluations: evaluations,
                topic: resolvedTopic, srsNextReview: srsNextReview,
                onPracticeAgain: {
                    answers = [:]; evaluations = [:]; userAnswer = ""
                    topic = ""; srsNextReview = nil
                    withAnimation(.spring(response: 0.4, dampingFraction: 0.85)) { state = .topic }
                }
            )
            .transition(.asymmetric(insertion: .move(edge: .trailing), removal: .move(edge: .leading)))
        }
    }

    @ViewBuilder
    private var srsReviewView: some View {
        if srsReviewIndex < srsReviewQueue.count {
            SRSFlashcardReviewView(
                item: srsReviewQueue[srsReviewIndex],
                currentIndex: srsReviewIndex,
                total: srsReviewQueue.count,
                onRate: { rating in
                    Task { await rateCard(item: srsReviewQueue[srsReviewIndex], rating: rating) }
                }
            )
        } else {
            SRSReviewCompleteView(reviewedCount: srsReviewedCount) {
                Task { await loadAll() }
                settings.syncSRSDue(api: api)
                state = .topic
            }
        }
    }

    // MARK: - Actions

    private func loadAll() async {
        async let s1 = try? api.suggestions()
        async let s2 = try? api.srsDue()
        async let s3 = try? api.srsStats()
        let (sugResult, dueResult, statsResult) = await (s1, s2, s3)
        if let r = sugResult {
            let courses = ["Operating Systems", "Computer Networks", "Algorithms", "Financial Accounting",
                           "Apache Arrow", "Trino", "ClickHouse", "Distributed Systems"]
            let others = r.suggestions.filter { s in
                !courses.contains(where: { s.lowercased().contains($0.lowercased()) })
            }
            suggestions = Array((courses + others).prefix(8))
        }
        if let d = dueResult { srsDueItems = d.due }
        if let st = statsResult { srsStatsData = st }
    }

    private func rateCard(item: SRSDueItem, rating: String) async {
        if let idx = item.card_index {
            _ = try? await api.srsCardRecord(cardIndex: idx, rating: rating)
        }
        srsReviewedCount += 1
        srsReviewIndex += 1
        if settings.hapticEnabled { UIImpactFeedbackGenerator(style: .light).impactOccurred() }
    }

    private func startSmartStudySession() async {
        state = .loading; errorMessage = nil
        do {
            let session = try await api.studySession()
            guard !session.exercises.isEmpty else {
                errorMessage = "No exercises available. Try a specific topic."; state = .topic; return
            }
            exercises = session.exercises
            resolvedTopic = session.topics.isEmpty ? "Smart Study" : session.topics.joined(separator: ", ")
            userAnswer = ""; state = .question(index: 0)
        } catch {
            errorMessage = "Could not load study session. Check your connection."; state = .topic
        }
    }

    private func generateQuestions() async {
        let t = topic.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !t.isEmpty else { return }
        state = .loading; errorMessage = nil
        do {
            let response = try await api.practice(topic: t)
            exercises = response.exercises; resolvedTopic = response.topic
            if exercises.isEmpty {
                errorMessage = "No questions generated. Try a different topic."; state = .topic
            } else {
                userAnswer = ""; state = .question(index: 0)
            }
        } catch {
            errorMessage = error.localizedDescription; state = .topic
        }
    }

    private func checkAnswer(index: Int) async {
        let exercise = exercises[index]
        let answer = userAnswer.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !answer.isEmpty else { return }
        isEvaluating = true
        do {
            let req = EvaluateRequest(question: exercise.question, user_answer: answer,
                                      correct_answer: exercise.answer, explanation: exercise.explanation,
                                      topic: resolvedTopic)
            let evalResponse = try await api.evaluateAnswer(req)
            answers[index] = answer; evaluations[index] = evalResponse; isEvaluating = false
            if settings.hapticEnabled {
                if evalResponse.score == "correct" {
                    UINotificationFeedbackGenerator().notificationOccurred(.success)
                } else if evalResponse.score == "partial" {
                    UIImpactFeedbackGenerator(style: .medium).impactOccurred()
                } else {
                    UINotificationFeedbackGenerator().notificationOccurred(.error)
                }
            }
            if index == exercises.count - 1 { await recordToSRS() }
            state = .result(index: index, response: evalResponse)
        } catch { isEvaluating = false }
    }

    private func recordToSRS() async {
        let correct = evaluations.values.filter { $0.score == "correct" }.count
        let partial = evaluations.values.filter { $0.score == "partial" }.count
        let total = exercises.count
        let pct = total > 0 ? (Double(correct) + Double(partial) * 0.5) / Double(total) : 0
        let score = pct >= 0.8 ? "correct" : pct >= 0.4 ? "partial" : "incorrect"
        if let result = try? await api.srsRecord(topic: resolvedTopic, score: score,
                                                  correctCount: correct, totalCount: total) {
            srsNextReview = result.next_review
        }
    }
}

// MARK: - SRS Flashcard Review

private struct SRSFlashcardReviewView: View {
    let item: SRSDueItem
    let currentIndex: Int
    let total: Int
    let onRate: (String) -> Void

    @State private var isFlipped = false
    @State private var rotation: Double = 0

    var body: some View {
        ScrollView {
            VStack(spacing: 24) {
                // Progress
                VStack(spacing: 6) {
                    HStack {
                        VStack(alignment: .leading, spacing: 1) {
                            Text("\(currentIndex) / \(total) cards reviewed")
                                .font(.system(size: 12, weight: .semibold)).foregroundStyle(.secondary)
                                .textCase(.uppercase).tracking(0.5)
                            Text("Card \(currentIndex + 1) of \(total)")
                                .font(.system(size: 10)).foregroundStyle(.tertiary)
                        }
                        Spacer()
                        Text(item.topic)
                            .font(.system(size: 12, weight: .medium)).foregroundStyle(Color.warmAmber)
                            .padding(.horizontal, 10).padding(.vertical, 4)
                            .background(Color.warmAmber.opacity(0.12)).clipShape(Capsule())
                    }
                    GeometryReader { geo in
                        ZStack(alignment: .leading) {
                            RoundedRectangle(cornerRadius: 3)
                                .fill(Color(UIColor.systemGray5))
                                .frame(height: 6)
                            RoundedRectangle(cornerRadius: 3)
                                .fill(Color.warmAmber)
                                .frame(width: total > 0 ? geo.size.width * CGFloat(currentIndex) / CGFloat(total) : 0, height: 6)
                                .animation(.spring(response: 0.5, dampingFraction: 0.8), value: currentIndex)
                        }
                    }
                    .frame(height: 6)
                }

                // Flip card
                ZStack {
                    // Back
                    VStack(alignment: .leading, spacing: 12) {
                        Label("Answer", systemImage: "lightbulb.fill")
                            .font(.system(size: 11, weight: .semibold)).foregroundStyle(Color.warmAmber)
                            .textCase(.uppercase).tracking(0.6)
                        Text(item.answer ?? "").font(.system(size: 16)).lineSpacing(5)
                            .frame(maxWidth: .infinity, alignment: .leading)
                        if let exp = item.explanation, !exp.isEmpty {
                            Divider().overlay(Color.warmAmber.opacity(0.2))
                            Text(exp).font(.system(size: 13)).foregroundStyle(.secondary).lineSpacing(4)
                        }
                    }
                    .padding(20).frame(maxWidth: .infinity, minHeight: 200, alignment: .topLeading)
                    .background(Color.warmCard).clipShape(RoundedRectangle(cornerRadius: 18))
                    .overlay(RoundedRectangle(cornerRadius: 18).stroke(Color.warmAmber.opacity(0.25), lineWidth: 1.5))
                    .shadow(color: Color.warmAmber.opacity(0.12), radius: 10, x: 0, y: 4)
                    .rotation3DEffect(.degrees(isFlipped ? 0 : 180), axis: (x: 0, y: 1, z: 0))
                    .opacity(isFlipped ? 1 : 0)

                    // Front
                    VStack(spacing: 16) {
                        Label("Question", systemImage: "questionmark.circle")
                            .font(.system(size: 11, weight: .semibold)).foregroundStyle(.secondary)
                            .textCase(.uppercase).tracking(0.6).frame(maxWidth: .infinity, alignment: .leading)
                        Text(item.question ?? "").font(.system(size: 18, weight: .semibold))
                            .lineSpacing(5).multilineTextAlignment(.center).frame(maxWidth: .infinity)
                        Text("Tap to reveal answer").font(.system(size: 13))
                            .foregroundStyle(Color.warmAmber.opacity(0.7))
                    }
                    .padding(24).frame(maxWidth: .infinity, minHeight: 200)
                    .background(Color.warmCard).clipShape(RoundedRectangle(cornerRadius: 18))
                    .overlay(RoundedRectangle(cornerRadius: 18).stroke(Color.warmGold.opacity(0.3), lineWidth: 1.5))
                    .shadow(color: Color.warmGold.opacity(0.15), radius: 12, x: 0, y: 5)
                    .rotation3DEffect(.degrees(isFlipped ? 180 : 0), axis: (x: 0, y: 1, z: 0))
                    .opacity(isFlipped ? 0 : 1)
                }
                .rotation3DEffect(.degrees(rotation), axis: (x: 0, y: 1, z: 0))
                .onTapGesture {
                    withAnimation(.spring(response: 0.5, dampingFraction: 0.8)) {
                        rotation += 180; isFlipped.toggle()
                    }
                }

                if !isFlipped {
                    Text("Think about it, then tap the card")
                        .font(.system(size: 13)).foregroundStyle(.tertiary).italic()
                }

                // Rating buttons — shown after flip
                if isFlipped {
                    VStack(spacing: 12) {
                        Text("How well did you know it?")
                            .font(.system(size: 14, weight: .semibold)).foregroundStyle(.secondary)
                        HStack(spacing: 10) {
                            SRSRatingButton(label: "Again", sublabel: "<1d", color: .warmRed) { onRate("again") }
                            SRSRatingButton(label: "Hard",  sublabel: "~2d", color: .warmYellow) { onRate("hard") }
                            SRSRatingButton(label: "Good",  sublabel: "~4d", color: .warmGreen) { onRate("good") }
                            SRSRatingButton(label: "Easy",  sublabel: "~7d", color: .accentBlue) { onRate("easy") }
                        }
                    }
                    .transition(.move(edge: .bottom).combined(with: .opacity))
                    .animation(.spring(response: 0.4, dampingFraction: 0.8), value: isFlipped)
                }
            }
            .padding(16)
        }
        .background(Color.warmBg.ignoresSafeArea())
        .onChange(of: item.topic) { _, _ in isFlipped = false; rotation = 0 }
    }
}

private struct SRSRatingButton: View {
    let label: String
    let sublabel: String
    let color: Color
    let action: () -> Void
    var body: some View {
        Button(action: action) {
            VStack(spacing: 2) {
                Text(label).font(.system(size: 14, weight: .bold))
                Text(sublabel).font(.system(size: 10)).opacity(0.75)
            }
            .frame(maxWidth: .infinity).padding(.vertical, 12)
            .foregroundStyle(.white).background(color).clipShape(RoundedRectangle(cornerRadius: 12))
        }
        .buttonStyle(.plain)
    }
}

// MARK: - SRS Review Complete

private struct SRSReviewCompleteView: View {
    let reviewedCount: Int
    let onDone: () -> Void
    var body: some View {
        VStack(spacing: 28) {
            Spacer()
            VStack(spacing: 12) {
                Image(systemName: "checkmark.seal.fill").font(.system(size: 56)).foregroundStyle(Color.warmAmber)
                Text("Review Complete!").font(.system(size: 28, weight: .bold, design: .rounded))
                Text("You reviewed \(reviewedCount) flashcard\(reviewedCount == 1 ? "" : "s").")
                    .font(.system(size: 16)).foregroundStyle(.secondary)
            }
            Button(action: onDone) {
                Text("Done").font(.system(size: 16, weight: .semibold))
                    .frame(maxWidth: .infinity).padding(.vertical, 15)
                    .background(Color.warmAmber).foregroundStyle(.white).clipShape(RoundedRectangle(cornerRadius: 14))
            }
            .padding(.horizontal, 32)
            Spacer()
        }
        .background(Color.warmBg.ignoresSafeArea())
    }
}

// MARK: - Topic Input

private struct TopicInputView: View {
    @Binding var topic: String
    @Binding var practiceMode: PracticeMode
    let suggestions: [String]
    let errorMessage: String?
    var srsDueItems: [SRSDueItem] = []
    var srsStats: SRSStatsResponse? = nil
    let onGenerate: () async -> Void
    var onStudySession: (() async -> Void)? = nil
    var onReviewSRS: (() -> Void)? = nil
    var onStudyDueTopic: ((String) -> Void)? = nil

    @FocusState private var isFocused: Bool

    private var flashcards: [SRSDueItem] { srsDueItems.filter { $0.type == "flashcard" } }
    private var dueTopics: [SRSDueItem]  { srsDueItems.filter { $0.type == "topic" || $0.type == nil } }
    private var totalDue: Int { srsDueItems.count }
    private var flashcardDueCount: Int { flashcards.count }
    private var topicIsEmpty: Bool { topic.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
    private var todayISO: String {
        let f = DateFormatter(); f.dateFormat = "yyyy-MM-dd"; return f.string(from: Date())
    }
    private var reviewedToday: Int {
        srsStats?.topics.filter { $0.last_reviewed == todayISO }.count ?? 0
    }

    var body: some View {
        ScrollView {
            VStack(spacing: 24) {
                headerSection
                statsSection
                PracticeModePicker(practiceMode: $practiceMode)
                    .padding(.horizontal, 16)
                flashcardBannerSection
                dueTopicsSection
                TopicInputSection(
                    topic: $topic, isFocused: _isFocused,
                    suggestions: suggestions, errorMessage: errorMessage,
                    onGenerate: onGenerate
                )
                .padding(.horizontal, 16)
                ActionButtonsSection(
                    topicIsEmpty: topicIsEmpty, isFocused: _isFocused,
                    onGenerate: onGenerate, onStudySession: onStudySession
                )
                .padding(.horizontal, 16)
            }
            .padding(.bottom, 32)
        }
        .background(Color.warmBg.ignoresSafeArea())
    }

    private var headerSection: some View {
        VStack(spacing: 6) {
            ZStack(alignment: .topTrailing) {
                Text("Practice").font(.system(size: 34, weight: .bold, design: .rounded)).tracking(-0.5)
                if flashcardDueCount > 0 {
                    Text("\(flashcardDueCount)")
                        .font(.system(size: 12, weight: .bold))
                        .foregroundStyle(.white)
                        .padding(.horizontal, 7).padding(.vertical, 3)
                        .background(Color.warmRed)
                        .clipShape(Capsule())
                        .offset(x: 44, y: -4)
                }
            }
            Text("Quiz yourself on what you've learned.")
                .font(.system(size: 15)).foregroundStyle(.secondary).multilineTextAlignment(.center)
        }
        .padding(.top, 20)
    }

    @ViewBuilder
    private var statsSection: some View {
        if totalDue > 0 || reviewedToday > 0 {
            HStack(spacing: 10) {
                if totalDue > 0 {
                    PracticeStatPill(icon: "calendar.badge.exclamationmark", value: "\(totalDue)", label: "due", color: .red)
                }
                if reviewedToday > 0 {
                    PracticeStatPill(icon: "checkmark.circle", value: "\(reviewedToday)", label: "reviewed", color: .green)
                }
                if let st = srsStats, st.total_topics > 0 {
                    PracticeStatPill(icon: "brain", value: "\(st.total_topics)", label: "topics", color: Color.accentBlue)
                }
                Spacer()
            }
            .padding(.horizontal, 16)
        }
    }

    @ViewBuilder
    private var flashcardBannerSection: some View {
        if !flashcards.isEmpty {
            Button { isFocused = false; onReviewSRS?() } label: {
                HStack(spacing: 14) {
                    ZStack {
                        Circle().fill(Color.warmAmber.opacity(0.15)).frame(width: 44, height: 44)
                        Image(systemName: "rectangle.stack.fill").font(.system(size: 20)).foregroundStyle(Color.warmAmber)
                    }
                    VStack(alignment: .leading, spacing: 2) {
                        let n = flashcards.count
                        let shown = practiceMode.cardLimit.map { min($0, n) } ?? n
                        Text("\(shown) of \(n) flashcard\(n == 1 ? "" : "s") due today")
                            .font(.system(size: 15, weight: .semibold)).foregroundStyle(.primary)
                        Text("Tap to review · \(practiceMode.rawValue)")
                            .font(.system(size: 12)).foregroundStyle(.secondary)
                    }
                    Spacer()
                    Image(systemName: "chevron.right").font(.system(size: 13, weight: .semibold)).foregroundStyle(Color.warmAmber)
                }
                .padding(14)
                .background(Color.warmAmber.opacity(0.08))
                .clipShape(RoundedRectangle(cornerRadius: 14))
                .overlay(RoundedRectangle(cornerRadius: 14).stroke(Color.warmAmber.opacity(0.3), lineWidth: 1))
            }
            .buttonStyle(.plain).padding(.horizontal, 16)
        }
    }

    @ViewBuilder
    private var dueTopicsSection: some View {
        if !dueTopics.isEmpty {
            VStack(alignment: .leading, spacing: 8) {
                Label("Topics to Review", systemImage: "calendar.badge.clock")
                    .font(.system(size: 11, weight: .semibold)).foregroundStyle(.tertiary)
                    .textCase(.uppercase).tracking(0.6)
                VStack(spacing: 6) {
                    ForEach(Array(dueTopics.prefix(4))) { item in
                        TopicDueRow(item: item) { isFocused = false; onStudyDueTopic?(item.topic) }
                    }
                }
            }
            .padding(.horizontal, 16)
        }
    }
}

// MARK: - Practice Mode Picker

private struct PracticeModePicker: View {
    @Binding var practiceMode: PracticeMode

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Session Mode")
                .font(.system(size: 11, weight: .semibold)).foregroundStyle(.tertiary)
                .textCase(.uppercase).tracking(0.6)
            HStack(spacing: 8) {
                ForEach(PracticeMode.allCases) { mode in
                    PracticeModeButton(mode: mode, isSelected: practiceMode == mode) {
                        withAnimation(.spring(response: 0.3, dampingFraction: 0.8)) {
                            practiceMode = mode
                        }
                    }
                }
            }
        }
    }
}

private struct PracticeModeButton: View {
    let mode: PracticeMode
    let isSelected: Bool
    let onTap: () -> Void

    var body: some View {
        Button(action: onTap) {
            VStack(spacing: 4) {
                Image(systemName: mode.icon)
                    .font(.system(size: 16, weight: isSelected ? .semibold : .regular))
                    .foregroundStyle(isSelected ? Color.warmAmber : .secondary)
                Text(mode.rawValue)
                    .font(.system(size: 11, weight: isSelected ? .semibold : .regular))
                    .foregroundStyle(isSelected ? Color.warmAmber : .secondary)
                    .lineLimit(1).minimumScaleFactor(0.8)
                Text(mode.subtitle)
                    .font(.system(size: 10))
                    .foregroundStyle(isSelected ? Color.warmAmber.opacity(0.7) : Color.secondary.opacity(0.6))
            }
            .frame(maxWidth: .infinity)
            .padding(.vertical, 10).padding(.horizontal, 6)
            .background(isSelected ? Color.warmAmber.opacity(0.12) : Color.warmCard)
            .clipShape(RoundedRectangle(cornerRadius: 12))
            .overlay(RoundedRectangle(cornerRadius: 12).stroke(
                isSelected ? Color.warmAmber.opacity(0.5) : Color.clear, lineWidth: 1.5))
        }
        .buttonStyle(.plain)
    }
}

// Input sub-struct (factored out to help type-checker)
private struct TopicInputSection: View {
    @Binding var topic: String
    @FocusState var isFocused: Bool
    let suggestions: [String]
    let errorMessage: String?
    let onGenerate: () async -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            TextField("Topic (e.g. Operating Systems)", text: $topic)
                .font(.system(size: 17)).padding(14).background(Color.warmCard)
                .clipShape(RoundedRectangle(cornerRadius: 14))
                .overlay(RoundedRectangle(cornerRadius: 14).stroke(
                    isFocused ? Color.accentBlue : Color.accentBlue.opacity(0.2),
                    lineWidth: isFocused ? 2 : 0.8))
                .focused($isFocused).submitLabel(.go)
                .onSubmit { Task { await onGenerate() } }.onAppear { isFocused = true }

            VStack(alignment: .leading, spacing: 8) {
                Text("Current courses")
                    .font(.system(size: 11, weight: .semibold)).foregroundStyle(.tertiary)
                    .textCase(.uppercase).tracking(0.6)
                LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 8) {
                    ForEach(suggestions, id: \.self) { s in
                        Button { topic = s; Task { await onGenerate() } } label: {
                            Text(s).font(.system(size: 13, weight: .medium)).foregroundStyle(.primary)
                                .lineLimit(1).minimumScaleFactor(0.85).frame(maxWidth: .infinity)
                                .padding(.horizontal, 12).padding(.vertical, 10).background(Color.warmCard)
                                .clipShape(RoundedRectangle(cornerRadius: 12))
                                .overlay(RoundedRectangle(cornerRadius: 12).stroke(Color.accentBlue.opacity(0.2), lineWidth: 0.8))
                        }
                        .buttonStyle(.plain)
                    }
                }
            }

            if let err = errorMessage {
                HStack(spacing: 8) {
                    Image(systemName: "exclamationmark.circle.fill").foregroundStyle(.red)
                    Text(err).font(.system(size: 13)).foregroundStyle(Color.red.opacity(0.85)).lineSpacing(2)
                    Spacer()
                }
                .padding(12).background(Color.red.opacity(0.08)).clipShape(RoundedRectangle(cornerRadius: 10))
            }
        }
    }
}

private struct ActionButtonsSection: View {
    let topicIsEmpty: Bool
    @FocusState var isFocused: Bool
    let onGenerate: () async -> Void
    let onStudySession: (() async -> Void)?

    var body: some View {
        VStack(spacing: 10) {
            Button { isFocused = false; Task { await onGenerate() } } label: {
                HStack(spacing: 8) {
                    Image(systemName: "sparkles").font(.system(size: 14))
                    Text("Generate Questions").font(.system(size: 16, weight: .semibold))
                }
                .frame(maxWidth: .infinity).padding(.vertical, 15)
                .background(topicIsEmpty ? Color(UIColor.systemGray4) : Color.accentBlue)
                .foregroundStyle(.white).clipShape(RoundedRectangle(cornerRadius: 14))
            }
            .disabled(topicIsEmpty).animation(.easeInOut(duration: 0.2), value: topicIsEmpty)

            if let session = onStudySession {
                Button { isFocused = false; Task { await session() } } label: {
                    HStack(spacing: 8) {
                        Image(systemName: "brain").font(.system(size: 14))
                        Text("Smart Study Session").font(.system(size: 16, weight: .semibold))
                    }
                    .frame(maxWidth: .infinity).padding(.vertical, 15).background(Color.warmCard)
                    .foregroundStyle(Color.accentBlue).clipShape(RoundedRectangle(cornerRadius: 14))
                    .overlay(RoundedRectangle(cornerRadius: 14).stroke(Color.accentBlue.opacity(0.35), lineWidth: 1))
                }
            }
        }
    }
}

private struct TopicDueRow: View {
    let item: SRSDueItem
    let onTap: () -> Void
    var body: some View {
        Button(action: onTap) {
            HStack(spacing: 10) {
                ZStack {
                    Circle()
                        .fill(item.overdue_days > 3 ? Color.red.opacity(0.12) : Color.accentBlue.opacity(0.12))
                        .frame(width: 30, height: 30)
                    Image(systemName: item.overdue_days > 0 ? "exclamationmark" : "clock")
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundStyle(item.overdue_days > 3 ? .red : Color.accentBlue)
                }
                VStack(alignment: .leading, spacing: 1) {
                    Text(item.topic).font(.system(size: 14, weight: .semibold)).foregroundStyle(.primary).lineLimit(1)
                    Text(item.overdue_days > 0
                         ? "\(item.overdue_days) day\(item.overdue_days == 1 ? "" : "s") overdue"
                         : "Due today")
                        .font(.system(size: 12)).foregroundStyle(.secondary)
                }
                Spacer()
                Image(systemName: "chevron.right").font(.system(size: 11, weight: .medium)).foregroundStyle(.tertiary)
            }
            .padding(.horizontal, 12).padding(.vertical, 10).background(Color.warmCard)
            .clipShape(RoundedRectangle(cornerRadius: 10))
            .overlay(RoundedRectangle(cornerRadius: 10).stroke(Color.accentBlue.opacity(0.15), lineWidth: 0.5))
        }
        .buttonStyle(.plain)
    }
}

private struct PracticeStatPill: View {
    let icon: String; let value: String; let label: String; let color: Color
    var body: some View {
        HStack(spacing: 5) {
            Image(systemName: icon).font(.system(size: 11, weight: .semibold)).foregroundStyle(color)
            Text("\(value) \(label)").font(.system(size: 12, weight: .semibold)).foregroundStyle(color)
        }
        .padding(.horizontal, 10).padding(.vertical, 6).background(color.opacity(0.1)).clipShape(Capsule())
    }
}

// MARK: - Loading

private struct PracticeLoadingView: View {
    var body: some View {
        ScrollView {
            VStack(spacing: 20) {
                VStack(spacing: 10) {
                    ProgressView().scaleEffect(1.2).tint(Color.warmAmber)
                    Text("Generating questions...").font(.system(size: 15)).foregroundStyle(.secondary)
                }
                .padding(.top, 32).padding(.bottom, 8)
                ForEach(0..<3, id: \.self) { _ in
                    RoundedRectangle(cornerRadius: 14).fill(Color.warmCard)
                        .frame(maxWidth: .infinity).frame(height: 140)
                        .overlay(ShimmerView().clipShape(RoundedRectangle(cornerRadius: 14)))
                        .padding(.horizontal, 16)
                }
            }
            .padding(.top, 8)
        }
        .background(Color.warmBg.ignoresSafeArea())
    }
}

// MARK: - Question Card

private struct QuestionCardView: View {
    let exercise: PracticeExercise
    let index: Int
    let total: Int
    @Binding var userAnswer: String
    let isEvaluating: Bool
    let onCheck: () async -> Void

    @FocusState private var answerFocused: Bool
    @State private var selectedOption: String? = nil

    private var diffColor: Color {
        switch exercise.difficulty.lowercased() {
        case "easy":   return .warmGreen
        case "medium": return .warmGold
        case "hard":   return .warmRed
        default:       return .secondary
        }
    }
    private var answerIsEmpty: Bool { userAnswer.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                // Progress header
                VStack(alignment: .leading, spacing: 6) {
                    HStack {
                        Text("Question \(index + 1) of \(total)")
                            .font(.system(size: 12, weight: .semibold)).foregroundStyle(.secondary)
                            .textCase(.uppercase).tracking(0.5)
                        Spacer()
                        HStack(spacing: 6) {
                            if exercise.isMultipleChoice {
                                Text("MCQ").font(.system(size: 11, weight: .semibold))
                                    .foregroundStyle(Color.accentBlue).padding(.horizontal, 7).padding(.vertical, 3)
                                    .background(Color.accentBlue.opacity(0.1)).clipShape(Capsule())
                            }
                            Text(exercise.difficulty.capitalized)
                                .font(.system(size: 12, weight: .semibold)).foregroundStyle(diffColor)
                                .padding(.horizontal, 10).padding(.vertical, 4)
                                .background(diffColor.opacity(0.12)).clipShape(Capsule())
                        }
                    }
                    ProgressView(value: Double(index + 1), total: Double(total)).tint(Color.warmAmber)
                }

                // Question
                VStack(alignment: .leading, spacing: 8) {
                    Text(exercise.questionText)
                        .font(.system(size: 17, weight: .semibold)).lineSpacing(4)
                        .fixedSize(horizontal: false, vertical: true).frame(maxWidth: .infinity, alignment: .leading)
                    if let hint = exercise.source_hint {
                        Label(hint, systemImage: "doc.text").font(.system(size: 12)).foregroundStyle(.tertiary).padding(.top, 4)
                    }
                }
                .padding(16).frame(maxWidth: .infinity, alignment: .leading).background(Color.warmCard)
                .clipShape(RoundedRectangle(cornerRadius: 14))
                .overlay(RoundedRectangle(cornerRadius: 14).stroke(Color.warmAmber.opacity(0.15), lineWidth: 0.8))
                .shadow(color: Color.warmAmber.opacity(0.08), radius: 6, x: 0, y: 3)

                // Answer area
                if exercise.isMultipleChoice, let options = exercise.options {
                    MCQOptionsView(options: options, selectedOption: $selectedOption, userAnswer: $userAnswer)
                } else {
                    FreeTextAnswerView(userAnswer: $userAnswer, answerFocused: _answerFocused)
                }

                // Check button
                Button { Task { await onCheck() } } label: {
                    HStack(spacing: 8) {
                        if isEvaluating {
                            ProgressView().tint(.white)
                            Text("Evaluating...").font(.system(size: 16, weight: .semibold))
                        } else {
                            Image(systemName: "checkmark.seal").font(.system(size: 14))
                            Text("Check Answer").font(.system(size: 16, weight: .semibold))
                        }
                    }
                    .frame(maxWidth: .infinity).padding(.vertical, 15)
                    .background(answerIsEmpty || isEvaluating ? Color(UIColor.systemGray4) : Color.warmAmber)
                    .foregroundStyle(.white).clipShape(RoundedRectangle(cornerRadius: 14))
                }
                .disabled(answerIsEmpty || isEvaluating)
            }
            .padding(16)
        }
        .background(Color.warmBg.ignoresSafeArea())
        .onAppear { selectedOption = nil; if !exercise.isMultipleChoice { answerFocused = true } }
    }
}

private struct MCQOptionsView: View {
    let options: [String]
    @Binding var selectedOption: String?
    @Binding var userAnswer: String
    var body: some View {
        VStack(spacing: 8) {
            ForEach(options, id: \.self) { option in
                let sel = selectedOption == option
                Button { selectedOption = option; userAnswer = option } label: {
                    HStack(spacing: 12) {
                        ZStack {
                            Circle()
                                .strokeBorder(sel ? Color.warmAmber : Color(UIColor.separator).opacity(0.5), lineWidth: sel ? 2 : 1)
                                .background(Circle().fill(sel ? Color.warmAmber : .clear)).frame(width: 22, height: 22)
                            if sel { Image(systemName: "checkmark").font(.system(size: 10, weight: .bold)).foregroundStyle(.white) }
                        }
                        Text(option).font(.system(size: 15)).foregroundStyle(.primary)
                            .frame(maxWidth: .infinity, alignment: .leading).multilineTextAlignment(.leading)
                    }
                    .padding(14)
                    .background(sel ? Color.warmAmber.opacity(0.07) : Color.warmCard)
                    .clipShape(RoundedRectangle(cornerRadius: 12))
                    .overlay(RoundedRectangle(cornerRadius: 12).stroke(
                        sel ? Color.warmAmber.opacity(0.5) : Color(UIColor.separator).opacity(0.3),
                        lineWidth: sel ? 1.5 : 0.5))
                }
                .buttonStyle(.plain).animation(.easeInOut(duration: 0.15), value: sel)
            }
        }
    }
}

private struct FreeTextAnswerView: View {
    @Binding var userAnswer: String
    @FocusState var answerFocused: Bool
    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Your answer").font(.system(size: 11, weight: .semibold)).foregroundStyle(.secondary)
                .textCase(.uppercase).tracking(0.6)
            ZStack(alignment: .topLeading) {
                TextEditor(text: $userAnswer).font(.system(size: 15)).frame(minHeight: 120).focused($answerFocused)
                if userAnswer.isEmpty {
                    Text("Type your answer here...").font(.system(size: 15))
                        .foregroundStyle(Color(UIColor.placeholderText)).padding(.top, 8).padding(.leading, 5).allowsHitTesting(false)
                }
            }
            .padding(10).background(Color.warmCard).clipShape(RoundedRectangle(cornerRadius: 12))
            .overlay(RoundedRectangle(cornerRadius: 12).stroke(
                answerFocused ? Color.warmAmber : Color.warmAmber.opacity(0.2), lineWidth: answerFocused ? 2 : 0.8))
        }
    }
}

// MARK: - Result View

private struct ResultView: View {
    let exercise: PracticeExercise
    let evalResponse: EvaluateResponse
    let isLast: Bool
    let onNext: () -> Void
    let onSeeScore: () -> Void

    private var scoreIcon: String {
        switch evalResponse.score {
        case "correct": return "checkmark.circle.fill"
        case "partial":  return "minus.circle.fill"
        default:         return "xmark.circle.fill"
        }
    }
    private var scoreColor: Color {
        switch evalResponse.score {
        case "correct": return .warmGreen
        case "partial":  return .warmYellow
        default:         return .warmRed
        }
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                // Score banner
                HStack(spacing: 12) {
                    Image(systemName: scoreIcon).font(.system(size: 36)).foregroundStyle(scoreColor)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(evalResponse.score.capitalized).font(.system(size: 20, weight: .bold)).foregroundStyle(scoreColor)
                        Text(exercise.question).font(.system(size: 13)).foregroundStyle(.secondary).lineLimit(2)
                    }
                }
                .padding(16).frame(maxWidth: .infinity, alignment: .leading)
                .background(scoreColor.opacity(0.08)).clipShape(RoundedRectangle(cornerRadius: 14))
                .overlay(RoundedRectangle(cornerRadius: 14).stroke(scoreColor.opacity(0.35), lineWidth: 1.5))

                // Feedback
                InfoCard(label: "Feedback", icon: "text.bubble") {
                    Text(evalResponse.feedback).font(.system(size: 15)).lineSpacing(4)
                }

                // Key gap
                if let gap = evalResponse.key_gap {
                    InfoCard(label: "Key gap", icon: "exclamationmark.triangle") {
                        Text(gap).font(.system(size: 13)).foregroundStyle(.secondary).lineSpacing(3)
                    }
                }

                // Correct answer
                VStack(alignment: .leading, spacing: 8) {
                    Label("Correct answer", systemImage: "lightbulb")
                        .font(.system(size: 11, weight: .semibold)).foregroundStyle(.tertiary)
                        .textCase(.uppercase).tracking(0.6)
                    Text(exercise.answer).font(.system(size: 15)).lineSpacing(3)
                }
                .padding(14).frame(maxWidth: .infinity, alignment: .leading)
                .background(Color.warmGreen.opacity(0.07)).clipShape(RoundedRectangle(cornerRadius: 14))
                .overlay(RoundedRectangle(cornerRadius: 14).stroke(Color.warmGreen.opacity(0.2), lineWidth: 0.8))

                if isLast {
                    Button { onSeeScore() } label: {
                        HStack(spacing: 8) {
                            Image(systemName: "chart.bar").font(.system(size: 14))
                            Text("See Score").font(.system(size: 16, weight: .semibold))
                        }
                        .frame(maxWidth: .infinity).padding(.vertical, 15)
                        .background(Color.warmAmber).foregroundStyle(.white).clipShape(RoundedRectangle(cornerRadius: 14))
                    }
                } else {
                    Button { onNext() } label: {
                        HStack(spacing: 8) {
                            Text("Next Question").font(.system(size: 16, weight: .semibold))
                            Image(systemName: "arrow.right").font(.system(size: 14, weight: .semibold))
                        }
                        .frame(maxWidth: .infinity).padding(.vertical, 15)
                        .background(Color.warmAmber).foregroundStyle(.white).clipShape(RoundedRectangle(cornerRadius: 14))
                    }
                }
            }
            .padding(20)
        }
        .background(Color.warmBg.ignoresSafeArea())
    }
}

private struct InfoCard<Content: View>: View {
    let label: String; let icon: String; @ViewBuilder let content: () -> Content
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Label(label, systemImage: icon)
                .font(.system(size: 11, weight: .semibold)).foregroundStyle(.tertiary)
                .textCase(.uppercase).tracking(0.6)
            content()
        }
        .padding(14).frame(maxWidth: .infinity, alignment: .leading).background(Color.warmCard)
        .clipShape(RoundedRectangle(cornerRadius: 14))
        .shadow(color: Color.warmAmber.opacity(0.06), radius: 4, x: 0, y: 2)
    }
}

// MARK: - Feynman Reflection View

private struct FeynmanView: View {
    let exercise: PracticeExercise
    let evalResponse: EvaluateResponse
    let onContinue: () -> Void

    @State private var reflection = ""
    @FocusState private var focused: Bool
    private var reflIsEmpty: Bool { reflection.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                VStack(alignment: .leading, spacing: 6) {
                    HStack(spacing: 8) {
                        Image(systemName: "brain.head.profile").font(.system(size: 18)).foregroundStyle(Color.warmAmber)
                        Text("Feynman check").font(.system(size: 20, weight: .bold))
                    }
                    Text("The best way to lock in a concept is to explain it yourself. Don't look at the answer — write it in your own words.")
                        .font(.system(size: 14)).foregroundStyle(.secondary).lineSpacing(3)
                }
                .padding(.top, 4)

                if let gap = evalResponse.key_gap {
                    VStack(alignment: .leading, spacing: 6) {
                        Label("What to focus on", systemImage: "target")
                            .font(.system(size: 11, weight: .semibold)).foregroundStyle(.tertiary)
                            .textCase(.uppercase).tracking(0.6)
                        Text(gap).font(.system(size: 14)).foregroundStyle(.primary).lineSpacing(3)
                    }
                    .padding(14).frame(maxWidth: .infinity, alignment: .leading)
                    .background(Color.warmGold.opacity(0.08)).clipShape(RoundedRectangle(cornerRadius: 12))
                    .overlay(RoundedRectangle(cornerRadius: 12).stroke(Color.warmAmber.opacity(0.2), lineWidth: 1))
                }

                VStack(alignment: .leading, spacing: 6) {
                    Text("Explain it in your own words")
                        .font(.system(size: 11, weight: .semibold)).foregroundStyle(.secondary)
                        .textCase(.uppercase).tracking(0.6)
                    ZStack(alignment: .topLeading) {
                        TextEditor(text: $reflection).font(.system(size: 15)).frame(minHeight: 100).focused($focused)
                        if reflection.isEmpty {
                            Text("Pretend you're explaining this to someone who's never heard of it...")
                                .font(.system(size: 15)).foregroundStyle(Color(UIColor.placeholderText))
                                .padding(.top, 8).padding(.leading, 5).allowsHitTesting(false)
                        }
                    }
                    .padding(10).background(Color.warmCard).clipShape(RoundedRectangle(cornerRadius: 12))
                    .overlay(RoundedRectangle(cornerRadius: 12).stroke(
                        focused ? Color.warmAmber : Color.warmAmber.opacity(0.2), lineWidth: focused ? 2 : 0.8))
                }

                Button { onContinue() } label: {
                    HStack(spacing: 8) {
                        Text(reflIsEmpty ? "Skip for now" : "Got it — Next Question").font(.system(size: 16, weight: .semibold))
                        if !reflIsEmpty { Image(systemName: "arrow.right").font(.system(size: 14, weight: .semibold)) }
                    }
                    .frame(maxWidth: .infinity).padding(.vertical, 15)
                    .background(reflIsEmpty ? Color(UIColor.systemGray4) : Color.warmAmber)
                    .foregroundStyle(.white).clipShape(RoundedRectangle(cornerRadius: 14))
                }
                .animation(.easeInOut(duration: 0.15), value: reflIsEmpty)
            }
            .padding(20)
        }
        .background(Color.warmBg.ignoresSafeArea())
        .onAppear { focused = true }
    }
}

// MARK: - Summary View

private struct SummaryView: View {
    let exercises: [PracticeExercise]
    let evaluations: [Int: EvaluateResponse]
    let topic: String
    let srsNextReview: String?
    let onPracticeAgain: () -> Void

    private var correctCount: Int { evaluations.values.filter { $0.score == "correct" }.count }
    private var partialCount: Int  { evaluations.values.filter { $0.score == "partial" }.count }
    private var masteryPct: Int {
        guard !exercises.isEmpty else { return 0 }
        return Int(((Double(correctCount) + Double(partialCount) * 0.5) / Double(exercises.count)) * 100)
    }
    private var masteryColor: Color { masteryPct >= 80 ? .warmGreen : masteryPct >= 40 ? .warmGold : .warmRed }
    private var nextReviewLabel: String? {
        guard let nr = srsNextReview else { return nil }
        let fmt = DateFormatter(); fmt.dateFormat = "yyyy-MM-dd"
        guard let date = fmt.date(from: nr) else { return nr }
        let days = Calendar.current.dateComponents([.day], from: Date(), to: date).day ?? 0
        if days == 0 { return "Review again: today" }
        if days == 1 { return "Review again: tomorrow" }
        fmt.dateStyle = .medium; fmt.timeStyle = .none
        return "Review again: \(fmt.string(from: date))"
    }

    var body: some View {
        ScrollView {
            VStack(spacing: 24) {
                // Score header
                VStack(spacing: 8) {
                    Text("\(correctCount) / \(exercises.count)")
                        .font(.system(size: 52, weight: .bold, design: .rounded)).tracking(-1).foregroundStyle(masteryColor)
                    Text("correct on \(topic)").font(.system(size: 16)).foregroundStyle(.secondary).multilineTextAlignment(.center)
                    VStack(alignment: .leading, spacing: 4) {
                        HStack {
                            Text("Session mastery").font(.system(size: 11, weight: .semibold)).foregroundStyle(.tertiary)
                                .textCase(.uppercase).tracking(0.5)
                            Spacer()
                            Text("\(masteryPct)%").font(.system(size: 11, weight: .semibold)).foregroundStyle(masteryColor)
                        }
                        GeometryReader { geo in
                            ZStack(alignment: .leading) {
                                RoundedRectangle(cornerRadius: 3).fill(Color(UIColor.systemGray5)).frame(height: 6)
                                RoundedRectangle(cornerRadius: 3).fill(masteryColor)
                                    .frame(width: geo.size.width * CGFloat(masteryPct) / 100, height: 6)
                                    .animation(.spring(response: 0.6, dampingFraction: 0.8), value: masteryPct)
                            }
                        }
                        .frame(height: 6)
                    }
                    .padding(.horizontal, 24).padding(.top, 4)
                }
                .padding(.top, 16)

                // Per-question list
                VStack(spacing: 8) {
                    ForEach(Array(exercises.enumerated()), id: \.offset) { idx, ex in
                        HStack(spacing: 12) {
                            let ev = evaluations[idx]
                            Image(systemName: qIcon(ev?.score)).font(.system(size: 16))
                                .foregroundStyle(qColor(ev?.score)).frame(width: 24)
                            VStack(alignment: .leading, spacing: 2) {
                                Text(ex.question).font(.system(size: 14)).foregroundStyle(.primary)
                                    .lineLimit(2).frame(maxWidth: .infinity, alignment: .leading)
                                if let e = evaluations[idx] {
                                    Text(e.score.capitalized).font(.system(size: 12)).foregroundStyle(qColor(e.score))
                                }
                            }
                        }
                        .padding(12).background(Color.warmCard).clipShape(RoundedRectangle(cornerRadius: 14))
                        .shadow(color: Color.warmAmber.opacity(0.05), radius: 2, x: 0, y: 1)
                    }
                }
                .padding(.horizontal, 16)

                // SRS next review
                if let label = nextReviewLabel {
                    HStack(spacing: 10) {
                        Image(systemName: "calendar.badge.clock").font(.system(size: 16)).foregroundStyle(Color.warmAmber)
                        Text(label).font(.system(size: 14)).foregroundStyle(.primary)
                        Spacer()
                        Image(systemName: "checkmark.circle.fill").font(.system(size: 14)).foregroundStyle(Color.warmGreen)
                    }
                    .padding(14).background(Color.warmAmber.opacity(0.07)).clipShape(RoundedRectangle(cornerRadius: 12))
                    .overlay(RoundedRectangle(cornerRadius: 12).stroke(Color.warmAmber.opacity(0.2), lineWidth: 1))
                    .padding(.horizontal, 16)
                }

                // Buttons
                VStack(spacing: 12) {
                    ShareLink(item: "I scored \(correctCount)/\(exercises.count) on \(topic) with Neuron") {
                        HStack(spacing: 8) {
                            Image(systemName: "square.and.arrow.up").font(.system(size: 14))
                            Text("Share Result").font(.system(size: 16, weight: .semibold))
                        }
                        .frame(maxWidth: .infinity).padding(.vertical, 15)
                        .background(Color.warmAmber).foregroundStyle(.white).clipShape(RoundedRectangle(cornerRadius: 14))
                    }
                    .buttonStyle(.plain)

                    Button { onPracticeAgain() } label: {
                        HStack(spacing: 8) {
                            Image(systemName: "arrow.counterclockwise").font(.system(size: 14))
                            Text("Practice Again").font(.system(size: 16, weight: .semibold))
                        }
                        .frame(maxWidth: .infinity).padding(.vertical, 15)
                        .background(Color.warmAmber.opacity(0.1)).foregroundStyle(Color.warmAmber)
                        .clipShape(RoundedRectangle(cornerRadius: 14))
                    }
                }
                .padding(.horizontal, 16).padding(.bottom, 32)
            }
        }
        .background(Color.warmBg.ignoresSafeArea())
    }

    private func qIcon(_ score: String?) -> String {
        switch score {
        case "correct": return "checkmark.circle.fill"
        case "partial":  return "minus.circle.fill"
        default:         return "xmark.circle.fill"
        }
    }
    private func qColor(_ score: String?) -> Color {
        switch score {
        case "correct": return .warmGreen
        case "partial":  return .warmYellow
        default:         return .warmRed
        }
    }
}
