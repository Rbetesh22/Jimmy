import Foundation
import Combine

// MARK: - API Errors

enum NeuronError: LocalizedError {
    case badURL
    case serverNotReachable
    case serverError(Int, String)
    case noData
    case decodingFailed(String)
    case networkUnavailable

    var errorDescription: String? {
        switch self {
        case .badURL:
            return "Invalid server URL"
        case .serverNotReachable:
            return "Can't reach Neuron. Make sure the server is running."
        case .serverError(let code, let msg):
            return "Server error \(code): \(msg)"
        case .noData:
            return "No data received"
        case .decodingFailed(let detail):
            return "Response format error: \(detail)"
        case .networkUnavailable:
            return "Network unavailable — check your connection"
        }
    }
}

// MARK: - Response Models

struct StatusResponse: Codable {
    let total_chunks: Int
    let sources: [String: Int]
}

struct DigestResponse: Codable {
    let result: String
    let cached_at: String?
}

struct NewsResponse: Codable {
    let articles: [NewsArticle]
    let by_category: [String: [NewsArticle]]
    let cached_at: String?
}

struct NewsArticle: Codable, Identifiable {
    var id: String { url }
    let title: String
    let url: String
    let description: String?
    let image: String?
    let category: String
    let source: String
    let time_ago: String?
    let pub_date: String?
}

struct NewsSummaryResponse: Codable {
    let summary: String
}

struct SparkResponse: Codable {
    let sparks: [Spark]
    let cached_at: String?
}

struct Spark: Codable, Identifiable {
    var id: String { (title ?? "") + (recent_item ?? "") }
    let title: String?
    let connection: String?
    let why_it_matters: String?
    let recent_item: String?
    let past_item: String?
    let icon: String?
}

struct AnalogyResponse: Codable {
    let analogies: [Analogy]
    let domains_found: [String]?
    let cached_at: String?
    let message: String?
}

struct Analogy: Codable, Identifiable {
    var id: String { (concept_a ?? "") + (concept_b ?? "") }
    let domain_a: String?
    let concept_a: String?
    let domain_b: String?
    let concept_b: String?
    let analogy: String?
    let deeper_insight: String?
}

struct DailyResponse: Codable {
    let fact: DailyFact?
    let vocab: DailyVocab?
    let date: String?
}

struct DailyFact: Codable {
    let text: String
    let source: String?
}

struct DailyVocab: Codable {
    let word: String
    let definition: String
    let context: String?
    let source: String?
}

struct AskRequest: Codable {
    let q: String
    let n_results: Int
}

struct Recommendation: Codable, Identifiable {
    var id: String { title + type }
    let type: String
    let title: String
    let author_or_show: String?
    let why: String?
    let link: String?
    let link_label: String?
    let link2: String?
    let link2_label: String?
}

struct RecommendationsResponse: Codable {
    let recommendations: [Recommendation]
}

struct IngestTextRequest: Codable {
    let text: String
    let source: String
}

struct AskStreamEvent: Codable {
    let type: String
    let text: String?
    let answer: String?
    let sources: [SourceChunk]?
    let detail: String?
    let related_questions: [String]?
}

struct SourceChunk: Codable, Identifiable {
    var id: String { (title ?? "") + (source ?? "") }
    let title: String?
    let source: String?
    let excerpt: String?
    let full_text: String?
    let url: String?
    let icon: String?
    let index: Int?
}

struct SuggestionsAPIResponse: Codable {
    let suggestions: [String]
}

// Alias so call sites can use either name
typealias SuggestionsResponse = SuggestionsAPIResponse

// MARK: - Recent / Upcoming Models

struct RecentItem: Codable {
    let title: String
    let date: String?
    let source: String?
    let excerpt: String?
    let url: String?
}

struct RecentResponse: Codable {
    let result: String
    let by_source: [String: [RecentItem]]
    let days: Int?
}

struct UpcomingEvent: Codable {
    let title: String
    let date: String?
    let calendar: String?
    let account: String?
    let url: String?
    let excerpt: String?
}

struct UpcomingResponse: Codable {
    let result: String
    let events: [UpcomingEvent]
    let days: Int?
}

// MARK: - Practice Models

