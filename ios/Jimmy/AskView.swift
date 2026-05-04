import SwiftUI
import UIKit

// MARK: - Query Mode

enum QueryMode: String, CaseIterable {
    case ask = "Ask"
    case context = "Context"
    case resurface = "Resurface"
    case connections = "Connections"

    var icon: String {
        switch self {
        case .ask:         return "sparkles"
        case .context:     return "doc.text.magnifyingglass"
        case .resurface:   return "arrow.counterclockwise"
        case .connections: return "link"
        }
    }
}

struct AskView: View {
    @EnvironmentObject var api: APIClient
    @EnvironmentObject var settings: AppSettings
    @State private var query = ""
    @State private var isStreaming = false
    @State private var streamingStarted = false
    @State private var sources: [SourceChunk] = []
    @State private var streamVersion: Int = 0
    @State private var streamingTask: Task<Void, Never>? = nil
    @State private var suggestions: [String] = []
    @State private var suggestionsLoaded = false
    @State private var selectedQueryMode: QueryMode = .ask
    @FocusState private var inputFocused: Bool

    // Search-result model: one result at a time, with history for back navigation
    @State private var currentQuestion: String = ""
    @State private var currentAnswer: String = ""
    @State private var currentAnswerID: UUID = UUID()
    @State private var history: [HistoryEntry] = []  // previous questions (for back nav)
    @State private var followUpChips: [String] = []

    struct HistoryEntry: Identifiable {
        let id = UUID()
        let question: String
        let answer: String
        let sources: [SourceChunk]
    }

    private var hasResult: Bool { !currentQuestion.isEmpty }

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                if hasResult {
                    // MARK: Result view — search result page
                    resultView
                } else {
                    // MARK: Empty state — search start page
                    emptyState
                }

