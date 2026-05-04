import SwiftUI

// MARK: - Learn View (Duolingo-style lessons from your KB)

private let accent = Color(hex: "#c1440e")
private let cardBg = Color(hex: "faf9f7")

struct LearnView: View {
    @EnvironmentObject var api: APIClient
    @EnvironmentObject var settings: AppSettings

    @State private var phase: LearnPhase = .picker
    @State private var topicInput = ""
    @State private var lesson: LearnResponse? = nil
    @State private var cardIndex = 0
    @State private var xp = 0
    @State private var hearts = 3
    @State private var isLoading = false
    @State private var errorMsg: String? = nil
    @State private var answered = false
    @State private var lastCorrect: Bool? = nil
    @State private var lastExplanation: String? = nil
    @FocusState private var inputFocused: Bool

    enum LearnPhase { case picker, loading, lesson, finish }

    private let quickTopics = [
        "Virtual Memory", "TCP/IP", "Sorting Algorithms",
        "Apache Arrow", "ClickHouse", "Neural Networks",
        "Financial Accounting", "Options & Derivatives",
        "Byzantine Fault Tolerance", "Graph Theory",
        "DNS", "Venture Capital", "The Torah",
    ]

    private var currentCard: LearnCard? {
        guard let plan = lesson?.lesson_plan, cardIndex < plan.count else { return nil }
        return plan[cardIndex]
    }

    private var totalCards: Int { lesson?.lesson_plan.count ?? 1 }
    private var progress: Double { Double(cardIndex) / Double(max(totalCards, 1)) }

