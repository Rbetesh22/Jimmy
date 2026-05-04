import SwiftUI

struct SparksView: View {
    @EnvironmentObject var api: APIClient
    @EnvironmentObject var settings: AppSettings
    @State private var sparks: [Spark] = []
    @State private var analogies: [Analogy] = []
    @State private var crossDomainConnections: [Analogy] = []
    @State private var todayAnalogy: Analogy? = nil
    @State private var isLoading = true
    @State private var loadError = false
    @State private var isRefreshingFresh = false
    @State private var selectedTab: SparksTab = .connections
    @State private var isShufflingAnalogies = false
    @State private var isLoadingMore = false
    @State private var isLoadingCrossDomain = false
    @State private var crossDomainLoadError = false

    enum SparksTab { case connections, crossDomain, analogies }

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                // Tab picker
                Picker("", selection: $selectedTab) {
                    Text("Connections").tag(SparksTab.connections)
                    Text("Cross-Domain").tag(SparksTab.crossDomain)
                    Text("Analogies").tag(SparksTab.analogies)
                }
                .pickerStyle(.segmented)
                .padding(.horizontal, 16)
                .padding(.vertical, 10)

                Group {
                    if isLoading {
                        SparkSkeletonView()
                    } else if loadError {
                        SparksErrorView(onRetry: { Task { await load() } })
                    } else if selectedTab == .connections {
                        if sparks.isEmpty {
                            EmptySparksView(onRetry: { Task { await loadFresh() } })
                        } else {
                            sparksList
                        }
                    } else if selectedTab == .crossDomain {
                        crossDomainTab
                    } else {
                        if analogies.isEmpty && todayAnalogy == nil {
                            EmptyAnalogiesView()
                        } else {
                            analogiesList
                        }
                    }
                }
            }
            .background(Color(hex: "f5f0e8"))
            .navigationTitle("Connections")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    HStack(spacing: 12) {
                        if selectedTab == .analogies {
                            Button {
                                Task { await shuffleAnalogies() }
                            } label: {
                                if isShufflingAnalogies {
                                    ProgressView().scaleEffect(0.75)
                                } else {
                                    Image(systemName: "shuffle")
                                        .foregroundStyle(Color(hex: "#0071e3"))
                                }
                            }
                            .disabled(isShufflingAnalogies)
                        }
                        if selectedTab == .crossDomain {
                            Button {
                                Task { await loadCrossDomain() }
                            } label: {
                                if isLoadingCrossDomain {
                                    ProgressView().scaleEffect(0.75)
                                } else {
                                    Image(systemName: "arrow.clockwise")
                                        .foregroundStyle(Color(hex: "#0071e3"))
                                }
                            }
                            .disabled(isLoadingCrossDomain)
                        } else {
                            Button {
                                Task { await loadFresh() }
                            } label: {
                                Image(systemName: "arrow.clockwise")
                                    .foregroundStyle(Color(hex: "#0071e3"))
                            }
                            .disabled(isRefreshingFresh)
                        }
                    }
                }
            }
            .task { await load() }
            .onChange(of: selectedTab) { _, newTab in
                if newTab == .crossDomain && crossDomainConnections.isEmpty && !isLoadingCrossDomain {
                    Task { await loadCrossDomain() }
                }
            }
        }
    }

    // MARK: - Sparks (Connections) List

    private var sparksList: some View {
        ScrollView {
            VStack(spacing: 0) {
                HStack {
                    Text("\(sparks.count) connections found")
                        .font(.system(size: 12))
                        .foregroundStyle(.tertiary)
                    Spacer()
                    if isRefreshingFresh {
                        HStack(spacing: 5) {
                            ProgressView().scaleEffect(0.7)
                            Text("Refreshing…").font(.system(size: 12)).foregroundStyle(.tertiary)
                        }
                    }
                }
                .padding(.horizontal, 16).padding(.top, 8).padding(.bottom, 6)

                LazyVStack(spacing: 10) {
                    ForEach(Array(sparks.enumerated()), id: \.element.id) { i, spark in
                        SparkCard(spark: spark, index: i)
                    }

                    // Load more footer
                    loadMoreFooter
                }
                .padding(.horizontal, 16).padding(.bottom, 20)
            }
        }
        .refreshable { await loadFresh() }
    }

    @ViewBuilder
    private var loadMoreFooter: some View {
        if isLoadingMore {
            ProgressView()
                .padding(.vertical, 16)
        } else {
            Button {
                Task { await loadMoreSparks() }
            } label: {
                HStack(spacing: 6) {
                    Image(systemName: "plus.circle")
                        .font(.system(size: 14))
                    Text("Load more connections")
                        .font(.system(size: 14, weight: .medium))
                }
                .foregroundStyle(Color(hex: "#0071e3"))
                .frame(maxWidth: .infinity)
                .padding(.vertical, 12)
                .background(Color(hex: "#0071e3").opacity(0.08))
                .clipShape(RoundedRectangle(cornerRadius: 12))
            }
            .buttonStyle(.plain)
            .padding(.top, 6)
        }
    }

    // MARK: - Cross-Domain Tab

    @ViewBuilder
    private var crossDomainTab: some View {
        if isLoadingCrossDomain {
            SparkSkeletonView()
        } else if crossDomainLoadError {
            SparksErrorView(onRetry: { Task { await loadCrossDomain() } })
        } else if crossDomainConnections.isEmpty {
            EmptyAnalogiesView()
        } else {
            crossDomainList
        }
    }

    private var crossDomainList: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Surprising connections across domains")
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundStyle(.primary)
                    Text("Ideas from different fields that illuminate each other")
                        .font(.system(size: 12))
                        .foregroundStyle(.tertiary)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, 16)
                .padding(.top, 12)
                .padding(.bottom, 10)

                LazyVStack(spacing: 12) {
                    ForEach(Array(crossDomainConnections.enumerated()), id: \.element.id) { i, analogy in
                        CrossDomainCard(analogy: analogy, index: i)
                            .onTapGesture {
                                let q = "How are \(analogy.domain_a ?? "Domain A") and \(analogy.domain_b ?? "Domain B") connected? Specifically: \(analogy.concept_a ?? "") and \(analogy.concept_b ?? "")."
                                settings.pendingAskQuery = q
                                if settings.hapticEnabled {
                                    UIImpactFeedbackGenerator(style: .medium).impactOccurred()
                                }
                            }
                    }
                }
                .padding(.horizontal, 16)
                .padding(.bottom, 24)
            }
        }
        .refreshable { await loadCrossDomain() }
    }

    // MARK: - Analogies List

    private var analogiesList: some View {
        ScrollView {
            VStack(spacing: 0) {
                // Today's analogy from /today endpoint — featured at top with lightbulb
                if let todayA = todayAnalogy {
                    TodayAnalogyBanner(analogy: todayA)
                        .padding(.horizontal, 16)
                        .padding(.top, 12)
                        .padding(.bottom, 8)
                }

                // Today's Connection banner (from analogies list) only if no /today analogy
                if let featured = analogies.first, todayAnalogy == nil {
                    TodaysConnectionBanner(analogy: featured)
                        .padding(.horizontal, 16)
                        .padding(.top, 12)
                        .padding(.bottom, 8)
                }

                Text("Cross-domain analogies from your knowledge base")
                    .font(.system(size: 12))
                    .foregroundStyle(.tertiary)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 16)
                    .padding(.top, 4)
                    .padding(.bottom, 8)

                LazyVStack(spacing: 12) {
                    let displayAnalogies: [Analogy] = todayAnalogy != nil
                        ? Array(analogies.prefix(3))
                        : Array(analogies.dropFirst().prefix(3))
                    ForEach(Array(displayAnalogies.enumerated()), id: \.element.id) { i, analogy in
                        AnalogyCard(analogy: analogy, index: i)
                            .onTapGesture {
                                let q = "How are \(analogy.domain_a ?? "Domain A") and \(analogy.domain_b ?? "Domain B") connected? Specifically: \(analogy.concept_a ?? "") and \(analogy.concept_b ?? "")."
                                settings.pendingAskQuery = q
                                if settings.hapticEnabled {
                                    UIImpactFeedbackGenerator(style: .medium).impactOccurred()
                                }
                            }
                    }
                }
                .padding(.horizontal, 16)
            }
            .padding(.bottom, 24)
        }
        .refreshable { await loadFresh() }
    }

    // MARK: - Data Loading

    private func load() async {
        isLoading = true
        loadError = false
        async let sparksTask = try? api.sparks()
        async let analogiesTask = try? api.analogies()
        async let todayTask = try? api.today()
        let (sparksResult, analogiesResult, todayResult) = await (sparksTask, analogiesTask, todayTask)
        sparks = sparksResult?.sparks ?? []
        analogies = analogiesResult?.analogies ?? []
        todayAnalogy = todayResult?.analogy
        if sparks.isEmpty && analogies.isEmpty && todayAnalogy == nil {
            loadError = true
        }
        isLoading = false
    }

    private func loadFresh() async {
        isRefreshingFresh = true
        async let sparksTask = try? api.sparks(refresh: true)
        async let analogiesTask = try? api.analogies(refresh: true)
        async let todayTask = try? api.today()
        let (sparksResult, analogiesResult, todayResult) = await (sparksTask, analogiesTask, todayTask)
        if let s = sparksResult { withAnimation { sparks = s.sparks } }
        if let a = analogiesResult { withAnimation { analogies = a.analogies } }
        if let t = todayResult { withAnimation { todayAnalogy = t.analogy } }
        isRefreshingFresh = false
    }

    private func loadMoreSparks() async {
        guard !isLoadingMore else { return }
        isLoadingMore = true
        if let result = try? await api.sparks(refresh: true) {
            withAnimation(.spring(response: 0.4, dampingFraction: 0.85)) {
                let existingIds = Set(sparks.map { $0.id })
                let newSparks = result.sparks.filter { !existingIds.contains($0.id) }
                sparks.append(contentsOf: newSparks.isEmpty ? result.sparks : newSparks)
            }
        }
        isLoadingMore = false
    }

    private func loadCrossDomain() async {
        isLoadingCrossDomain = true
        crossDomainLoadError = false
        if let result = try? await api.fetchCrossDomain() {
            withAnimation(.spring(response: 0.4, dampingFraction: 0.8)) {
                crossDomainConnections = result.analogies
            }
        } else {
            crossDomainLoadError = true
        }
        isLoadingCrossDomain = false
    }

    private func shuffleAnalogies() async {
        isShufflingAnalogies = true
        if let result = try? await api.analogies(randomTopic: true) {
            withAnimation(.spring(response: 0.4, dampingFraction: 0.8)) {
                analogies = result.analogies
            }
        }
        isShufflingAnalogies = false
    }
}