struct PracticeRequest: Codable { let topic: String }
struct PracticeExercise: Codable, Identifiable {
    var id: String { question }
    let type: String
    let question: String
    let difficulty: String
    let answer: String
    let explanation: String
    let source_hint: String?
    let options: [String]?  // Non-nil for multiple_choice type

    var isMultipleChoice: Bool { type == "multiple_choice" && options != nil }

    /// Clean question text (strips embedded A/B/C/D lines if options are present separately)
    var questionText: String {
        guard isMultipleChoice else { return question }
        return question.components(separatedBy: "\n").first ?? question
    }
}
struct PracticeResponse: Codable {
    let exercises: [PracticeExercise]
    let topic: String
}
struct EvaluateRequest: Codable {
    let question: String
    let user_answer: String
    let correct_answer: String
    let explanation: String
    let topic: String
}
struct EvaluateResponse: Codable {
    let score: String  // "correct" | "partial" | "incorrect"
    let feedback: String
    let key_gap: String?
    let follow_up: String?
}

// MARK: - SRS Models

struct SRSRecordRequest: Codable {
    let topic: String
    let score: String
    let correct_count: Int
    let total_count: Int
}
struct SRSRecordResponse: Codable {
    let ok: Bool
    let next_review: String?
    let interval_days: Int?
}
struct SRSCardRecordRequest: Codable {
    let card_index: Int
    let rating: String  // "again" | "hard" | "good" | "easy"
}
struct SRSCardRecordResponse: Codable {
    let ok: Bool
    let next_review: String?
    let interval_days: Int?
}
struct SRSDueItem: Codable, Identifiable {
    var id: String {
        if let q = question, !q.isEmpty { return "\(card_index ?? -1)-\(q.prefix(40))" }
        return topic
    }
    let type: String          // "topic" or "flashcard"
    let topic: String
    let next_review: String?
    let repetitions: Int
    let ef: Double
    let overdue_days: Int
    let last_reviewed: String?
    // Flashcard-specific fields (nil for topic items)
    let card_index: Int?
    let question: String?
    let answer: String?
    let explanation: String?
}
struct SRSDueResponse: Codable {
    let due: [SRSDueItem]
    let count: Int
    let topic_count: Int
    let flashcard_count: Int
    let date: String
}
struct SRSTopicStat: Codable, Identifiable {
    var id: String { topic }
    let topic: String
    let mastery: Int
    let repetitions: Int
    let next_review: String?
    let is_due: Bool
    let ef: Double
    let interval: Int
    let recent_scores: [String]
    let last_reviewed: String?
}
struct SRSStatsResponse: Codable {
    let topics: [SRSTopicStat]
    let due_count: Int
    let upcoming_7d: Int
    let total_topics: Int
}

struct StudySessionResponse: Codable {
    let exercises: [PracticeExercise]
    let topics: [String]
    let topic_count: Int?
    let message: String?
}

// MARK: - Study Plan Models

struct StudyPlanDay: Codable, Identifiable {
    var id: String { date }
    let day: String
    let date: String
    let focus: String
    let topics: [String]
    let duration_min: Int
    let is_today: Bool?
}

struct StudyPlanExam: Codable, Identifiable {
    var id: String { name + date }
    let name: String
    let date: String
    let days_remaining: Int
    let topic_guess: String?
}

struct StudyPlanResponse: Codable {
    let week: String
    let exams: [StudyPlanExam]
    let plan: [StudyPlanDay]
    let srs_due: Int
    let streak_days: Int?
    let today_focus: String
    let today_duration_min: Int
    let today_topics: [String]
    let today_schedule: [String]?
    let cached_at: String?
}

// MARK: - Today Combined Model

struct TodayResponse: Codable {
    let fact: DailyFact?
    let vocab: DailyVocab?
    let date: String?
    let events: [UpcomingEvent]?
    let suggestions: [String]?
    let spark: Spark?
    // Extended fields from the enhanced /today endpoint
    let digest: String?
    let digest_cached_at: String?
    let srs_due: [SRSDueItem]?
    let analogy: Analogy?
    let resurface: TodayResurface?
}

struct TodayResurface: Codable {
    let result: String?
    let sources: [SourceChunk]?
    let period: String?
    let days_back: Int?
}

// MARK: - Library Models

