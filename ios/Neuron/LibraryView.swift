import SwiftUI
import UIKit
import UniformTypeIdentifiers
import AVFoundation
import Speech

// MARK: - Ingest Mode

enum IngestMode: String, CaseIterable {
    case voice, note, url, file, youtube, goodnotes

    var title: String {
        switch self {
        case .voice:      return "Voice Reflection"
        case .note:       return "Quick Note"
        case .url:        return "Save a Link"
        case .file:       return "Upload File"
        case .youtube:    return "YouTube"
        case .goodnotes:  return "Paste Text"
        }
    }

    var subtitle: String {
        switch self {
        case .voice:      return "Record what you learned today"
        case .note:       return "Jot down a thought or paste text"
        case .url:        return "Save any article or webpage"
        case .file:       return "Import a PDF or document"
        case .youtube:    return "Save a video by URL"
        case .goodnotes:  return "Paste any text to save to library"
        }
    }

    var icon: String {
        switch self {
        case .voice:      return "mic.fill"
        case .note:       return "square.and.pencil"
        case .url:        return "link"
        case .file:       return "doc.fill"
        case .youtube:    return "play.rectangle.fill"
        case .goodnotes:  return "doc.on.clipboard"
        }
    }

    var color: Color {
        switch self {
        case .voice:      return Color.purple
        case .note:       return Color(hex: "#0071e3")
        case .url:        return Color.teal
        case .file:       return Color.orange
        case .youtube:    return Color.red
        case .goodnotes:  return Color.indigo
        }
    }
}

// MARK: - Shelf Tab

enum ShelfTab: String, CaseIterable {
    case all = "All"
    case read = "Read"
    case reading = "Reading"
    case wantToRead = "Want to Read"

    var apiValue: String {
        switch self {
        case .all:        return ""
        case .read:       return "read"
        case .reading:    return "reading"
        case .wantToRead: return "want"
        }
    }

    /// Match against server-returned status strings
    func matches(status: String?) -> Bool {
        let s = (status ?? "").lowercased()
        switch self {
        case .all:        return true
        case .read:       return s == "read"
        case .reading:    return s == "currently reading" || s == "reading"
        case .wantToRead: return s == "want to read" || s == "want_to_read"
        }
    }
}

// MARK: - Library Sort Option

enum LibrarySortOption: String, CaseIterable {
    case recent = "Recent"
    case alphabetical = "A–Z"
    case rating = "Rating"
    case status = "Status"

    var systemImage: String {
        switch self {
        case .recent:       return "clock"
        case .alphabetical: return "textformat.abc"
        case .rating:       return "star"
        case .status:       return "tag"
        }
    }
}

// MARK: - Reading Progress Store

class ReadingProgressStore {
    static let shared = ReadingProgressStore()
    private let defaults = UserDefaults.standard
    private let keyPrefix = "neuron_reading_progress_"

    func progress(for title: String) -> Double {
        defaults.double(forKey: keyPrefix + title)
    }

    func setProgress(_ value: Double, for title: String) {
        defaults.set(max(0, min(1, value)), forKey: keyPrefix + title)
    }
}

// MARK: - Source Type Icon Helper

func sourceTypeIcon(for book: LibraryBook) -> (systemName: String, color: Color) {
    // Try to infer source type from notes or author field
    // Default to books.vertical.fill for library books
    return ("books.vertical.fill", Color(hex: "#0071e3"))
}

// MARK: - Library View

struct LibraryView: View {
    @EnvironmentObject var api: APIClient
    @EnvironmentObject var settings: AppSettings

    @State private var selectedTab: ShelfTab = .all
    @State private var books: [LibraryBook] = []
    @State private var counts: LibraryCounts? = nil
    @State private var searchQuery = ""
    @State private var isLoadingBooks = false
    @State private var status: StatusResponse? = nil
    @State private var showAddBook = false
    @State private var showSettings = false
    @State private var selectedBook: LibraryBook? = nil
    @State private var toast: String? = nil
    @State private var toastIsError = false
    @State private var showIngest = false
    @State private var sortOption: LibrarySortOption = .recent

    // Ingest states
    @State private var noteText = ""
    @State private var urlText = ""
    @State private var pasteText = ""
    @State private var isIngesting = false
    @State private var expandedMode: IngestMode? = nil
    @FocusState private var noteEditorFocused: Bool
    @FocusState private var urlFieldFocused: Bool
    @FocusState private var pasteEditorFocused: Bool

    private let columns = [
        GridItem(.flexible(), spacing: 12),
        GridItem(.flexible(), spacing: 12)
    ]

