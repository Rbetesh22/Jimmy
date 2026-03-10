import Foundation
import Combine

// MARK: - Response Models

struct StatusResponse: Codable {
    let total_chunks: Int
    let sources: [String: Int]
    let top_sources: [TopSource]?
    let recent_topics: [String]?
    let knowledge_age_days: Int?
    let last_ingest_date: String?

    struct TopSource: Codable {
        let source: String
        let chunks: Int
    }
}

struct DigestResponse: Codable {
    let result: String
    let sources: [DigestSource]?
    let topic: String?
    let cached_at: String?

    struct DigestSource: Codable {
        let source: String?
        let title: String?
        let count: Int?
    }
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
    let pub_date: String?
    let category: String
    let source: String
    let time_ago: String?
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
}

struct DailyResponse: Codable {
    let fact: DailyFact?
    let vocab: VocabWord?
    let date: String?
    let cached_at: String?
}

struct DailyFact: Codable {
    let text: String
    let source: String?
}

struct VocabWord: Codable {
    let word: String?
    let definition: String?
    let context: String?
    let source: String?
    // Legacy / optional fields that may appear in some responses
    let pronunciation: String?
    let part_of_speech: String?
    let etymology: String?
    let example: String?
}

struct HealthResponse: Codable {
    let status: String
    let timestamp: String?
    let kb_size: Int?
    let bm25_loaded: Bool?
    let llm_model: String?
    let uptime_seconds: Double?
}

struct AskRequest: Codable {
    let q: String
    let n_results: Int
}

// SRS/study recommendation from /recommendations endpoint
struct SRSRecommendation: Codable, Identifiable {
    var id: String { topic + (source ?? "") }
    let topic: String
    let reason: String?
    let estimated_time_minutes: Int?
    let priority: String?
    let source: String?
}

// Media recommendation from /suggestions endpoint
struct MediaRecommendation: Codable, Identifiable {
    var id: String { title + type }
    let type: String
    let title: String
    let why: String?
    let link: String?
}

struct StreakContext: Codable {
    let streak_days: Int?
    let message: String?
    let task: String?
    let task_type: String?
}

struct RecommendationsResponse: Codable {
    let recommendations: [SRSRecommendation]
    let media_recommendations: [MediaRecommendation]?
    let streak_context: StreakContext?
    let srs_due_count: Int?
    let cached_at: String?
}

// /suggestions endpoint returns a different shape
struct SuggestionsResponse: Codable {
    let suggestions: [String]
    let recommendations: [SuggestionRecommendation]?
    let cached_at: String?

    struct SuggestionRecommendation: Codable, Identifiable {
        var id: String { title + type }
        let type: String
        let title: String
        let why: String?
        let link: String?
    }
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

// MARK: - API Client

@MainActor
class APIClient: ObservableObject {
    static let shared = APIClient()

    private var baseURL: String { AppSettings.shared.serverURL }

    private var session: URLSession {
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = AppSettings.shared.apiTimeout
        config.timeoutIntervalForResource = AppSettings.shared.apiTimeout * 2
        return URLSession(configuration: config)
    }

    // MARK: - Status

    func status() async throws -> StatusResponse {
        try await get("/status")
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

    // MARK: - Sparks

    func sparks() async throws -> SparkResponse {
        try await get("/spark?days_recent=14&days_old=60")
    }

    // MARK: - Recommendations

    func recommendations() async throws -> RecommendationsResponse {
        try await get("/recommendations")
    }

    // MARK: - Suggestions

    func suggestions() async throws -> SuggestionsResponse {
        try await get("/suggestions")
    }

    // MARK: - Health (fast check — use this for connection tests, not /status)

    func health() async throws -> HealthResponse {
        try await get("/health")
    }

    // MARK: - Daily

    func daily() async throws -> DailyResponse {
        try await get("/daily")
    }

    // MARK: - Ask (streaming)

    enum AskEvent {
        case token(String)
        case sources([SourceChunk])
        case done(String, [SourceChunk]?)
    }

    func askStream(query: String) throws -> AsyncThrowingStream<AskEvent, Error> {
        guard let url = URL(string: baseURL + "/ask/stream") else {
            throw URLError(.badURL)
        }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONEncoder().encode(AskRequest(q: query, n_results: 25))

        return AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    let (stream, _) = try await URLSession.shared.bytes(for: req)
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
                            continuation.yield(.done(event.answer ?? "", event.sources))
                        default: break
                        }
                    }
                    continuation.finish()
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

    func ingestURL(_ urlStr: String) async throws {
        struct Req: Codable { let url: String }
        try await post("/ingest/url", body: Req(url: urlStr))
    }

    // MARK: - Refresh

    func refresh() async throws {
        struct Empty: Codable {}
        try await post("/refresh", body: Empty())
    }

    // MARK: - Helpers

    private func get<T: Decodable>(_ path: String) async throws -> T {
        guard let url = URL(string: baseURL + path) else { throw URLError(.badURL) }
        let (data, resp) = try await session.data(from: url)
        if let http = resp as? HTTPURLResponse, http.statusCode != 200 {
            throw URLError(.badServerResponse)
        }
        return try JSONDecoder().decode(T.self, from: data)
    }

    @discardableResult
    private func post<B: Encodable, T: Decodable>(_ path: String, body: B) async throws -> T {
        guard let url = URL(string: baseURL + path) else { throw URLError(.badURL) }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONEncoder().encode(body)
        let (data, _) = try await session.data(for: req)
        return try JSONDecoder().decode(T.self, from: data)
    }

    private func post<B: Encodable>(_ path: String, body: B) async throws {
        guard let url = URL(string: baseURL + path) else { throw URLError(.badURL) }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONEncoder().encode(body)
        _ = try await session.data(for: req)
    }
}