struct LibraryBook: Codable, Identifiable {
    var id: String { title }
    let title: String
    let author: String?
    let status: String?
    let rating: Int?
    let notes: String?
    let date_added: String?
    let date: String?         // server returns "date" field
    let cover_url: String?
}

struct LibraryCounts: Codable {
    let total: Int?
    let read: Int?
    let reading: Int?
    let want: Int?
}

struct LibraryResponse: Codable {
    let books: [LibraryBook]
    let counts: LibraryCounts?
    let total: Int?
}

struct BookConnectionsResponse: Codable {
    let title: String
    let connections: [BookConnection]
    let count: Int?
}

struct BookConnection: Codable, Identifiable {
    var id: String { (source_title ?? "") + (excerpt ?? "") }
    let source_title: String?
    let excerpt: String?
    let source: String?
    let relevance: String?
}

struct AskResponse: Codable {
    let answer: String
    let sources: [SourceChunk]?
}

// MARK: - Cross-Domain Models

struct CrossDomainResponse: Codable {
    let analogies: [Analogy]
    let domains_found: [String]?
    let cached_at: String?
    let message: String?
}

// MARK: - Resurface Model

struct ResurfaceItem: Codable {
    let title: String
    let source: String?
    let excerpt: String?
    let date: String?
    let url: String?
}

struct ResurfaceResponse: Codable {
    let item: ResurfaceItem?
    let message: String?
}

// MARK: - Search Models

struct SearchResult: Codable, Identifiable {
    var id: String { (title ?? "") + (source ?? "") + (date ?? "") }
    let title: String?
    let source: String?
    let content: String?
    let content_preview: String?
    let date: String?
    let url: String?
    let composite_score: Double?

    var preview: String { content_preview ?? content ?? "" }

    var sourceIcon: String {
        switch source?.lowercased() {
        case "canvas":               return "🎓"
        case "note", "apple_notes":  return "📝"
        case "notion":               return "🗒️"
        case "goodreads", "kindle",
             "book", "readwise":     return "📚"
        case "google_calendar",
             "calendar":             return "📅"
        case "web", "url":           return "🌐"
        case "youtube":              return "📺"
        case "granola":              return "🎙️"
        case "gmail":                return "✉️"
        default:                     return "📄"
        }
    }

    var sourceLabel: String {
        (source ?? "").replacingOccurrences(of: "_", with: " ").capitalized
    }
}

struct SearchResponse: Codable {
    let results: [SearchResult]
    let query: String
    let total: Int?
    let offset: Int?
    let has_more: Bool?
}

// MARK: - Prune Noise Model

struct PruneNoiseResponse: Codable {
    let removed: Int?
    let remaining: Int?
    let breakdown: [String: Int]?
}

// MARK: - Voice Summary Model

struct VoiceSummaryResponse: Codable {
    let ok: Bool
    let date: String
    let day: String?
    let briefing: String       // Short spoken briefing (≤200 words) — for TTS
    let summary: String        // Full written summary of today's memos
    let memo_count: Int
    let exam_events: [String]?
    let message: String?
}

// MARK: - Voice Ingest Response

struct VoiceIngestResponse: Codable {
    let ok: Bool
    let title: String?
    let transcript: String?
    let cleaned_transcript: String?
    let chunks: Int?
    let documents: Int?
}

// MARK: - Learning Report Model

struct LearningReportResponse: Codable {
    let report: String?
    let summary: String?
    let topics: [String]?
    let period: String?
}

// MARK: - Timeline Models

struct TimelineResponse: Codable {
    let weeks: [TimelineWeek]
    let heatmap: [HeatmapDay]
    let total: Int
    let period_weeks: Int
    let events: [TimelineEvent]?
    let streak: Int?
}
struct TimelineWeek: Codable {
    let week_start: String
    let label: String
    let total_items: Int
    let sources: [String: Int]
    let top_items: [TimelineItem]
}
struct HeatmapDay: Codable { let date: String; let count: Int }
struct TimelineItem: Codable { let title: String; let source: String; let date: String; let url: String?; let type: String? }
struct TimelineEvent: Codable, Identifiable {
    var id: String { "\(date)__\(title)" }
    let date: String
    let title: String
    let snippet: String?
    let source: String
    let type: String?
    let url: String?
}

// MARK: - Recap Model