    var filteredBooks: [LibraryBook] {
        let tabFiltered = books.filter { selectedTab.matches(status: $0.status) }
        let searched: [LibraryBook]
        if searchQuery.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            searched = tabFiltered
        } else {
            let q = searchQuery.lowercased()
            searched = tabFiltered.filter {
                $0.title.lowercased().contains(q) ||
                ($0.author ?? "").lowercased().contains(q)
            }
        }
        switch sortOption {
        case .recent:
            return searched.sorted {
                let d0 = $0.date_added ?? $0.date ?? ""
                let d1 = $1.date_added ?? $1.date ?? ""
                return d0 > d1
            }
        case .alphabetical:
            return searched.sorted { $0.title.localizedCaseInsensitiveCompare($1.title) == .orderedAscending }
        case .rating:
            return searched.sorted { ($0.rating ?? 0) > ($1.rating ?? 0) }
        case .status:
            return searched.sorted { ($0.status ?? "").localizedCaseInsensitiveCompare($1.status ?? "") == .orderedAscending }
        }
    }

    var avgRating: Double? {
        let rated = books.filter { ($0.rating ?? 0) > 0 }
        guard !rated.isEmpty else { return nil }
        return Double(rated.reduce(0) { $0 + ($1.rating ?? 0) }) / Double(rated.count)
    }

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                // Stats header
                if !books.isEmpty {
                    LibraryStatsBar(counts: counts, avgRating: avgRating)
                }

                // Shelf tab bar
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) {
                        ForEach(ShelfTab.allCases, id: \.self) { tab in
                            Button {
                                withAnimation(.easeInOut(duration: 0.18)) {
                                    selectedTab = tab
                                    searchQuery = ""
                                }
                            } label: {
                                HStack(spacing: 5) {
                                    Text(tab.rawValue)
                                    if let badge = tabBadge(tab) {
                                        Text(badge)
                                            .font(.system(size: 10, weight: .semibold))
                                            .padding(.horizontal, 5)
                                            .padding(.vertical, 2)
                                            .background(selectedTab == tab ? Color.white.opacity(0.3) : Color(UIColor.secondarySystemFill))
                                            .clipShape(Capsule())
                                    }
                                }
                                .font(.system(size: 13, weight: selectedTab == tab ? .semibold : .regular))
                                .foregroundStyle(selectedTab == tab ? Color(UIColor.systemBackground) : .secondary)
                                .padding(.horizontal, 14)
                                .padding(.vertical, 7)
                                .background(selectedTab == tab ? Color(hex: "#0071e3") : Color.clear)
                                .clipShape(Capsule())
                                .overlay(Capsule().stroke(Color(UIColor.separator), lineWidth: selectedTab == tab ? 0 : 0.5))
                            }
                            .buttonStyle(.plain)
                        }
                    }
                    .padding(.horizontal, 16)
                    .padding(.vertical, 10)
                }

                Divider()

                // Search bar
                if !books.isEmpty {
                    HStack(spacing: 10) {
                        Image(systemName: "magnifyingglass")
                            .font(.system(size: 14))
                            .foregroundStyle(.tertiary)
                        TextField("Search books…", text: $searchQuery)
                            .font(.system(size: 15))
                        if !searchQuery.isEmpty {
                            Button { searchQuery = "" } label: {
                                Image(systemName: "xmark.circle.fill")
                                    .foregroundStyle(.tertiary)
                            }
                            .buttonStyle(.plain)
                        }
                    }
                    .padding(.horizontal, 14)
                    .padding(.vertical, 9)
                    .background(Color(UIColor.secondarySystemFill))
                    .clipShape(RoundedRectangle(cornerRadius: 10))
                    .padding(.horizontal, 16)
                    .padding(.top, 10)
                    .padding(.bottom, 6)
                }

                if isLoadingBooks && books.isEmpty {
                    Spacer()
                    ProgressView()
                    Spacer()
                } else if filteredBooks.isEmpty && !isLoadingBooks {
                    emptyShelfView
                } else {
                    ScrollView {
                        LazyVGrid(columns: columns, spacing: 14) {
                            ForEach(filteredBooks) { book in
                                BookGridCell(book: book)
                                    .onTapGesture {
                                        selectedBook = book
                                    }
                            }
                        }
                        .padding(16)
                        .padding(.bottom, 20)
                    }
                    .refreshable {
                        await loadBooks()
                    }
                }
            }
            .background(Color(red: 0.98, green: 0.98, blue: 0.97))
            .navigationTitle("Books")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button { showSettings = true } label: {
                        Image(systemName: "gearshape")
                            .foregroundStyle(Color(hex: "#0071e3"))
                    }
                }
                ToolbarItem(placement: .topBarTrailing) {
                    HStack(spacing: 12) {
                        Menu {
                            ForEach(LibrarySortOption.allCases, id: \.self) { option in
                                Button {
                                    withAnimation(.easeInOut(duration: 0.2)) { sortOption = option }
                                } label: {
                                    Label(option.rawValue, systemImage: option.systemImage)
                                }
                            }
                        } label: {
                            Image(systemName: "arrow.up.arrow.down")
                                .foregroundStyle(Color(hex: "#0071e3"))
                        }
                        Button {
                            showIngest.toggle()
                        } label: {
                            Image(systemName: "plus.circle")
                                .foregroundStyle(Color(hex: "#0071e3"))
                        }
                        Button {
                            showAddBook = true
                        } label: {
                            Image(systemName: "book.badge.plus")
                                .foregroundStyle(Color(hex: "#0071e3"))
                        }
                    }
                }
            }
            .sheet(isPresented: $showSettings) {
                SettingsView()
                    .environmentObject(settings)
                    .environmentObject(api)
            }
            .sheet(isPresented: $showAddBook) {
                AddBookSheet { title, status in
                    await addBook(title: title, status: status)
                }
                .environmentObject(api)
            }
            .sheet(item: $selectedBook) { book in
                BookDetailSheet(book: book, onUpdate: { updatedBook in
                    // Refresh books after update
                    Task { await loadBooks() }
                })
                .environmentObject(api)
                .environmentObject(settings)
            }
            .sheet(isPresented: $showIngest) {
                IngestSheet(
                    noteText: $noteText,
                    urlText: $urlText,
                    pasteText: $pasteText,
                    isIngesting: $isIngesting,
                    expandedMode: $expandedMode,
                    noteEditorFocused: $noteEditorFocused,
                    urlFieldFocused: $urlFieldFocused,
                    pasteEditorFocused: $pasteEditorFocused,
                    onSaveNote: { await ingestNote() },
                    onSaveURL: { await ingestURL() },
                    onSavePaste: { await ingestPaste() },
                    onFileSuccess: {
                        await loadStatus()
                        settings.recordActivity()
                        settings.totalNotesAdded += 1
                    },
                    showToast: { msg, isErr in showToast(msg, isError: isErr) }
                )
                .environmentObject(api)
                .environmentObject(settings)
            }
            .task {
                await loadBooks()
                await loadStatus()
            }
            .overlay(alignment: .bottom) {
                if let t = toast {
                    ToastView(message: t, isError: toastIsError)
                        .padding(.bottom, 24)
                        .transition(.move(edge: .bottom).combined(with: .opacity))
                }
            }
            .animation(.spring(response: 0.35, dampingFraction: 0.85), value: toast)
        }
    }

    private func tabBadge(_ tab: ShelfTab) -> String? {
        guard let c = counts else { return nil }
        switch tab {
        case .all:        return c.total.map { "\($0)" }
        case .read:       return c.read.map { "\($0)" }
        case .reading:    return c.reading.map { "\($0)" }
        case .wantToRead: return c.want.map { "\($0)" }
        }
    }

    private var emptyShelfView: some View {
        VStack(spacing: 18) {
            Spacer()
            if !searchQuery.isEmpty {
                Image(systemName: "magnifyingglass")
                    .font(.system(size: 44, weight: .light))
                    .foregroundStyle(.tertiary)
                Text("No results for \"\(searchQuery)\"")
                    .font(.system(size: 17, weight: .semibold))
                Button("Clear Search") { searchQuery = "" }
                    .font(.system(size: 14, weight: .medium))
                    .foregroundStyle(Color(hex: "#0071e3"))
            } else {
                Image(systemName: selectedTab == .all ? "books.vertical" : "book")
                    .font(.system(size: 52, weight: .ultraLight))
                    .foregroundStyle(Color(hex: "#0071e3").opacity(0.3))
                Text(selectedTab == .all ? "Your library is empty" : "No \(selectedTab.rawValue) books")
                    .font(.system(size: 18, weight: .semibold))
                Text(selectedTab == .all ? "Add books to track what you've read, are reading, or want to read." : "Mark books as \"\(selectedTab.rawValue)\" to see them here.")
                    .font(.system(size: 14))
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal, 40)
                if selectedTab == .all {
                    Button {
                        showAddBook = true
                    } label: {
                        Label("Add Your First Book", systemImage: "plus")
                            .font(.system(size: 14, weight: .semibold))
                            .foregroundStyle(.white)
                            .padding(.horizontal, 22)
                            .padding(.vertical, 11)
                            .background(Color(hex: "#0071e3"))
                            .clipShape(Capsule())
                    }
                    .buttonStyle(.plain)
                    .padding(.top, 4)
                }
            }
            Spacer()
        }
    }

    private func loadBooks() async {
        isLoadingBooks = true
        // Always fetch all books so we have counts; filter client-side
        if let result = try? await api.fetchLibrary(shelf: "") {
            withAnimation(.easeInOut(duration: 0.3)) {
                books = result.books
                counts = result.counts
            }
        }
        isLoadingBooks = false
    }

    private func loadStatus() async {
        status = try? await api.status()
    }

    private func addBook(title: String, status: String) async {
        do {
            try await api.updateBook(title: title, status: status)
            await loadBooks()
            showToast("Added \"\(title)\"", isError: false)
        } catch {
            showToast("Failed to add book", isError: true)
        }
    }

    private func ingestNote() async {
        let t = noteText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !t.isEmpty else { return }
        isIngesting = true
        do {
            try await api.ingestNote(t)
            noteText = ""
            noteEditorFocused = false
            await loadStatus()
            settings.recordActivity()
            settings.totalNotesAdded += 1
            UINotificationFeedbackGenerator().notificationOccurred(.success)
            showToast("Saved to library", isError: false)
        } catch {
            showToast("Failed to save", isError: true)
        }
        isIngesting = false
    }

    private func ingestURL() async {
        let u = urlText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !u.isEmpty else { return }
        isIngesting = true
        do {
            try await api.ingestURL(u)
            urlText = ""
            urlFieldFocused = false
            await loadStatus()
            settings.recordActivity()
            settings.totalNotesAdded += 1
            UINotificationFeedbackGenerator().notificationOccurred(.success)
            showToast("Link saved to library", isError: false)
        } catch {
            showToast("Failed to save link", isError: true)
        }
        isIngesting = false
    }

    private func ingestPaste() async {
        let t = pasteText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !t.isEmpty else { return }
        isIngesting = true
        do {
            try await api.ingestNote(t)
            pasteText = ""
            pasteEditorFocused = false
            await loadStatus()
            settings.recordActivity()
            settings.totalNotesAdded += 1
            UINotificationFeedbackGenerator().notificationOccurred(.success)
            showToast("Saved to library", isError: false)
        } catch {
            showToast("Failed to save", isError: true)
        }
        isIngesting = false
    }

    private func showToast(_ msg: String, isError: Bool) {
        toastIsError = isError
        toast = msg
        DispatchQueue.main.asyncAfter(deadline: .now() + 2.5) { toast = nil }
    }
}

// MARK: - Library Stats Bar