                // MARK: Input bar — pill-shaped search bar, always at bottom
                searchBar
            }
            .background(Color(hex: "faf9f7"))
            .navigationTitle("")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    if hasResult && !history.isEmpty {
                        Button {
                            navigateBack()
                        } label: {
                            HStack(spacing: 4) {
                                Image(systemName: "chevron.left")
                                    .font(.system(size: 13, weight: .semibold))
                                Text("Back")
                                    .font(.system(size: 15))
                            }
                            .foregroundStyle(Color(hex: "#0071e3"))
                        }
                    }
                }
                ToolbarItem(placement: .principal) {
                    if hasResult && sources.count > 0 {
                        VStack(spacing: 1) {
                            Text("Answer")
                                .font(.system(size: 13, weight: .semibold))
                                .foregroundStyle(.primary)
                            Text("from \(sources.count) source\(sources.count == 1 ? "" : "s")")
                                .font(.system(size: 11))
                                .foregroundStyle(Color(hex: "#0071e3"))
                        }
                        .transition(.opacity.combined(with: .scale(scale: 0.95)))
                        .animation(.easeInOut(duration: 0.2), value: sources.count)
                    }
                }
                ToolbarItem(placement: .topBarTrailing) {
                    if hasResult {
                        Button {
                            withAnimation(.easeInOut(duration: 0.2)) {
                                clearAll()
                            }
                            if settings.hapticEnabled {
                                UIImpactFeedbackGenerator(style: .light).impactOccurred()
                            }
                        } label: {
                            Text("Clear")
                                .font(.system(size: 15))
                                .foregroundStyle(Color(hex: "#0071e3"))
                        }
                    }
                }
            }
            .onAppear {
                guard !suggestionsLoaded else { return }
                Task { await loadSuggestions() }
            }
            .onChange(of: settings.pendingAskQuery) { _, newVal in
                if let q = newVal, !q.isEmpty {
                    query = q
                    settings.pendingAskQuery = nil
                    DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) {
                        sendQuery()
                    }
                }
            }
        }
    }

    // MARK: - Result View

    private var resultView: some View {
        ScrollViewReader { proxy in
            ScrollView {
                VStack(alignment: .leading, spacing: 0) {

                    // History breadcrumb strip (if there are previous questions)
                    if !history.isEmpty {
                        historyStrip
                            .padding(.horizontal, 16)
                            .padding(.top, 12)
                            .padding(.bottom, 8)
                    }

                    // Question headline
                    Text(currentQuestion)
                        .font(.system(size: 22, weight: .bold))
                        .tracking(-0.5)
                        .foregroundStyle(.primary)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.horizontal, 16)
                        .padding(.top, history.isEmpty ? 16 : 4)
                        .padding(.bottom, 12)

                    // Sources bar — above the answer (appears as soon as sources arrive)
                    if !sources.isEmpty {
                        SourcesBar(sources: sources)
                            .padding(.horizontal, 16)
                            .padding(.bottom, 16)
                            .transition(.opacity.combined(with: .move(edge: .top)))
                            .id("sources")
                    }

                    Divider()
                        .padding(.horizontal, 16)
                        .padding(.bottom, 16)

                    // Answer as clean prose
                    if isStreaming && !streamingStarted && currentAnswer.isEmpty {
                        TypingIndicatorView()
                            .padding(.horizontal, 16)
                            .padding(.bottom, 16)
                    } else {
                        VStack(alignment: .leading, spacing: 12) {
                            let displayAnswer = currentAnswer.isEmpty ? "…" : (isStreaming ? currentAnswer + "▋" : currentAnswer)
                            Text(currentAnswer.isEmpty
                                    ? AttributedString("…")
                                    : renderMarkdown(displayAnswer))
                                .font(.system(size: 16))
                                .lineSpacing(5)
                                .foregroundStyle(.primary)
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .textSelection(.enabled)
                                .id("answer-\(currentAnswerID)")
                                .contextMenu {
                                    Button {
                                        UIPasteboard.general.string = currentAnswer
                                        if settings.hapticEnabled {
                                            UINotificationFeedbackGenerator().notificationOccurred(.success)
                                        }
                                    } label: {
                                        Label("Copy", systemImage: "doc.on.doc")
                                    }
                                    if !currentAnswer.isEmpty {
                                        ShareLink(item: buildShareText()) {
                                            Label("Share", systemImage: "square.and.arrow.up")
                                        }
                                        Button {
                                            if #available(iOS 16.0, *) {
                                                if let image = generateAskShareImage(question: currentQuestion, answer: currentAnswer, sourceCount: sources.count) {
                                                    let av = UIActivityViewController(activityItems: [image], applicationActivities: nil)
                                                    UIApplication.shared.connectedScenes
                                                        .compactMap { $0 as? UIWindowScene }
                                                        .first?.windows.first?.rootViewController?
                                                        .present(av, animated: true)
                                                }
                                            }
                                        } label: {
                                            Label("Share as Image", systemImage: "photo")
                                        }
                                    }
                                }

                            // Action buttons — appear when done
                            if !isStreaming && !currentAnswer.isEmpty {
                                answerActions
                                    .transition(.opacity)
                            }
                        }
                        .padding(.horizontal, 16)
                        .padding(.bottom, 24)
                    }

                    // Follow-up chips — shown after streaming completes
                    if !isStreaming && !currentAnswer.isEmpty && !followUpChips.isEmpty {
                        followUpChipsView
                            .padding(.horizontal, 16)
                            .padding(.bottom, 20)
                            .transition(.opacity.combined(with: .move(edge: .bottom)))
                    }

                    Color.clear.frame(height: 1).id("bottom")
                }
                .animation(.spring(response: 0.4, dampingFraction: 0.85), value: sources.count)
            }
            .scrollDismissesKeyboard(.interactively)
            .onChange(of: streamVersion) { _, _ in
                proxy.scrollTo("bottom", anchor: .bottom)
            }
            .onChange(of: sources.count) { _, _ in
                withAnimation { proxy.scrollTo("sources", anchor: .top) }
            }
        }
    }

    // MARK: - Answer Actions

    @State private var showCopyConfirm = false

    private var answerActions: some View {
        HStack(spacing: 16) {
            Button {
                UIPasteboard.general.string = currentAnswer
                withAnimation(.spring(response: 0.2)) { showCopyConfirm = true }
                DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) {
                    withAnimation { showCopyConfirm = false }
                }
            } label: {
                Label(
                    showCopyConfirm ? "Copied" : "Copy",
                    systemImage: showCopyConfirm ? "checkmark" : "doc.on.doc"
                )
                .font(.system(size: 12, weight: .medium))
                .foregroundStyle(showCopyConfirm ? Color.green : Color(UIColor.tertiaryLabel))
            }
            .buttonStyle(.plain)
            .animation(.easeInOut(duration: 0.15), value: showCopyConfirm)

            let shareText = buildShareText()
            ShareLink(item: shareText, subject: Text("From Jimmy"), message: Text("")) {
                Label("Share", systemImage: "square.and.arrow.up")
                    .font(.system(size: 12, weight: .medium))
                    .foregroundStyle(Color(UIColor.tertiaryLabel))
            }
            .buttonStyle(.plain)
        }
        .padding(.top, 4)
    }

    // MARK: - Follow-up Chips

    private var followUpChipsView: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Follow-up")
                .font(.system(size: 11, weight: .semibold))
                .foregroundStyle(.tertiary)
                .textCase(.uppercase)
                .tracking(0.6)
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 8) {
                    ForEach(followUpChips, id: \.self) { chip in
                        Button {
                            query = chip
                            if settings.hapticEnabled {
                                UISelectionFeedbackGenerator().selectionChanged()
                            }
                            sendQuery()
                        } label: {
                            HStack(spacing: 5) {
                                Image(systemName: "arrow.turn.down.right")
                                    .font(.system(size: 10, weight: .medium))
                                Text(chip)
                                    .font(.system(size: 13))
                                    .lineLimit(1)
                            }
                            .foregroundStyle(Color(hex: "#0071e3"))
                            .padding(.horizontal, 12)
                            .padding(.vertical, 8)
                            .background(Color(hex: "#0071e3").opacity(0.08))
                            .clipShape(Capsule())
                            .overlay(Capsule().stroke(Color(hex: "#0071e3").opacity(0.2), lineWidth: 0.5))
                        }
                        .buttonStyle(.plain)
                    }
                }
            }
        }
    }

    private func generateFollowUpChips(for question: String) -> [String] {
        // Extract first key noun from question (simple heuristic: first non-question-word noun)
        let stopWords: Set<String> = ["what", "who", "where", "when", "why", "how", "is", "are", "was", "were",
                                       "the", "a", "an", "do", "does", "did", "can", "could", "my", "me", "i",
                                       "tell", "me", "about", "give", "show", "find", "list", "explain"]
        let words = question.lowercased()
            .components(separatedBy: .whitespacesAndNewlines)
            .map { $0.trimmingCharacters(in: .punctuationCharacters) }
            .filter { !$0.isEmpty && !stopWords.contains($0) && $0.count > 2 }
        let keyNoun = words.first.map { $0.prefix(1).uppercased() + $0.dropFirst() } ?? "this topic"
        return [
            "Tell me more about \(keyNoun)",
            "How does this relate to what I've studied?",
            "What are the most important takeaways?"
        ]
    }

    private func buildShareText() -> String {
        var parts: [String] = []
        if !currentQuestion.isEmpty { parts.append("Q: \(currentQuestion)") }
        let answer = currentAnswer.prefix(600).description
        parts.append(answer)
        parts.append("— Answered by Jimmy, my personal second brain")
        return parts.joined(separator: "\n\n")
    }

    @MainActor
    private func generateAskShareImage(question: String, answer: String, sourceCount: Int) -> UIImage? {
        if #available(iOS 16.0, *) {
            let view = AskShareCard(question: question, answer: answer, sourceCount: sourceCount)
            let renderer = ImageRenderer(content: view)
            renderer.scale = 3.0
            return renderer.uiImage
        }
        return nil
    }

    // MARK: - History Strip

    private var historyStrip: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 6) {
                ForEach(history) { entry in
                    Button {
                        // Tapping a history entry restores it
                        restoreHistoryEntry(entry)
                    } label: {
                        Text(entry.question)
                            .font(.system(size: 12))
                            .foregroundStyle(Color(UIColor.secondaryLabel))
                            .lineLimit(1)
                            .padding(.horizontal, 10)
                            .padding(.vertical, 5)
                            .background(Color(hex: "faf9f7"))
                            .clipShape(Capsule())
                            .overlay(Capsule().stroke(Color(UIColor.separator).opacity(0.4), lineWidth: 0.5))
                    }
                    .buttonStyle(.plain)
                }

                // Current question badge (non-tappable, highlighted)
                Text(currentQuestion)
                    .font(.system(size: 12, weight: .medium))
                    .foregroundStyle(Color(hex: "#0071e3"))
                    .lineLimit(1)
                    .padding(.horizontal, 10)
                    .padding(.vertical, 5)
                    .background(Color(hex: "#0071e3").opacity(0.08))
                    .clipShape(Capsule())
                    .overlay(Capsule().stroke(Color(hex: "#0071e3").opacity(0.25), lineWidth: 0.5))
            }
        }
    }

    // MARK: - Empty State

    private var emptyState: some View {
        GeometryReader { geo in
            ScrollView {
                VStack(spacing: 28) {
                    Spacer().frame(height: 32)

                    // Simple heading
                    Text("What do you want to know?")
                        .font(.system(size: 20, weight: .semibold))
                        .tracking(-0.3)
                        .foregroundStyle(.primary)
                        .multilineTextAlignment(.center)

                    // Query mode pills
                    queryModePills

                    // Suggestion cards — 2-column grid
                    let chips = suggestions.isEmpty ? defaultSuggestions : suggestions
                    LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 10) {
                        ForEach(Array(chips.prefix(6).enumerated()), id: \.element) { _, s in
                            Button {
                                query = s
                                sendQuery()
                            } label: {
                                Text(s)
                                    .font(.system(size: 13))
                                    .foregroundStyle(.primary)
                                    .multilineTextAlignment(.leading)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                                    .padding(12)
                                    .background(Color(hex: "faf9f7"))
                                    .clipShape(RoundedRectangle(cornerRadius: 10))
                            }
                            .buttonStyle(.plain)
                        }
                    }
                    .padding(.horizontal, 16)

                    // Explore threads
                    VStack(alignment: .leading, spacing: 10) {
                        Text("Explore a Thread")
                            .font(.system(size: 11, weight: .semibold))
                            .foregroundStyle(.tertiary)
                            .textCase(.uppercase)
                            .tracking(0.6)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(.horizontal, 16)

                        LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 8) {
                            ForEach(exploreThreads, id: \.title) { thread in
                                Button {
                                    query = thread.query
                                    sendQuery()
                                } label: {
                                    VStack(alignment: .leading, spacing: 4) {
                                        Text(thread.emoji)
                                            .font(.system(size: 20))
                                        Text(thread.title)
                                            .font(.system(size: 13, weight: .semibold))
                                            .foregroundStyle(.primary)
                                            .lineLimit(2)
                                            .multilineTextAlignment(.leading)
                                        Text(thread.subtitle)
                                            .font(.system(size: 11))
                                            .foregroundStyle(.secondary)
                                            .lineLimit(1)
                                    }
                                    .frame(maxWidth: .infinity, alignment: .leading)
                                    .padding(12)
                                    .background(Color(hex: "faf9f7"))
                                    .clipShape(RoundedRectangle(cornerRadius: 12))
                                }
                                .buttonStyle(.plain)
                            }
                        }
                        .padding(.horizontal, 16)
                    }

                    Spacer()
                }
                .frame(width: geo.size.width)
            }
            .scrollDismissesKeyboard(.interactively)
        }
    }

    // MARK: - Query Mode Pills

    private var queryModePills: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                ForEach(QueryMode.allCases, id: \.self) { mode in
                    Button {
                        withAnimation(.easeInOut(duration: 0.18)) {
                            selectedQueryMode = mode
                        }
                        if settings.hapticEnabled {
                            UISelectionFeedbackGenerator().selectionChanged()
                        }
                    } label: {
                        HStack(spacing: 5) {
                            Image(systemName: mode.icon)
                                .font(.system(size: 11, weight: selectedQueryMode == mode ? .semibold : .regular))
                            Text(mode.rawValue)
                                .font(.system(size: 13, weight: selectedQueryMode == mode ? .semibold : .regular))
                        }
                        .foregroundStyle(selectedQueryMode == mode ? .white : Color(UIColor.secondaryLabel))
                        .padding(.horizontal, 12)
                        .padding(.vertical, 7)
                        .background(
                            selectedQueryMode == mode
                                ? Color.black
                                : Color.clear
                        )
                        .clipShape(Capsule())
                        .overlay(
                            Capsule().stroke(
                                selectedQueryMode == mode
                                    ? Color.clear
                                    : Color(UIColor.separator).opacity(0.6),
                                lineWidth: 1
                            )
                        )
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(.horizontal, 20)
            .padding(.vertical, 2)
        }
    }

    // MARK: - Search Bar

    private var searchBar: some View {
        VStack(spacing: 0) {
            // Hairline separator only when showing results
            if hasResult { Divider() }

            // Query mode pills: show when focused (in result view) or always in empty state
            if hasResult && inputFocused {
                queryModePills
                    .padding(.top, 8)
                    .transition(.opacity.combined(with: .move(edge: .bottom)))
            }

            HStack(spacing: 10) {
                Image(systemName: "magnifyingglass")
                    .font(.system(size: 15, weight: .medium))
                    .foregroundStyle(Color(UIColor.tertiaryLabel))

                TextField(hasResult ? "Ask a follow-up…" : "Ask anything about your second brain…", text: $query, axis: .vertical)
                    .textFieldStyle(.plain)
                    .font(.system(size: 15))
                    .lineLimit(1...5)
                    .focused($inputFocused)
                    .submitLabel(.search)
                    .autocorrectionDisabled(false)
                    .onSubmit { sendQuery() }

                if !query.isEmpty || isStreaming {
                    Button(action: isStreaming ? cancelStreaming : sendQuery) {
                        Image(systemName: isStreaming ? "stop.circle.fill" : "arrow.up.circle.fill")
                            .font(.system(size: 28))
                            .foregroundStyle(
                                isStreaming
                                    ? Color(hex: "#0071e3")
                                    : (query.isEmpty ? Color(UIColor.quaternaryLabel) : Color(hex: "#0071e3"))
                            )
                            .animation(.spring(response: 0.25, dampingFraction: 0.7), value: isStreaming)
                    }
                    .disabled(query.isEmpty && !isStreaming)
                    .scaleEffect(isStreaming ? 1.05 : 1.0)
                    .animation(.spring(response: 0.25, dampingFraction: 0.7), value: isStreaming)
                }
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 10)
            .background(Color(hex: "faf9f7"))
            .clipShape(RoundedRectangle(cornerRadius: 20))
            .shadow(color: Color.black.opacity(0.06), radius: 6, x: 0, y: 2)
            .padding(.horizontal, 12)
            .padding(.top, 10)
            .padding(.bottom, 8)
            .padding(.bottom, hasResult ? 0 : 4)
        }
        .background(.regularMaterial)
    }

    // MARK: - Navigation

    private func navigateBack() {
        guard let previous = history.last else { return }
        // Push current back into history at end would be wrong — swap
        // Save current as a new entry won't work either; just pop history
        withAnimation(.easeInOut(duration: 0.2)) {
            history.removeLast()
            currentQuestion = previous.question
            currentAnswer = previous.answer
            sources = previous.sources
            currentAnswerID = UUID()
        }
        if settings.hapticEnabled {
            UIImpactFeedbackGenerator(style: .light).impactOccurred()
        }
    }

    private func restoreHistoryEntry(_ entry: HistoryEntry) {
        // Remove all entries after this one
        guard let idx = history.firstIndex(where: { $0.id == entry.id }) else { return }
        withAnimation(.easeInOut(duration: 0.2)) {
            history = Array(history.prefix(idx))
            currentQuestion = entry.question
            currentAnswer = entry.answer
            sources = entry.sources
            currentAnswerID = UUID()
        }
    }

    private func clearAll() {
        currentQuestion = ""
        currentAnswer = ""
        sources = []
        history = []
        followUpChips = []
        currentAnswerID = UUID()
    }

    // MARK: - Data

    private struct ExploreThread {
        let emoji: String
        let title: String
        let subtitle: String
        let query: String
    }

    private var exploreThreads: [ExploreThread] {
        [
            ExploreThread(emoji: "📖", title: "What am I reading?", subtitle: "From Goodreads", query: "What books am I currently reading and what are their main ideas?"),
            ExploreThread(emoji: "🧠", title: "What do I know about AI?", subtitle: "From all sources", query: "What do I know about AI, machine learning, and LLMs?"),
            ExploreThread(emoji: "🏢", title: "Datadog prep", subtitle: "For your new role", query: "What do I know about Apache Arrow, Trino, ClickHouse, and query engines?"),
            ExploreThread(emoji: "📅", title: "What happened recently?", subtitle: "Last few days", query: "What are the most recent things I saved or noted?"),
            ExploreThread(emoji: "🌍", title: "Israel & Middle East", subtitle: "From notes & news", query: "What do I know about Israel, the Middle East, and current events there?"),
            ExploreThread(emoji: "💡", title: "Entrepreneurship", subtitle: "From Securent & books", query: "What do I know about startups, venture capital, and entrepreneurship?"),
        ]
    }

    private let defaultSuggestions = [
        "What's the parasha this week?",
        "Summarize my OS notes on virtual memory",
        "What do I know about Apache Arrow?",
        "What's due soon in my courses?",
        "What have I learned about distributed systems?",
        "What do I know about Trino or ClickHouse?",
    ]

    private func loadSuggestions() async {
        if let result = try? await api.suggestions() {
            suggestions = result.suggestions
            suggestionsLoaded = true
        }
    }

    private func cancelStreaming() {
        streamingTask?.cancel()
        streamingTask = nil
        isStreaming = false
        streamingStarted = false
        if settings.hapticEnabled {
            UIImpactFeedbackGenerator(style: .light).impactOccurred()
        }
    }

    private func sendQuery() {
        let q = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !q.isEmpty, !isStreaming else { return }
        query = ""
        inputFocused = false

        if settings.hapticEnabled {
            UIImpactFeedbackGenerator(style: .medium).impactOccurred()
        }
        settings.recordActivity()
        settings.totalQueriesAsked += 1

        // Save current result to history before replacing (keep last 10 turns)
        if !currentQuestion.isEmpty {
            let entry = HistoryEntry(
                question: currentQuestion,
                answer: currentAnswer,
                sources: sources
            )
            history.append(entry)
            if history.count > 10 {
                history.removeFirst(history.count - 10)
            }
        }

        // Reset for new result
        withAnimation(.easeInOut(duration: 0.15)) {
            currentQuestion = q
            currentAnswer = ""
            sources = []
            followUpChips = []
            streamingStarted = false
            currentAnswerID = UUID()
        }

        isStreaming = true
        let answerID = currentAnswerID

        streamingTask = Task { @MainActor in
            do {
                let stream = try api.askStream(query: q)
                for try await event in stream {
                    // Guard against stale tasks (user navigated back mid-stream)
                    guard currentAnswerID == answerID else { break }
                    switch event {
                    case .token(let t):
                        streamingStarted = true
                        currentAnswer += t
                        streamVersion &+= 1
                    case .sources(let srcs):
                        withAnimation(.easeInOut(duration: 0.3)) { sources = srcs }
                    case .done(let finalAnswer, let srcs, let relatedQs):
                        if !finalAnswer.isEmpty { currentAnswer = finalAnswer }
                        if let srcs {
                            withAnimation(.easeInOut(duration: 0.3)) { sources = srcs }
                        }
                        if let relatedQs, !relatedQs.isEmpty {
                            withAnimation(.easeInOut(duration: 0.3)) {
                                followUpChips = relatedQs
                            }
                        }
                    }
                }
                if settings.hapticEnabled {
                    UIImpactFeedbackGenerator(style: .light).impactOccurred()
                }
                // If server didn't provide related questions, fall back to local generation
                if followUpChips.isEmpty {
                    withAnimation(.easeInOut(duration: 0.3)) {
                        followUpChips = generateFollowUpChips(for: q)
                    }
                }
            } catch is CancellationError {
                // User cancelled — leave partial text in place
            } catch {
                guard currentAnswerID == answerID else { return }
                currentAnswer = "Sorry, I couldn't reach the server. Check your connection in Settings."
                if settings.hapticEnabled {
                    UINotificationFeedbackGenerator().notificationOccurred(.error)
                }
            }
            isStreaming = false
            streamingStarted = false
            streamingTask = nil
        }
    }
}

