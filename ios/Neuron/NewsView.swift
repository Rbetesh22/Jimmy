import SwiftUI
import UIKit

// MARK: - Read-state persistence (in-memory + UserDefaults)
private let kReadArticles = "neuron.readArticles"
private let kReadCount    = "neuron.readCount"

private class ReadStore: ObservableObject {
    static let shared = ReadStore()
    @Published var readIDs: Set<String>
    @Published var totalReadCount: Int

    private init() {
        let saved = UserDefaults.standard.stringArray(forKey: kReadArticles) ?? []
        readIDs = Set(saved)
        totalReadCount = UserDefaults.standard.integer(forKey: kReadCount)
    }

    func markRead(_ id: String) {
        guard !readIDs.contains(id) else { return }
        readIDs.insert(id)
        totalReadCount += 1
        persist()
    }

    func markUnread(_ id: String) {
        guard readIDs.contains(id) else { return }
        readIDs.remove(id)
        totalReadCount = max(0, totalReadCount - 1)
        persist()
    }

    func toggleRead(_ id: String) {
        if readIDs.contains(id) { markUnread(id) } else { markRead(id) }
    }

    func isRead(_ id: String) -> Bool { readIDs.contains(id) }

    private func persist() {
        UserDefaults.standard.set(Array(readIDs), forKey: kReadArticles)
        UserDefaults.standard.set(totalReadCount, forKey: kReadCount)
    }
}

// MARK: - NewsView

struct NewsView: View {
    @EnvironmentObject var api: APIClient
    @EnvironmentObject var settings: AppSettings
    @StateObject fileprivate var readStore = ReadStore.shared
    @State private var byCategory: [String: [NewsArticle]] = [:]
    @State private var summary: String = ""
    @State private var selectedCat: String = "All"
    @State private var isLoading = true
    @State private var summaryExpanded = true
    @State private var isLoadingMore = false
    @State private var currentOffset = 0
    @State private var hasMore = true
    private let pageSize = 30

    private let catOrder = ["World", "Israel", "Politics", "Tech", "AI", "Finance", "Sports", "Torah"]

    private var allCats: [String] {
        let present = catOrder.filter { byCategory[$0] != nil }
        let extra = byCategory.keys.filter { !catOrder.contains($0) }.sorted()
        return ["All"] + present + extra
    }

    private var displayArticles: [NewsArticle] {
        if selectedCat == "All" {
            return catOrder.flatMap { byCategory[$0] ?? [] }
                + byCategory.filter { !catOrder.contains($0.key) }.values.flatMap { $0 }
        } else {
            return byCategory[selectedCat] ?? []
        }
    }

    private func articleCount(for cat: String) -> Int {
        if cat == "All" { return byCategory.values.reduce(0) { $0 + $1.count } }
        return byCategory[cat]?.count ?? 0
    }

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                // AI summary banner
                if !summary.isEmpty {
                    VStack(alignment: .leading, spacing: 0) {
                        Button {
                            withAnimation(.spring(response: 0.35, dampingFraction: 0.85)) {
                                summaryExpanded.toggle()
                            }
                        } label: {
                            HStack(alignment: .top, spacing: 10) {
                                Image(systemName: "brain")
                                    .font(.system(size: 13))
                                    .foregroundStyle(Color(hex: "#0071e3"))
                                    .padding(.top, 1)

                                let attrSummary = (try? AttributedString(markdown: summary)) ?? AttributedString(summary)
                                Text(attrSummary)
                                    .font(.system(size: 13.5))
                                    .foregroundStyle(.secondary)
                                    .lineSpacing(3)
                                    .lineLimit(summaryExpanded ? nil : 4)
                                    .frame(maxWidth: .infinity, alignment: .leading)

                                Image(systemName: summaryExpanded ? "chevron.up" : "chevron.down")
                                    .font(.system(size: 11, weight: .medium))
                                    .foregroundStyle(Color(UIColor.tertiaryLabel))
                                    .padding(.top, 2)
                            }
                            .padding(.horizontal, 16)
                            .padding(.vertical, 12)
                        }
                        .buttonStyle(.plain)
                    }
                    .background(Color(hex: "faf9f7"))

                    Divider()
                }

