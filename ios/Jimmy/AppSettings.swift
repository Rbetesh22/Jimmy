import Foundation
import Combine

class AppSettings: ObservableObject {
    static let shared = AppSettings()

    @Published var serverURL: String {
        didSet { UserDefaults.standard.set(serverURL, forKey: "serverURL") }
    }
    @Published var isOnboarded: Bool {
        didSet { UserDefaults.standard.set(isOnboarded, forKey: "isOnboarded") }
    }
    @Published var userName: String {
        didSet { UserDefaults.standard.set(userName, forKey: "userName") }
    }
    @Published var apiTimeout: TimeInterval {
        didSet { UserDefaults.standard.set(apiTimeout, forKey: "apiTimeout") }
    }
    @Published var hapticEnabled: Bool {
        didSet { UserDefaults.standard.set(hapticEnabled, forKey: "hapticEnabled") }
    }

    // Cross-tab navigation: set to route a query to AskView
    @Published var pendingAskQuery: String? = nil

    // MARK: - SRS due count (for Practice tab badge)

    @Published var srsDueCount: Int = 0
    @Published var lastSRSSync: Date = Date.distantPast

    /// Fetch the current SRS due count and update srsDueCount.
    @MainActor
    func syncSRSDue(api: APIClient) {
        Task {
            if let response = try? await api.srsDue() {
                srsDueCount = response.count
                lastSRSSync = Date()
            }
        }
    }

    // MARK: - Streak tracking

    /// Streak milestone thresholds celebrated with the full-screen overlay.
    let streakMilestones: [Int] = [3, 7, 14, 30, 60, 100]

    @Published var currentStreak: Int {
        didSet { UserDefaults.standard.set(currentStreak, forKey: "currentStreak") }
    }
    @Published var longestStreak: Int {
        didSet { UserDefaults.standard.set(longestStreak, forKey: "longestStreak") }
    }
    @Published var lastActiveDate: Date? {
        didSet { UserDefaults.standard.set(lastActiveDate, forKey: "lastActiveDate") }
    }
    @Published var totalQueriesAsked: Int {
        didSet { UserDefaults.standard.set(totalQueriesAsked, forKey: "totalQueriesAsked") }
    }
    @Published var totalNotesAdded: Int {
        didSet { UserDefaults.standard.set(totalNotesAdded, forKey: "totalNotesAdded") }
    }

    /// The highest streak milestone that has been shown to the user.
    /// Used to avoid re-celebrating the same milestone.
    @Published var lastStreakMilestone: Int {
        didSet { UserDefaults.standard.set(lastStreakMilestone, forKey: "lastStreakMilestone") }
    }

    // MARK: - Notification preferences

    /// Master switch — notifications only fire when this is true AND the individual toggle is on.
    @Published var notificationsEnabled: Bool {
        didSet { UserDefaults.standard.set(notificationsEnabled, forKey: "notificationsEnabled") }
    }
    @Published var notifyMorningBriefing: Bool {
        didSet { UserDefaults.standard.set(notifyMorningBriefing, forKey: "notifyMorningBriefing") }
    }
    /// Hour (24-h) for the morning briefing notification (default 8).
    @Published var morningBriefingHour: Int {
        didSet { UserDefaults.standard.set(morningBriefingHour, forKey: "morningBriefingHour") }
    }
    @Published var notifyStudyReminder: Bool {
        didSet { UserDefaults.standard.set(notifyStudyReminder, forKey: "notifyStudyReminder") }
    }
    @Published var notifyStreakReminder: Bool {
        didSet { UserDefaults.standard.set(notifyStreakReminder, forKey: "notifyStreakReminder") }
    }
    @Published var notifySRSReminder: Bool {
        didSet { UserDefaults.standard.set(notifySRSReminder, forKey: "notifySRSReminder") }
    }
    @Published var notifyRandomResurface: Bool {
        didSet { UserDefaults.standard.set(notifyRandomResurface, forKey: "notifyRandomResurface") }
    }

