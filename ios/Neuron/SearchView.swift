import SwiftUI

// MARK: - SearchView

struct SearchView: View {
    @EnvironmentObject var api: APIClient
    @EnvironmentObject var settings: AppSettings

    @State private var query: String = ""
    @State private var results: [SearchResult] = []
    @State private var isLoading = false
    @State private var hasMore = false
    @State private var currentOffset = 0
    @State private var selectedSource: String = ""
    @State private var errorMessage: String? = nil
    @State private var hasSearched = false
    @FocusState private var searchFocused: Bool
    @State private var searchTask: Task<Void, Never>? = nil
    @State private var debounceTask: Task<Void, Never>? = nil

    // Recent searches
    @State private var recentSearches: [String] = []
    private let recentSearchesKey = "neuron_recent_searches"
    private let maxRecentSearches = 10

    // Expanded result sheet
    @State private var expandedResult: SearchResult? = nil

    // Save confirmation
    @State private var savedResultID: String? = nil

    private let sources: [(id: String, label: String, icon: String)] = [
        ("",          "All",      ""),
        ("books",     "Books",    "📚"),
        ("web",       "Web",      "🌐"),
        ("notes",     "Notes",    "📝"),
        ("youtube",   "YouTube",  "📺"),
        ("canvas",    "Canvas",   "🎓"),
        ("calendar",  "Calendar", "📅"),
    ]

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {

                // MARK: Search bar
                VStack(spacing: 10) {
                    HStack(spacing: 10) {
                        Image(systemName: "magnifyingglass")
                            .font(.system(size: 15, weight: .medium))
                            .foregroundStyle(Color(UIColor.tertiaryLabel))

                        TextField("Search your second brain…", text: $query)
                            .textFieldStyle(.plain)
                            .font(.system(size: 15))
                            .focused($searchFocused)
                            .autocorrectionDisabled(true)
                            .autocapitalization(.none)
                            .onChange(of: query) { _, newVal in
                                debounceSearch(newVal)
                            }
                            .onSubmit {
                                if !query.trimmingCharacters(in: .whitespaces).isEmpty {
                                    saveRecentSearch(query)
                                    triggerSearch(query, offset: 0)
                                }
                            }

                        if !query.isEmpty {
                            Button {
                                query = ""
                                results = []
                                hasSearched = false
                                errorMessage = nil
                                debounceTask?.cancel()
                                searchTask?.cancel()
                            } label: {
                                Image(systemName: "xmark.circle.fill")
                                    .font(.system(size: 16))
                                    .foregroundStyle(Color(UIColor.tertiaryLabel))
                            }
                            .buttonStyle(.plain)
                        }
                    }
                    .padding(.horizontal, 14)
                    .padding(.vertical, 10)
                    .background(Color(hex: "faf9f7"))
                    .clipShape(RoundedRectangle(cornerRadius: 16))
                    .shadow(color: .black.opacity(0.05), radius: 5, x: 0, y: 2)

                    // Source filter chips
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 7) {
                            ForEach(sources, id: \.id) { src in
                                Button {
                                    selectedSource = src.id
                                    if !query.isEmpty {
                                        triggerSearch(query, offset: 0)
                                    }
                                    if settings.hapticEnabled {
                                        UISelectionFeedbackGenerator().selectionChanged()
                                    }
                                } label: {
                                    HStack(spacing: 4) {
                                        if !src.icon.isEmpty {
                                            Text(src.icon)
                                                .font(.system(size: 11))
                                        }
                                        Text(src.label)
                                            .font(.system(size: 12.5, weight: selectedSource == src.id ? .semibold : .regular))
                                    }
                                    .padding(.horizontal, 11)
                                    .padding(.vertical, 6)
                                    .background(
                                        selectedSource == src.id
                                            ? Color(hex: "#0071e3")
                                            : Color(UIColor.systemBackground)
                                    )
                                    .foregroundStyle(
                                        selectedSource == src.id
                                            ? Color.white
                                            : Color(UIColor.secondaryLabel)
                                    )
                                    .clipShape(Capsule())
                                    .overlay(
                                        Capsule().stroke(
                                            selectedSource == src.id
                                                ? Color.clear
                                                : Color(UIColor.separator).opacity(0.5),
                                            lineWidth: 1
                                        )
                                    )
                                }
                                .buttonStyle(.plain)
                            }
                        }
                        .padding(.horizontal, 2)
                    }
                }
                .padding(.horizontal, 14)
                .padding(.top, 12)
                .padding(.bottom, 10)
                .background(.regularMaterial)

                Divider()

                // MARK: Results / empty state
                if isLoading && results.isEmpty {
                    Spacer()
                    ProgressView()
                        .progressViewStyle(.circular)
                        .tint(Color(hex: "#0071e3"))
                    Spacer()

                } else if let err = errorMessage {
                    Spacer()
                    VStack(spacing: 8) {
                        Image(systemName: "exclamationmark.triangle")
                            .font(.system(size: 28))
                            .foregroundStyle(.orange)
                        Text(err)
                            .font(.system(size: 14))
                            .foregroundStyle(Color(UIColor.secondaryLabel))
                            .multilineTextAlignment(.center)
                    }
                    .padding()
                    Spacer()

                } else if hasSearched && results.isEmpty {
                    Spacer()
                    VStack(spacing: 8) {
                        Image(systemName: "doc.text.magnifyingglass")
                            .font(.system(size: 28))
                            .foregroundStyle(Color(UIColor.tertiaryLabel))
                        Text("No results for \"\(query)\"")
                            .font(.system(size: 14))
                            .foregroundStyle(Color(UIColor.secondaryLabel))
                    }
                    .padding()
                    Spacer()

                } else if !hasSearched {
                    // Show recent searches when focused & query empty
                    if searchFocused && query.isEmpty && !recentSearches.isEmpty {
                        recentSearchesView
                    } else {
                        Spacer()
                        VStack(spacing: 10) {
                            Image(systemName: "magnifyingglass")
                                .font(.system(size: 32))
                                .foregroundStyle(Color(UIColor.tertiaryLabel))
                            Text("Search your entire knowledge base")
                                .font(.system(size: 15))
                                .foregroundStyle(Color(UIColor.secondaryLabel))
                            Text("Notes · Books · Canvas · Web · Calendar")
                                .font(.system(size: 12))
                                .foregroundStyle(Color(UIColor.tertiaryLabel))
                        }
                        .padding()
                        Spacer()
                    }

                } else {
                    ScrollView {
                        LazyVStack(spacing: 0) {
                            ForEach(results) { result in
                                SearchResultCard(
                                    result: result,
                                    query: query,
                                    savedResultID: $savedResultID,
                                    onExpand: { expandedResult = result },
                                    onSave: { saveResult(result) }
                                )
                                .padding(.horizontal, 14)
                                .padding(.top, 10)
                            }

                            // Load more
                            if hasMore {
                                Button {
                                    loadMore()
                                } label: {
                                    HStack(spacing: 6) {
                                        if isLoading {
                                            ProgressView()
                                                .progressViewStyle(.circular)
                                                .scaleEffect(0.75)
                                                .tint(Color(hex: "#0071e3"))
                                        }
                                        Text("Load more")
                                            .font(.system(size: 13.5, weight: .medium))
                                            .foregroundStyle(Color(hex: "#0071e3"))
                                    }
                                    .padding(.vertical, 14)
                                    .frame(maxWidth: .infinity)
                                }
                                .buttonStyle(.plain)
                                .padding(.horizontal, 14)
                            }

                            Color.clear.frame(height: 24)
                        }
                    }
                }
            }
            .background(Color(hex: "faf9f7"))
            .navigationTitle("Search")
            .navigationBarTitleDisplayMode(.inline)
            .onAppear {
                searchFocused = true
                loadRecentSearches()
            }
            .sheet(item: $expandedResult) { result in
                ResultDetailSheet(result: result, query: query)
            }
        }
    }

    // MARK: - Recent Searches View

    private var recentSearchesView: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                HStack {
                    Text("Recent Searches")
                        .font(.system(size: 12, weight: .semibold))
                        .foregroundStyle(.tertiary)
                        .tracking(0.5)
                        .textCase(.uppercase)
                    Spacer()
                    Button("Clear") {
                        recentSearches = []
                        UserDefaults.standard.removeObject(forKey: recentSearchesKey)
                    }
                    .font(.system(size: 12))
                    .foregroundStyle(Color(hex: "#0071e3"))
                }
                .padding(.horizontal, 14)
                .padding(.top, 16)
                .padding(.bottom, 10)

                // Chips
                FlowLayout(spacing: 8) {
                    ForEach(recentSearches, id: \.self) { term in
                        Button {
                            query = term
                            triggerSearch(term, offset: 0)
                        } label: {
                            HStack(spacing: 5) {
                                Image(systemName: "clock")
                                    .font(.system(size: 10))
                                Text(term)
                                    .font(.system(size: 13))
                            }
                            .padding(.horizontal, 12)
                            .padding(.vertical, 7)
                            .background(Color(UIColor.systemBackground))
                            .foregroundStyle(Color(UIColor.label))
                            .clipShape(Capsule())
                            .overlay(Capsule().stroke(Color(UIColor.separator).opacity(0.5), lineWidth: 0.5))
                        }
                        .buttonStyle(.plain)
                    }
                }
                .padding(.horizontal, 14)
                .padding(.bottom, 16)
            }
        }
    }

    // MARK: - Debounce + Search Logic

    private func debounceSearch(_ value: String) {
        debounceTask?.cancel()
        guard !value.trimmingCharacters(in: .whitespaces).isEmpty else {
            results = []
            hasSearched = false
            errorMessage = nil
            return
        }
        debounceTask = Task { @MainActor in
            try? await Task.sleep(nanoseconds: 300_000_000)
            guard !Task.isCancelled else { return }
            triggerSearch(value, offset: 0)
        }
    }

    private func triggerSearch(_ q: String, offset: Int) {
        let q = q.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !q.isEmpty else { return }

        searchTask?.cancel()
        if offset == 0 {
            results = []
            errorMessage = nil
        }
        isLoading = true

        searchTask = Task { @MainActor in
            do {
                let resp = try await api.search(query: q, source: selectedSource, offset: offset)
                guard !Task.isCancelled else { return }
                if offset == 0 {
                    results = resp.results
                } else {
                    results.append(contentsOf: resp.results)
                }
                currentOffset = offset
                hasMore = resp.has_more ?? false
                hasSearched = true
                errorMessage = nil
                // Save to recent searches on successful search
                saveRecentSearch(q)
            } catch is CancellationError {
                // ignore
            } catch {
                guard !Task.isCancelled else { return }
                errorMessage = "Search failed — check your connection."
                hasSearched = true
            }
            isLoading = false
        }
    }

    private func loadMore() {
        triggerSearch(query, offset: currentOffset + 8)
    }

    // MARK: - Recent Searches Persistence

    private func loadRecentSearches() {
        recentSearches = UserDefaults.standard.stringArray(forKey: recentSearchesKey) ?? []
    }

    private func saveRecentSearch(_ term: String) {
        let trimmed = term.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        var searches = recentSearches.filter { $0 != trimmed }
        searches.insert(trimmed, at: 0)
        if searches.count > maxRecentSearches {
            searches = Array(searches.prefix(maxRecentSearches))
        }
        recentSearches = searches
        UserDefaults.standard.set(searches, forKey: recentSearchesKey)
    }

    // MARK: - Save Result

    private func saveResult(_ result: SearchResult) {
        Task { @MainActor in
            do {
                let text = [result.title, result.preview].compactMap { $0 }.filter { !$0.isEmpty }.joined(separator: "\n\n")
                try await api.ingestNote(text)
                savedResultID = result.id
                if settings.hapticEnabled {
                    UINotificationFeedbackGenerator().notificationOccurred(.success)
                }
                try? await Task.sleep(nanoseconds: 2_000_000_000)
                savedResultID = nil
            } catch {
                // silently fail — user sees no crash
            }
        }
    }
}

