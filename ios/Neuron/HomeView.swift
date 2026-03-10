import SwiftUI

struct HomeView: View {
    @EnvironmentObject var api: APIClient
    @EnvironmentObject var settings: AppSettings

    @State private var digestSections: [(title: String, body: String)] = []
    @State private var digestRaw: String = ""
    @State private var newsArticles: [NewsArticle] = []
    @State private var spark: Spark? = nil
    @State private var dailyFact: DailyFact? = nil
    @State private var dailyVocab: VocabWord? = nil
    @State private var isLoading = true
    @State private var isRefreshingDigest = false
    @State private var showSettings = false

    private var greeting: String {
        let h = Calendar.current.component(.hour, from: Date())
        let name = settings.userName.isEmpty ? "" : ", \(settings.userName)"
        if h < 12 { return "Good morning\(name)" }
        if h < 17 { return "Good afternoon\(name)" }
        return "Good evening\(name)"
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 0) {

                    // Greeting
                    VStack(alignment: .leading, spacing: 4) {
                        Text(greeting)
                            .font(.system(size: 28, weight: .bold))
                            .tracking(-0.5)
                        Text(Date().formatted(.dateTime.weekday(.wide).month(.wide).day()))
                            .font(.system(size: 14))
                            .foregroundStyle(.secondary)
                    }
                    .padding(.horizontal, 20)
                    .padding(.top, 16)
                    .padding(.bottom, 20)

                    if isLoading {
                        HomeSkeletonView()
                    } else {
                        // Daily Briefing — hero feature
                        if !digestSections.isEmpty || !digestRaw.isEmpty {
                            DigestCard(
                                sections: digestSections,
                                raw: digestRaw,
                                isRefreshing: $isRefreshingDigest,
                                onRefresh: { await refreshDigest() }
                            )
                            .padding(.horizontal, 16)
                            .padding(.bottom, 16)
                        }

                        // News carousel
                        if !newsArticles.isEmpty {
                            VStack(alignment: .leading, spacing: 10) {
                                SectionHeader(title: "Today's World")
                                    .padding(.horizontal, 20)

                                ScrollView(.horizontal, showsIndicators: false) {
                                    HStack(spacing: 12) {
                                        ForEach(newsArticles.prefix(8)) { article in
                                            NewsCardCompact(article: article)
                                        }
                                    }
                                    .padding(.horizontal, 20)
                                    .padding(.vertical, 2)
                                }
                            }
                            .padding(.bottom, 20)
                        }

                        // Daily fact + vocab — stacks vertically on narrow screens
                        if dailyFact != nil || dailyVocab != nil {
                            ViewThatFits(in: .horizontal) {
                                // Wide: side by side
                                HStack(alignment: .top, spacing: 12) {
                                    dailyCards
                                }
                                .padding(.horizontal, 16)
                                .padding(.bottom, 16)

                                // Narrow: stacked
                                VStack(alignment: .leading, spacing: 12) {
                                    dailyCards
                                }
                                .padding(.horizontal, 16)
                                .padding(.bottom, 16)
                            }
                        }

                        // Top spark
                        if let spark = spark {
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
                            .padding(.bottom, 32)
                        }
                    }
                }
            }
            .background(Color(UIColor.systemGroupedBackground))
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button { showSettings = true } label: {
                        Image(systemName: "gearshape")
                            .font(.system(size: 16))
                    }
                }
            }
            .sheet(isPresented: $showSettings) {
                SettingsView()
            }
            .task { await loadAll() }
            .refreshable { await loadAll() }
        }
    }

    @ViewBuilder
    private var dailyCards: some View {
        if let fact = dailyFact {
            DailyFactCard(fact: fact.text, source: fact.source)
        }
        if let vocab = dailyVocab {
            DailyVocabCard(vocab: vocab)
        }
    }

    private func loadAll() async {
        isLoading = true
        async let d = try? api.digest()
        async let n = try? api.news()
        async let s = try? api.sparks()
        async let daily = try? api.daily()

        let (digest, news, sparks, dailyData) = await (d, n, s, daily)

        if let result = digest?.result {
            digestRaw = result
            digestSections = parseDigestSections(result)
        }

        if let articles = news?.articles {
            // Filter out articles with nil or empty image strings, then take first 8
            newsArticles = Array(articles.filter {
                if let img = $0.image { return !img.isEmpty }
                return false
            }.prefix(8))
        }

        spark = sparks?.sparks.first
        dailyFact = dailyData?.fact
        dailyVocab = dailyData?.vocab
        isLoading = false
    }

    private func refreshDigest() async {
        isRefreshingDigest = true
        if let result = try? await api.digest(refresh: true) {
            digestRaw = result.result
            digestSections = parseDigestSections(result.result)
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
            let title = String(text[titleRange]).trimmingCharacters(in: .whitespacesAndNewlines)
            let body = cleanForDisplay(String(text[bodyRange]))
            guard !body.isEmpty else { return nil }
            return (title, body)
        }
    }

    /// Cleans AI-generated markdown for clean display in SwiftUI Text views.
    /// - Strips [N] source citations
    /// - Converts markdown bullets to Unicode bullets
    /// - Preserves bold/italic (handled by AttributedString)
    private func cleanForDisplay(_ text: String) -> String {
        var s = text
        // Remove [1], [2], ... citations
        s = s.replacingOccurrences(of: #"\s*\[\d+(?:,\s*\d+)*\]"#, with: "", options: .regularExpression)
        // Convert "- item" and "* item" list lines to bullet points
        s = s.replacingOccurrences(of: #"(?m)^[\-\*]\s+"#, with: "\u{2022} ", options: .regularExpression)
        // Collapse 3+ blank lines to 2
        s = s.replacingOccurrences(of: #"\n{3,}"#, with: "\n\n", options: .regularExpression)
        return s.trimmingCharacters(in: .whitespacesAndNewlines)
    }
}

// MARK: - Text cleaning

/// Strips AI citation markers and converts markdown list syntax for clean SwiftUI display.
func cleanAIText(_ text: String) -> String {
    var s = text
    s = s.replacingOccurrences(of: #"\s*\[\d+(?:,\s*\d+)*\]"#, with: "", options: .regularExpression)
    s = s.replacingOccurrences(of: #"(?m)^[\-\*]\s+"#, with: "\u{2022} ", options: .regularExpression)
    s = s.replacingOccurrences(of: #"\n{3,}"#, with: "\n\n", options: .regularExpression)
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

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            // Header
            HStack {
                Label("Daily Briefing", systemImage: "text.alignleft")
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(.tertiary)
                    .textCase(.uppercase)
                    .tracking(0.8)
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
                            .font(.system(size: 12, weight: .medium))
                            .foregroundStyle(.tertiary)
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
                    .font(.system(size: 14))
                    .foregroundStyle(.secondary)
                    .lineSpacing(4)
                    .padding(16)
            } else {
                let visible = expanded ? sections : Array(sections.prefix(1))
                ForEach(Array(visible.enumerated()), id: \.offset) { idx, sec in
                    DigestSection(
                        title: sec.title,
                        content: sec.body,
                        icon: sectionIcons[sec.title] ?? "circle.fill",
                        isLast: idx == visible.count - 1 && (expanded || sections.count == 1)
                    )
                }

                if sections.count > 1 {
                    Button {
                        withAnimation(.spring(response: 0.35, dampingFraction: 0.85)) {
                            expanded.toggle()
                        }
                    } label: {
                        HStack(spacing: 6) {
                            Text(expanded ? "Show less" : "Read full briefing")
                                .font(.system(size: 13, weight: .medium))
                            Image(systemName: expanded ? "chevron.up" : "chevron.down")
                                .font(.system(size: 11, weight: .medium))
                        }
                        .foregroundStyle(.secondary)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 12)
                    }
                    .buttonStyle(.plain)
                    .overlay(alignment: .top) { Divider() }
                }
            }
        }
        .background(Color(UIColor.secondarySystemGroupedBackground))
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
                    .font(.system(size: 10, weight: .semibold))
                    .foregroundStyle(.tertiary)
                    .tracking(0.5)
            }
            .padding(.horizontal, 16)
            .padding(.top, 14)
            .padding(.bottom, 8)

            Text(renderedContent)
                .font(.system(size: 14))
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
    let fact: String
    let source: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Label("Today's Fact", systemImage: "sparkles")
                .font(.system(size: 10, weight: .semibold))
                .foregroundStyle(.tertiary)
                .textCase(.uppercase)
                .tracking(0.7)

            Text(renderMarkdown(fact))
                .font(.system(size: 13))
                .foregroundStyle(.secondary)
                .lineSpacing(3)

            if let src = source, !src.isEmpty {
                Text(src)
                    .font(.system(size: 11))
                    .foregroundStyle(.tertiary)
                    .lineLimit(1)
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color(UIColor.secondarySystemGroupedBackground))
        .clipShape(RoundedRectangle(cornerRadius: 12))
    }
}

struct DailyVocabCard: View {
    let vocab: VocabWord

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Label("Word of the Day", systemImage: "text.quote")
                .font(.system(size: 10, weight: .semibold))
                .foregroundStyle(.tertiary)
                .textCase(.uppercase)
                .tracking(0.7)

            VStack(alignment: .leading, spacing: 3) {
                HStack(alignment: .firstTextBaseline, spacing: 5) {
                    Text(vocab.word ?? "")
                        .font(.system(size: 16, weight: .bold))
                        .foregroundStyle(.primary)
                    // part_of_speech is optional — only shown if server provides it
                    if let pos = vocab.part_of_speech {
                        Text(pos)
                            .font(.system(size: 11))
                            .italic()
                            .foregroundStyle(.tertiary)
                    }
                }
                // pronunciation is optional — only shown if server provides it
                if let pron = vocab.pronunciation {
                    Text(pron)
                        .font(.system(size: 11))
                        .foregroundStyle(.tertiary)
                }
            }

            if let def = vocab.definition {
                Text(def)
                    .font(.system(size: 12.5))
                    .foregroundStyle(.secondary)
                    .lineSpacing(2)
            }

            // context replaces etymology — server returns this field
            if let ctx = vocab.context {
                Text(ctx)
                    .font(.system(size: 11))
                    .italic()
                    .foregroundStyle(.tertiary)
                    .lineSpacing(2)
            } else if let etym = vocab.etymology {
                Text(etym)
                    .font(.system(size: 11))
                    .italic()
                    .foregroundStyle(.tertiary)
                    .lineSpacing(2)
            }

            // example is optional — only shown if server provides it
            if let ex = vocab.example {
                Text("\u{201C}\(ex)\u{201D}")
                    .font(.system(size: 11.5))
                    .foregroundStyle(.secondary)
                    .lineSpacing(2)
                    .padding(.leading, 8)
                    .overlay(alignment: .leading) {
                        Rectangle()
                            .fill(Color(UIColor.separator))
                            .frame(width: 2)
                    }
            }

            if let src = vocab.source, !src.isEmpty {
                Text(src)
                    .font(.system(size: 11))
                    .foregroundStyle(.tertiary)
                    .lineLimit(1)
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color(UIColor.secondarySystemGroupedBackground))
        .clipShape(RoundedRectangle(cornerRadius: 12))
    }
}