    private init() {
        // Simulator runs on the Mac — use localhost directly. Real device uses tunnel.
        #if targetEnvironment(simulator)
        let defaultURL = "http://localhost:7700"
        UserDefaults.standard.set(true, forKey: "isOnboarded")  // skip onboarding in sim
        #else
        let defaultURL = "https://spine-multi-subsidiaries-projection.trycloudflare.com"
        #endif
        self.serverURL = UserDefaults.standard.string(forKey: "serverURL") ?? defaultURL
        #if targetEnvironment(simulator)
        self.isOnboarded = true
        #else
        self.isOnboarded = UserDefaults.standard.bool(forKey: "isOnboarded")
        #endif
        self.userName    = UserDefaults.standard.string(forKey: "userName") ?? ""
        let timeout      = UserDefaults.standard.double(forKey: "apiTimeout")
        self.apiTimeout  = timeout > 0 ? timeout : 15.0
        let hapticStored = UserDefaults.standard.object(forKey: "hapticEnabled")
        self.hapticEnabled      = hapticStored != nil ? UserDefaults.standard.bool(forKey: "hapticEnabled") : true
        self.currentStreak      = UserDefaults.standard.integer(forKey: "currentStreak")
        self.longestStreak      = UserDefaults.standard.integer(forKey: "longestStreak")
        self.lastActiveDate     = UserDefaults.standard.object(forKey: "lastActiveDate") as? Date
        self.totalQueriesAsked  = UserDefaults.standard.integer(forKey: "totalQueriesAsked")
        self.totalNotesAdded    = UserDefaults.standard.integer(forKey: "totalNotesAdded")
        self.lastStreakMilestone = UserDefaults.standard.integer(forKey: "lastStreakMilestone")
        self.notificationsEnabled  = UserDefaults.standard.bool(forKey: "notificationsEnabled")

        // Per-type toggles — default to true so existing users get them all on
        let morningSet = UserDefaults.standard.object(forKey: "notifyMorningBriefing")
        self.notifyMorningBriefing = morningSet != nil
            ? UserDefaults.standard.bool(forKey: "notifyMorningBriefing") : true

        let briefingHour = UserDefaults.standard.integer(forKey: "morningBriefingHour")
        self.morningBriefingHour = briefingHour > 0 ? briefingHour : 8

        let studySet = UserDefaults.standard.object(forKey: "notifyStudyReminder")
        self.notifyStudyReminder = studySet != nil
            ? UserDefaults.standard.bool(forKey: "notifyStudyReminder") : true

        let streakSet = UserDefaults.standard.object(forKey: "notifyStreakReminder")
        self.notifyStreakReminder = streakSet != nil
            ? UserDefaults.standard.bool(forKey: "notifyStreakReminder") : true

        let srsSet = UserDefaults.standard.object(forKey: "notifySRSReminder")
        self.notifySRSReminder = srsSet != nil
            ? UserDefaults.standard.bool(forKey: "notifySRSReminder") : true

        let resurfaceSet = UserDefaults.standard.object(forKey: "notifyRandomResurface")
        self.notifyRandomResurface = resurfaceSet != nil
            ? UserDefaults.standard.bool(forKey: "notifyRandomResurface") : true
    }

    // MARK: - Activity recording

    /// Call when the user does something meaningful (asks a question, adds a note).
    /// Returns the new milestone streak value if a milestone was just hit, nil otherwise.
    @discardableResult
    func recordActivity() -> Int? {
        let today = Calendar.current.startOfDay(for: Date())
        if let last = lastActiveDate {
            let lastDay = Calendar.current.startOfDay(for: last)
            let diff = Calendar.current.dateComponents([.day], from: lastDay, to: today).day ?? 0
            if diff == 0 {
                // Already recorded today — cancel streak reminder so it doesn't fire tonight
                NotificationScheduler.shared.cancelStreakReminder()
                return nil
            } else if diff == 1 {
                currentStreak += 1
            } else {
                currentStreak = 1
            }
        } else {
            currentStreak = 1
        }
        if currentStreak > longestStreak {
            longestStreak = currentStreak
        }
        lastActiveDate = Date()

        // Cancel tonight's streak reminder since user is active today
        NotificationScheduler.shared.cancelStreakReminder()

        // Check for a new streak milestone
        return detectMilestone()
    }

    // MARK: - Milestone detection

    /// Returns the milestone if the current streak just crossed one for the first time.
    func detectMilestone() -> Int? {
        let streak = currentStreak
        // Find the highest milestone the streak has reached
        guard let milestone = streakMilestones.filter({ $0 <= streak }).max() else { return nil }
        // Only celebrate once per milestone
        guard milestone > lastStreakMilestone else { return nil }
        lastStreakMilestone = milestone
        return milestone
    }
}