struct LibraryStatsBar: View {
    let counts: LibraryCounts?
    let avgRating: Double?

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 12) {
                if let total = counts?.total {
                    LibStatChip(value: "\(total)", label: "Books")
                }
                if let read = counts?.read, read > 0 {
                    LibStatChip(value: "\(read)", label: "Read")
                }
                if let reading = counts?.reading, reading > 0 {
                    LibStatChip(value: "\(reading)", label: "Reading")
                }
                if let want = counts?.want, want > 0 {
                    LibStatChip(value: "\(want)", label: "Saved")
                }
                if let avg = avgRating {
                    LibStatChip(value: String(format: "⭐ %.1f", avg), label: "Avg Rating")
                }
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 10)
        }
        .background(Color(UIColor.secondarySystemGroupedBackground))
    }
}

struct LibStatChip: View {
    let value: String
    let label: String

    var body: some View {
        VStack(spacing: 2) {
            Text(value)
                .font(.system(size: 17, weight: .bold, design: .rounded))
                .foregroundStyle(Color(hex: "#0071e3"))
            Text(label)
                .font(.system(size: 10))
                .foregroundStyle(.secondary)
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 8)
        .background(Color(UIColor.tertiarySystemFill))
        .clipShape(RoundedRectangle(cornerRadius: 10))
    }
}

// MARK: - Book Grid Cell

struct BookGridCell: View {
    let book: LibraryBook
    @State private var coverLoaded = false

    private var coverURL: URL? {
        if let cu = book.cover_url, !cu.isEmpty {
            return URL(string: cu)
        }
        let encoded = book.title.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? book.title
        return URL(string: "https://covers.openlibrary.org/b/title/\(encoded)-M.jpg")
    }

    private var readingProgress: Double {
        ReadingProgressStore.shared.progress(for: book.title)
    }

    private var isCurrentlyReading: Bool {
        let s = (book.status ?? "").lowercased()
        return s.contains("reading") && !s.contains("want")
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            // Cover image
            ZStack(alignment: .bottomLeading) {
                AsyncImage(url: coverURL) { phase in
                    switch phase {
                    case .success(let image):
                        image
                            .resizable()
                            .aspectRatio(contentMode: .fill)
                            .opacity(coverLoaded ? 1 : 0)
                            .onAppear { withAnimation(.easeInOut(duration: 0.3)) { coverLoaded = true } }
                    default:
                        BookCoverPlaceholder(title: book.title)
                    }
                }
                .frame(height: 160)
                .clipped()
                .clipShape(RoundedRectangle(cornerRadius: 10))

                // Status dot
                if let status = book.status, !status.isEmpty {
                    StatusDot(status: status)
                        .padding(8)
                }

                // Source type icon (top right corner)
                VStack {
                    HStack {
                        Spacer()
                        Image(systemName: "books.vertical.fill")
                            .font(.system(size: 11, weight: .semibold))
                            .foregroundStyle(.white)
                            .padding(5)
                            .background(Color.black.opacity(0.45))
                            .clipShape(RoundedRectangle(cornerRadius: 6))
                    }
                    Spacer()
                }
                .padding(6)
            }
            .frame(maxWidth: .infinity)
            .frame(height: 160)

            VStack(alignment: .leading, spacing: 3) {
                Text(book.title)
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(.primary)
                    .lineLimit(2)
                    .lineSpacing(1)

                if let author = book.author, !author.isEmpty {
                    Text(author)
                        .font(.system(size: 11))
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }

                if let rating = book.rating, rating > 0 {
                    StarRatingView(rating: rating, size: 10, interactive: false, onRate: nil)
                }

                // Reading progress bar (shown for currently reading or any book with progress > 0)
                if isCurrentlyReading || readingProgress > 0 {
                    ReadingProgressBar(progress: readingProgress)
                        .padding(.top, 2)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

// MARK: - Reading Progress Bar

struct ReadingProgressBar: View {
    let progress: Double  // 0.0 to 1.0

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    RoundedRectangle(cornerRadius: 2)
                        .fill(Color(UIColor.systemGray5))
                        .frame(height: 4)
                    RoundedRectangle(cornerRadius: 2)
                        .fill(Color(hex: "#0071e3"))
                        .frame(width: max(4, geo.size.width * CGFloat(progress)), height: 4)
                }
            }
            .frame(height: 4)

            if progress > 0 {
                Text("\(Int(progress * 100))%")
                    .font(.system(size: 9, weight: .medium, design: .rounded))
                    .foregroundStyle(.secondary)
            }
        }
    }
}

// MARK: - Book Cover Placeholder

struct BookCoverPlaceholder: View {
    let title: String

    private var bgColor: Color {
        let colors: [Color] = [
            Color(red: 0.18, green: 0.38, blue: 0.7),
            Color(red: 0.55, green: 0.27, blue: 0.68),
            Color(red: 0.18, green: 0.55, blue: 0.45),
            Color(red: 0.72, green: 0.35, blue: 0.18),
            Color(red: 0.5, green: 0.18, blue: 0.18),
        ]
        let idx = abs(title.hashValue) % colors.count
        return colors[idx]
    }

    var body: some View {
        ZStack {
            bgColor.opacity(0.85)
            VStack(spacing: 8) {
                Image(systemName: "book.closed.fill")
                    .font(.system(size: 28, weight: .light))
                    .foregroundStyle(.white.opacity(0.7))
                Text(title.prefix(30).description)
                    .font(.system(size: 10, weight: .semibold))
                    .foregroundStyle(.white.opacity(0.9))
                    .multilineTextAlignment(.center)
                    .lineLimit(3)
                    .padding(.horizontal, 8)
            }
        }
    }
}

// MARK: - Status Dot

struct StatusDot: View {
    let status: String

    private var dotColor: Color {
        let s = status.lowercased()
        if s == "read" { return .green }
        if s.contains("reading") { return Color(hex: "#0071e3") }
        if s.contains("want") { return .orange }
        return .gray
    }

    private var label: String {
        let s = status.lowercased()
        if s == "read" { return "Read" }
        if s.contains("reading") { return "Reading" }
        if s.contains("want") { return "Want" }
        return status.capitalized
    }

    var body: some View {
        HStack(spacing: 4) {
            Circle().fill(dotColor).frame(width: 6, height: 6)
            Text(label)
                .font(.system(size: 9, weight: .semibold))
                .foregroundStyle(.white)
        }
        .padding(.horizontal, 6)
        .padding(.vertical, 3)
        .background(Color.black.opacity(0.55))
        .clipShape(Capsule())
    }
}

// MARK: - Star Rating View

struct StarRatingView: View {
    let rating: Int
    let size: CGFloat
    let interactive: Bool
    let onRate: ((Int) -> Void)?

    var body: some View {
        HStack(spacing: 2) {
            ForEach(1...5, id: \.self) { i in
                Image(systemName: i <= rating ? "star.fill" : "star")
                    .font(.system(size: size))
                    .foregroundStyle(i <= rating ? Color.orange : Color(UIColor.tertiaryLabel))
                    .onTapGesture {
                        if interactive { onRate?(i) }
                    }
            }
        }
    }
}

// MARK: - Book Detail Sheet

struct BookDetailSheet: View {
    let book: LibraryBook
    let onUpdate: (LibraryBook) -> Void

    @EnvironmentObject var api: APIClient
    @EnvironmentObject var settings: AppSettings
    @Environment(\.dismiss) private var dismiss

    @State private var connections: [BookConnection] = []
    @State private var isLoadingConnections = false
    @State private var currentStatus: String
    @State private var currentRating: Int
    @State private var notesText: String
    @State private var readingProgress: Double
    @State private var askQuery = ""
    @State private var askAnswer = ""
    @State private var isAsking = false
    @State private var isSaving = false
    @State private var toast: String? = nil
    @State private var toastIsError = false
    @FocusState private var askFocused: Bool

    init(book: LibraryBook, onUpdate: @escaping (LibraryBook) -> Void) {
        self.book = book
        self.onUpdate = onUpdate
        _currentStatus = State(initialValue: book.status ?? "Want to read")
        _currentRating = State(initialValue: book.rating ?? 0)
        _notesText = State(initialValue: book.notes ?? "")
        _readingProgress = State(initialValue: ReadingProgressStore.shared.progress(for: book.title))
    }