struct RecapResponse: Codable {
    let narrative: String?
    let topics_this_week: [String]?
    let most_active_areas: [String]?
    let books: [RecapBook]?
    let key_insights: [String]?
    let connections: [String]?
    let open_question: String?
    let sources: [SourceChunk]?
    let period: String?
    // Fallback for unstructured response
    let result: String?
}
struct RecapBook: Codable {
    let title: String
    let status: String?
}

// MARK: - API Client

@MainActor
class APIClient: ObservableObject {
    static let shared = APIClient()

    private var baseURL: String { AppSettings.shared.serverURL }

    private var session: URLSession {
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = AppSettings.shared.apiTimeout
        config.timeoutIntervalForResource = AppSettings.shared.apiTimeout * 3
        return URLSession(configuration: config)
    }

    // MARK: - Status

    func status() async throws -> StatusResponse {
        try await get("/status")
    }

    struct HealthResponse: Decodable { let status: String }
    func health() async throws -> HealthResponse {
        try await get("/health")
    }

    // MARK: - Digest

    func digest(refresh: Bool = false) async throws -> DigestResponse {
        try await get("/digest\(refresh ? "?refresh=true" : "")")
    }

    // MARK: - News

    func news() async throws -> NewsResponse {
        try await get("/news")
    }

    func newsSummary() async throws -> NewsSummaryResponse {
        try await get("/news/summary")
    }

    func newsPage(offset: Int, limit: Int) async throws -> NewsResponse {
        try await get("/news?offset=\(offset)&limit=\(limit)")
    }

    // MARK: - Sparks

    func sparks() async throws -> SparkResponse {
        try await sparks(refresh: false)
    }

    func sparks(refresh: Bool) async throws -> SparkResponse {
        let refreshParam = refresh ? "&refresh=true" : ""
        return try await get("/spark?days_recent=14&days_old=60\(refreshParam)")
    }

    func analogies(refresh: Bool = false, randomTopic: Bool = false) async throws -> AnalogyResponse {
        if randomTopic {
            return try await get("/analogies?random_topic=true")
        }
        let q = refresh ? "?refresh=true" : ""
        return try await get("/analogies\(q)")
    }

    // MARK: - Suggestions

    func suggestions() async throws -> SuggestionsAPIResponse {
        try await get("/suggestions")
    }

    // MARK: - Recommendations

    func recommendations() async throws -> RecommendationsResponse {
        try await get("/recommendations")
    }

    // MARK: - Daily

    func daily() async throws -> DailyResponse {
        try await get("/daily")
    }

    // MARK: - Ask (streaming)

    enum AskEvent {
        case token(String)
        case sources([SourceChunk])
        case done(String, [SourceChunk]?, [String]?)
    }

    func askStream(query: String) throws -> AsyncThrowingStream<AskEvent, Error> {
        guard let url = URL(string: baseURL + "/ask/stream") else {
            throw NeuronError.badURL
        }
        var req = makeRequest(url: url, method: "POST")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.timeoutInterval = AppSettings.shared.apiTimeout * 4  // Streaming needs longer timeout
        req.httpBody = try JSONEncoder().encode(AskRequest(q: query, n_results: 25))

        return AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    let (stream, response) = try await URLSession.shared.bytes(for: req)
                    if let http = response as? HTTPURLResponse, http.statusCode != 200 {
                        continuation.finish(throwing: NeuronError.serverError(http.statusCode, "Streaming request failed"))
                        return
                    }
                    for try await line in stream.lines {
                        try Task.checkCancellation()
                        guard line.hasPrefix("data: ") else { continue }
                        let json = String(line.dropFirst(6))
                        guard let data = json.data(using: .utf8),
                              let event = try? JSONDecoder().decode(AskStreamEvent.self, from: data)
                        else { continue }

                        switch event.type {
                        case "token":
                            let t = event.text ?? ""
                            continuation.yield(.token(t))
                        case "sources":
                            if let srcs = event.sources {
                                continuation.yield(.sources(srcs))
                            }
                        case "done":
                            continuation.yield(.done(event.answer ?? "", event.sources, event.related_questions))
                        default: break
                        }
                    }
                    continuation.finish()
                } catch is CancellationError {
                    continuation.finish(throwing: CancellationError())
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }

