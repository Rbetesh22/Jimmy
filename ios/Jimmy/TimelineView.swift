import SwiftUI

// MARK: - Filter

enum TimelineFilter: String, CaseIterable {
    case all    = "All"
    case notes  = "Notes"
    case canvas = "Canvas"
    case books  = "Books"
    case video  = "Video"

    var systemImage: String {
        switch self {
        case .all:    return "square.grid.2x2"
        case .notes:  return "note.text"
        case .canvas: return "graduationcap"
        case .books:  return "book.closed"
        case .video:  return "play.rectangle"
        }
    }

    func matches(_ event: TimelineEvent) -> Bool {
        switch self {
        case .all:    return true
        case .notes:  return (event.type ?? event.source) == "note"
        case .canvas: return event.source.lowercased() == "canvas" || (event.type ?? "") == "class"
        case .books:  return (event.type ?? event.source) == "book"
        case .video:  return event.source.lowercased() == "youtube" || (event.type ?? "") == "video"
        }
    }
}

// MARK: - Source helpers

private func sourceIcon(_ source: String) -> String {
    switch source.lowercased() {
    case "canvas":      return "graduationcap.fill"
    case "youtube":     return "play.rectangle.fill"
    case "notion":      return "note.text"
    case "apple_notes": return "note.text"
    case "github":      return "chevron.left.forwardslash.chevron.right"
    case "gmail":       return "envelope.fill"
    case "goodnotes":   return "pencil.and.list.clipboard"
    case "book", "library": return "book.closed.fill"
    case "pocket":      return "bookmark.fill"
    default:            return "doc.text.fill"
    }
}

private func sourceColor(_ source: String) -> Color {
    switch source.lowercased() {
    case "canvas":      return .blue
    case "youtube":     return .red
    case "notion":      return .purple
    case "apple_notes": return Color(hex: "#f5a623")
    case "github":      return .orange
    case "gmail":       return .green
    case "goodnotes":   return Color(hex: "#ff6b6b")
    case "book", "library": return Color(hex: "#6c5ce7")
    case "pocket":      return .teal
    default:            return .secondary
    }
}

// MARK: - Date grouping

private enum DateGroup: Hashable {
    case today
    case yesterday
    case thisWeek
    case earlier(String)  // "Week of Mar 3"

    var title: String {
        switch self {
        case .today:           return "Today"
        case .yesterday:       return "Yesterday"
        case .thisWeek:        return "This Week"
        case .earlier(let s):  return s
        }
    }
}

private func groupLabel(for dateStr: String) -> DateGroup {
    let formatter = DateFormatter()
    formatter.dateFormat = "yyyy-MM-dd"
    guard let date = formatter.date(from: dateStr) else { return .earlier(dateStr) }
    let cal = Calendar.current
    if cal.isDateInToday(date)     { return .today }
    if cal.isDateInYesterday(date) { return .yesterday }
    if let weekAgo = cal.date(byAdding: .day, value: -6, to: Date()), date >= weekAgo {
        return .thisWeek
    }
    let weekFormatter = DateFormatter()
    weekFormatter.dateFormat = "MMM d"
    let monday = cal.date(from: cal.dateComponents([.yearForWeekOfYear, .weekOfYear], from: date)) ?? date
    return .earlier("Week of \(weekFormatter.string(from: monday))")
}

// MARK: - Main View

struct TimelineView: View {
    @EnvironmentObject var api: APIClient
    @EnvironmentObject var settings: AppSettings

    @State private var data: TimelineResponse? = nil
    @State private var isLoading = true
    @State private var errorMessage: String? = nil
    @State private var activeFilter: TimelineFilter = .all
    @State private var showRecap = false
    @State private var recapData: RecapResponse? = nil
    @State private var recapLoading = false
    @State private var recapError: String? = nil