    var body: some View {
        NavigationStack {
            Group {
                switch phase {
                case .picker:  pickerView
                case .loading: loadingView
                case .lesson:  lessonView
                case .finish:  finishView
                }
            }
            .background(Color(hex: "f5f0e8").ignoresSafeArea())
            .navigationTitle(phase == .picker ? "Learn" : (lesson?.topic ?? ""))
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                if phase == .lesson || phase == .finish {
                    ToolbarItem(placement: .topBarLeading) {
                        Button { phase = .picker; lesson = nil; xp = 0; hearts = 3 } label: {
                            Image(systemName: "xmark")
                                .font(.system(size: 14, weight: .medium))
                                .foregroundStyle(.secondary)
                        }
                    }
                }
            }
        }
    }

    // MARK: - Picker

    private var pickerView: some View {
        ScrollView {
            VStack(spacing: 24) {
                // Hero
                VStack(spacing: 8) {
                    Text("🎓")
                        .font(.system(size: 52))
                    Text("What do you want\nto learn?")
                        .font(.system(size: 24, weight: .bold))
                        .tracking(-0.5)
                        .multilineTextAlignment(.center)
                    Text("I'll teach you from scratch using your own notes.")
                        .font(.system(size: 14))
                        .foregroundStyle(.secondary)
                        .multilineTextAlignment(.center)
                }
                .padding(.top, 24)

                // Text input
                HStack(spacing: 10) {
                    TextField("Any topic…", text: $topicInput)
                        .font(.system(size: 16))
                        .focused($inputFocused)
                        .submitLabel(.go)
                        .onSubmit { startLesson(topic: topicInput) }
                        .padding(.horizontal, 14)
                        .padding(.vertical, 12)
                        .background(Color(UIColor.systemBackground))
                        .clipShape(RoundedRectangle(cornerRadius: 12))

                    Button { startLesson(topic: topicInput) } label: {
                        Text("Go")
                            .font(.system(size: 16, weight: .semibold))
                            .foregroundStyle(.white)
                            .padding(.horizontal, 18)
                            .padding(.vertical, 12)
                            .background(accent)
                            .clipShape(RoundedRectangle(cornerRadius: 12))
                    }
                    .disabled(topicInput.trimmingCharacters(in: .whitespaces).isEmpty)
                }
                .padding(.horizontal, 20)

                if let err = errorMsg {
                    Text(err)
                        .font(.system(size: 13))
                        .foregroundStyle(.red)
                        .padding(.horizontal, 20)
                }

                // Quick pick chips
                VStack(alignment: .leading, spacing: 10) {
                    Text("Quick pick")
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundStyle(.tertiary)
                        .textCase(.uppercase)
                        .tracking(0.6)
                        .padding(.horizontal, 20)

                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 8) {
                            ForEach(quickTopics, id: \.self) { topic in
                                Button { startLesson(topic: topic) } label: {
                                    Text(topic)
                                        .font(.system(size: 13))
                                        .foregroundStyle(.primary)
                                        .padding(.horizontal, 14)
                                        .padding(.vertical, 8)
                                        .background(Color(UIColor.systemBackground))
                                        .clipShape(Capsule())
                                        .overlay(Capsule().stroke(Color(UIColor.separator).opacity(0.5), lineWidth: 0.5))
                                }
                                .buttonStyle(.plain)
                            }
                        }
                        .padding(.horizontal, 20)
                    }
                }

                Spacer().frame(height: 32)
            }
        }
        .scrollDismissesKeyboard(.interactively)
    }

    // MARK: - Loading

    private var loadingView: some View {
        VStack(spacing: 16) {
            Text("📚")
                .font(.system(size: 44))
            Text("Building your lesson…")
                .font(.system(size: 17, weight: .semibold))
            Text("Searching your knowledge base and crafting cards")
                .font(.system(size: 14))
                .foregroundStyle(.secondary)
            ProgressView()
                .tint(accent)
                .padding(.top, 4)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    // MARK: - Lesson

    private var lessonView: some View {
        VStack(spacing: 0) {
            // Progress bar + stats
            VStack(spacing: 12) {
                GeometryReader { geo in
                    ZStack(alignment: .leading) {
                        RoundedRectangle(cornerRadius: 4)
                            .fill(Color(UIColor.systemFill))
                            .frame(height: 8)
                        RoundedRectangle(cornerRadius: 4)
                            .fill(accent)
                            .frame(width: geo.size.width * progress, height: 8)
                            .animation(.spring(response: 0.4, dampingFraction: 0.85), value: progress)
                    }
                }
                .frame(height: 8)
                .padding(.horizontal, 20)

                HStack {
                    // Hearts
                    HStack(spacing: 2) {
                        ForEach(0..<3, id: \.self) { i in
                            Text(i < hearts ? "❤️" : "🖤")
                                .font(.system(size: 16))
                        }
                    }
                    Spacer()
                    // XP
                    HStack(spacing: 4) {
                        Image(systemName: "bolt.fill")
                            .font(.system(size: 11, weight: .bold))
                            .foregroundStyle(accent)
                        Text("\(xp) XP")
                            .font(.system(size: 13, weight: .bold))
                            .foregroundStyle(accent)
                    }
                }
                .padding(.horizontal, 20)
            }
            .padding(.top, 12)
            .padding(.bottom, 8)

            // Card
            if let card = currentCard {
                ScrollView {
                    cardView(card)
                        .padding(20)
                        .padding(.bottom, 32)
                }
                .scrollDismissesKeyboard(.interactively)
            }
        }
    }

    @ViewBuilder
    private func cardView(_ card: LearnCard) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            switch card.type {
            case "intro":
                introCard(card)
            case "concept":
                conceptCard(card)
            case "multiple_choice":
                mcqCard(card)
            case "true_false":
                tfCard(card)
            case "summary":
                summaryCard(card)
            default:
                conceptCard(card)
            }
        }
        .padding(20)
        .background(Color(UIColor.systemBackground))
        .clipShape(RoundedRectangle(cornerRadius: 20))
        .shadow(color: .black.opacity(0.05), radius: 10, x: 0, y: 3)
    }

    // MARK: - Card types

    private func introCard(_ card: LearnCard) -> some View {
        VStack(alignment: .leading, spacing: 16) {
            Text(card.emoji ?? "🚀")
                .font(.system(size: 52))
                .frame(maxWidth: .infinity, alignment: .center)
                .padding(.bottom, 4)

            if let title = card.title {
                Text(title)
                    .font(.system(size: 22, weight: .bold))
                    .tracking(-0.4)
                    .frame(maxWidth: .infinity, alignment: .center)
                    .multilineTextAlignment(.center)
            }

            if let hook = card.hook {
                Text(hook)
                    .font(.system(size: 16, weight: .semibold))
                    .foregroundStyle(accent)
                    .multilineTextAlignment(.center)
                    .frame(maxWidth: .infinity, alignment: .center)
                    .lineSpacing(3)
            }

            if let body = card.body {
                Text(body)
                    .font(.system(size: 15))
                    .foregroundStyle(.secondary)
                    .lineSpacing(4)
                    .multilineTextAlignment(.center)
                    .frame(maxWidth: .infinity, alignment: .center)
            }

            Button { advanceCard() } label: {
                Text("Let's go →")
                    .font(.system(size: 16, weight: .semibold))
                    .foregroundStyle(.white)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 14)
                    .background(accent)
                    .clipShape(RoundedRectangle(cornerRadius: 14))
            }
            .buttonStyle(.plain)
            .padding(.top, 8)
        }
    }

    private func conceptCard(_ card: LearnCard) -> some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(spacing: 10) {
                Text(card.emoji ?? "💡")
                    .font(.system(size: 32))
                VStack(alignment: .leading, spacing: 2) {
                    Text("Concept")
                        .font(.system(size: 10, weight: .bold))
                        .foregroundStyle(.tertiary)
                        .textCase(.uppercase)
                        .tracking(0.6)
                    if let title = card.title {
                        Text(title)
                            .font(.system(size: 18, weight: .bold))
                            .tracking(-0.3)
                    }
                }
            }

            if let body = card.body {
                Text(body)
                    .font(.system(size: 15))
                    .foregroundStyle(.secondary)
                    .lineSpacing(4)
            }

            if let analogy = card.analogy {
                VStack(alignment: .leading, spacing: 6) {
                    Text("🔄  Think of it like…")
                        .font(.system(size: 11, weight: .bold))
                        .foregroundStyle(Color(hex: "#a0652a"))
                        .textCase(.uppercase)
                        .tracking(0.4)
                    Text(analogy)
                        .font(.system(size: 14))
                        .foregroundStyle(.primary)
                        .lineSpacing(3)
                }
                .padding(12)
                .background(Color(hex: "#fff3e8"))
                .clipShape(RoundedRectangle(cornerRadius: 10))
            }

            if let kp = card.key_point {
                HStack(alignment: .top, spacing: 10) {
                    Rectangle()
                        .fill(accent)
                        .frame(width: 3)
                        .clipShape(RoundedRectangle(cornerRadius: 2))
                    Text(kp)
                        .font(.system(size: 14, weight: .semibold))
                        .foregroundStyle(.primary)
                        .lineSpacing(3)
                }
                .padding(.vertical, 4)
            }

            Button { advanceCard() } label: {
                Text("Got it →")
                    .font(.system(size: 16, weight: .semibold))
                    .foregroundStyle(.white)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 14)
                    .background(accent)
                    .clipShape(RoundedRectangle(cornerRadius: 14))
            }
            .buttonStyle(.plain)
            .padding(.top, 4)
        }
    }

    @State private var selectedOption: Int? = nil

    private func mcqCard(_ card: LearnCard) -> some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(spacing: 8) {
                Text(card.emoji ?? "❓")
                    .font(.system(size: 28))
                Text("Quick check")
                    .font(.system(size: 10, weight: .bold))
                    .foregroundStyle(.tertiary)
                    .textCase(.uppercase)
                    .tracking(0.6)
            }

            if let q = card.questionText {
                Text(q)
                    .font(.system(size: 17, weight: .semibold))
                    .tracking(-0.2)
                    .lineSpacing(3)
            }

            if let opts = card.options {
                VStack(spacing: 8) {
                    ForEach(Array(opts.enumerated()), id: \.offset) { idx, opt in
                        let correctIdx = ["A","B","C","D"].firstIndex(of: card.answerLetter?.uppercased() ?? "") ?? -1
                        let isCorrect = idx == correctIdx
                        let isSelected = selectedOption == idx

                        Button {
                            guard !answered else { return }
                            selectedOption = idx
                            submitMCQ(selectedIdx: idx, correctIdx: correctIdx, explanation: card.explanation)
                        } label: {
                            Text(opt)
                                .font(.system(size: 14))
                                .foregroundStyle(
                                    answered ? (isCorrect ? Color(hex: "1a7a3c") : (isSelected ? Color(hex: "c0392b") : .secondary)) : .primary
                                )
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .padding(.horizontal, 14)
                                .padding(.vertical, 12)
                                .background(
                                    answered
                                    ? (isCorrect ? Color(hex: "e8f8ee") : (isSelected ? Color(hex: "fff0f0") : Color(UIColor.systemFill).opacity(0.3)))
                                    : Color(UIColor.systemFill).opacity(0.4)
                                )
                                .clipShape(RoundedRectangle(cornerRadius: 10))
                                .overlay(
                                    RoundedRectangle(cornerRadius: 10)
                                        .stroke(
                                            answered ? (isCorrect ? Color(hex: "27ae60") : (isSelected ? Color(hex: "e74c3c") : Color.clear)) : Color.clear,
                                            lineWidth: 1.5
                                        )
                                )
                        }
                        .buttonStyle(.plain)
                        .disabled(answered)
                    }
                }
            }

            feedbackSection
        }
    }

    private func tfCard(_ card: LearnCard) -> some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(spacing: 8) {
                Text(card.emoji ?? "🤔")
                    .font(.system(size: 28))
                Text("True or False?")
                    .font(.system(size: 10, weight: .bold))
                    .foregroundStyle(.tertiary)
                    .textCase(.uppercase)
                    .tracking(0.6)
            }

            if let stmt = card.statement {
                Text(stmt)
                    .font(.system(size: 18, weight: .semibold))
                    .tracking(-0.3)
                    .lineSpacing(4)
            }

            HStack(spacing: 12) {
                ForEach([true, false], id: \.self) { choice in
                    let correct = card.answerBool
                    let isCorrect = correct == choice
                    let wasChosen = answered && selectedOption == (choice ? 0 : 1)

                    Button {
                        guard !answered else { return }
                        selectedOption = choice ? 0 : 1
                        submitTF(chosen: choice, correct: correct ?? false, explanation: card.explanation)
                    } label: {
                        Text(choice ? "✓  True" : "✗  False")
                            .font(.system(size: 16, weight: .bold))
                            .foregroundStyle(
                                answered ? (isCorrect ? Color(hex: "1a7a3c") : (wasChosen ? Color(hex: "c0392b") : .secondary)) : .primary
                            )
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 16)
                            .background(
                                answered
                                ? (isCorrect ? Color(hex: "e8f8ee") : (wasChosen ? Color(hex: "fff0f0") : Color(UIColor.systemFill).opacity(0.3)))
                                : Color(UIColor.systemFill).opacity(0.4)
                            )
                            .clipShape(RoundedRectangle(cornerRadius: 14))
                            .overlay(
                                RoundedRectangle(cornerRadius: 14)
                                    .stroke(
                                        answered ? (isCorrect ? Color(hex: "27ae60") : (wasChosen ? Color(hex: "e74c3c") : Color.clear)) : Color.clear,
                                        lineWidth: 1.5
                                    )
                            )
                    }
                    .buttonStyle(.plain)
                    .disabled(answered)
                }
            }

            feedbackSection
        }
    }

    private func summaryCard(_ card: LearnCard) -> some View {
        VStack(spacing: 16) {
            Text(card.emoji ?? "🎉")
                .font(.system(size: 52))
                .frame(maxWidth: .infinity, alignment: .center)

            Text(card.title ?? "Lesson complete!")
                .font(.system(size: 22, weight: .bold))
                .tracking(-0.4)
                .multilineTextAlignment(.center)
                .frame(maxWidth: .infinity, alignment: .center)

            Text("You earned **+\(xp) XP** on \(lesson?.topic ?? "")")
                .font(.system(size: 14))
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)

            if let recap = card.recap, !recap.isEmpty {
                VStack(alignment: .leading, spacing: 8) {
                    Text("Key takeaways")
                        .font(.system(size: 11, weight: .bold))
                        .foregroundStyle(.tertiary)
                        .textCase(.uppercase)
                        .tracking(0.6)
                    ForEach(recap, id: \.self) { point in
                        HStack(alignment: .top, spacing: 8) {
                            Text("✓")
                                .font(.system(size: 13, weight: .bold))
                                .foregroundStyle(accent)
                            Text(point)
                                .font(.system(size: 14))
                                .foregroundStyle(.secondary)
                                .lineSpacing(3)
                        }
                    }
                }
                .padding(14)
                .background(Color(UIColor.secondarySystemBackground))
                .clipShape(RoundedRectangle(cornerRadius: 12))
            }

            Button { advanceCard() } label: {
                Text("Finish →")
                    .font(.system(size: 16, weight: .semibold))
                    .foregroundStyle(.white)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 14)
                    .background(accent)
                    .clipShape(RoundedRectangle(cornerRadius: 14))
            }
            .buttonStyle(.plain)
        }
    }

    // MARK: - Feedback section (shown after answering a question)

    @ViewBuilder
    private var feedbackSection: some View {
        if answered, let correct = lastCorrect {
            VStack(alignment: .leading, spacing: 8) {
                HStack(spacing: 6) {
                    Text(correct ? "🎉" : "💡")
                        .font(.system(size: 16))
                    Text(correct ? "Correct! +10 XP" : "Not quite")
                        .font(.system(size: 14, weight: .semibold))
                        .foregroundStyle(correct ? Color(hex: "1a7a3c") : Color(hex: "8a5700"))
                }
                if let exp = lastExplanation {
                    Text(exp)
                        .font(.system(size: 13))
                        .foregroundStyle(.secondary)
                        .lineSpacing(3)
                }
                Button { advanceCard() } label: {
                    Text("Continue →")
                        .font(.system(size: 15, weight: .semibold))
                        .foregroundStyle(.white)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 12)
                        .background(correct ? Color(hex: "27ae60") : accent)
                        .clipShape(RoundedRectangle(cornerRadius: 12))
                }
                .buttonStyle(.plain)
                .padding(.top, 2)
            }
            .padding(12)
            .background(correct ? Color(hex: "e8f8ee") : Color(hex: "fff8e8"))
            .clipShape(RoundedRectangle(cornerRadius: 12))
            .transition(.opacity.combined(with: .move(edge: .bottom)))
        }
    }

    // MARK: - Finish screen

    private var finishView: some View {
        VStack(spacing: 20) {
            Spacer()
            Text("🏆")
                .font(.system(size: 64))
            Text("Lesson Complete!")
                .font(.system(size: 26, weight: .bold))
                .tracking(-0.5)
            Text("You just learned \(lesson?.topic ?? "") from scratch.")
                .font(.system(size: 15))
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
            Text("+\(xp) XP")
                .font(.system(size: 36, weight: .heavy))
                .foregroundStyle(accent)

            Text(hearts >= 3 ? "❤️❤️❤️  Perfect score!" : hearts == 2 ? "❤️❤️🖤  Good work" : hearts == 1 ? "❤️🖤🖤  Keep practicing" : "🖤🖤🖤  Try again!")
                .font(.system(size: 14))
                .foregroundStyle(.secondary)

            Spacer()

            VStack(spacing: 10) {
                Button {
                    guard let t = lesson?.topic else { return }
                    startLesson(topic: t)
                } label: {
                    Text("↻  Learn it again")
                        .font(.system(size: 16, weight: .semibold))
                        .foregroundStyle(.white)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 14)
                        .background(accent)
                        .clipShape(RoundedRectangle(cornerRadius: 14))
                }
                .buttonStyle(.plain)

                Button {
                    phase = .picker
                    lesson = nil
                    xp = 0
                    hearts = 3
                } label: {
                    Text("← New topic")
                        .font(.system(size: 16, weight: .medium))
                        .foregroundStyle(.primary)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 14)
                        .background(Color(UIColor.systemFill))
                        .clipShape(RoundedRectangle(cornerRadius: 14))
                }
                .buttonStyle(.plain)
            }
            .padding(.horizontal, 24)
            .padding(.bottom, 32)
        }
        .frame(maxWidth: .infinity)
        .padding(.horizontal, 24)
    }

    // MARK: - Actions

    private func startLesson(topic: String) {
        let t = topic.trimmingCharacters(in: .whitespaces)
        guard !t.isEmpty else { return }
        errorMsg = nil
        phase = .loading
        cardIndex = 0
        xp = 0
        hearts = 3
        answered = false
        selectedOption = nil
        lastCorrect = nil
        lastExplanation = nil

        Task {
            do {
                let response = try await api.learn(topic: t)
                if let err = response.error {
                    errorMsg = err
                    phase = .picker
                    return
                }
                lesson = response
                phase = .lesson
            } catch {
                errorMsg = error.localizedDescription
                phase = .picker
            }
        }
    }

    private func advanceCard() {
        answered = false
        selectedOption = nil
        lastCorrect = nil
        lastExplanation = nil
        withAnimation(.spring(response: 0.35, dampingFraction: 0.85)) {
            cardIndex += 1
            if cardIndex >= totalCards {
                phase = .finish
            }
        }
        if settings.hapticEnabled {
            UISelectionFeedbackGenerator().selectionChanged()
        }
    }

    private func submitMCQ(selectedIdx: Int, correctIdx: Int, explanation: String?) {
        let correct = selectedIdx == correctIdx
        if correct { xp += 10 } else { hearts = max(0, hearts - 1) }
        withAnimation(.easeInOut(duration: 0.2)) {
            answered = true
            lastCorrect = correct
            lastExplanation = explanation
        }
        if settings.hapticEnabled {
            UINotificationFeedbackGenerator().notificationOccurred(correct ? .success : .warning)
        }
    }

    private func submitTF(chosen: Bool, correct: Bool, explanation: String?) {
        let isRight = chosen == correct
        if isRight { xp += 10 } else { hearts = max(0, hearts - 1) }
        withAnimation(.easeInOut(duration: 0.2)) {
            answered = true
            lastCorrect = isRight
            lastExplanation = explanation
        }
        if settings.hapticEnabled {
            UINotificationFeedbackGenerator().notificationOccurred(isRight ? .success : .warning)
        }
    }
}

// Convenience to strip embedded A/B/C/D lines from MCQ questions
private extension LearnCard {
    var questionText: String? {
        guard let q = question else { return nil }
        return q.components(separatedBy: "\n").first ?? q
    }
}