    // MARK: - Ingest

    func ingestNote(_ text: String) async throws {
        struct Req: Codable { let text: String; let source: String }
        try await post("/ingest/text", body: Req(text: text, source: "note"))
    }

    func ingestVoiceMemo(_ text: String, durationSeconds: Double = 0) async throws {
        guard let url = URL(string: baseURL + "/ingest/voice") else { throw NeuronError.badURL }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")
        let encoded = "text=\(text.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? "")&duration_seconds=\(durationSeconds)"
        req.httpBody = encoded.data(using: .utf8)
        let (_, resp) = try await session.data(for: req)
        guard let http = resp as? HTTPURLResponse, (200...299).contains(http.statusCode) else {
            throw NeuronError.serverError(0, "Voice memo ingest failed")
        }
    }

    func ingestURL(_ urlStr: String) async throws {
        struct Req: Codable { let url: String }
        try await post("/ingest/url", body: Req(url: urlStr))
    }

    func ingestFile(data: Data, filename: String, mimeType: String) async throws {
        guard let url = URL(string: baseURL + "/ingest/file") else { throw NeuronError.badURL }
        let boundary = "Boundary-\(UUID().uuidString)"
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")

        var body = Data()
        let boundaryPrefix = "--\(boundary)\r\n"
        body.append(boundaryPrefix.data(using: .utf8)!)
        body.append("Content-Disposition: form-data; name=\"file\"; filename=\"\(filename)\"\r\n".data(using: .utf8)!)
        body.append("Content-Type: \(mimeType)\r\n\r\n".data(using: .utf8)!)
        body.append(data)
        body.append("\r\n--\(boundary)--\r\n".data(using: .utf8)!)
        req.httpBody = body

        let respData: Data
        let resp: URLResponse
        do {
            (respData, resp) = try await session.data(for: req)
        } catch let urlError as URLError where
            urlError.code == .cannotConnectToHost ||
            urlError.code == .networkConnectionLost ||
            urlError.code == .notConnectedToInternet {
            throw NeuronError.serverNotReachable
        }
        if let http = resp as? HTTPURLResponse, http.statusCode != 200 {
            let bodyStr = String(data: respData, encoding: .utf8) ?? ""
            throw NeuronError.serverError(http.statusCode, bodyStr.isEmpty ? "File ingest endpoint not yet available on server" : bodyStr)
        }
    }

    // MARK: - Practice

    func practice(topic: String) async throws -> PracticeResponse {
        struct Req: Codable { let topic: String }
        return try await post("/practice", body: Req(topic: topic))
    }

    func evaluateAnswer(_ req: EvaluateRequest) async throws -> EvaluateResponse {
        return try await post("/practice/evaluate", body: req)
    }

    // MARK: - SRS

    func studySession() async throws -> StudySessionResponse {
        struct Empty: Codable {}
        return try await post("/study-session", body: Empty())
    }

    @discardableResult
    func srsRecord(topic: String, score: String, correctCount: Int, totalCount: Int) async throws -> SRSRecordResponse {
        let req = SRSRecordRequest(topic: topic, score: score, correct_count: correctCount, total_count: totalCount)
        return try await post("/srs/record", body: req)
    }

    func srsDue() async throws -> SRSDueResponse {
        try await get("/srs/due")
    }

    @discardableResult
    func srsCardRecord(cardIndex: Int, rating: String) async throws -> SRSCardRecordResponse {
        let req = SRSCardRecordRequest(card_index: cardIndex, rating: rating)
        return try await post("/srs/card/record", body: req)
    }

    func srsStats() async throws -> SRSStatsResponse {
        try await get("/srs/stats")
    }

    // MARK: - Timeline

    func timeline(weeks: Int = 16, days: Int = 0) async throws -> TimelineResponse {
        if days > 0 {
            return try await get("/timeline?days=\(days)")
        }
        return try await get("/timeline?weeks=\(weeks)")
    }

    func recap(refresh: Bool = false) async throws -> RecapResponse {
        try await get("/recap\(refresh ? "?refresh=true" : "")")
    }

    // MARK: - Recent / Upcoming

    func recent() async throws -> RecentResponse {
        try await get("/recent")
    }

    func upcoming() async throws -> UpcomingResponse {
        try await get("/upcoming")
    }