    private var coverURL: URL? {
        if let cu = book.cover_url, !cu.isEmpty {
            return URL(string: cu)
        }
        let encoded = book.title.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? book.title
        return URL(string: "https://covers.openlibrary.org/b/title/\(encoded)-M.jpg")
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    // Hero section
                    HStack(alignment: .top, spacing: 16) {
                        AsyncImage(url: coverURL) { phase in
                            switch phase {
                            case .success(let image):
                                image.resizable().aspectRatio(contentMode: .fill)
                            default:
                                BookCoverPlaceholder(title: book.title)
                            }
                        }
                        .frame(width: 80, height: 120)
                        .clipShape(RoundedRectangle(cornerRadius: 8))
                        .shadow(color: .black.opacity(0.15), radius: 6, x: 0, y: 3)

                        VStack(alignment: .leading, spacing: 8) {
                            Text(book.title)
                                .font(.system(size: 20, weight: .bold))
                                .tracking(-0.3)
                                .lineSpacing(2)

                            if let author = book.author, !author.isEmpty {
                                Text(author)
                                    .font(.system(size: 14))
                                    .foregroundStyle(.secondary)
                            }

                            // Status badge
                            StatusBadge(status: currentStatus)

                            // Star rating (tappable)
                            StarRatingView(rating: currentRating, size: 18, interactive: true) { newRating in
                                currentRating = newRating
                                Task { await saveUpdate() }
                            }
                        }

                        Spacer()
                    }
                    .padding(20)

                    Divider().padding(.horizontal, 16)

                    // Status update buttons
                    VStack(alignment: .leading, spacing: 12) {
                        Text("Update Status")
                            .font(.system(size: 11, weight: .semibold))
                            .foregroundStyle(.tertiary)
                            .textCase(.uppercase)
                            .tracking(0.6)

                        HStack(spacing: 8) {
                            ForEach([("Read", "Read"), ("Reading", "Currently reading"), ("Want to Read", "Want to read")], id: \.1) { label, val in
                                Button {
                                    currentStatus = val
                                    Task { await saveUpdate() }
                                } label: {
                                    Text(label)
                                        .font(.system(size: 12, weight: .medium))
                                        .foregroundStyle(currentStatus.lowercased() == val.lowercased() ? .white : .secondary)
                                        .padding(.horizontal, 12)
                                        .padding(.vertical, 7)
                                        .background(currentStatus.lowercased() == val.lowercased() ? Color(hex: "#0071e3") : Color(UIColor.tertiarySystemFill))
                                        .clipShape(Capsule())
                                }
                                .buttonStyle(.plain)
                            }
                        }
                    }
                    .padding(.horizontal, 20)
                    .padding(.vertical, 14)

                    Divider().padding(.horizontal, 16)

                    // Reading progress section (only for currently reading books)
                    let statusLower = currentStatus.lowercased()
                    if statusLower.contains("reading") && !statusLower.contains("want") {
                        VStack(alignment: .leading, spacing: 12) {
                            Text("Reading Progress")
                                .font(.system(size: 11, weight: .semibold))
                                .foregroundStyle(.tertiary)
                                .textCase(.uppercase)
                                .tracking(0.6)

                            HStack(spacing: 12) {
                                Slider(value: $readingProgress, in: 0...1, step: 0.01)
                                    .tint(Color(hex: "#0071e3"))
                                    .onChange(of: readingProgress) { _, newValue in
                                        ReadingProgressStore.shared.setProgress(newValue, for: book.title)
                                    }
                                Text("\(Int(readingProgress * 100))%")
                                    .font(.system(size: 13, weight: .semibold, design: .rounded))
                                    .foregroundStyle(Color(hex: "#0071e3"))
                                    .frame(width: 38, alignment: .trailing)
                            }
                        }
                        .padding(.horizontal, 20)
                        .padding(.vertical, 14)

                        Divider().padding(.horizontal, 16)
                    }

                    // Connections section
                    VStack(alignment: .leading, spacing: 12) {
                        HStack {
                            Text("Connections in your KB")
                                .font(.system(size: 11, weight: .semibold))
                                .foregroundStyle(.tertiary)
                                .textCase(.uppercase)
                                .tracking(0.6)
                            Spacer()
                            if isLoadingConnections {
                                ProgressView().scaleEffect(0.7)
                            }
                        }

                        if connections.isEmpty && !isLoadingConnections {
                            Text("No connections found yet. Ask questions to build connections.")
                                .font(.system(size: 13))
                                .foregroundStyle(.secondary)
                                .padding(.vertical, 8)
                        } else {
                            ForEach(connections) { conn in
                                ConnectionRow(connection: conn)
                            }
                        }
                    }
                    .padding(.horizontal, 20)
                    .padding(.vertical, 14)

                    Divider().padding(.horizontal, 16)

                    // Ask about this book
                    VStack(alignment: .leading, spacing: 12) {
                        Text("Ask about this book")
                            .font(.system(size: 11, weight: .semibold))
                            .foregroundStyle(.tertiary)
                            .textCase(.uppercase)
                            .tracking(0.6)

                        HStack(spacing: 10) {
                            TextField("Ask anything about \(book.title)…", text: $askQuery)
                                .font(.system(size: 14))
                                .focused($askFocused)
                                .onSubmit { Task { await askBook() } }

                            Button {
                                Task { await askBook() }
                            } label: {
                                if isAsking {
                                    ProgressView().scaleEffect(0.8)
                                } else {
                                    Image(systemName: "arrow.up.circle.fill")
                                        .font(.system(size: 24))
                                        .foregroundStyle(askQuery.isEmpty ? Color(UIColor.tertiaryLabel) : Color(hex: "#0071e3"))
                                }
                            }
                            .disabled(askQuery.isEmpty || isAsking)
                        }
                        .padding(12)
                        .background(Color(UIColor.tertiarySystemFill))
                        .clipShape(RoundedRectangle(cornerRadius: 10))

                        if !askAnswer.isEmpty {
                            Text(askAnswer)
                                .font(.system(size: 14))
                                .foregroundStyle(.primary)
                                .lineSpacing(4)
                                .padding(12)
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .background(Color(UIColor.secondarySystemGroupedBackground))
                                .clipShape(RoundedRectangle(cornerRadius: 10))
                        }
                    }
                    .padding(.horizontal, 20)
                    .padding(.vertical, 14)

                    Divider().padding(.horizontal, 16)

                    // Notes
                    VStack(alignment: .leading, spacing: 12) {
                        Text("Notes")
                            .font(.system(size: 11, weight: .semibold))
                            .foregroundStyle(.tertiary)
                            .textCase(.uppercase)
                            .tracking(0.6)

                        ZStack(alignment: .topLeading) {
                            if notesText.isEmpty {
                                Text("Add notes, quotes, or thoughts…")
                                    .font(.system(size: 14))
                                    .foregroundStyle(Color(UIColor.placeholderText))
                                    .padding(.top, 8).padding(.leading, 4)
                                    .allowsHitTesting(false)
                            }
                            TextEditor(text: $notesText)
                                .font(.system(size: 14))
                                .frame(minHeight: 100)
                                .scrollContentBackground(.hidden)
                        }
                        .padding(10)
                        .background(Color(UIColor.tertiarySystemFill))
                        .clipShape(RoundedRectangle(cornerRadius: 10))

                        Button {
                            Task { await saveUpdate() }
                        } label: {
                            HStack(spacing: 6) {
                                if isSaving {
                                    ProgressView().scaleEffect(0.8).tint(.white)
                                } else {
                                    Text("Save Notes")
                                        .font(.system(size: 14, weight: .semibold))
                                }
                            }
                            .foregroundStyle(.white)
                            .frame(maxWidth: .infinity)
                            .frame(height: 42)
                            .background(Color(hex: "#0071e3"))
                            .clipShape(RoundedRectangle(cornerRadius: 10))
                        }
                        .buttonStyle(.plain)
                        .disabled(isSaving)
                    }
                    .padding(.horizontal, 20)
                    .padding(.vertical, 14)
                    .padding(.bottom, 20)
                }
            }
            .background(Color(red: 0.98, green: 0.98, blue: 0.97))
            .navigationTitle(book.title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }
                        .foregroundStyle(Color(hex: "#0071e3"))
                }
            }
            .task {
                await loadConnections()
            }
            .overlay(alignment: .bottom) {
                if let t = toast {
                    ToastView(message: t, isError: toastIsError)
                        .padding(.bottom, 24)
                        .transition(.move(edge: .bottom).combined(with: .opacity))
                }
            }
            .animation(.spring(response: 0.35, dampingFraction: 0.85), value: toast)
        }
    }

    private func loadConnections() async {
        isLoadingConnections = true
        if let result = try? await api.fetchBookConnections(title: book.title) {
            withAnimation { connections = result.connections }
        }
        isLoadingConnections = false
    }

    private func askBook() async {
        let q = askQuery.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !q.isEmpty else { return }
        isAsking = true
        askAnswer = ""
        do {
            let result = try await api.askAboutBook(q: q, book: book.title)
            withAnimation { askAnswer = result.answer }
            askQuery = ""
            askFocused = false
        } catch {
            askAnswer = "Could not get an answer. Check your connection."
        }
        isAsking = false
    }

    private func saveUpdate() async {
        isSaving = true
        do {
            let notes = notesText.trimmingCharacters(in: .whitespacesAndNewlines)
            try await api.updateBook(
                title: book.title,
                status: currentStatus,
                rating: currentRating > 0 ? currentRating : nil,
                notes: notes.isEmpty ? nil : notes
            )
            showToast("Saved", isError: false)
            onUpdate(book)
        } catch {
            showToast("Could not save", isError: true)
        }
        isSaving = false
    }

    private func showToast(_ msg: String, isError: Bool) {
        toastIsError = isError
        toast = msg
        DispatchQueue.main.asyncAfter(deadline: .now() + 2) { toast = nil }
    }
}