                // Category tabs
                ScrollViewReader { tabProxy in
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 6) {
                            ForEach(allCats, id: \.self) { cat in
                                let count = articleCount(for: cat)
                                Button {
                                    withAnimation(.easeInOut(duration: 0.2)) { selectedCat = cat }
                                    withAnimation { tabProxy.scrollTo(cat, anchor: .center) }
                                    if settings.hapticEnabled {
                                        UIImpactFeedbackGenerator(style: .light).impactOccurred()
                                    }
                                } label: {
                                    HStack(spacing: 4) {
                                        Text(cat)
                                            .font(.system(size: 13, weight: selectedCat == cat ? .semibold : .regular))
                                        if count > 0 && cat != "All" && selectedCat != cat {
                                            Text("\(count)")
                                                .font(.system(size: 11, design: .rounded))
                                                .foregroundStyle(Color(UIColor.tertiaryLabel))
                                        }
                                    }
                                    .foregroundStyle(selectedCat == cat ? Color(UIColor.systemBackground) : .secondary)
                                    .padding(.horizontal, 14)
                                    .padding(.vertical, 6)
                                    .background(selectedCat == cat ? Color(hex: "#0071e3") : Color.clear)
                                    .clipShape(Capsule())
                                    .overlay(
                                        Capsule()
                                            .stroke(Color(UIColor.separator), lineWidth: selectedCat == cat ? 0 : 0.5)
                                    )
                                    .animation(.spring(response: 0.3, dampingFraction: 0.85), value: selectedCat)
                                }
                                .id(cat)
                            }
                        }
                        .padding(.horizontal, 16)
                        .padding(.vertical, 10)
                    }
                    .onChange(of: selectedCat) { _, newCat in
                        withAnimation { tabProxy.scrollTo(newCat, anchor: .center) }
                    }
                }

                Divider()

                // Articles
                if isLoading {
                    NewsSkeletonView()
                } else if displayArticles.isEmpty {
                    VStack(spacing: 12) {
                        Spacer()
                        Image(systemName: "newspaper")
                            .font(.system(size: 48, weight: .light))
                            .foregroundStyle(Color(UIColor.tertiaryLabel))
                            .frame(width: 48, height: 48)
                        Text("No articles")
                            .font(.system(size: 17, weight: .semibold))
                            .foregroundStyle(.primary)
                        Text("No articles in this category")
                            .font(.system(size: 15))
                            .foregroundStyle(.secondary)
                            .multilineTextAlignment(.center)
                        Spacer()
                    }
                } else {
                    ScrollView {
                        LazyVStack(spacing: 0) {
                            ForEach(Array(displayArticles.enumerated()), id: \.element.id) { index, article in
                                NewsRow(article: article, isFirst: index == 0, readStore: readStore)
                                    .padding(.horizontal, 16)
                                    .swipeActions(edge: .trailing, allowsFullSwipe: true) {
                                        Button {
                                            withAnimation { readStore.toggleRead(article.id) }
                                            if settings.hapticEnabled {
                                                UIImpactFeedbackGenerator(style: .light).impactOccurred()
                                            }
                                        } label: {
                                            Label(
                                                readStore.isRead(article.id) ? "Unread" : "Read",
                                                systemImage: readStore.isRead(article.id) ? "envelope.badge" : "checkmark.circle"
                                            )
                                        }
                                        .tint(.gray)
                                    }
                                    .swipeActions(edge: .leading, allowsFullSwipe: true) {
                                        Button {
                                            Task {
                                                try? await api.ingestURL(article.url)
                                            }
                                            if settings.hapticEnabled {
                                                UIImpactFeedbackGenerator(style: .medium).impactOccurred()
                                            }
                                        } label: {
                                            Label("Save", systemImage: "bookmark.fill")
                                        }
                                        .tint(.blue)
                                    }

                                if index < displayArticles.count - 1 {
                                    Divider().padding(.horizontal, 16)
                                }
                            }

                            // Load more footer
                            if hasMore && !isLoading {
                                loadMoreFooter
                            }
                        }
                        .padding(.vertical, 8)
                    }
                    .refreshable { await reload() }
                }
            }
            .background(Color(hex: "f5f0e8"))
            .navigationTitle("Today's World")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button { Task { await reload() } } label: {
                        Image(systemName: "arrow.clockwise")
                            .foregroundStyle(Color(hex: "#0071e3"))
                    }
                }
            }
            .task { await load() }
        }
    }

    @ViewBuilder
    private var loadMoreFooter: some View {
        if isLoadingMore {
            HStack {
                Spacer()
                ProgressView()
                    .padding(.vertical, 16)
                Spacer()
            }
        } else {
            Button {
                Task { await loadMore() }
            } label: {
                Text("Load more")
                    .font(.system(size: 14, weight: .medium))
                    .foregroundStyle(Color(hex: "#0071e3"))
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 14)
            }
            .buttonStyle(.plain)
        }
    }

    // MARK: - Data loading

    private func load() async {
        isLoading = true
        currentOffset = 0
        hasMore = true
        async let newsTask = try? api.news()
        async let sumTask  = try? api.newsSummary()
        let (news, sum) = await (newsTask, sumTask)
        let fetched = news?.by_category ?? [:]
        let totalFetched = fetched.values.reduce(0) { $0 + $1.count }
        withAnimation(.easeInOut(duration: 0.3)) {
            byCategory = fetched
            summary    = sum?.summary ?? ""
            currentOffset = totalFetched
            hasMore = totalFetched >= pageSize
            isLoading  = false
        }
    }

    private func reload() async {
        currentOffset = 0
        hasMore = true
        async let newsTask = try? api.news()
        async let sumTask  = try? api.newsSummary()
        let (news, sum) = await (newsTask, sumTask)
        let fetched = news?.by_category ?? [:]
        let totalFetched = fetched.values.reduce(0) { $0 + $1.count }
        withAnimation(.easeInOut(duration: 0.3)) {
            byCategory = fetched
            summary    = sum?.summary ?? ""
            currentOffset = totalFetched
            hasMore = totalFetched >= pageSize
        }
    }

    private func loadMore() async {
        guard !isLoadingMore && hasMore else { return }
        isLoadingMore = true
        if let more = try? await api.newsPage(offset: currentOffset, limit: pageSize) {
            let newByCategory = more.by_category
            let totalFetched = newByCategory.values.reduce(0) { $0 + $1.count }
            withAnimation {
                for (cat, articles) in newByCategory {
                    byCategory[cat, default: []].append(contentsOf: articles)
                }
                currentOffset += totalFetched
                hasMore = totalFetched >= pageSize
            }
        } else {
            hasMore = false
        }
        isLoadingMore = false
    }
}