// MARK: - Ask Share Card (for ImageRenderer)

struct AskShareCard: View {
    let question: String
    let answer: String
    let sourceCount: Int

    var body: some View {
        ZStack {
            Color.white
            VStack(alignment: .leading, spacing: 16) {
                // Logo mark
                HStack(spacing: 8) {
                    RoundedRectangle(cornerRadius: 6)
                        .fill(LinearGradient(colors: [Color(hex: "#5856d6"), Color(hex: "#0071e3")], startPoint: .topLeading, endPoint: .bottomTrailing))
                        .frame(width: 22, height: 22)
                    Text("Jimmy")
                        .font(.system(size: 14, weight: .bold))
                        .foregroundStyle(Color(hex: "#1d1d1f"))
                }

                Text(String(question.prefix(100)) + (question.count > 100 ? "..." : ""))
                    .font(.system(size: 17, weight: .bold))
                    .foregroundStyle(Color(hex: "#1d1d1f"))
                    .lineSpacing(2)

                Text(String(answer.prefix(280)) + (answer.count > 280 ? "..." : ""))
                    .font(.system(size: 14))
                    .foregroundStyle(Color(hex: "#6e6e73"))
                    .lineSpacing(4)

                Spacer()

                Text("from \(sourceCount) sources in your second brain")
                    .font(.system(size: 12))
                    .foregroundStyle(Color(hex: "#0071e3"))
            }
            .padding(24)
        }
        .frame(width: 380, height: 320)
        .clipShape(RoundedRectangle(cornerRadius: 20))
        .overlay(RoundedRectangle(cornerRadius: 20).stroke(Color.black.opacity(0.08), lineWidth: 1))
    }
}