    var body: some View {
        Group {
            if isLoading {
                TimelineSkeletonView()
            } else if let err = errorMessage {
                TimelineErrorView(message: err, onRetry: { Task { await load() } })
            } else if let data = data {
                TimelineContentView(
                    data: data,
                    activeFilter: $activeFilter,
                    showRecap: $showRecap
                )
            }
        }
        .navigationTitle("Learning History")
        .navigationBarTitleDisplayMode(.large)
        .toolbar {
            if let data = data {
                ToolbarItem(placement: .topBarTrailing) {
                    ShareLink(item: shareText(from: data)) {
                        Image(systemName: "square.and.arrow.up")
                            .font(.system(size: 15))
                            .foregroundStyle(Color(hex: "#0071e3"))
                    }
                }
            }
        }
        .task { await load() }
        .refreshable { await load(force: true) }
        .background(Color(UIColor.systemGroupedBackground))
        .sheet(isPresented: $showRecap) {
            RecapSheet(
                recapData: $recapData,
                isLoading: $recapLoading,
                errorMessage: $recapError
            )
            .onAppear { Task { await loadRecap() } }
        }
    }

    private func shareText(from data: TimelineResponse) -> String {
        let streak = data.streak ?? 0
        let streakLine = streak > 0 ? "\n• \(streak)-day learning streak" : ""
        return """
        My Jimmy learning history:
        • \(data.total) items in my second brain
        • \(data.period_weeks) weeks of active learning\(streakLine)

        Jimmy — your AI second brain
        """
    }

    private func load(force: Bool = false) async {
        isLoading = data == nil
        errorMessage = nil
        do {
            data = try await api.timeline(weeks: 16)
        } catch {
            errorMessage = error.localizedDescription
        }
        isLoading = false
    }

    private func loadRecap() async {
        guard recapData == nil else { return }
        recapLoading = true
        recapError = nil
        do {
            recapData = try await api.recap()
        } catch {
            recapError = error.localizedDescription
        }
        recapLoading = false
    }
}

// MARK: - Content

private struct TimelineContentView: View {
    let data: TimelineResponse
    @Binding var activeFilter: TimelineFilter
    @Binding var showRecap: Bool

    private var streak: Int { data.streak ?? 0 }

    // Group filtered events by date bucket
    private var groupedEvents: [(DateGroup, [TimelineEvent])] {
        let events = (data.events ?? []).filter { activeFilter.matches($0) }
        var order: [DateGroup] = []
        var groups: [DateGroup: [TimelineEvent]] = [:]
        for event in events {
            let g = groupLabel(for: event.date)
            if groups[g] == nil {
                order.append(g)
                groups[g] = []
            }
            groups[g]!.append(event)
        }
        return order.map { ($0, groups[$0] ?? []) }
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {

                // ── Streak + Recap button ────────────────────────────────────
                HStack(alignment: .center) {
                    if streak > 0 {
                        StreakBadge(streak: streak)
                    } else {
                        VStack(alignment: .leading, spacing: 2) {
                            Text("\(data.total)")
                                .font(.system(size: 34, weight: .bold, design: .rounded))
                                .foregroundStyle(Color(hex: "#0071e3"))
                            Text("items in your second brain")
                                .font(.system(size: 13))
                                .foregroundStyle(.secondary)
                        }
                    }
                    Spacer()
                    Button {
                        showRecap = true
                    } label: {
                        HStack(spacing: 6) {
                            Image(systemName: "sparkles")
                                .font(.system(size: 13, weight: .semibold))
                            Text("Weekly Recap")
                                .font(.system(size: 14, weight: .semibold))
                        }
                        .foregroundStyle(.white)
                        .padding(.horizontal, 14)
                        .padding(.vertical, 8)
                        .background(Color(hex: "#0071e3"))
                        .clipShape(Capsule())
                    }
                }
                .padding(.horizontal, 20)
                .padding(.top, 16)
                .padding(.bottom, 20)

                // ── Heatmap ──────────────────────────────────────────────────
                VStack(alignment: .leading, spacing: 8) {
                    Text("Activity")
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundStyle(.tertiary)
                        .textCase(.uppercase)
                        .tracking(0.7)
                        .padding(.horizontal, 20)

                    HeatmapView(days: data.heatmap)
                        .padding(.horizontal, 16)

                    HeatmapView(days: data.heatmap).legend
                        .padding(.horizontal, 20)
                }
                .padding(.bottom, 24)

                // ── Filter chips ─────────────────────────────────────────────
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) {
                        ForEach(TimelineFilter.allCases, id: \.self) { filter in
                            FilterChip(
                                filter: filter,
                                isActive: activeFilter == filter,
                                action: { activeFilter = filter }
                            )
                        }
                    }
                    .padding(.horizontal, 20)
                    .padding(.vertical, 2)
                }
                .padding(.bottom, 20)