// MARK: - FlowLayout (chips layout)

struct FlowLayout: Layout {
    var spacing: CGFloat = 8

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let width = proposal.width ?? 0
        var height: CGFloat = 0
        var x: CGFloat = 0
        var y: CGFloat = 0
        var rowHeight: CGFloat = 0

        for view in subviews {
            let size = view.sizeThatFits(.unspecified)
            if x + size.width > width, x > 0 {
                y += rowHeight + spacing
                x = 0
                rowHeight = 0
            }
            rowHeight = max(rowHeight, size.height)
            x += size.width + spacing
        }
        height = y + rowHeight
        return CGSize(width: width, height: height)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var x = bounds.minX
        var y = bounds.minY
        var rowHeight: CGFloat = 0

        for view in subviews {
            let size = view.sizeThatFits(.unspecified)
            if x + size.width > bounds.maxX, x > bounds.minX {
                y += rowHeight + spacing
                x = bounds.minX
                rowHeight = 0
            }
            view.place(at: CGPoint(x: x, y: y), proposal: ProposedViewSize(size))
            rowHeight = max(rowHeight, size.height)
            x += size.width + spacing
        }
    }
}

// MARK: - SearchResultCard

struct SearchResultCard: View {
    let result: SearchResult
    let query: String
    @Binding var savedResultID: String?
    var onExpand: () -> Void
    var onSave: () -> Void
    @EnvironmentObject var settings: AppSettings