// MARK: - Gradient Helpers

private enum TopicCategory {
    case technology, science, philosophy, history, art, nature, economics, psychology, other

    static func from(text: String) -> TopicCategory {
        let lower = text.lowercased()
        if lower.contains("algorithm") || lower.contains("code") || lower.contains("software") ||
           lower.contains("computer") || lower.contains("data") || lower.contains("ai") ||
           lower.contains("machine learning") || lower.contains("crypto") || lower.contains("network") ||
           lower.contains("distributed") || lower.contains("protocol") { return .technology }
        if lower.contains("physics") || lower.contains("biology") || lower.contains("chemistry") ||
           lower.contains("quantum") || lower.contains("evolution") || lower.contains("neural") ||
           lower.contains("molecule") || lower.contains("gene") { return .science }
        if lower.contains("philosophy") || lower.contains("ethics") || lower.contains("logic") ||
           lower.contains("metaphysics") || lower.contains("epistemology") { return .philosophy }
        if lower.contains("history") || lower.contains("ancient") || lower.contains("war") ||
           lower.contains("empire") || lower.contains("civilization") { return .history }
        if lower.contains("art") || lower.contains("music") || lower.contains("film") ||
           lower.contains("literature") || lower.contains("design") || lower.contains("creative") { return .art }
        if lower.contains("nature") || lower.contains("ecology") || lower.contains("environment") ||
           lower.contains("climate") || lower.contains("ecosystem") { return .nature }
        if lower.contains("economics") || lower.contains("market") || lower.contains("finance") ||
           lower.contains("trade") || lower.contains("business") || lower.contains("money") { return .economics }
        if lower.contains("psychology") || lower.contains("behavior") || lower.contains("cognitive") ||
           lower.contains("brain") || lower.contains("mind") || lower.contains("emotion") { return .psychology }
        return .other
    }

    var gradientColors: [Color] {
        switch self {
        case .technology:  return [Color(hex: "#0071e3"), Color(hex: "#5b8af0")]
        case .science:     return [Color(hex: "#34c759"), Color(hex: "#30d158")]
        case .philosophy:  return [Color(hex: "#af52de"), Color(hex: "#bf5af2")]
        case .history:     return [Color(hex: "#ff9500"), Color(hex: "#ffcc00")]
        case .art:         return [Color(hex: "#ff375f"), Color(hex: "#ff6b6b")]
        case .nature:      return [Color(hex: "#30b0c7"), Color(hex: "#32ade6")]
        case .economics:   return [Color(hex: "#ff6b00"), Color(hex: "#ff9500")]
        case .psychology:  return [Color(hex: "#ac8e68"), Color(hex: "#c7956c")]
        case .other:       return [Color(hex: "#636366"), Color(hex: "#8e8e93")]
        }
    }