// MARK: - Status Badge

struct StatusBadge: View {
    let status: String

    private var color: Color {
        let s = status.lowercased()
        if s == "read" { return .green }
        if s.contains("reading") { return Color(hex: "#0071e3") }
        if s.contains("want") { return .orange }
        return .gray
    }

    private var label: String {
        let s = status.lowercased()
        if s == "read" { return "Read" }
        if s.contains("reading") { return "Currently Reading" }
        if s.contains("want") { return "Want to Read" }
        return status.capitalized
    }

    var body: some View {
        Text(label)
            .font(.system(size: 11, weight: .semibold))
            .foregroundStyle(color)
            .padding(.horizontal, 10)
            .padding(.vertical, 4)
            .background(color.opacity(0.1))
            .clipShape(Capsule())
            .overlay(Capsule().stroke(color.opacity(0.3), lineWidth: 0.5))
    }
}

// MARK: - Connection Row

struct ConnectionRow: View {
    let connection: BookConnection

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            if let title = connection.source_title, !title.isEmpty {
                Text(title)
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(Color(hex: "#0071e3"))
                    .lineLimit(1)
            }
            if let excerpt = connection.excerpt, !excerpt.isEmpty {
                Text(excerpt)
                    .font(.system(size: 13))
                    .foregroundStyle(.secondary)
                    .lineLimit(3)
                    .lineSpacing(2)
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color(UIColor.secondarySystemGroupedBackground))
        .clipShape(RoundedRectangle(cornerRadius: 10))
    }
}

// MARK: - Add Book Sheet

struct AddBookSheet: View {
    let onAdd: (String, String) async -> Void

    @Environment(\.dismiss) private var dismiss
    @EnvironmentObject var api: APIClient

    @State private var title = ""
    @State private var selectedStatus = "Want to read"
    @State private var isAdding = false
    @FocusState private var titleFocused: Bool

    let statusOptions = [("Want to Read", "Want to read"), ("Currently Reading", "Currently reading"), ("Read", "Read")]

    var body: some View {
        NavigationStack {
            Form {
                Section("Book Title") {
                    TextField("Enter book title…", text: $title)
                        .focused($titleFocused)
                }
                Section("Status") {
                    Picker("Status", selection: $selectedStatus) {
                        ForEach(statusOptions, id: \.1) { label, val in
                            Text(label).tag(val)
                        }
                    }
                    .pickerStyle(.segmented)
                }
            }
            .navigationTitle("Add Book")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("Cancel") { dismiss() }
                        .foregroundStyle(.secondary)
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        Task {
                            isAdding = true
                            await onAdd(title, selectedStatus)
                            isAdding = false
                            dismiss()
                        }
                    } label: {
                        if isAdding {
                            ProgressView().scaleEffect(0.8)
                        } else {
                            Text("Add")
                                .fontWeight(.semibold)
                        }
                    }
                    .disabled(title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || isAdding)
                    .foregroundStyle(Color(hex: "#0071e3"))
                }
            }
            .onAppear { titleFocused = true }
        }
    }
}

// MARK: - Ingest Sheet

struct IngestSheet: View {
    @Binding var noteText: String
    @Binding var urlText: String
    @Binding var pasteText: String
    @Binding var isIngesting: Bool
    @Binding var expandedMode: IngestMode?
    var noteEditorFocused: FocusState<Bool>.Binding
    var urlFieldFocused: FocusState<Bool>.Binding
    var pasteEditorFocused: FocusState<Bool>.Binding
    let onSaveNote: () async -> Void
    let onSaveURL: () async -> Void
    let onSavePaste: () async -> Void
    let onFileSuccess: () async -> Void
    let showToast: (String, Bool) -> Void

    @EnvironmentObject var api: APIClient
    @EnvironmentObject var settings: AppSettings
    @Environment(\.dismiss) private var dismiss
    @State private var status: StatusResponse? = nil

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 0) {
                    if let s = status {
                        StatsCard(status: s, settings: settings)
                    }

                    VStack(spacing: 0) {
                        ForEach(IngestMode.allCases, id: \.self) { mode in
                            IngestModeRow(
                                mode: mode,
                                isExpanded: expandedMode == mode,
                                noteText: $noteText,
                                urlText: $urlText,
                                pasteText: $pasteText,
                                isIngesting: $isIngesting,
                                noteEditorFocused: noteEditorFocused,
                                urlFieldFocused: urlFieldFocused,
                                pasteEditorFocused: pasteEditorFocused,
                                onTap: {
                                    withAnimation(.spring(response: 0.3, dampingFraction: 0.85)) {
                                        expandedMode = expandedMode == mode ? nil : mode
                                    }
                                },
                                onSaveNote: onSaveNote,
                                onSaveURL: onSaveURL,
                                onSavePaste: onSavePaste,
                                onFileSuccess: { await onFileSuccess(); status = try? await api.status() },
                                showToast: showToast
                            )
                            if mode != IngestMode.allCases.last {
                                Divider().padding(.leading, 56)
                            }
                        }
                    }
                    .background(Color(UIColor.secondarySystemGroupedBackground))
                    .clipShape(RoundedRectangle(cornerRadius: 14))
                    .padding(.horizontal, 16)
                    .padding(.top, 16)
                    .padding(.bottom, 16)

                    if let s = status, !s.sources.isEmpty {
                        SourcesCard(sources: s.sources)
                    }

                    Button {
                        Task {
                            do {
                                try await api.refresh()
                                status = try? await api.status()
                                showToast("Sources synced", false)
                            } catch {
                                showToast("Sync failed", true)
                            }
                        }
                    } label: {
                        Label("Sync sources", systemImage: "arrow.triangle.2.circlepath")
                            .font(.system(size: 14, weight: .medium))
                            .foregroundStyle(.secondary)
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 12)
                            .background(Color(UIColor.secondarySystemGroupedBackground))
                            .clipShape(RoundedRectangle(cornerRadius: 12))
                            .padding(.horizontal, 16)
                    }
                    .buttonStyle(.plain)
                }
                .padding(.bottom, 20)
            }
            .background(Color(UIColor.systemGroupedBackground))
            .ignoresSafeArea(.keyboard, edges: .bottom)
            .navigationTitle("Add to Library")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }
                        .foregroundStyle(Color(hex: "#0071e3"))
                }
            }
            .task { status = try? await api.status() }
        }
    }
}

// MARK: - Toast View

struct ToastView: View {
    let message: String
    let isError: Bool

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: isError ? "exclamationmark.circle.fill" : "checkmark.circle.fill")
                .foregroundStyle(isError ? Color.red : Color.green)
                .font(.system(size: 14))
            Text(message)
                .font(.system(size: 14, weight: .medium))
                .foregroundStyle(.white)
        }
        .padding(.horizontal, 18)
        .padding(.vertical, 10)
        .background(Color(UIColor.label))
        .clipShape(Capsule())
        .shadow(color: Color.black.opacity(0.15), radius: 8, x: 0, y: 4)
    }
}