    // MARK: - Today Combined

    func today() async throws -> TodayResponse {
        try await get("/today")
    }

    // MARK: - Study Plan

    func studyPlan() async throws -> StudyPlanResponse {
        try await get("/study-plan")
    }

    // MARK: - Library

    func fetchLibrary(shelf: String = "") async throws -> LibraryResponse {
        let q = shelf.isEmpty ? "" : "?shelf=\(shelf.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? shelf)"
        return try await get("/library\(q)")
    }

    func updateBook(title: String, status: String? = nil, rating: Int? = nil, notes: String? = nil) async throws {
        struct Req: Encodable {
            let title: String
            let status: String?
            let rating: Int?
            let notes: String?
        }
        let req = Req(title: title, status: status, rating: rating, notes: notes)
        struct OkResp: Codable { let ok: Bool? }
        let _: OkResp = try await post("/library/book", body: req)
    }

    func fetchBookConnections(title: String) async throws -> BookConnectionsResponse {
        let encoded = title.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? title
        return try await get("/library/connections/\(encoded)")
    }

    func askAboutBook(q: String, book: String) async throws -> AskResponse {
        struct Req: Encodable { let q: String; let book: String }
        return try await post("/library/ask", body: Req(q: q, book: book))
    }

    // MARK: - Cross Domain

    func fetchCrossDomain(topic: String = "") async throws -> CrossDomainResponse {
        let q = topic.isEmpty ? "" : "?topic=\(topic.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? topic)"
        return try await get("/cross-domain\(q)")
    }

    // MARK: - Resurface Random

    func resurfaceRandom() async throws -> ResurfaceResponse {
        try await get("/resurface/random")
    }

    // MARK: - Search

    func search(query: String, source: String = "", offset: Int = 0, n: Int = 8) async throws -> SearchResponse {
        var params = URLComponents()
        params.queryItems = [
            URLQueryItem(name: "q", value: query),
            URLQueryItem(name: "n", value: "\(n)"),
            URLQueryItem(name: "offset", value: "\(offset)"),
        ]
        if !source.isEmpty {
            params.queryItems?.append(URLQueryItem(name: "source", value: source))
        }
        let qs = params.percentEncodedQuery ?? ""
        return try await get("/search?\(qs)")
    }

    // MARK: - Prune Noise

    func pruneNoise() async throws -> PruneNoiseResponse {
        struct Empty: Codable {}
        return try await post("/admin/prune-noise", body: Empty())
    }

    // MARK: - Learning Report

    func learningReport() async throws -> LearningReportResponse {
        try await get("/learning-report")
    }

    // MARK: - Voice Summary (Daily Briefing)

    func voiceSummary() async throws -> VoiceSummaryResponse {
        try await get("/daily/voice-summary")
    }

    // MARK: - Ingest Voice (multipart audio file)

    func ingestVoiceAudio(audioData: Data, filename: String, title: String) async throws {
        _ = try await ingestVoiceAudioWithResponse(audioData: audioData, filename: filename, title: title)
    }

    func ingestVoiceAudioWithResponse(audioData: Data, filename: String, title: String) async throws -> VoiceIngestResponse {
        guard let url = URL(string: baseURL + "/ingest/voice") else { throw NeuronError.badURL }
        let boundary = "Boundary-\(UUID().uuidString)"
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        req.timeoutInterval = AppSettings.shared.apiTimeout * 3

        var body = Data()
        // "file" is the field name the server expects (FastAPI File(...))
        body.append("--\(boundary)\r\n".data(using: .utf8)!)
        body.append("Content-Disposition: form-data; name=\"file\"; filename=\"\(filename)\"\r\n".data(using: .utf8)!)
        body.append("Content-Type: audio/m4a\r\n\r\n".data(using: .utf8)!)
        body.append(audioData)
        body.append("\r\n".data(using: .utf8)!)
        // title field
        body.append("--\(boundary)\r\n".data(using: .utf8)!)
        body.append("Content-Disposition: form-data; name=\"title\"\r\n\r\n".data(using: .utf8)!)
        body.append(title.data(using: .utf8)!)
        body.append("\r\n".data(using: .utf8)!)
        body.append("--\(boundary)--\r\n".data(using: .utf8)!)
        req.httpBody = body

        let data: Data
        let resp: URLResponse
        do {
            (data, resp) = try await session.data(for: req)
        } catch let urlError as URLError where
            urlError.code == .cannotConnectToHost ||
            urlError.code == .networkConnectionLost ||
            urlError.code == .notConnectedToInternet {
            throw NeuronError.serverNotReachable
        }
        if let http = resp as? HTTPURLResponse, http.statusCode != 200 {
            let bodyStr = String(data: data, encoding: .utf8) ?? ""
            throw NeuronError.serverError(http.statusCode, bodyStr)
        }
        do {
            return try JSONDecoder().decode(VoiceIngestResponse.self, from: data)
        } catch {
            throw NeuronError.decodingFailed(error.localizedDescription)
        }
    }