    var accentColor: Color { gradientColors[0] }
}

// MARK: - SparkCard

struct SparkCard: View {
    let spark: Spark
    let index: Int
    @State private var expanded = false
    @State private var copied = false
    @State private var appeared = false
    @State private var isSaving = false
    @State private var saveSuccess = false
    @EnvironmentObject var settings: AppSettings
    @EnvironmentObject var api: APIClient

    private var category: TopicCategory {
        TopicCategory.from(text: (spark.title ?? "") + " " + (spark.connection ?? ""))
    }

    private var accentColor: Color { category.accentColor }
    private var gradientColors: [Color] { category.gradientColors }

    private static let technicalKeywords = ["algorithm", "distributed", "consensus", "byzantine", "protocol", "theorem", "proof", "complexity", "cryptography", "heuristic"]

    private var isTechnical: Bool {
        let lower = (spark.title ?? "").lowercased()
        return Self.technicalKeywords.contains { lower.contains($0) }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            // Gradient top bar — 4px accent strip
            LinearGradient(colors: gradientColors, startPoint: .leading, endPoint: .trailing)
                .frame(maxWidth: .infinity)
                .frame(height: 4)

            Button {
                withAnimation(.spring(response: 0.3, dampingFraction: 0.8)) {
                    expanded.toggle()
                }
                if settings.hapticEnabled {
                    UIImpactFeedbackGenerator(style: .light).impactOccurred()
                }
            } label: {
                VStack(alignment: .leading, spacing: 10) {
                    // Title row with optional Technical tag and action buttons
                    HStack(alignment: .top, spacing: 8) {
                        Text(spark.title ?? "Connection")
                            .font(.system(size: 18, weight: .bold))
                            .tracking(-0.4)
                            .foregroundStyle(.primary)
                            .multilineTextAlignment(.leading)
                            .lineSpacing(2)
                            .frame(maxWidth: .infinity, alignment: .leading)

                        VStack(alignment: .trailing, spacing: 6) {
                            HStack(spacing: 6) {
                                if isTechnical {
                                    Text("Technical")
                                        .font(.system(size: 10, weight: .semibold))
                                        .foregroundStyle(accentColor)
                                        .padding(.horizontal, 7)
                                        .padding(.vertical, 3)
                                        .background(accentColor.opacity(0.1))
                                        .clipShape(Capsule())
                                }

                                // Bookmark button
                                Button {
                                    Task { await saveSparkToNotes() }
                                } label: {
                                    Group {
                                        if isSaving {
                                            ProgressView().scaleEffect(0.6)
                                        } else {
                                            Image(systemName: saveSuccess ? "bookmark.fill" : "bookmark")
                                                .font(.system(size: 13, weight: .medium))
                                                .foregroundStyle(saveSuccess ? accentColor : .secondary)
                                        }
                                    }
                                    .frame(width: 28, height: 28)
                                    .background(Color(UIColor.tertiarySystemFill))
                                    .clipShape(RoundedRectangle(cornerRadius: 7))
                                }
                                .buttonStyle(.plain)

                                // Share button
                                Button {
                                    shareSparkCard()
                                } label: {
                                    Image(systemName: "square.and.arrow.up")
                                        .font(.system(size: 13, weight: .medium))
                                        .foregroundStyle(.secondary)
                                        .frame(width: 28, height: 28)
                                        .background(Color(UIColor.tertiarySystemFill))
                                        .clipShape(RoundedRectangle(cornerRadius: 7))
                                }
                                .buttonStyle(.plain)
                            }
                            Image(systemName: expanded ? "chevron.up" : "chevron.down")
                                .font(.system(size: 10, weight: .medium))
                                .foregroundStyle(.quaternary)
                        }
                    }

                    // Connection between two items — two-column layout
                    if let recent = spark.recent_item, let past = spark.past_item {
                        HStack(alignment: .top, spacing: 8) {
                            VStack(alignment: .leading, spacing: 4) {
                                Text("RECENT")
                                    .font(.system(size: 9, weight: .bold))
                                    .foregroundStyle(accentColor)
                                    .tracking(0.5)
                                Text(recent)
                                    .font(.system(size: 12))
                                    .foregroundStyle(.primary)
                                    .lineLimit(3)
                                    .lineSpacing(1.5)
                            }
                            .padding(8)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .background(accentColor.opacity(0.07))
                            .clipShape(RoundedRectangle(cornerRadius: 8))

                            Text("↔")
                                .font(.system(size: 14, weight: .medium))
                                .foregroundStyle(accentColor.opacity(0.5))
                                .padding(.top, 16)

                            VStack(alignment: .leading, spacing: 4) {
                                Text("OLDER")
                                    .font(.system(size: 9, weight: .bold))
                                    .foregroundStyle(.secondary)
                                    .tracking(0.5)
                                Text(past)
                                    .font(.system(size: 12))
                                    .foregroundStyle(.secondary)
                                    .lineLimit(3)
                                    .lineSpacing(1.5)
                            }
                            .padding(8)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .background(Color(UIColor.tertiarySystemFill))
                            .clipShape(RoundedRectangle(cornerRadius: 8))
                        }
                    } else if let conn = spark.connection {
                        Text(expanded ? conn : String(conn.prefix(120)) + (conn.count > 120 ? "…" : ""))
                            .font(.system(size: 14))
                            .foregroundStyle(.secondary)
                            .lineSpacing(3)
                            .multilineTextAlignment(.leading)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                }
                .padding(16)
            }
            .buttonStyle(.plain)

            if expanded {
                Divider().padding(.horizontal, 16)

                VStack(alignment: .leading, spacing: 16) {
                    // Full connection text if not already shown as two-row
                    if spark.recent_item != nil, let conn = spark.connection {
                        Text(conn)
                            .font(.system(size: 14))
                            .foregroundStyle(.secondary)
                            .lineSpacing(3)
                    }

                    // Why it matters — italicized with gradient left accent bar
                    if let why = spark.why_it_matters, !why.isEmpty {
                        HStack(alignment: .top, spacing: 10) {
                            LinearGradient(colors: gradientColors, startPoint: .top, endPoint: .bottom)
                                .frame(width: 3)
                                .clipShape(Capsule())
                            Text(why)
                                .font(.system(size: 14).italic())
                                .foregroundStyle(.secondary)
                                .lineSpacing(3)
                        }
                    }

                    // "Ask Jimmy about this" — full width accent button
                    Button {
                        let query = spark.title ?? spark.connection ?? ""
                        settings.pendingAskQuery = "Tell me more about: \(query)"
                        if settings.hapticEnabled {
                            UIImpactFeedbackGenerator(style: .medium).impactOccurred()
                        }
                    } label: {
                        HStack(spacing: 6) {
                            Text("Ask Jimmy about this")
                                .font(.system(size: 14, weight: .semibold))
                            Text("→")
                                .font(.system(size: 14, weight: .semibold))
                        }
                        .foregroundStyle(accentColor)
                        .frame(maxWidth: .infinity)
                        .padding(12)
                        .background(accentColor.opacity(0.08))
                        .clipShape(RoundedRectangle(cornerRadius: 10))
                    }
                    .buttonStyle(.plain)

                    // Bottom action row
                    HStack(spacing: 8) {
                        // Explore button
                        if let recent = spark.recent_item, let past = spark.past_item {
                            Button {
                                settings.pendingAskQuery = "Tell me more about the connection between \"\(recent)\" and \"\(past)\""
                                if settings.hapticEnabled {
                                    UIImpactFeedbackGenerator(style: .light).impactOccurred()
                                }
                            } label: {
                                Text("Explore →")
                                    .font(.system(size: 12.5, weight: .semibold))
                                    .foregroundStyle(.white)
                                    .padding(.horizontal, 14)
                                    .padding(.vertical, 7)
                                    .background(
                                        LinearGradient(colors: gradientColors, startPoint: .leading, endPoint: .trailing)
                                    )
                                    .clipShape(Capsule())
                            }
                            .buttonStyle(.plain)
                        }

                        // Copy
                        if let conn = spark.connection {
                            Button {
                                UIPasteboard.general.string = conn
                                withAnimation(.easeInOut(duration: 0.2)) { copied = true }
                                if settings.hapticEnabled {
                                    UINotificationFeedbackGenerator().notificationOccurred(.success)
                                }
                                DispatchQueue.main.asyncAfter(deadline: .now() + 1.8) {
                                    withAnimation { copied = false }
                                }
                            } label: {
                                Label(copied ? "Copied" : "Copy", systemImage: copied ? "checkmark" : "doc.on.doc")
                                    .font(.system(size: 12.5, weight: .medium))
                                    .foregroundStyle(copied ? Color.green : .secondary)
                                    .padding(.horizontal, 14)
                                    .padding(.vertical, 7)
                                    .background(Color(UIColor.tertiarySystemFill))
                                    .clipShape(Capsule())
                            }
                            .buttonStyle(.plain)
                        }

                        Spacer()

                        // ShareLink
                        if let title = spark.title, let conn = spark.connection {
                            ShareLink(
                                item: "\(title)\n\n\(conn)\n\n— Jimmy, my second brain",
                                subject: Text("Connection from Jimmy"),
                                message: Text(title)
                            ) {
                                Image(systemName: "square.and.arrow.up")
                                    .font(.system(size: 14))
                                    .foregroundStyle(.secondary)
                                    .padding(8)
                                    .background(Color(UIColor.tertiarySystemFill))
                                    .clipShape(RoundedRectangle(cornerRadius: 8))
                            }
                            .buttonStyle(.plain)
                        }
                    }
                }
                .padding(16)
                .transition(.opacity.combined(with: .move(edge: .top)))
            }
        }
        .background(
            ZStack {
                Color(hex: "faf9f7")
                LinearGradient(
                    colors: [gradientColors[0].opacity(0.04), Color.clear],
                    startPoint: .topLeading,
                    endPoint: .bottomTrailing
                )
            }
        )
        .clipShape(RoundedRectangle(cornerRadius: 16))
        .overlay(
            RoundedRectangle(cornerRadius: 16)
                .stroke(
                    LinearGradient(
                        colors: [gradientColors[0].opacity(0.25), Color(UIColor.separator).opacity(0.2)],
                        startPoint: .topLeading,
                        endPoint: .bottomTrailing
                    ),
                    lineWidth: 0.8
                )
        )
        .shadow(color: gradientColors[0].opacity(0.08), radius: 16, x: 0, y: 6)
        .shadow(color: .black.opacity(0.04), radius: 4, x: 0, y: 2)
        .opacity(appeared ? 1 : 0)
        .offset(y: appeared ? 0 : 16)
        .animation(.spring(response: 0.4, dampingFraction: 0.85).delay(Double(index) * 0.06), value: appeared)
        .onAppear { appeared = true }
    }