// MARK: - Skeleton

struct HomeSkeletonView: View {
    @State private var opacity: Double = 0.4

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            ForEach(0..<3, id: \.self) { _ in
                RoundedRectangle(cornerRadius: 14)
                    .fill(Color(UIColor.secondarySystemGroupedBackground))
                    .frame(maxWidth: .infinity)
                    .frame(height: 90)
                    .padding(.horizontal, 16)
            }
        }
        .opacity(opacity)
        .onAppear {
            withAnimation(.easeInOut(duration: 0.9).repeatForever(autoreverses: true)) {
                opacity = 1.0
            }
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
                .tracking(0.8)
            content
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color(UIColor.secondarySystemGroupedBackground))
        .clipShape(RoundedRectangle(cornerRadius: 14))
    }
}

struct SectionHeader: View {
    let title: String

    var body: some View {
        Text(title)
            .font(.system(size: 11, weight: .semibold))
            .foregroundStyle(.tertiary)
            .textCase(.uppercase)
            .tracking(0.8)
            .frame(maxWidth: .infinity, alignment: .leading)
    }
}

struct NewsCardCompact: View {
    let article: NewsArticle

    var body: some View {
        Link(destination: URL(string: article.url) ?? URL(string: "https://apple.com")!) {
            VStack(alignment: .leading, spacing: 6) {
                if let imgStr = article.image, let imgURL = URL(string: imgStr) {
                    AsyncImage(url: imgURL) { image in
                        image.resizable().aspectRatio(contentMode: .fill)
                    } placeholder: {
                        Color(UIColor.systemGray5)
                    }
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
        .background(Color(UIColor.secondarySystemGroupedBackground))
        .clipShape(RoundedRectangle(cornerRadius: 14))
        .padding(.bottom, 6)
    }
}