// MARK: - Ingest Mode Row

struct IngestModeRow: View {
    let mode: IngestMode
    let isExpanded: Bool
    @Binding var noteText: String
    @Binding var urlText: String
    @Binding var pasteText: String
    @Binding var isIngesting: Bool
    var noteEditorFocused: FocusState<Bool>.Binding
    var urlFieldFocused: FocusState<Bool>.Binding
    var pasteEditorFocused: FocusState<Bool>.Binding
    let onTap: () -> Void
    let onSaveNote: () async -> Void
    let onSaveURL: () async -> Void
    let onSavePaste: () async -> Void
    let onFileSuccess: () async -> Void
    let showToast: (String, Bool) -> Void

    var body: some View {
        VStack(spacing: 0) {
            Button(action: onTap) {
                HStack(spacing: 12) {
                    Image(systemName: mode.icon)
                        .font(.system(size: 15))
                        .foregroundStyle(mode.color)
                        .frame(width: 32, height: 32)
                        .background(mode.color.opacity(0.12))
                        .clipShape(RoundedRectangle(cornerRadius: 8))

                    VStack(alignment: .leading, spacing: 2) {
                        Text(mode.title)
                            .font(.system(size: 15, weight: .medium))
                            .foregroundStyle(.primary)
                        Text(mode.subtitle)
                            .font(.system(size: 12))
                            .foregroundStyle(.secondary)
                    }

                    Spacer()

                    Image(systemName: isExpanded ? "chevron.up" : "chevron.down")
                        .font(.system(size: 11, weight: .medium))
                        .foregroundStyle(.tertiary)
                }
                .padding(.horizontal, 14)
                .padding(.vertical, 13)
            }
            .buttonStyle(.plain)

            if isExpanded {
                Divider().padding(.leading, 56)

                Group {
                    switch mode {
                    case .note:
                        NoteInputCard(
                            text: $noteText,
                            isIngesting: $isIngesting,
                            isFocused: noteEditorFocused,
                            onSave: onSaveNote
                        )
                        .padding(.horizontal, 14)
                        .padding(.vertical, 12)
                    case .url:
                        URLInputCard(
                            text: $urlText,
                            isIngesting: $isIngesting,
                            isFocused: urlFieldFocused,
                            onSave: onSaveURL
                        )
                        .padding(.horizontal, 14)
                        .padding(.vertical, 12)
                    case .file:
                        FileUploadCard(
                            isIngesting: $isIngesting,
                            onSuccess: onFileSuccess,
                            showToast: showToast
                        )
                        .padding(.horizontal, 14)
                        .padding(.vertical, 12)
                    case .goodnotes:
                        NoteInputCard(
                            text: $pasteText,
                            isIngesting: $isIngesting,
                            isFocused: pasteEditorFocused,
                            onSave: onSavePaste
                        )
                        .padding(.horizontal, 14)
                        .padding(.vertical, 12)
                    case .voice:
                        VoiceRecordCard(
                            isIngesting: $isIngesting,
                            onSuccess: onFileSuccess,
                            showToast: showToast
                        )
                        .padding(.horizontal, 14)
                        .padding(.vertical, 12)
                    default:
                        HStack(spacing: 10) {
                            Image(systemName: "clock")
                                .font(.system(size: 13))
                                .foregroundStyle(.tertiary)
                            Text("Coming soon")
                                .font(.system(size: 13))
                                .foregroundStyle(.secondary)
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.horizontal, 14)
                        .padding(.vertical, 16)
                    }
                }
                .transition(.opacity.combined(with: .move(edge: .top)))
            }
        }
    }
}

// MARK: - Stats Card

struct StatsCard: View {
    let status: StatusResponse
    let settings: AppSettings

    var body: some View {
        HStack(spacing: 0) {
            StatPill(value: "\(status.total_chunks)", label: "passages")
            Divider().frame(height: 40).padding(.horizontal, 20)
            StatPill(value: "\(status.sources.count)", label: "sources")
            if settings.totalNotesAdded > 0 {
                Divider().frame(height: 40).padding(.horizontal, 20)
                StatPill(value: "\(settings.totalNotesAdded)", label: "added")
            }
            Spacer()
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 16)
        .frame(maxWidth: .infinity)
        .background(Color(UIColor.secondarySystemGroupedBackground))
        .clipShape(RoundedRectangle(cornerRadius: 14))
        .padding(.horizontal, 16)
    }
}

struct StatPill: View {
    let value: String
    let label: String

    var body: some View {
        VStack(alignment: .leading, spacing: 1) {
            Text(value)
                .font(.system(size: 26, weight: .bold, design: .rounded))
                .foregroundStyle(Color(hex: "#0071e3"))
            Text(label)
                .font(.system(size: 11))
                .foregroundStyle(.secondary)
        }
        .contentTransition(.numericText())
    }
}

// MARK: - Note Input

struct NoteInputCard: View {
    @Binding var text: String
    @Binding var isIngesting: Bool
    var isFocused: FocusState<Bool>.Binding
    let onSave: () async -> Void

    private let maxChars = 5000
    var isEmpty: Bool { text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
    var charCount: Int { text.count }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Spacer()
                if let clip = UIPasteboard.general.string,
                   !clip.isEmpty,
                   text.isEmpty {
                    Button("Paste") {
                        text = String(clip.prefix(maxChars))
                    }
                    .font(.system(size: 12, weight: .medium))
                    .foregroundStyle(Color(hex: "#0071e3"))
                }
            }

            ZStack(alignment: .topLeading) {
                if text.isEmpty {
                    Text("Jot something down…")
                        .font(.system(size: 15))
                        .foregroundStyle(Color(UIColor.placeholderText))
                        .padding(.top, 8)
                        .padding(.leading, 4)
                        .allowsHitTesting(false)
                }
                TextEditor(text: $text)
                    .font(.system(size: 15))
                    .frame(minHeight: 100)
                    .scrollContentBackground(.hidden)
                    .focused(isFocused)
                    .onChange(of: text) { _, new in
                        if new.count > maxChars {
                            text = String(new.prefix(maxChars))
                        }
                    }
            }
            .padding(10)
            .background(Color(UIColor.tertiarySystemFill))
            .clipShape(RoundedRectangle(cornerRadius: 10))

            HStack {
                if !text.isEmpty {
                    Text("\(charCount)/\(maxChars)")
                        .font(.system(size: 11, design: .rounded))
                        .foregroundStyle(charCount > maxChars - 200 ? Color.orange : Color(UIColor.tertiaryLabel))
                        .animation(.easeInOut(duration: 0.2), value: charCount)
                }
                Spacer()
            }

            SaveButton(isEmpty: isEmpty, isIngesting: isIngesting) {
                Task { await onSave() }
            }
        }
    }
}

// MARK: - URL Input

struct URLInputCard: View {
    @Binding var text: String
    @Binding var isIngesting: Bool
    var isFocused: FocusState<Bool>.Binding
    let onSave: () async -> Void