    private func saveSparkToNotes() async {
        guard !isSaving else { return }
        isSaving = true
        let title = spark.title ?? "Connection"
        let conn = spark.connection ?? ""
        let whyText = spark.why_it_matters.map { "\n\nWhy it matters: \($0)" } ?? ""
        let text = "# \(title)\n\n\(conn)\(whyText)\n\n— Saved from Jimmy Sparks"
        do {
            try await api.ingestNote(text)
            withAnimation(.spring(response: 0.3, dampingFraction: 0.7)) { saveSuccess = true }
            if settings.hapticEnabled {
                UINotificationFeedbackGenerator().notificationOccurred(.success)
            }
        } catch {
            if settings.hapticEnabled {
                UINotificationFeedbackGenerator().notificationOccurred(.error)
            }
        }
        isSaving = false
    }

    private func shareSparkCard() {
        let text = "Connection: \(spark.title ?? "")\n\n\(spark.connection ?? "")\n\nDiscovered by Jimmy — your second brain"
        let av = UIActivityViewController(activityItems: [text], applicationActivities: nil)
        if let scene = UIApplication.shared.connectedScenes.first(where: { $0.activationState == .foregroundActive }) as? UIWindowScene,
           let root = scene.windows.first(where: { $0.isKeyWindow })?.rootViewController {
            root.present(av, animated: true)
        }
    }
}

// MARK: - CrossDomainCard

struct CrossDomainCard: View {
    let analogy: Analogy
    let index: Int
    @EnvironmentObject var settings: AppSettings
    @EnvironmentObject var api: APIClient
    @State private var appeared = false
    @State private var isSaving = false
    @State private var saveSuccess = false

    private static let palette: [Color] = [
        Color(hex: "#0071e3"), .purple, .teal, .orange, Color(hex: "#ff375f"), .green
    ]