                // ── Events timeline or week cards ─────────────────────────────
                if let events = data.events, !events.isEmpty {
                    if groupedEvents.isEmpty {
                        Text("No items match this filter.")
                            .font(.system(size: 14))
                            .foregroundStyle(.secondary)
                            .padding(.horizontal, 20)
                            .padding(.vertical, 32)
                    } else {
                        ForEach(groupedEvents, id: \.0) { group, events in
                            EventGroupSection(label: group.title, events: events)
                        }
                    }
                } else {
                    // Fallback: week cards
                    VStack(spacing: 12) {
                        ForEach(data.weeks, id: \.week_start) { week in
                            WeekCard(week: week)
                                .padding(.horizontal, 16)
                        }
                    }
                    .padding(.bottom, 32)
                }

                Spacer(minLength: 48)
            }
        }
        .background(Color(UIColor.systemGroupedBackground))
    }
}

// MARK: - Streak Badge

private struct StreakBadge: View {
    let streak: Int

    var body: some View {
        HStack(spacing: 10) {
            ZStack {
                Circle()
                    .fill(Color(hex: "#ff9f0a").opacity(0.15))
                    .frame(width: 48, height: 48)
                Image(systemName: "flame.fill")
                    .font(.system(size: 22))
                    .foregroundStyle(Color(hex: "#ff9f0a"))
            }
            VStack(alignment: .leading, spacing: 2) {
                Text("\(streak)-day streak")
                    .font(.system(size: 17, weight: .bold, design: .rounded))
                    .foregroundStyle(Color(hex: "#ff9f0a"))
                Text("Keep it up!")
                    .font(.system(size: 12))
                    .foregroundStyle(.secondary)
            }
        }
    }
}

// MARK: - Filter Chip

private struct FilterChip: View {
    let filter: TimelineFilter
    let isActive: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 5) {
                Image(systemName: filter.systemImage)
                    .font(.system(size: 11, weight: .medium))
                Text(filter.rawValue)
                    .font(.system(size: 13, weight: .medium))
            }
            .foregroundStyle(isActive ? .white : Color(UIColor.label))
            .padding(.horizontal, 12)
            .padding(.vertical, 7)
            .background(
                isActive
                    ? Color(hex: "#0071e3")
                    : Color(UIColor.secondarySystemGroupedBackground)
            )
            .clipShape(Capsule())
        }
        .buttonStyle(.plain)
    }
}

// MARK: - Event Group Section

private struct EventGroupSection: View {
    let label: String
    let events: [TimelineEvent]

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text(label)
                .font(.system(size: 11, weight: .semibold))
                .foregroundStyle(.tertiary)
                .textCase(.uppercase)
                .tracking(0.7)
                .padding(.horizontal, 20)
                .padding(.bottom, 10)

            // Timeline: left line + event cards
            VStack(alignment: .leading, spacing: 0) {
                ForEach(Array(events.enumerated()), id: \.element.id) { idx, event in
                    TimelineEventRow(event: event, isLast: idx == events.count - 1)
                }
            }
            .padding(.leading, 20)
            .padding(.bottom, 20)
        }
    }
}

// MARK: - Timeline Event Row

private struct TimelineEventRow: View {
    let event: TimelineEvent
    let isLast: Bool