    private var isSaved: Bool { savedResultID == result.id }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            // Source row
            HStack(spacing: 5) {
                // Source type icon
                sourceIconView

                Text(result.sourceLabel.isEmpty ? "Source" : result.sourceLabel)
                    .font(.system(size: 10, weight: .semibold))
                    .tracking(0.3)
                    .textCase(.uppercase)
                    .foregroundStyle(.tertiary)

                if let date = result.date, !date.isEmpty {
                    Text("·")
                        .foregroundStyle(.tertiary)
                        .font(.system(size: 10))
                    Text(timeAgo(date))
                        .font(.system(size: 10))
                        .foregroundStyle(.tertiary)
                }

                Spacer()

                // Relevance score dot
                if let score = result.composite_score {
                    relevanceDot(score: score)
                    Text("\(Int(score * 100))%")
                        .font(.system(size: 10))
                        .foregroundStyle(.tertiary)
                }
            }
            .padding(.horizontal, 14)
            .padding(.top, 13)

            // Title (bold)
            if let title = result.title, !title.isEmpty {
                Text(title)
                    .font(.system(size: 14, weight: .bold))
                    .foregroundStyle(.primary)
                    .lineLimit(2)
                    .padding(.horizontal, 14)
                    .padding(.top, 6)
            }

            // Snippet with highlighted search terms (2-3 lines)
            highlightedSnippet(text: String(result.preview.prefix(280)), queryTerms: query)
                .font(.system(size: 13.5))
                .foregroundStyle(Color(UIColor.secondaryLabel))
                .lineSpacing(3)
                .lineLimit(3)
                .padding(.horizontal, 14)
                .padding(.top, 5)