    private var colorA: Color { Self.palette[index % Self.palette.count] }
    private var colorB: Color { Self.palette[(index + 2) % Self.palette.count] }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            // Gradient top bar
            LinearGradient(colors: [colorA, colorB], startPoint: .leading, endPoint: .trailing)
                .frame(maxWidth: .infinity).frame(height: 4)

            VStack(alignment: .leading, spacing: 14) {
                // Header row
                HStack(spacing: 6) {
                    Image(systemName: "arrow.left.arrow.right.circle.fill")
                        .font(.system(size: 12))
                        .foregroundStyle(colorA)
                    Text("CROSS-DOMAIN CONNECTION")
                        .font(.system(size: 10, weight: .bold))
                        .foregroundStyle(colorA)
                        .tracking(0.6)
                    Spacer()
                    // Bookmark
                    Button {
                        Task { await saveConnection() }
                    } label: {
                        Group {
                            if isSaving {
                                ProgressView().scaleEffect(0.6)
                            } else {
                                Image(systemName: saveSuccess ? "bookmark.fill" : "bookmark")
                                    .font(.system(size: 12))
                                    .foregroundStyle(saveSuccess ? colorA : .secondary)
                            }
                        }
                        .frame(width: 26, height: 26)
                        .background(Color(UIColor.tertiarySystemFill))
                        .clipShape(RoundedRectangle(cornerRadius: 6))
                    }
                    .buttonStyle(.plain)
                }

                // "X connects to Y" format
                HStack(spacing: 10) {
                    domainBox(domain: analogy.domain_a ?? "Domain A", concept: analogy.concept_a ?? "", color: colorA)

                    VStack(spacing: 2) {
                        Text("connects")
                            .font(.system(size: 9, weight: .medium))
                            .foregroundStyle(.tertiary)
                        Text("to")
                            .font(.system(size: 9, weight: .medium))
                            .foregroundStyle(.tertiary)
                    }

                    domainBox(domain: analogy.domain_b ?? "Domain B", concept: analogy.concept_b ?? "", color: colorB)
                }

                // "because..." explanation
                if let a = analogy.analogy, !a.isEmpty {
                    HStack(alignment: .top, spacing: 8) {
                        Text("because")
                            .font(.system(size: 12, weight: .semibold))
                            .foregroundStyle(.tertiary)
                            .padding(.top, 1)
                        Text(a)
                            .font(.system(size: 14))
                            .foregroundStyle(.primary)
                            .lineSpacing(2.5)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                    .padding(12)
                    .background(
                        LinearGradient(
                            colors: [colorA.opacity(0.06), colorB.opacity(0.04)],
                            startPoint: .topLeading,
                            endPoint: .bottomTrailing
                        )
                    )
                    .clipShape(RoundedRectangle(cornerRadius: 10))
                }

                // Deeper insight
                if let insight = analogy.deeper_insight, !insight.isEmpty {
                    HStack(alignment: .top, spacing: 8) {
                        LinearGradient(colors: [colorA, colorB], startPoint: .top, endPoint: .bottom)
                            .frame(width: 3)
                            .clipShape(Capsule())
                        Text(insight)
                            .font(.system(size: 13).italic())
                            .foregroundStyle(.secondary)
                            .lineSpacing(2)
                    }
                    .frame(maxHeight: 70)
                }

                // Action row
                HStack(spacing: 8) {
                    Button {
                        let q = "How are \(analogy.domain_a ?? "Domain A") and \(analogy.domain_b ?? "Domain B") connected? Specifically: \(analogy.concept_a ?? "") and \(analogy.concept_b ?? "")."
                        settings.pendingAskQuery = q
                        if settings.hapticEnabled {
                            UIImpactFeedbackGenerator(style: .medium).impactOccurred()
                        }
                    } label: {
                        Text("Explore →")
                            .font(.system(size: 13, weight: .semibold))
                            .foregroundStyle(.white)
                            .padding(.horizontal, 16)
                            .padding(.vertical, 9)
                            .background(
                                LinearGradient(colors: [colorA, colorB], startPoint: .leading, endPoint: .trailing)
                            )
                            .clipShape(Capsule())
                    }
                    .buttonStyle(.plain)

                    Spacer()

                    if let conceptA = analogy.concept_a, let conceptB = analogy.concept_b {
                        ShareLink(
                            item: "\(conceptA) connects to \(conceptB)\n\n\(analogy.analogy ?? "")\n\n— Jimmy cross-domain connection",
                            subject: Text("Cross-Domain Connection"),
                            message: Text("\(conceptA) connects to \(conceptB)")
                        ) {
                            Image(systemName: "square.and.arrow.up")
                                .font(.system(size: 13))
                                .foregroundStyle(.secondary)
                                .padding(8)
                                .background(Color(UIColor.tertiarySystemFill))
                                .clipShape(RoundedRectangle(cornerRadius: 8))
                        }
                        .buttonStyle(.plain)
                    }
                }
            }
            .padding(16)
        }
        .background(
            ZStack {
                Color(hex: "faf9f7")
                LinearGradient(
                    colors: [colorA.opacity(0.03), colorB.opacity(0.02)],
                    startPoint: .topLeading,
                    endPoint: .bottomTrailing
                )
            }
        )
        .clipShape(RoundedRectangle(cornerRadius: 16))
        .overlay(
            RoundedRectangle(cornerRadius: 16)
                .stroke(
                    LinearGradient(
                        colors: [colorA.opacity(0.3), colorB.opacity(0.2)],
                        startPoint: .topLeading,
                        endPoint: .bottomTrailing
                    ),
                    lineWidth: 0.8
                )
        )
        .shadow(color: colorA.opacity(0.1), radius: 16, x: 0, y: 6)
        .shadow(color: .black.opacity(0.04), radius: 4, x: 0, y: 2)
        .opacity(appeared ? 1 : 0)
        .offset(y: appeared ? 0 : 16)
        .animation(.spring(response: 0.4, dampingFraction: 0.85).delay(Double(index) * 0.07), value: appeared)
        .onAppear { appeared = true }
    }

    @ViewBuilder
    private func domainBox(domain: String, concept: String, color: Color) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(domain.uppercased())
                .font(.system(size: 9, weight: .bold))
                .foregroundStyle(color)
                .tracking(0.5)
                .lineLimit(1)
            Text(concept)
                .font(.system(size: 13, weight: .semibold))
                .foregroundStyle(.primary)
                .lineLimit(2)
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 8)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(color.opacity(0.1))
        .clipShape(RoundedRectangle(cornerRadius: 10))
        .overlay(RoundedRectangle(cornerRadius: 10).stroke(color.opacity(0.25), lineWidth: 1))
    }

    private func saveConnection() async {
        guard !isSaving else { return }
        isSaving = true
        let a = analogy.concept_a ?? ""
        let b = analogy.concept_b ?? ""
        let domA = analogy.domain_a ?? ""
        let domB = analogy.domain_b ?? ""
        let explanation = analogy.analogy ?? ""
        let insightText = analogy.deeper_insight.map { "\n\nDeeper insight: \($0)" } ?? ""
        let text = "# \(a) connects to \(b)\n\n\(domA) ↔ \(domB)\n\nbecause \(explanation)\(insightText)\n\n— Saved from Jimmy Cross-Domain Connections"
        do {
            try await api.ingestNote(text)
            withAnimation(.spring(response: 0.3, dampingFraction: 0.7)) { saveSuccess = true }
            if settings.hapticEnabled {
                UINotificationFeedbackGenerator().notificationOccurred(.success)
            }
        } catch {
            if settings.hapticEnabled {
                UINotificationFeedbackGenerator().notificationOccurred(.error)
            }
        }
        isSaving = false
    }
}