    @State private var appeared = false

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            // Timeline spine
            VStack(spacing: 0) {
                ZStack {
                    Circle()
                        .fill(sourceColor(event.source).opacity(0.15))
                        .frame(width: 32, height: 32)
                    Image(systemName: sourceIcon(event.source))
                        .font(.system(size: 13))
                        .foregroundStyle(sourceColor(event.source))
                }
                if !isLast {
                    Rectangle()
                        .fill(Color(UIColor.separator))
                        .frame(width: 1.5)
                        .frame(maxHeight: .infinity)
                        .padding(.top, 4)
                }
            }
            .frame(width: 32)

            // Card
            VStack(alignment: .leading, spacing: 4) {
                HStack(alignment: .firstTextBaseline) {
                    Text(event.title)
                        .font(.system(size: 14, weight: .semibold))
                        .foregroundStyle(Color(UIColor.label))
                        .lineLimit(2)
                    Spacer()
                    Text(event.source.replacingOccurrences(of: "_", with: " ").capitalized)
                        .font(.system(size: 10, weight: .medium))
                        .foregroundStyle(sourceColor(event.source))
                        .padding(.horizontal, 7)
                        .padding(.vertical, 3)
                        .background(sourceColor(event.source).opacity(0.12))
                        .clipShape(Capsule())
                }

                if let snippet = event.snippet, !snippet.isEmpty {
                    Text(snippet)
                        .font(.system(size: 12))
                        .foregroundStyle(.secondary)
                        .lineLimit(2)
                }
            }
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color(UIColor.secondarySystemGroupedBackground))
            .clipShape(RoundedRectangle(cornerRadius: 10))
            .padding(.trailing, 16)
            .padding(.bottom, isLast ? 0 : 10)
            .opacity(appeared ? 1 : 0)
            .offset(y: appeared ? 0 : 12)
            .animation(.spring(response: 0.4, dampingFraction: 0.85).delay(0.05), value: appeared)
            .onAppear { appeared = true }
        }
    }
}

// MARK: - Heatmap

private struct HeatmapView: View {
    let days: [HeatmapDay]

    private let cellSize: CGFloat = 11
    private let spacing: CGFloat = 3

    private var monthBoundaries: [(columnIndex: Int, name: String)] {
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy-MM-dd"
        let monthFormatter = DateFormatter()
        monthFormatter.dateFormat = "MMM"

        var result: [(columnIndex: Int, name: String)] = []
        var prevMonth: Int? = nil

        for (idx, day) in days.enumerated() {
            guard let date = formatter.date(from: day.date) else { continue }
            let month = Calendar.current.component(.month, from: date)
            let col = idx / 7
            if month != prevMonth {
                let name = monthFormatter.string(from: date)
                if result.last?.columnIndex != col {
                    result.append((columnIndex: col, name: name))
                }
                prevMonth = month
            }
        }
        return result
    }

    private var totalColumns: Int {
        (days.count + 6) / 7
    }

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            VStack(alignment: .leading, spacing: 4) {
                ZStack(alignment: .topLeading) {
                    Color.clear.frame(height: 14)
                    ForEach(monthBoundaries, id: \.columnIndex) { boundary in
                        Text(boundary.name)
                            .font(.system(size: 10, weight: .medium))
                            .foregroundStyle(.secondary)
                            .offset(x: CGFloat(boundary.columnIndex) * (cellSize + spacing))
                    }
                }
                .frame(width: CGFloat(totalColumns) * (cellSize + spacing) - spacing)

                LazyHGrid(
                    rows: Array(repeating: GridItem(.fixed(cellSize), spacing: spacing), count: 7),
                    spacing: spacing
                ) {
                    ForEach(days, id: \.date) { day in
                        RoundedRectangle(cornerRadius: 2)
                            .fill(
                                day.count == 0
                                    ? Color(UIColor.systemGray5)
                                    : Color(hex: "#0071e3").opacity(min(0.15 + Double(day.count) * 0.2, 1.0))
                            )
                            .frame(width: cellSize, height: cellSize)
                    }
                }
                .frame(height: 7 * cellSize + 6 * spacing)
            }
            .padding(.horizontal, 4)
        }
    }

    var legend: some View {
        HStack(spacing: 6) {
            Text("Less")
                .font(.system(size: 10))
                .foregroundStyle(.tertiary)
            ForEach([0.07, 0.22, 0.42, 0.65, 1.0], id: \.self) { opacity in
                RoundedRectangle(cornerRadius: 2)
                    .fill(Color(hex: "#0071e3").opacity(opacity))
                    .frame(width: 10, height: 10)
            }
            Text("More")
                .font(.system(size: 10))
                .foregroundStyle(.tertiary)
        }
    }
}