    var isEmpty: Bool { text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
    var isValidURL: Bool {
        let t = text.trimmingCharacters(in: .whitespacesAndNewlines)
        return t.hasPrefix("http://") || t.hasPrefix("https://")
    }
    var isYouTube: Bool {
        let t = text.lowercased()
        return t.contains("youtube.com") || t.contains("youtu.be")
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 8) {
                Image(systemName: isYouTube ? "play.rectangle.fill" : "link")
                    .foregroundStyle(isYouTube ? Color.red : (isEmpty ? Color(UIColor.tertiaryLabel) : Color(hex: "#0071e3")))
                    .font(.system(size: 14))

                TextField("https://…", text: $text)
                    .font(.system(size: 15))
                    .keyboardType(.URL)
                    .autocapitalization(.none)
                    .autocorrectionDisabled()
                    .focused(isFocused)

                if !text.isEmpty {
                    Button {
                        text = ""
                    } label: {
                        Image(systemName: "xmark.circle.fill")
                            .foregroundStyle(Color(UIColor.tertiaryLabel))
                            .font(.system(size: 16))
                    }
                    .buttonStyle(.plain)
                } else if let clip = UIPasteboard.general.string,
                   (clip.hasPrefix("http://") || clip.hasPrefix("https://")) {
                    Button("Paste") {
                        text = clip
                    }
                    .font(.system(size: 13, weight: .medium))
                    .foregroundStyle(Color(hex: "#0071e3"))
                }
            }
            .padding(12)
            .background(Color(UIColor.tertiarySystemFill))
            .clipShape(RoundedRectangle(cornerRadius: 10))
            .overlay(
                RoundedRectangle(cornerRadius: 10)
                    .stroke(
                        !isEmpty && !isValidURL ? Color.red.opacity(0.5) : Color.clear,
                        lineWidth: 1
                    )
            )

            if !isEmpty && !isValidURL {
                Label("Enter a valid URL starting with http:// or https://", systemImage: "exclamationmark.circle")
                    .font(.system(size: 11))
                    .foregroundStyle(Color.red.opacity(0.8))
                    .transition(.opacity.combined(with: .move(edge: .top)))
            }

            if isYouTube && isValidURL {
                HStack(spacing: 6) {
                    Image(systemName: "play.rectangle.fill")
                        .font(.system(size: 12))
                        .foregroundStyle(Color.red)
                    Text("YouTube video detected")
                        .font(.system(size: 12, weight: .medium))
                        .foregroundStyle(Color.green)
                }
                .transition(.opacity.combined(with: .move(edge: .top)))
            }

            SaveButton(isEmpty: isEmpty || !isValidURL, isIngesting: isIngesting) {
                Task { await onSave() }
            }
        }
        .animation(.spring(response: 0.25, dampingFraction: 0.8), value: isValidURL)
        .animation(.spring(response: 0.25, dampingFraction: 0.8), value: isYouTube)
    }
}

struct SaveButton: View {
    let isEmpty: Bool
    let isIngesting: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Group {
                if isIngesting {
                    ProgressView().tint(.white)
                } else {
                    Text("Save to Library")
                        .font(.system(size: 15, weight: .semibold))
                }
            }
            .frame(maxWidth: .infinity)
            .frame(height: 44)
            .background(isEmpty ? Color(UIColor.systemGray4) : Color(hex: "#0071e3"))
            .foregroundStyle(.white)
            .clipShape(RoundedRectangle(cornerRadius: 10))
            .animation(.easeInOut(duration: 0.2), value: isEmpty)
        }
        .disabled(isEmpty || isIngesting)
    }
}

// MARK: - Document Picker

struct DocumentPickerView: UIViewControllerRepresentable {
    let onPick: (URL) -> Void

    func makeCoordinator() -> Coordinator { Coordinator(onPick: onPick) }

    func makeUIViewController(context: Context) -> UIDocumentPickerViewController {
        let types: [UTType] = [.pdf, .plainText, .data, .content]
        let picker = UIDocumentPickerViewController(forOpeningContentTypes: types, asCopy: true)
        picker.delegate = context.coordinator
        picker.allowsMultipleSelection = false
        return picker
    }

    func updateUIViewController(_ uiViewController: UIDocumentPickerViewController, context: Context) {}

    class Coordinator: NSObject, UIDocumentPickerDelegate {
        let onPick: (URL) -> Void
        init(onPick: @escaping (URL) -> Void) { self.onPick = onPick }

        func documentPicker(_ controller: UIDocumentPickerViewController, didPickDocumentsAt urls: [URL]) {
            guard let url = urls.first else { return }
            onPick(url)
        }
    }
}

// MARK: - File Upload Card

struct FileUploadCard: View {
    @EnvironmentObject var api: APIClient
    @Binding var isIngesting: Bool
    let onSuccess: () async -> Void
    let showToast: (String, Bool) -> Void

    @State private var showPicker = false
    @State private var selectedFileName: String? = nil

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Button {
                showPicker = true
            } label: {
                HStack(spacing: 10) {
                    Image(systemName: "folder.badge.plus")
                        .font(.system(size: 15))
                        .foregroundStyle(Color.orange)
                    Text(selectedFileName ?? "Choose File")
                        .font(.system(size: 15))
                        .foregroundStyle(selectedFileName != nil ? .primary : .secondary)
                        .lineLimit(1)
                        .truncationMode(.middle)
                    Spacer()
                    if selectedFileName != nil {
                        Image(systemName: "checkmark.circle.fill")
                            .foregroundStyle(Color.green)
                            .font(.system(size: 14))
                    }
                }
                .padding(12)
                .background(Color(UIColor.tertiarySystemFill))
                .clipShape(RoundedRectangle(cornerRadius: 10))
            }
            .buttonStyle(.plain)
            .sheet(isPresented: $showPicker) {
                DocumentPickerView { url in
                    selectedFileName = url.lastPathComponent
                    Task { await uploadFile(at: url) }
                }
                .ignoresSafeArea()
            }

            Text("Supported: PDF, text, and most document types")
                .font(.system(size: 11))
                .foregroundStyle(.tertiary)

            if isIngesting {
                HStack(spacing: 8) {
                    ProgressView().scaleEffect(0.85)
                    Text("Uploading…")
                        .font(.system(size: 13))
                        .foregroundStyle(.secondary)
                }
            }
        }
    }

    private func uploadFile(at url: URL) async {
        guard let data = try? Data(contentsOf: url) else {
            showToast("Could not read file", true)
            return
        }
        let filename = url.lastPathComponent
        let ext = url.pathExtension.lowercased()
        let mimeType: String
        switch ext {
        case "pdf":  mimeType = "application/pdf"
        case "txt":  mimeType = "text/plain"
        case "md":   mimeType = "text/markdown"
        case "html", "htm": mimeType = "text/html"
        default:     mimeType = "application/octet-stream"
        }
        isIngesting = true
        do {
            try await api.ingestFile(data: data, filename: filename, mimeType: mimeType)
            selectedFileName = nil
            await onSuccess()
            showToast("File saved to library", false)
        } catch {
            showToast("Failed to upload: \(error.localizedDescription)", true)
        }
        isIngesting = false
    }
}

// MARK: - Voice Recorder Helper

@MainActor
class VoiceRecorder: NSObject, ObservableObject {
    @Published var isRecording = false
    @Published var elapsedSeconds = 0

    private var recorder: AVAudioRecorder?
    private var countTimer: Timer?
    var audioURL: URL?

    func requestPermission() async -> Bool {
        await withCheckedContinuation { cont in
            AVAudioSession.sharedInstance().requestRecordPermission { granted in
                cont.resume(returning: granted)
            }
        }
    }

    func start() throws {
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("neuron_voice_\(Int(Date().timeIntervalSince1970)).m4a")
        let settings: [String: Any] = [
            AVFormatIDKey: Int(kAudioFormatMPEG4AAC),
            AVSampleRateKey: 16000,
            AVNumberOfChannelsKey: 1,
            AVEncoderAudioQualityKey: AVAudioQuality.high.rawValue,
        ]
        try AVAudioSession.sharedInstance().setCategory(.record, mode: .default)
        try AVAudioSession.sharedInstance().setActive(true)
        recorder = try AVAudioRecorder(url: url, settings: settings)
        recorder?.record()
        audioURL = url
        isRecording = true
        elapsedSeconds = 0
        countTimer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
            guard let self else { return }
            Task { @MainActor in
                self.elapsedSeconds += 1
                if self.elapsedSeconds >= 180 { self.stop() }  // 3-min cap
            }
        }
    }

    func stop() {
        recorder?.stop()
        countTimer?.invalidate()
        countTimer = nil
        isRecording = false
        try? AVAudioSession.sharedInstance().setActive(false)
    }

    var formattedTime: String {
        String(format: "%d:%02d", elapsedSeconds / 60, elapsedSeconds % 60)
    }
}

// MARK: - Voice Record Card

struct VoiceRecordCard: View {
    @EnvironmentObject var api: APIClient
    @Binding var isIngesting: Bool
    let onSuccess: () async -> Void
    let showToast: (String, Bool) -> Void