            // Action row
            HStack(spacing: 8) {
                // Ask about this
                Button {
                    let q = "Tell me more about \(result.title ?? query)"
                    settings.pendingAskQuery = q
                    if settings.hapticEnabled {
                        UISelectionFeedbackGenerator().selectionChanged()
                    }
                } label: {
                    HStack(spacing: 4) {
                        Image(systemName: "sparkles")
                            .font(.system(size: 10, weight: .medium))
                        Text("Ask")
                            .font(.system(size: 12, weight: .medium))
                    }
                    .foregroundStyle(Color(hex: "#0071e3"))
                    .padding(.horizontal, 10)
                    .padding(.vertical, 6)
                    .background(Color(hex: "#0071e3").opacity(0.08))
                    .clipShape(Capsule())
                    .overlay(Capsule().stroke(Color(hex: "#0071e3").opacity(0.2), lineWidth: 0.5))
                }
                .buttonStyle(.plain)

                // Expand full text
                Button {
                    onExpand()
                } label: {
                    HStack(spacing: 3) {
                        Image(systemName: "arrow.up.left.and.arrow.down.right")
                            .font(.system(size: 9, weight: .semibold))
                        Text("Expand")
                            .font(.system(size: 12, weight: .medium))
                    }
                    .foregroundStyle(Color(UIColor.secondaryLabel))
                    .padding(.horizontal, 10)
                    .padding(.vertical, 6)
                    .background(Color(UIColor.systemBackground))
                    .clipShape(Capsule())
                    .overlay(Capsule().stroke(Color(UIColor.separator).opacity(0.5), lineWidth: 0.5))
                }
                .buttonStyle(.plain)

                // Save result
                Button {
                    onSave()
                } label: {
                    HStack(spacing: 3) {
                        Image(systemName: isSaved ? "checkmark" : "square.and.arrow.down")
                            .font(.system(size: 9, weight: .semibold))
                        Text(isSaved ? "Saved" : "Save")
                            .font(.system(size: 12, weight: .medium))
                    }
                    .foregroundStyle(isSaved ? Color.green : Color(UIColor.secondaryLabel))
                    .padding(.horizontal, 10)
                    .padding(.vertical, 6)
                    .background(isSaved ? Color.green.opacity(0.08) : Color(UIColor.systemBackground))
                    .clipShape(Capsule())
                    .overlay(Capsule().stroke(isSaved ? Color.green.opacity(0.3) : Color(UIColor.separator).opacity(0.5), lineWidth: 0.5))
                }
                .buttonStyle(.plain)

                if let urlStr = result.url, !urlStr.isEmpty, let url = URL(string: urlStr) {
                    Link(destination: url) {
                        HStack(spacing: 3) {
                            Text("Open")
                                .font(.system(size: 12, weight: .medium))
                            Image(systemName: "arrow.up.right")
                                .font(.system(size: 9, weight: .semibold))
                        }
                        .foregroundStyle(Color(UIColor.secondaryLabel))
                        .padding(.horizontal, 10)
                        .padding(.vertical, 6)
                        .background(Color(UIColor.systemBackground))
                        .clipShape(Capsule())
                        .overlay(Capsule().stroke(Color(UIColor.separator).opacity(0.5), lineWidth: 0.5))
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(.horizontal, 14)
            .padding(.top, 9)
            .padding(.bottom, 13)
        }
        .background(Color(UIColor.systemBackground))
        .clipShape(RoundedRectangle(cornerRadius: 14))
        .overlay(RoundedRectangle(cornerRadius: 14).stroke(Color(UIColor.separator).opacity(0.3), lineWidth: 0.5))
    }

    // MARK: - Source Icon

    @ViewBuilder
    private var sourceIconView: some View {
        switch result.source?.lowercased() {
        case "goodreads", "kindle", "book", "readwise":
            Image(systemName: "book.closed.fill")
                .font(.system(size: 10))
                .foregroundStyle(.brown)
        case "web", "url":
            Image(systemName: "globe")
                .font(.system(size: 10))
                .foregroundStyle(.blue)
        case "note", "apple_notes", "notion":
            Image(systemName: "note.text")
                .font(.system(size: 10))
                .foregroundStyle(.yellow)
        case "youtube":
            Image(systemName: "play.rectangle.fill")
                .font(.system(size: 10))
                .foregroundStyle(.red)
        case "canvas":
            Image(systemName: "graduationcap.fill")
                .font(.system(size: 10))
                .foregroundStyle(.indigo)
        case "google_calendar", "calendar":
            Image(systemName: "calendar")
                .font(.system(size: 10))
                .foregroundStyle(.orange)
        case "gmail":
            Image(systemName: "envelope.fill")
                .font(.system(size: 10))
                .foregroundStyle(.blue)
        case "granola":
            Image(systemName: "mic.fill")
                .font(.system(size: 10))
                .foregroundStyle(.purple)
        default:
            Text(result.sourceIcon)
                .font(.system(size: 10))
        }
    }

    // MARK: - Relevance Dot

    private func relevanceDot(score: Double) -> some View {
        Circle()
            .fill(relevanceColor(score: score))
            .frame(width: 6, height: 6)
    }

    private func relevanceColor(score: Double) -> Color {
        if score >= 0.75 { return .green }
        if score >= 0.50 { return .orange }
        return Color(UIColor.tertiaryLabel)
    }

    // MARK: - Highlighted Snippet

    private func highlightedSnippet(text: String, queryTerms: String) -> Text {
        let terms = queryTerms
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .components(separatedBy: .whitespaces)
            .filter { $0.count > 1 }

        guard !terms.isEmpty else {
            return Text(text)
        }

        // Split text into segments, bolding matching terms
        var result = Text("")
        var remaining = text[text.startIndex...]

        while !remaining.isEmpty {
            // Find the earliest matching term
            var earliestRange: Range<String.Index>? = nil
            var earliestTerm = ""

            for term in terms {
                if let range = remaining.range(of: term, options: [.caseInsensitive, .diacriticInsensitive]) {
                    if earliestRange == nil || range.lowerBound < earliestRange!.lowerBound {
                        earliestRange = range
                        earliestTerm = String(remaining[range])
                    }
                }
            }

            if let range = earliestRange {
                // Append text before match
                let before = String(remaining[remaining.startIndex..<range.lowerBound])
                if !before.isEmpty {
                    result = result + Text(before)
                }
                // Append bolded match
                result = result + Text(earliestTerm).bold().foregroundColor(.primary)
                remaining = remaining[range.upperBound...]
            } else {
                // No more matches
                result = result + Text(String(remaining))
                break
            }
        }

        return result
    }

    private func timeAgo(_ dateStr: String) -> String {
        let fmts = ["yyyy-MM-dd'T'HH:mm:ssZ", "yyyy-MM-dd'T'HH:mm:ss", "yyyy-MM-dd"]
        let formatter = DateFormatter()
        var date: Date? = nil
        for fmt in fmts {
            formatter.dateFormat = fmt
            if let d = formatter.date(from: dateStr) { date = d; break }
        }
        guard let date else { return dateStr }
        let days = Int(Date().timeIntervalSince(date) / 86400)
        if days == 0 { return "Today" }
        if days == 1 { return "Yesterday" }
        if days < 7  { return "\(days)d ago" }
        if days < 30 { return "\(days / 7)w ago" }
        return "\(days / 30)mo ago"
    }
}

// MARK: - ResultDetailSheet

struct ResultDetailSheet: View {
    let result: SearchResult
    let query: String
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    // Header
                    HStack(spacing: 8) {
                        Text(result.sourceIcon)
                            .font(.system(size: 18))
                        VStack(alignment: .leading, spacing: 2) {
                            Text(result.sourceLabel.isEmpty ? "Source" : result.sourceLabel)
                                .font(.system(size: 11, weight: .semibold))
                                .tracking(0.4)
                                .textCase(.uppercase)
                                .foregroundStyle(.tertiary)
                            if let date = result.date, !date.isEmpty {
                                Text(date)
                                    .font(.system(size: 11))
                                    .foregroundStyle(.tertiary)
                            }
                        }
                        Spacer()
                        if let score = result.composite_score {
                            VStack(spacing: 2) {
                                Text("\(Int(score * 100))%")
                                    .font(.system(size: 13, weight: .semibold))
                                    .foregroundStyle(score >= 0.75 ? .green : score >= 0.5 ? .orange : .secondary)
                                Text("relevance")
                                    .font(.system(size: 10))
                                    .foregroundStyle(.tertiary)
                            }
                        }
                    }
                    .padding(.horizontal, 20)
                    .padding(.top, 20)