// MARK: - Today Analogy Banner (from /today endpoint)

struct TodayAnalogyBanner: View {
    let analogy: Analogy
    @EnvironmentObject var settings: AppSettings
    @State private var appeared = false

    private let colorA = Color(hex: "#ff9500")
    private let colorB = Color(hex: "#af52de")

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            // Header with lightbulb icon
            HStack(spacing: 6) {
                Image(systemName: "lightbulb.fill")
                    .font(.system(size: 13, weight: .bold))
                    .foregroundStyle(colorA)
                Text("TODAY'S ANALOGY")
                    .font(.system(size: 10, weight: .bold))
                    .foregroundStyle(colorA)
                    .tracking(1.0)
                Spacer()
            }

            // Two concept boxes
            HStack(spacing: 10) {
                conceptBox(domain: analogy.domain_a ?? "", concept: analogy.concept_a ?? "", color: colorA)

                Image(systemName: "equal.circle.fill")
                    .font(.system(size: 20))
                    .foregroundStyle(.tertiary)

                conceptBox(domain: analogy.domain_b ?? "", concept: analogy.concept_b ?? "", color: colorB)
            }

            // Analogy text with lightbulb
            if let a = analogy.analogy, !a.isEmpty {
                HStack(alignment: .top, spacing: 10) {
                    Image(systemName: "lightbulb")
                        .font(.system(size: 15))
                        .foregroundStyle(colorA)
                        .padding(.top, 1)
                    Text(a)
                        .font(.system(size: 14).italic())
                        .foregroundStyle(.primary)
                        .lineSpacing(3)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                .padding(12)
                .background(
                    LinearGradient(
                        colors: [colorA.opacity(0.08), colorB.opacity(0.05)],
                        startPoint: .topLeading,
                        endPoint: .bottomTrailing
                    )
                )
                .clipShape(RoundedRectangle(cornerRadius: 10))
            }

            // Deeper insight
            if let insight = analogy.deeper_insight, !insight.isEmpty {
                HStack(alignment: .top, spacing: 8) {
                    LinearGradient(colors: [colorA, colorB], startPoint: .top, endPoint: .bottom)
                        .frame(width: 3)
                        .clipShape(Capsule())
                    Text(insight)
                        .font(.system(size: 13).italic())
                        .foregroundStyle(.secondary)
                        .lineSpacing(2)
                }
            }

            // Explore button
            Button {
                let q = "What is the analogy between \(analogy.concept_a ?? "") and \(analogy.concept_b ?? "")? How does understanding one help you understand the other?"
                settings.pendingAskQuery = q
                if settings.hapticEnabled {
                    UIImpactFeedbackGenerator(style: .medium).impactOccurred()
                }
            } label: {
                HStack(spacing: 5) {
                    Image(systemName: "lightbulb.fill")
                        .font(.system(size: 12))
                    Text("Explore this analogy")
                        .font(.system(size: 12.5, weight: .semibold))
                    Text("→")
                        .font(.system(size: 12.5))
                }
                .foregroundStyle(colorA)
                .padding(.horizontal, 14).padding(.vertical, 8)
                .background(colorA.opacity(0.1))
                .clipShape(Capsule())
            }
            .buttonStyle(.plain)
        }
        .padding(16)
        .background(
            LinearGradient(
                colors: [colorA.opacity(0.08), colorB.opacity(0.05), Color.clear],
                startPoint: .topLeading, endPoint: .bottomTrailing
            )
        )
        .clipShape(RoundedRectangle(cornerRadius: 16))
        .overlay(
            RoundedRectangle(cornerRadius: 16)
                .stroke(
                    LinearGradient(
                        colors: [colorA.opacity(0.3), colorB.opacity(0.2)],
                        startPoint: .topLeading, endPoint: .bottomTrailing
                    ),
                    lineWidth: 1
                )
        )
        .shadow(color: colorA.opacity(0.1), radius: 12, x: 0, y: 4)
        .opacity(appeared ? 1 : 0)
        .offset(y: appeared ? 0 : 10)
        .animation(.spring(response: 0.4, dampingFraction: 0.85), value: appeared)
        .onAppear { appeared = true }
    }

    @ViewBuilder
    private func conceptBox(domain: String, concept: String, color: Color) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(domain.uppercased())
                .font(.system(size: 8, weight: .bold))
                .foregroundStyle(color)
                .tracking(0.6)
            Text(concept)
                .font(.system(size: 13, weight: .semibold))
                .foregroundStyle(.primary)
                .lineLimit(2)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 10).padding(.vertical, 8)
        .background(color.opacity(0.1))
        .clipShape(RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(color.opacity(0.2), lineWidth: 1))
    }
}

// MARK: - Today's Connection Banner

struct TodaysConnectionBanner: View {
    let analogy: Analogy
    @EnvironmentObject var settings: AppSettings
    @State private var appeared = false