// MARK: - NewsRow

struct NewsRow: View {
    let article: NewsArticle
    let isFirst: Bool
    fileprivate let readStore: ReadStore
    @State private var imageLoaded = false
    @State private var showSafari = false
    @State private var isSaving = false
    @State private var savedToast = false
    @EnvironmentObject var api: APIClient
    @EnvironmentObject var settings: AppSettings

    private var isRead: Bool { readStore.isRead(article.id) }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Button {
                if settings.hapticEnabled {
                    UIImpactFeedbackGenerator(style: .light).impactOccurred()
                }
                readStore.markRead(article.id)
                showSafari = true
            } label: {
                HStack(alignment: .top, spacing: 12) {
                    VStack(alignment: .leading, spacing: 5) {
                        // Source + time row
                        HStack(spacing: 6) {
                            Text(article.source.uppercased())
                                .font(.system(size: 11, weight: .semibold))
                                .foregroundStyle(isRead ? Color(UIColor.tertiaryLabel) : categoryColor.opacity(0.85))
                                .tracking(0.4)

                            if let timeAgo = article.time_ago, !timeAgo.isEmpty {
                                Text("·")
                                    .font(.system(size: 11))
                                    .foregroundStyle(.quaternary)
                                Text(timeAgo)
                                    .font(.system(size: 11))
                                    .foregroundStyle(Color(UIColor.tertiaryLabel))
                            }

                            Spacer()

                            // Category tag
                            Text(article.category)
                                .font(.system(size: 10, weight: .medium))
                                .foregroundStyle(categoryColor)
                                .padding(.horizontal, 6)
                                .padding(.vertical, 2)
                                .background(categoryColor.opacity(0.1))
                                .clipShape(RoundedRectangle(cornerRadius: 4))
                        }

                        Text(article.title)
                            .font(.system(size: isFirst ? 17 : 14, weight: isFirst ? .bold : .semibold))
                            .foregroundStyle(isRead ? .secondary : .primary)
                            .lineLimit(isFirst ? 3 : 2)
                            .lineSpacing(2)
                            .frame(maxWidth: .infinity, alignment: .leading)

                        if let desc = article.description, !desc.isEmpty {
                            Text(desc)
                                .font(.system(size: 13))
                                .foregroundStyle(.secondary)
                                .lineLimit(isFirst ? 3 : 2)
                                .lineSpacing(2)
                        }
                    }

                    // Thumbnail
                    VStack(alignment: .trailing, spacing: 6) {
                        if let imgStr = article.image, let imgURL = URL(string: imgStr) {
                            AsyncImage(url: imgURL) { phase in
                                switch phase {
                                case .success(let image):
                                    image.resizable()
                                        .aspectRatio(contentMode: .fill)
                                        .opacity(imageLoaded ? 1 : 0)
                                        .onAppear { withAnimation(.easeInOut(duration: 0.3)) { imageLoaded = true } }
                                default:
                                    categoryPlaceholder
                                }
                            }
                            .frame(width: isFirst ? 100 : 72, height: isFirst ? 70 : 52)
                            .clipShape(RoundedRectangle(cornerRadius: 8))
                            .overlay(
                                isRead
                                    ? RoundedRectangle(cornerRadius: 8).fill(Color.black.opacity(0.25))
                                    : nil
                            )
                        } else {
                            categoryPlaceholder
                                .frame(width: isFirst ? 100 : 72, height: isFirst ? 70 : 52)
                                .clipShape(RoundedRectangle(cornerRadius: 8))
                        }
                    }
                }
            }
            .buttonStyle(.plain)