// MARK: - Week Card (fallback)

private struct WeekCard: View {
    let week: TimelineWeek

    private static let sourceColors: [String: Color] = [
        "canvas": .blue,
        "gmail": .green,
        "notion": .purple,
        "github": .orange,
        "youtube": .red,
        "note": .gray,
        "apple_notes": .yellow
    ]

    private func color(for source: String) -> Color {
        Self.sourceColors[source.lowercased()] ?? .secondary
    }

    private var sortedSources: [(key: String, value: Int)] {
        week.sources.sorted { $0.value > $1.value }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text(week.label)
                    .font(.system(size: 13, weight: .semibold))
                Spacer()
                Text("\(week.total_items) item\(week.total_items == 1 ? "" : "s")")
                    .font(.system(size: 12))
                    .foregroundStyle(.secondary)
            }

            if week.total_items > 0 && !week.sources.isEmpty {
                GeometryReader { geo in
                    HStack(spacing: 2) {
                        ForEach(sortedSources, id: \.key) { source, count in
                            let fraction = CGFloat(count) / CGFloat(week.total_items)
                            RoundedRectangle(cornerRadius: 3)
                                .fill(color(for: source))
                                .frame(width: max(geo.size.width * fraction - 2, 4))
                        }
                    }
                }
                .frame(height: 6)
            }

            if !week.sources.isEmpty {
                HStack(spacing: 6) {
                    ForEach(sortedSources, id: \.key) { source, _ in
                        HStack(spacing: 4) {
                            Circle()
                                .fill(color(for: source))
                                .frame(width: 6, height: 6)
                            Text(source.capitalized)
                                .font(.system(size: 11))
                                .foregroundStyle(.secondary)
                        }
                    }
                }
            }

            if !week.top_items.isEmpty {
                VStack(alignment: .leading, spacing: 4) {
                    ForEach(week.top_items.prefix(3), id: \.title) { item in
                        HStack(spacing: 6) {
                            Circle()
                                .fill(color(for: item.source))
                                .frame(width: 7, height: 7)
                            Text(item.title)
                                .font(.system(size: 12))
                                .foregroundStyle(.primary)
                                .lineLimit(1)
                                .truncationMode(.tail)
                        }
                    }
                }
                .padding(.top, 2)
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color(UIColor.secondarySystemGroupedBackground))
        .clipShape(RoundedRectangle(cornerRadius: 12))
    }
}

// MARK: - Recap Sheet

private struct RecapSheet: View {
    @Binding var recapData: RecapResponse?
    @Binding var isLoading: Bool
    @Binding var errorMessage: String?
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            Group {
                if isLoading {
                    VStack(spacing: 16) {
                        ProgressView()
                            .scaleEffect(1.3)
                        Text("Generating your weekly recap…")
                            .font(.system(size: 14))
                            .foregroundStyle(.secondary)
                    }
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else if let err = errorMessage {
                    VStack(spacing: 12) {
                        Image(systemName: "exclamationmark.triangle")
                            .font(.system(size: 32))
                            .foregroundStyle(.secondary)
                        Text(err)
                            .font(.system(size: 14))
                            .foregroundStyle(.secondary)
                            .multilineTextAlignment(.center)
                    }
                    .padding(32)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else if let r = recapData {
                    RecapContent(recap: r)
                }
            }
            .navigationTitle("Weekly Recap")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }
                        .font(.system(size: 16, weight: .semibold))
                        .foregroundStyle(Color(hex: "#0071e3"))
                }
            }
        }
    }
}