    private let colorA = Color(hex: "#0071e3")
    private let colorB = Color.purple

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 6) {
                Image(systemName: "sparkles")
                    .font(.system(size: 10, weight: .bold))
                    .foregroundStyle(colorA)
                Text("TODAY'S CONNECTION")
                    .font(.system(size: 10, weight: .bold))
                    .foregroundStyle(colorA)
                    .tracking(0.8)
            }

            HStack(spacing: 8) {
                VStack(alignment: .leading, spacing: 2) {
                    Text((analogy.domain_a ?? "").uppercased())
                        .font(.system(size: 8, weight: .bold))
                        .foregroundStyle(colorA)
                        .tracking(0.6)
                    Text(analogy.concept_a ?? "")
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundStyle(.primary)
                        .lineLimit(2)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, 10).padding(.vertical, 8)
                .background(colorA.opacity(0.1))
                .clipShape(RoundedRectangle(cornerRadius: 8))
                .overlay(RoundedRectangle(cornerRadius: 8).stroke(colorA.opacity(0.2), lineWidth: 1))

                Text("↔")
                    .font(.system(size: 16, weight: .medium))
                    .foregroundStyle(.tertiary)

                VStack(alignment: .leading, spacing: 2) {
                    Text((analogy.domain_b ?? "").uppercased())
                        .font(.system(size: 8, weight: .bold))
                        .foregroundStyle(colorB)
                        .tracking(0.6)
                    Text(analogy.concept_b ?? "")
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundStyle(.primary)
                        .lineLimit(2)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, 10).padding(.vertical, 8)
                .background(colorB.opacity(0.1))
                .clipShape(RoundedRectangle(cornerRadius: 8))
                .overlay(RoundedRectangle(cornerRadius: 8).stroke(colorB.opacity(0.2), lineWidth: 1))
            }

            if let a = analogy.analogy, !a.isEmpty {
                HStack(alignment: .top, spacing: 8) {
                    Image(systemName: "lightbulb.fill")
                        .font(.system(size: 12))
                        .foregroundStyle(colorA.opacity(0.7))
                    Text(a.count > 140 ? String(a.prefix(140)) + "…" : a)
                        .font(.system(size: 13).italic())
                        .foregroundStyle(.secondary)
                        .lineSpacing(2)
                }
            }

            Button {
                let q = "How are \(analogy.domain_a ?? "Domain A") and \(analogy.domain_b ?? "Domain B") connected? Specifically: \(analogy.concept_a ?? "") and \(analogy.concept_b ?? "")."
                settings.pendingAskQuery = q
                if settings.hapticEnabled {
                    UIImpactFeedbackGenerator(style: .medium).impactOccurred()
                }
            } label: {
                HStack(spacing: 5) {
                    Text("Explore this connection")
                        .font(.system(size: 12.5, weight: .semibold))
                    Text("→")
                        .font(.system(size: 12.5))
                }
                .foregroundStyle(colorA)
                .padding(.horizontal, 14).padding(.vertical, 7)
                .background(colorA.opacity(0.08))
                .clipShape(Capsule())
            }
            .buttonStyle(.plain)
        }
        .padding(14)
        .background(
            LinearGradient(
                colors: [colorA.opacity(0.06), colorB.opacity(0.04)],
                startPoint: .topLeading, endPoint: .bottomTrailing
            )
        )
        .clipShape(RoundedRectangle(cornerRadius: 14))
        .overlay(RoundedRectangle(cornerRadius: 14).stroke(colorA.opacity(0.15), lineWidth: 1))
        .opacity(appeared ? 1 : 0)
        .offset(y: appeared ? 0 : 10)
        .animation(.spring(response: 0.4, dampingFraction: 0.85), value: appeared)
        .onAppear { appeared = true }
    }
}

// MARK: - Analogy Card

struct AnalogyCard: View {
    let analogy: Analogy
    let index: Int
    @EnvironmentObject var settings: AppSettings
    @State private var appeared = false

    private static let palette: [Color] = [.purple, Color(hex: "#0071e3"), .orange, .green, Color(hex: "#ff375f"), .teal]

    private var colorA: Color { Self.palette[index % Self.palette.count] }
    private var colorB: Color { Self.palette[(index + 1) % Self.palette.count] }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            // Gradient accent bar
            LinearGradient(colors: [colorA, colorB], startPoint: .leading, endPoint: .trailing)
                .frame(maxWidth: .infinity).frame(height: 4)

            VStack(alignment: .leading, spacing: 12) {
                // Two domain pills side by side
                HStack(spacing: 10) {
                    domainPill(domain: analogy.domain_a ?? "", concept: analogy.concept_a ?? "", color: colorA)
                    Text("≈")
                        .font(.system(size: 22, weight: .bold))
                        .foregroundStyle(colorA.opacity(0.6))
                    domainPill(domain: analogy.domain_b ?? "", concept: analogy.concept_b ?? "", color: colorB)
                }

                // Analogy sentence with lightbulb icon
                if let a = analogy.analogy, !a.isEmpty {
                    HStack(alignment: .top, spacing: 8) {
                        Image(systemName: "lightbulb.fill")
                            .font(.system(size: 13))
                            .foregroundStyle(colorA.opacity(0.8))
                            .padding(.top, 1)
                        Text(a)
                            .font(.system(size: 14))
                            .foregroundStyle(.primary)
                            .lineSpacing(3)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                }

                // Deeper insight
                if let insight = analogy.deeper_insight, !insight.isEmpty {
                    HStack(alignment: .top, spacing: 8) {
                        LinearGradient(colors: [colorA, colorB], startPoint: .top, endPoint: .bottom)
                            .frame(width: 3)
                            .clipShape(Capsule())
                        Text(insight)
                            .font(.system(size: 13).italic())
                            .foregroundStyle(.secondary)
                            .lineSpacing(2)
                    }
                    .frame(maxHeight: 60)
                }

                // Ask button with gradient
                Button {
                    let q = "What is the analogy between \(analogy.concept_a ?? "") and \(analogy.concept_b ?? "")? How does understanding one help you understand the other?"
                    settings.pendingAskQuery = q
                    if settings.hapticEnabled {
                        UIImpactFeedbackGenerator(style: .medium).impactOccurred()
                    }
                } label: {
                    Text("Explore this analogy →")
                        .font(.system(size: 13.5, weight: .semibold))
                        .foregroundStyle(.white)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 10)
                        .background(
                            LinearGradient(colors: [colorA, colorB], startPoint: .leading, endPoint: .trailing)
                        )
                        .clipShape(RoundedRectangle(cornerRadius: 10))
                }
                .buttonStyle(.plain)
            }
            .padding(16)
        }
        .background(
            ZStack {
                Color(hex: "faf9f7")
                LinearGradient(
                    colors: [colorA.opacity(0.03), colorB.opacity(0.02)],
                    startPoint: .topLeading,
                    endPoint: .bottomTrailing
                )
            }
        )
        .clipShape(RoundedRectangle(cornerRadius: 16))
        .overlay(
            RoundedRectangle(cornerRadius: 16)
                .stroke(
                    LinearGradient(
                        colors: [colorA.opacity(0.25), Color(UIColor.separator).opacity(0.2)],
                        startPoint: .topLeading,
                        endPoint: .bottomTrailing
                    ),
                    lineWidth: 0.8
                )
        )
        .shadow(color: colorA.opacity(0.08), radius: 12, x: 0, y: 4)
        .opacity(appeared ? 1 : 0)
        .offset(y: appeared ? 0 : 16)
        .animation(.spring(response: 0.4, dampingFraction: 0.85).delay(Double(index) * 0.07), value: appeared)
        .onAppear { appeared = true }
    }

    @ViewBuilder
    private func domainPill(domain: String, concept: String, color: Color) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(domain.uppercased())
                .font(.system(size: 9, weight: .bold))
                .foregroundStyle(color)
                .tracking(0.5)
                .lineLimit(1)
            Text(concept)
                .font(.system(size: 13, weight: .semibold))
                .foregroundStyle(.primary)
                .lineLimit(2)
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 8)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(color.opacity(0.1))
        .clipShape(RoundedRectangle(cornerRadius: 10))
        .overlay(RoundedRectangle(cornerRadius: 10).stroke(color.opacity(0.25), lineWidth: 1))
    }
}