            // Action row: Share + Save to Neuron + Read indicator
            HStack(spacing: 14) {
                if let url = URL(string: article.url) {
                    ShareLink(item: url) {
                        HStack(spacing: 4) {
                            Image(systemName: "square.and.arrow.up")
                                .font(.system(size: 12))
                            Text("Share")
                                .font(.system(size: 12))
                        }
                        .foregroundStyle(Color(UIColor.tertiaryLabel))
                    }
                    .buttonStyle(.plain)
                }

                Button {
                    guard !isSaving else { return }
                    isSaving = true
                    Task {
                        do {
                            try await api.ingestURL(article.url)
                            await MainActor.run {
                                isSaving = false
                                savedToast = true
                            }
                            try? await Task.sleep(nanoseconds: 2_000_000_000)
                            await MainActor.run { savedToast = false }
                        } catch {
                            await MainActor.run { isSaving = false }
                        }
                    }
                    if settings.hapticEnabled {
                        UIImpactFeedbackGenerator(style: .light).impactOccurred()
                    }
                } label: {
                    HStack(spacing: 4) {
                        if isSaving {
                            ProgressView()
                                .scaleEffect(0.7)
                                .frame(width: 12, height: 12)
                        } else {
                            Image(systemName: savedToast ? "checkmark" : "brain")
                                .font(.system(size: 12))
                        }
                        Text(savedToast ? "Saved" : "Save to Neuron")
                            .font(.system(size: 12))
                    }
                    .foregroundStyle(savedToast ? Color(hex: "#34c759") : Color(UIColor.tertiaryLabel))
                    .animation(.easeInOut(duration: 0.2), value: savedToast)
                }
                .buttonStyle(.plain)

                Spacer()

                if isRead {
                    Button {
                        withAnimation { readStore.markUnread(article.id) }
                    } label: {
                        HStack(spacing: 3) {
                            Image(systemName: "checkmark.circle.fill")
                                .font(.system(size: 11))
                            Text("Read")
                                .font(.system(size: 11))
                        }
                        .foregroundStyle(Color(hex: "#34c759").opacity(0.7))
                    }
                    .buttonStyle(.plain)
                } else {
                    Button {
                        readStore.markRead(article.id)
                        showSafari = true
                    } label: {
                        HStack(spacing: 3) {
                            Image(systemName: "arrow.up.right")
                                .font(.system(size: 11))
                            Text("Read")
                                .font(.system(size: 11, weight: .medium))
                        }
                        .foregroundStyle(Color(hex: "#0071e3"))
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(.top, 8)
        }
        .padding(.vertical, 12)
        .opacity(isRead ? 0.72 : 1.0)
        .animation(.easeInOut(duration: 0.2), value: isRead)
        .fullScreenCover(isPresented: $showSafari) {
            if let url = URL(string: article.url) {
                SafariView(url: url)
                    .ignoresSafeArea()
            }
        }
    }

    private var categoryPlaceholder: some View {
        RoundedRectangle(cornerRadius: 8)
            .fill(categoryColor.opacity(0.12))
            .overlay(
                Text(categoryEmoji)
                    .font(.system(size: isFirst ? 28 : 20))
            )
    }

    private var categoryColor: Color {
        switch article.category.lowercased() {
        case "israel":   return Color(red: 0.0, green: 0.48, blue: 1.0)
        case "world":    return Color(red: 0.0, green: 0.6,  blue: 0.5)
        case "politics": return .red
        case "ai":       return .purple
        case "tech":     return Color(red: 0.35, green: 0.27, blue: 1.0)
        case "finance":  return .green
        case "sports":   return .orange
        case "torah":    return Color(red: 0.0, green: 0.5, blue: 0.3)
        default:         return .gray
        }
    }

    private var categoryEmoji: String {
        switch article.category.lowercased() {
        case "israel":   return "🇮🇱"
        case "world":    return "🌍"
        case "politics": return "🏛️"
        case "ai":       return "🤖"
        case "tech":     return "💻"
        case "finance":  return "📈"
        case "sports":   return "⚽"
        case "torah":    return "📖"
        default:         return "📰"
        }
    }
}

// MARK: - News Image Card (kept for future use)

struct NewsImageCard: View {
    let article: NewsArticle
    @State private var imageLoaded = false
    @State private var showSafari = false
    @EnvironmentObject var settings: AppSettings