// MARK: - Typing Indicator

struct TypingIndicatorView: View {
    @State private var animating = false

    var body: some View {
        HStack(spacing: 5) {
            ForEach(0..<3, id: \.self) { i in
                Circle()
                    .fill(Color.secondary.opacity(0.5))
                    .frame(width: 7, height: 7)
                    .scaleEffect(animating ? 1.3 : 0.7)
                    .opacity(animating ? 1.0 : 0.4)
                    .animation(
                        .easeInOut(duration: 0.55)
                            .repeatForever(autoreverses: true)
                            .delay(Double(i) * 0.18),
                        value: animating
                    )
            }
        }
        .onAppear { animating = true }
    }
}

// MARK: - Sources Bar

struct SourcesBar: View {
    let sources: [SourceChunk]
    @State private var expanded = false
    @State private var selectedTextSource: SourceChunk? = nil
    @State private var selectedURLSource: SourceChunk? = nil

    private let defaultVisible = 2

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            // Label row
            HStack {
                Text("Sources")
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(.tertiary)
                    .textCase(.uppercase)
                    .tracking(0.6)
                Spacer()
                if sources.count > defaultVisible {
                    Button {
                        withAnimation(.spring(response: 0.3, dampingFraction: 0.85)) {
                            expanded.toggle()
                        }
                    } label: {
                        HStack(spacing: 3) {
                            Text(expanded ? "Show less" : "\(sources.count - defaultVisible) more")
                                .font(.system(size: 11, weight: .medium))
                            if !expanded {
                                Image(systemName: "chevron.down")
                                    .font(.system(size: 9, weight: .semibold))
                            }
                        }
                        .foregroundStyle(Color(hex: "#0071e3"))
                        .padding(.horizontal, 8)
                        .padding(.vertical, 4)
                        .background(Color(hex: "#0071e3").opacity(0.08))
                        .clipShape(Capsule())
                    }
                    .buttonStyle(.plain)
                }
            }