    // MARK: - Refresh

    func refresh() async throws {
        struct Empty: Codable {}
        try await post("/refresh", body: Empty())
    }

    // MARK: - Helpers

    private func makeRequest(url: URL, method: String = "GET") -> URLRequest {
        var req = URLRequest(url: url)
        req.httpMethod = method
        // Bypass localtunnel's browser verification page
        req.setValue("true", forHTTPHeaderField: "bypass-tunnel-reminder")
        return req
    }

    private func get<T: Decodable>(_ path: String) async throws -> T {
        guard let url = URL(string: baseURL + path) else { throw NeuronError.badURL }
        let req = makeRequest(url: url)
        let data: Data
        let resp: URLResponse
        do {
            (data, resp) = try await session.data(for: req)
        } catch let urlError as URLError where
            urlError.code == .cannotConnectToHost ||
            urlError.code == .networkConnectionLost ||
            urlError.code == .notConnectedToInternet ||
            urlError.code == .timedOut ||
            urlError.code == .cannotFindHost ||
            urlError.code == .dnsLookupFailed ||
            urlError.code == .resourceUnavailable {
            throw NeuronError.serverNotReachable
        }
        if let http = resp as? HTTPURLResponse, http.statusCode != 200 {
            let body = String(data: data, encoding: .utf8) ?? ""
            throw NeuronError.serverError(http.statusCode, body)
        }
        do {
            return try JSONDecoder().decode(T.self, from: data)
        } catch {
            throw NeuronError.decodingFailed(error.localizedDescription)
        }
    }

    @discardableResult
    private func post<B: Encodable, T: Decodable>(_ path: String, body: B) async throws -> T {
        guard let url = URL(string: baseURL + path) else { throw NeuronError.badURL }
        var req = makeRequest(url: url, method: "POST")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONEncoder().encode(body)
        let data: Data
        let resp: URLResponse
        do {
            (data, resp) = try await session.data(for: req)
        } catch let urlError as URLError where
            urlError.code == .cannotConnectToHost ||
            urlError.code == .networkConnectionLost ||
            urlError.code == .notConnectedToInternet ||
            urlError.code == .timedOut ||
            urlError.code == .cannotFindHost {
            throw NeuronError.serverNotReachable
        }
        if let http = resp as? HTTPURLResponse, http.statusCode != 200 {
            let body = String(data: data, encoding: .utf8) ?? ""
            throw NeuronError.serverError(http.statusCode, body)
        }
        do {
            return try JSONDecoder().decode(T.self, from: data)
        } catch {
            throw NeuronError.decodingFailed(error.localizedDescription)
        }
    }

    private func post<B: Encodable>(_ path: String, body: B) async throws {
        guard let url = URL(string: baseURL + path) else { throw NeuronError.badURL }
        var req = makeRequest(url: url, method: "POST")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONEncoder().encode(body)
        let data: Data
        let resp: URLResponse
        do {
            (data, resp) = try await session.data(for: req)
        } catch let urlError as URLError where
            urlError.code == .cannotConnectToHost ||
            urlError.code == .networkConnectionLost ||
            urlError.code == .notConnectedToInternet ||
            urlError.code == .timedOut ||
            urlError.code == .cannotFindHost {
            throw NeuronError.serverNotReachable
        }
        if let http = resp as? HTTPURLResponse, http.statusCode != 200 {
            let body = String(data: data, encoding: .utf8) ?? ""
            throw NeuronError.serverError(http.statusCode, body)
        }
    }
}