private struct RecapContent: View {
    let recap: RecapResponse

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {

                // Narrative paragraph
                if let narrative = recap.narrative, !narrative.isEmpty {
                    Text(narrative)
                        .font(.system(size: 16))
                        .foregroundStyle(Color(UIColor.label))
                        .lineSpacing(4)
                        .padding(.horizontal, 20)
                        .padding(.top, 8)
                }

                // Topics studied
                if let topics = recap.topics_this_week, !topics.isEmpty {
                    RecapSection(title: "Topics This Week", icon: "brain.head.profile") {
                        FlowTagView(tags: topics, color: Color(hex: "#0071e3"))
                    }
                }

                // Most active areas
                if let areas = recap.most_active_areas, !areas.isEmpty {
                    RecapSection(title: "Most Active Areas", icon: "chart.bar.fill") {
                        FlowTagView(tags: areas, color: .purple)
                    }
                }

                // Books
                if let books = recap.books, !books.isEmpty {
                    RecapSection(title: "Books", icon: "book.closed.fill") {
                        VStack(alignment: .leading, spacing: 8) {
                            ForEach(books, id: \.title) { book in
                                HStack(spacing: 10) {
                                    Image(systemName: "book.closed.fill")
                                        .font(.system(size: 13))
                                        .foregroundStyle(.purple)
                                    Text(book.title)
                                        .font(.system(size: 14))
                                    Spacer()
                                    if let status = book.status {
                                        Text(status)
                                            .font(.system(size: 11, weight: .medium))
                                            .foregroundStyle(.secondary)
                                            .padding(.horizontal, 8)
                                            .padding(.vertical, 3)
                                            .background(Color(UIColor.tertiarySystemFill))
                                            .clipShape(Capsule())
                                    }
                                }
                            }
                        }
                        .padding(.horizontal, 20)
                    }
                }

                // Key insights
                if let insights = recap.key_insights, !insights.isEmpty {
                    RecapSection(title: "Key Insights", icon: "lightbulb.fill") {
                        VStack(alignment: .leading, spacing: 10) {
                            ForEach(insights, id: \.self) { insight in
                                HStack(alignment: .top, spacing: 10) {
                                    Circle()
                                        .fill(Color(hex: "#ff9f0a"))
                                        .frame(width: 6, height: 6)
                                        .padding(.top, 6)
                                    Text(insight)
                                        .font(.system(size: 14))
                                        .foregroundStyle(Color(UIColor.label))
                                        .lineSpacing(3)
                                }
                            }
                        }
                        .padding(.horizontal, 20)
                    }
                }

                // Cross-domain connections
                if let connections = recap.connections, !connections.isEmpty {
                    RecapSection(title: "Connections", icon: "arrow.triangle.branch") {
                        VStack(alignment: .leading, spacing: 10) {
                            ForEach(connections, id: \.self) { connection in
                                HStack(alignment: .top, spacing: 10) {
                                    Image(systemName: "link")
                                        .font(.system(size: 12))
                                        .foregroundStyle(Color(hex: "#0071e3"))
                                        .padding(.top, 2)
                                    Text(connection)
                                        .font(.system(size: 14))
                                        .foregroundStyle(Color(UIColor.label))
                                        .lineSpacing(3)
                                }
                            }
                        }
                        .padding(.horizontal, 20)
                    }
                }

                // Open question
                if let question = recap.open_question, !question.isEmpty {
                    VStack(alignment: .leading, spacing: 8) {
                        HStack(spacing: 6) {
                            Image(systemName: "questionmark.circle.fill")
                                .font(.system(size: 14))
                                .foregroundStyle(Color(hex: "#0071e3"))
                            Text("Open Question for Next Week")
                                .font(.system(size: 11, weight: .semibold))
                                .foregroundStyle(.secondary)
                                .textCase(.uppercase)
                                .tracking(0.5)
                        }
                        .padding(.horizontal, 20)

                        Text(question)
                            .font(.system(size: 15, weight: .medium))
                            .foregroundStyle(Color(UIColor.label))
                            .lineSpacing(3)
                            .padding(14)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .background(Color(hex: "#0071e3").opacity(0.08))
                            .clipShape(RoundedRectangle(cornerRadius: 10))
                            .padding(.horizontal, 16)
                    }
                }

                // Fallback: unstructured result
                if let result = recap.result, !result.isEmpty,
                   recap.narrative == nil {
                    Text(result)
                        .font(.system(size: 15))
                        .foregroundStyle(Color(UIColor.label))
                        .lineSpacing(4)
                        .padding(.horizontal, 20)
                        .padding(.top, 8)
                }

                Spacer(minLength: 32)
            }
        }
        .background(Color(UIColor.systemGroupedBackground))
    }
}