struct EmptyAnalogiesView: View {
    var body: some View {
        VStack(spacing: 12) {
            Image(systemName: "arrow.left.arrow.right")
                .font(.system(size: 40, weight: .light))
                .foregroundStyle(.tertiary)
            Text("Cross-domain analogies appear as you build your knowledge base across different subjects.")
                .font(.system(size: 15))
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 32)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding(.top, 80)
    }
}

struct SparkMetaRow: View {
    let label: String
    let value: String

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(label.uppercased())
                .font(.system(size: 10, weight: .semibold))
                .foregroundStyle(.tertiary)
                .tracking(0.5)
            Text(value)
                .font(.system(size: 13.5))
                .foregroundStyle(.secondary)
                .lineSpacing(3)
                .lineLimit(3)
        }
    }
}

// MARK: - Spark Skeleton

struct SparkSkeletonView: View {
    var body: some View {
        ScrollView {
            LazyVStack(spacing: 10) {
                ForEach(0..<5, id: \.self) { i in
                    VStack(alignment: .leading, spacing: 8) {
                        HStack(spacing: 10) {
                            RoundedRectangle(cornerRadius: 6)
                                .fill(Color(UIColor.tertiarySystemFill))
                                .frame(width: 22, height: 22)
                            RoundedRectangle(cornerRadius: 4)
                                .fill(Color(UIColor.tertiarySystemFill))
                                .frame(height: 14)
                        }
                        RoundedRectangle(cornerRadius: 4)
                            .fill(Color(UIColor.tertiarySystemFill))
                            .frame(maxWidth: .infinity)
                            .frame(height: 12)
                        RoundedRectangle(cornerRadius: 4)
                            .fill(Color(UIColor.tertiarySystemFill))
                            .frame(maxWidth: 200)
                            .frame(height: 12)
                    }
                    .padding(16)
                    .background(Color(hex: "faf9f7"))
                    .clipShape(RoundedRectangle(cornerRadius: 14))
                    .overlay(ShimmerView().clipShape(RoundedRectangle(cornerRadius: 14)))
                }
            }
            .padding(16)
        }
    }
}

struct SparksErrorView: View {
    let onRetry: () -> Void
    @State private var appeared = false

    var body: some View {
        VStack(spacing: 16) {
            Image(systemName: "exclamationmark.circle")
                .font(.system(size: 48, weight: .light))
                .foregroundStyle(.tertiary)
                .frame(width: 48, height: 48)
                .scaleEffect(appeared ? 1.0 : 0.6)
                .opacity(appeared ? 1.0 : 0)
                .animation(.spring(response: 0.5, dampingFraction: 0.7), value: appeared)

            VStack(spacing: 5) {
                Text("Couldn't load connections")
                    .font(.system(size: 17, weight: .semibold))
                    .multilineTextAlignment(.center)
                    .opacity(appeared ? 1 : 0)
                    .animation(.easeInOut(duration: 0.3).delay(0.15), value: appeared)
                Text("Check that your Jimmy server is running")
                    .font(.system(size: 15))
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                    .opacity(appeared ? 1 : 0)
                    .animation(.easeInOut(duration: 0.3).delay(0.2), value: appeared)
            }

            Button(action: onRetry) {
                Label("Try Again", systemImage: "arrow.clockwise")
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundStyle(.white)
                    .padding(.horizontal, 20)
                    .padding(.vertical, 10)
                    .background(Color(hex: "#0071e3"))
                    .clipShape(Capsule())
            }
            .buttonStyle(.plain)
            .opacity(appeared ? 1 : 0)
            .animation(.easeInOut(duration: 0.3).delay(0.3), value: appeared)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding(40)
        .onAppear { appeared = true }
    }
}

struct EmptySparksView: View {
    let onRetry: () -> Void
    @State private var appeared = false

    var body: some View {
        VStack(spacing: 16) {
            Image(systemName: "bolt")
                .font(.system(size: 48, weight: .light))
                .foregroundStyle(.tertiary)
                .frame(width: 48, height: 48)
                .scaleEffect(appeared ? 1.0 : 0.6)
                .opacity(appeared ? 1.0 : 0)
                .animation(.spring(response: 0.5, dampingFraction: 0.7), value: appeared)

            VStack(spacing: 5) {
                Text("New connections appear as you use Jimmy")
                    .font(.system(size: 17, weight: .semibold))
                    .multilineTextAlignment(.center)
                    .opacity(appeared ? 1 : 0)
                    .animation(.easeInOut(duration: 0.3).delay(0.15), value: appeared)
                Text("Check back tomorrow")
                    .font(.system(size: 15))
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                    .opacity(appeared ? 1 : 0)
                    .animation(.easeInOut(duration: 0.3).delay(0.2), value: appeared)
            }

            Button(action: onRetry) {
                Label("Refresh", systemImage: "arrow.clockwise")
                    .font(.system(size: 14, weight: .medium))
                    .foregroundStyle(.secondary)
                    .padding(.horizontal, 18)
                    .padding(.vertical, 9)
                    .background(Color(hex: "faf9f7"))
                    .clipShape(Capsule())
            }
            .buttonStyle(.plain)
            .opacity(appeared ? 1 : 0)
            .animation(.easeInOut(duration: 0.3).delay(0.3), value: appeared)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding(40)
        .onAppear { appeared = true }
    }
}