                    // Title
                    if let title = result.title, !title.isEmpty {
                        Text(title)
                            .font(.system(size: 20, weight: .bold))
                            .padding(.horizontal, 20)
                    }

                    Divider()
                        .padding(.horizontal, 20)

                    // Full text
                    let fullText = result.content ?? result.content_preview ?? ""
                    if !fullText.isEmpty {
                        Text(fullText)
                            .font(.system(size: 15))
                            .foregroundStyle(.primary)
                            .lineSpacing(5)
                            .padding(.horizontal, 20)
                    }

                    // URL link
                    if let urlStr = result.url, !urlStr.isEmpty, let url = URL(string: urlStr) {
                        Link(destination: url) {
                            HStack(spacing: 6) {
                                Image(systemName: "link")
                                    .font(.system(size: 12))
                                Text(urlStr)
                                    .font(.system(size: 12))
                                    .lineLimit(1)
                                    .truncationMode(.middle)
                            }
                            .foregroundStyle(Color(hex: "#0071e3"))
                            .padding(.horizontal, 20)
                        }
                    }

                    Color.clear.frame(height: 24)
                }
            }
            .background(Color(hex: "faf9f7"))
            .navigationTitle("Result")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }
                        .font(.system(size: 15, weight: .medium))
                }
            }
        }
    }
}