private struct RecapSection<Content: View>: View {
    let title: String
    let icon: String
    @ViewBuilder let content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 6) {
                Image(systemName: icon)
                    .font(.system(size: 13))
                    .foregroundStyle(.secondary)
                Text(title)
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(.secondary)
                    .textCase(.uppercase)
                    .tracking(0.5)
            }
            .padding(.horizontal, 20)

            content
        }
    }
}

private struct FlowTagView: View {
    let tags: [String]
    let color: Color

    var body: some View {
        // Simple horizontal scroll for tags
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                ForEach(tags, id: \.self) { tag in
                    Text(tag)
                        .font(.system(size: 12, weight: .medium))
                        .foregroundStyle(color)
                        .padding(.horizontal, 10)
                        .padding(.vertical, 5)
                        .background(color.opacity(0.1))
                        .clipShape(Capsule())
                }
            }
            .padding(.horizontal, 20)
        }
    }
}

// MARK: - Skeleton

private struct TimelineSkeletonView: View {
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                VStack(alignment: .leading, spacing: 12) {
                    RoundedRectangle(cornerRadius: 4)
                        .fill(Color(UIColor.secondarySystemGroupedBackground))
                        .frame(width: 120, height: 14)
                        .overlay(ShimmerView().clipShape(RoundedRectangle(cornerRadius: 4)))
                        .padding(.horizontal, 20)

                    RoundedRectangle(cornerRadius: 8)
                        .fill(Color(UIColor.secondarySystemGroupedBackground))
                        .frame(maxWidth: .infinity)
                        .frame(height: 90)
                        .overlay(ShimmerView().clipShape(RoundedRectangle(cornerRadius: 8)))
                        .padding(.horizontal, 16)
                }

                VStack(spacing: 12) {
                    ForEach(0..<5, id: \.self) { _ in
                        RoundedRectangle(cornerRadius: 12)
                            .fill(Color(UIColor.secondarySystemGroupedBackground))
                            .frame(maxWidth: .infinity)
                            .frame(height: 72)
                            .overlay(ShimmerView().clipShape(RoundedRectangle(cornerRadius: 12)))
                            .padding(.horizontal, 16)
                    }
                }
            }
            .padding(.top, 16)
        }
        .background(Color(UIColor.systemGroupedBackground))
    }
}

// MARK: - Error

private struct TimelineErrorView: View {
    let message: String
    let onRetry: () -> Void

    var body: some View {
        VStack(spacing: 16) {
            Image(systemName: "exclamationmark.triangle")
                .font(.system(size: 36))
                .foregroundStyle(.secondary)
            Text(message)
                .font(.system(size: 15))
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
            Button("Try again", action: onRetry)
                .font(.system(size: 15, weight: .semibold))
                .foregroundStyle(Color(hex: "#0071e3"))
        }
        .padding(32)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}