            // Source chips
            let visible = expanded ? sources : Array(sources.prefix(defaultVisible))
            if expanded {
                // Grid layout when expanded
                LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 8) {
                    ForEach(visible) { src in
                        SourceChip(source: src) {
                            tapSource(src)
                        }
                    }
                }
            } else {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) {
                        ForEach(visible) { src in
                            SourceChip(source: src) {
                                tapSource(src)
                            }
                        }
                    }
                }
            }
        }
        .padding(.top, 4)
        // Text-only sources: use sheet with our custom viewer
        .sheet(item: $selectedTextSource) { src in
            SourceViewerSheet(source: src)
                .presentationDetents([.large])
                .presentationDragIndicator(.visible)
        }
        // URL sources: use fullScreenCover with SFSafariViewController (shares cookie store)
        .fullScreenCover(item: $selectedURLSource) { src in
            if let urlString = src.url, let url = URL(string: urlString) {
                SafariView(url: url)
                    .ignoresSafeArea()
            }
        }
    }

    private func tapSource(_ src: SourceChunk) {
        if let urlString = src.url, !urlString.isEmpty, URL(string: urlString) != nil {
            selectedURLSource = src
        } else {
            selectedTextSource = src
        }
    }
}

// MARK: - Source Chip

struct SourceChip: View {
    let source: SourceChunk
    let onTap: () -> Void