    @StateObject private var recorder = VoiceRecorder()
    @State private var permissionDenied = false
    @State private var isTranscribing = false
    @State private var editedText = ""
    @State private var showPreview = false

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            if permissionDenied {
                Label("Microphone access required. Enable it in Settings.", systemImage: "mic.slash")
                    .font(.system(size: 13))
                    .foregroundStyle(.secondary)
                Button("Open Settings") {
                    if let url = URL(string: UIApplication.openSettingsURLString) {
                        UIApplication.shared.open(url)
                    }
                }
                .font(.system(size: 13, weight: .medium))
                .foregroundStyle(Color(hex: "#0071e3"))
            } else if showPreview {
                previewArea
            } else if isTranscribing {
                HStack(spacing: 10) {
                    ProgressView().scaleEffect(0.85)
                    Text("Transcribing…")
                        .font(.system(size: 14))
                        .foregroundStyle(.secondary)
                }
            } else {
                Text(recorder.isRecording ? "Speak freely — what did you learn, think about, or want to remember?" : "Tap the mic and speak. Neuron will transcribe and save it to your knowledge base.")
                    .font(.system(size: 13))
                    .foregroundStyle(.secondary)
                    .lineLimit(3)

                Button {
                    Task { await toggleRecording() }
                } label: {
                    HStack(spacing: 12) {
                        ZStack {
                            Circle()
                                .fill(recorder.isRecording ? Color.red : Color.purple)
                                .frame(width: 44, height: 44)
                                .scaleEffect(recorder.isRecording ? 1.1 : 1.0)
                                .animation(recorder.isRecording ? Animation.easeInOut(duration: 0.7).repeatForever(autoreverses: true) : .default, value: recorder.isRecording)
                            Image(systemName: recorder.isRecording ? "stop.fill" : "mic.fill")
                                .font(.system(size: 18))
                                .foregroundStyle(.white)
                        }
                        VStack(alignment: .leading, spacing: 2) {
                            Text(recorder.isRecording ? "Recording…" : "Start Recording")
                                .font(.system(size: 15, weight: .semibold))
                                .foregroundStyle(.primary)
                            if recorder.isRecording {
                                Text(recorder.formattedTime)
                                    .font(.system(size: 12, design: .monospaced))
                                    .foregroundStyle(.red)
                            } else {
                                Text("Up to 3 minutes")
                                    .font(.system(size: 12))
                                    .foregroundStyle(.secondary)
                            }
                        }
                        Spacer()
                    }
                }
                .buttonStyle(.plain)
            }
        }
    }

    private var previewArea: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Transcription — edit if needed")
                .font(.system(size: 11, weight: .semibold))
                .foregroundStyle(.tertiary)
                .textCase(.uppercase)
                .tracking(0.5)

            ZStack(alignment: .topLeading) {
                if editedText.isEmpty {
                    Text("No speech detected")
                        .font(.system(size: 14))
                        .foregroundStyle(Color(UIColor.placeholderText))
                        .padding(.top, 8).padding(.leading, 4)
                        .allowsHitTesting(false)
                }
                TextEditor(text: $editedText)
                    .font(.system(size: 14))
                    .frame(minHeight: 90)
                    .scrollContentBackground(.hidden)
            }
            .padding(10)
            .background(Color(UIColor.tertiarySystemFill))
            .clipShape(RoundedRectangle(cornerRadius: 10))

            HStack(spacing: 10) {
                Button("Re-record") {
                    withAnimation { showPreview = false; editedText = "" }
                }
                .font(.system(size: 14, weight: .medium))
                .foregroundStyle(.secondary)

                Spacer()

                SaveButton(isEmpty: editedText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, isIngesting: isIngesting) {
                    Task { await saveMemo() }
                }
                .frame(width: 160)
            }
        }
    }

    private func toggleRecording() async {
        if recorder.isRecording {
            recorder.stop()
            await transcribeAudio()
        } else {
            let granted = await recorder.requestPermission()
            guard granted else { permissionDenied = true; return }
            do {
                try recorder.start()
            } catch {
                showToast("Could not start recording", true)
            }
        }
    }

    private func transcribeAudio() async {
        guard let audioURL = recorder.audioURL else {
            showToast("Recording not saved", true)
            return
        }
        guard SFSpeechRecognizer.authorizationStatus() != .denied else {
            withAnimation { showPreview = true }
            return
        }
        isTranscribing = true
        let transcript = await withCheckedContinuation { (cont: CheckedContinuation<String, Never>) in
            SFSpeechRecognizer.requestAuthorization { status in
                guard status == .authorized else { cont.resume(returning: ""); return }
                let recognizer = SFSpeechRecognizer(locale: Locale(identifier: "en-US"))
                guard recognizer?.isAvailable == true else { cont.resume(returning: ""); return }
                let request = SFSpeechURLRecognitionRequest(url: audioURL)
                request.shouldReportPartialResults = false
                recognizer?.recognitionTask(with: request) { result, error in
                    if let result, result.isFinal {
                        cont.resume(returning: result.bestTranscription.formattedString)
                    } else if error != nil {
                        cont.resume(returning: "")
                    }
                }
            }
        }
        isTranscribing = false
        editedText = transcript
        withAnimation { showPreview = true }
    }

    private func saveMemo() async {
        let text = editedText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        isIngesting = true
        do {
            let duration = Double(recorder.elapsedSeconds)
            try await api.ingestVoiceMemo(text, durationSeconds: duration)
            editedText = ""
            showPreview = false
            await onSuccess()
            showToast("Voice memo saved", false)
        } catch {
            showToast("Failed to save memo", true)
        }
        isIngesting = false
    }
}

// MARK: - Sources Card

struct SourcesCard: View {
    let sources: [String: Int]

    private var sorted: [(String, Int)] {
        sources.sorted { $0.value > $1.value }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Label("Connected Sources", systemImage: "folder")
                .font(.system(size: 11, weight: .semibold))
                .foregroundStyle(.tertiary)
                .textCase(.uppercase)
                .tracking(0.8)

            ForEach(sorted, id: \.0) { name, count in
                HStack(spacing: 10) {
                    Image(systemName: sourceSystemIcon(name))
                        .font(.system(size: 13))
                        .foregroundStyle(sourceColor(name))
                        .frame(width: 28, height: 28)
                        .background(sourceColor(name).opacity(0.12))
                        .clipShape(RoundedRectangle(cornerRadius: 7))

                    Text(sourceName(name))
                        .font(.system(size: 14))
                        .foregroundStyle(.primary)
                    Spacer()

                    let maxCount = sorted.first?.1 ?? 1
                    let fraction = CGFloat(count) / CGFloat(maxCount)
                    GeometryReader { geo in
                        ZStack(alignment: .leading) {
                            RoundedRectangle(cornerRadius: 2)
                                .fill(Color(UIColor.systemGray5))
                                .frame(height: 4)
                            RoundedRectangle(cornerRadius: 2)
                                .fill(sourceColor(name).opacity(0.6))
                                .frame(width: geo.size.width * fraction, height: 4)
                        }
                    }
                    .frame(width: 60, height: 4)

                    Text("\(count)")
                        .font(.system(size: 12, weight: .semibold, design: .rounded))
                        .foregroundStyle(.secondary)
                        .frame(width: 32, alignment: .trailing)
                }
            }
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color(UIColor.secondarySystemGroupedBackground))
        .clipShape(RoundedRectangle(cornerRadius: 14))
        .padding(.horizontal, 16)
    }

    private func sourceName(_ key: String) -> String {
        switch key.lowercased() {
        case "google_calendar": return "Google Calendar"
        case "gmail":           return "Gmail"
        case "canvas":          return "Canvas LMS"
        case "readwise":        return "Readwise"
        case "goodnotes":       return "GoodNotes"
        case "note":            return "Notes"
        case "url":             return "Saved Links"
        case "twitter":         return "Twitter / X"
        default:                return key.split(separator: "_").map { $0.capitalized }.joined(separator: " ")
        }
    }

    private func sourceSystemIcon(_ name: String) -> String {
        switch name.lowercased() {
        case "google_calendar": return "calendar"
        case "gmail":           return "envelope"
        case "canvas":          return "graduationcap"
        case "readwise":        return "book"
        case "goodnotes":       return "pencil"
        case "note":            return "note.text"
        case "url":             return "link"
        case "twitter":         return "bird"
        default:                return "doc.text"
        }
    }

    private func sourceColor(_ name: String) -> Color {
        switch name.lowercased() {
        case "google_calendar": return Color.blue
        case "gmail":           return Color.red
        case "canvas":          return Color.orange
        case "readwise":        return Color.purple
        case "goodnotes":       return Color.yellow
        case "note":            return Color(hex: "#0071e3")
        case "url":             return Color.teal
        case "twitter":         return Color.cyan
        default:                return Color.secondary
        }
    }
}