    var body: some View {
        Button {
            if settings.hapticEnabled {
                UIImpactFeedbackGenerator(style: .light).impactOccurred()
            }
            showSafari = true
        } label: {
            VStack(alignment: .leading, spacing: 6) {
                if let imgStr = article.image, let imgURL = URL(string: imgStr) {
                    AsyncImage(url: imgURL) { phase in
                        switch phase {
                        case .success(let image):
                            image.resizable()
                                .aspectRatio(contentMode: .fill)
                                .opacity(imageLoaded ? 1 : 0)
                                .onAppear {
                                    withAnimation(.easeInOut(duration: 0.3)) { imageLoaded = true }
                                }
                        default:
                            categoryPlaceholder
                        }
                    }
                    .frame(width: 160, height: 100)
                    .clipShape(RoundedRectangle(cornerRadius: 12))
                } else {
                    categoryPlaceholder
                        .frame(width: 160, height: 100)
                        .clipShape(RoundedRectangle(cornerRadius: 12))
                }

                Text(article.source.uppercased())
                    .font(.system(size: 10, weight: .semibold))
                    .foregroundStyle(Color(UIColor.tertiaryLabel))
                    .tracking(0.4)

                Text(article.title)
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(.primary)
                    .lineLimit(2)
                    .lineSpacing(1.5)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            .frame(width: 160)
        }
        .buttonStyle(.plain)
        .fullScreenCover(isPresented: $showSafari) {
            if let url = URL(string: article.url) {
                SafariView(url: url)
                    .ignoresSafeArea()
            }
        }
    }

    private var categoryPlaceholder: some View {
        RoundedRectangle(cornerRadius: 12)
            .fill(categoryColor.opacity(0.12))
            .overlay(
                Text(categoryEmoji)
                    .font(.system(size: 32))
            )
    }

    private var categoryColor: Color {
        switch article.category.lowercased() {
        case "israel":   return Color(red: 0.0, green: 0.48, blue: 1.0)
        case "world":    return Color(red: 0.0, green: 0.6,  blue: 0.5)
        case "politics": return .red
        case "ai":       return .purple
        case "tech":     return Color(red: 0.35, green: 0.27, blue: 1.0)
        case "finance":  return .green
        case "sports":   return .orange
        case "torah":    return Color(red: 0.0, green: 0.5, blue: 0.3)
        default:         return .gray
        }
    }

    private var categoryEmoji: String {
        switch article.category.lowercased() {
        case "israel":   return "🇮🇱"
        case "world":    return "🌍"
        case "politics": return "🏛️"
        case "ai":       return "🤖"
        case "tech":     return "💻"
        case "finance":  return "📈"
        case "sports":   return "⚽"
        case "torah":    return "📖"
        default:         return "📰"
        }
    }
}

// MARK: - News Skeleton

struct NewsSkeletonView: View {
    var body: some View {
        ScrollView {
            LazyVStack(spacing: 0) {
                ForEach(0..<8, id: \.self) { i in
                    VStack(alignment: .leading, spacing: 8) {
                        HStack(alignment: .top, spacing: 12) {
                            VStack(alignment: .leading, spacing: 8) {
                                // Source + time skeleton
                                HStack(spacing: 6) {
                                    RoundedRectangle(cornerRadius: 3)
                                        .fill(Color(UIColor.systemGray5))
                                        .frame(width: 60, height: 10)
                                    RoundedRectangle(cornerRadius: 3)
                                        .fill(Color(UIColor.systemGray5))
                                        .frame(width: 30, height: 10)
                                    Spacer()
                                    RoundedRectangle(cornerRadius: 4)
                                        .fill(Color(UIColor.systemGray5))
                                        .frame(width: 40, height: 16)
                                }
                                RoundedRectangle(cornerRadius: 3)
                                    .fill(Color(UIColor.systemGray5))
                                    .frame(maxWidth: .infinity)
                                    .frame(height: i == 0 ? 16 : 13)
                                RoundedRectangle(cornerRadius: 3)
                                    .fill(Color(UIColor.systemGray5))
                                    .frame(maxWidth: 180)
                                    .frame(height: i == 0 ? 16 : 13)
                            }
                            Spacer()
                            RoundedRectangle(cornerRadius: 8)
                                .fill(Color(UIColor.systemGray5))
                                .frame(width: i == 0 ? 100 : 72, height: i == 0 ? 70 : 52)
                        }
                        // Action row skeleton
                        HStack(spacing: 14) {
                            RoundedRectangle(cornerRadius: 3)
                                .fill(Color(UIColor.systemGray5))
                                .frame(width: 48, height: 10)
                            RoundedRectangle(cornerRadius: 3)
                                .fill(Color(UIColor.systemGray5))
                                .frame(width: 88, height: 10)
                            Spacer()
                        }
                    }
                    .padding(.horizontal, 16)
                    .padding(.vertical, 12)
                    .overlay(ShimmerView())
                    if i < 7 { Divider().padding(.horizontal, 16) }
                }
            }
            .padding(.vertical, 8)
        }
    }
}