    var body: some View {
        Button(action: onTap) {
            HStack(spacing: 6) {
                Text(source.icon ?? sourceEmoji)
                    .font(.system(size: 12))
                VStack(alignment: .leading, spacing: 1) {
                    if let src = source.source {
                        Text(src.replacingOccurrences(of: "_", with: " ").uppercased())
                            .font(.system(size: 8, weight: .semibold))
                            .foregroundStyle(.tertiary)
                            .tracking(0.3)
                    }
                    Text(source.title ?? "Source")
                        .font(.system(size: 11, weight: .medium))
                        .foregroundStyle(.primary)
                        .lineLimit(1)
                }
                if source.url != nil {
                    Image(systemName: "arrow.up.right")
                        .font(.system(size: 9, weight: .semibold))
                        .foregroundStyle(Color(hex: "#0071e3").opacity(0.6))
                }
            }
            .padding(.horizontal, 10)
            .padding(.vertical, 8)
            .background(Color(hex: "faf9f7"))
            .clipShape(RoundedRectangle(cornerRadius: 10))
            .overlay(
                RoundedRectangle(cornerRadius: 10)
                    .stroke(Color(UIColor.separator).opacity(0.4), lineWidth: 0.5)
            )
        }
        .buttonStyle(.plain)
    }

    private var sourceEmoji: String {
        switch source.source?.lowercased() {
        case "canvas":       return "🎓"
        case "note", "apple_notes": return "📝"
        case "notion":       return "🗒️"
        case "github":       return "💻"
        case "youtube":      return "📺"
        case "granola":      return "🎙️"
        case "gmail":        return "✉️"
        default:             return "📄"
        }
    }
}
