import UserNotifications
import Foundation

// MARK: - Notification identifiers

enum NeuronNotification {
    static let morningBriefing  = "morningBriefing"
    static let studyReminder    = "studyReminder"
    static let streakReminder   = "streakReminder"
    static let srsReminder      = "srsReminder"
    static let randomResurface  = "randomResurface"
}

// MARK: - Deep-link target (carried in userInfo)

enum NotificationTarget: String {
    case home     = "home"
    case ask      = "ask"
    case practice = "practice"
    case library  = "library"
    case sparks   = "sparks"
}

// MARK: - NotificationScheduler

class NotificationScheduler {
    static let shared = NotificationScheduler()

    // MARK: Permission

    func requestPermission() async -> Bool {
        let center = UNUserNotificationCenter.current()
        return (try? await center.requestAuthorization(options: [.alert, .sound, .badge])) ?? false
    }

    // MARK: Morning Briefing (8 AM daily, or custom hour)

    func scheduleMorningBriefing(userName: String, hour: Int = 8) {
        let center = UNUserNotificationCenter.current()
        center.removePendingNotificationRequests(withIdentifiers: [NeuronNotification.morningBriefing])

        let content = UNMutableNotificationContent()
        content.title = "Good morning\(userName.isEmpty ? "" : ", \(userName)")"
        content.body = "Your daily briefing is ready. 3 things to know today."
        content.sound = .default
        content.userInfo = ["target": NotificationTarget.home.rawValue]

        var comps = DateComponents()
        comps.hour   = hour
        comps.minute = 0
        let trigger = UNCalendarNotificationTrigger(dateMatching: comps, repeats: true)
        let request = UNNotificationRequest(
            identifier: NeuronNotification.morningBriefing,
            content: content,
            trigger: trigger
        )
        center.add(request)
    }

    // MARK: Study Reminder (fires N days before an exam — one-time)

    /// Schedule a study reminder that fires `daysBefore` days before `examDate`.
    /// - Parameters:
    ///   - examName: Name of the exam/event (e.g. "OS exam")
    ///   - examDate: The actual exam date
    ///   - daysBefore: How many days before the exam to fire (default 2)
    func scheduleStudyReminder(examName: String, examDate: Date, daysBefore: Int = 2) {
        let center = UNUserNotificationCenter.current()
        // Use a stable identifier so rescheduling replaces the old request
        let id = "\(NeuronNotification.studyReminder)_\(examName.replacingOccurrences(of: " ", with: "_"))"
        center.removePendingNotificationRequests(withIdentifiers: [id])

        guard let fireDate = Calendar.current.date(byAdding: .day, value: -daysBefore, to: examDate),
              fireDate > Date() else { return }

        let content = UNMutableNotificationContent()
        content.title = "\(examName) in \(daysBefore) day\(daysBefore == 1 ? "" : "s")"
        content.body  = "Quick review? Open Neuron to study."
        content.sound = .default
        content.userInfo = [
            "target":    NotificationTarget.practice.rawValue,
            "exam_name": examName
        ]

        var comps = Calendar.current.dateComponents([.year, .month, .day], from: fireDate)
        comps.hour   = 9
        comps.minute = 0
        let trigger = UNCalendarNotificationTrigger(dateMatching: comps, repeats: false)
        let request = UNNotificationRequest(identifier: id, content: content, trigger: trigger)
        center.add(request)
    }

    // MARK: Streak Reminder (8 PM if no activity today)

    func scheduleStreakReminder(streak: Int) {
        let center = UNUserNotificationCenter.current()
        center.removePendingNotificationRequests(withIdentifiers: [NeuronNotification.streakReminder])
        guard streak > 0 else { return }

        let content = UNMutableNotificationContent()
        content.title = "Keep your streak going"
        content.body  = "You've learned \(streak) day\(streak == 1 ? "" : "s") in a row. Don't break the chain!"
        content.sound = .default
        content.userInfo = ["target": NotificationTarget.home.rawValue]

        var comps = DateComponents()
        comps.hour   = 20
        comps.minute = 0
        let trigger = UNCalendarNotificationTrigger(dateMatching: comps, repeats: true)
        let request = UNNotificationRequest(
            identifier: NeuronNotification.streakReminder,
            content: content,
            trigger: trigger
        )
        center.add(request)
    }

    // MARK: SRS Due Reminder (3 PM when cards are due)

    func scheduleSRSReminder(dueTopics: [String]) {
        let center = UNUserNotificationCenter.current()
        center.removePendingNotificationRequests(withIdentifiers: [NeuronNotification.srsReminder])
        guard !dueTopics.isEmpty else { return }

        let content = UNMutableNotificationContent()
        let count = dueTopics.count
        if count == 1 {
            content.title = "\(dueTopics[0]) is due for review"
            content.body  = "Tap to start today's spaced repetition session — takes about 5 minutes."
        } else {
            let preview = dueTopics.prefix(2).joined(separator: ", ")
            let extra   = count > 2 ? " +\(count - 2) more" : ""
            content.title = "\(count) cards due for review"
            content.body  = "\(preview)\(extra) — tap to start today's session."
        }
        content.sound = .default
        content.badge = NSNumber(value: count)
        content.userInfo = [
            "target":     NotificationTarget.practice.rawValue,
            "due_count":  count
        ]

        var comps = DateComponents()
        comps.hour   = 15
        comps.minute = 0
        let trigger = UNCalendarNotificationTrigger(dateMatching: comps, repeats: true)
        let request = UNNotificationRequest(
            identifier: NeuronNotification.srsReminder,
            content: content,
            trigger: trigger
        )
        center.add(request)
    }

    // MARK: Random Resurface (random time 2–4 PM)

    /// Schedule a random resurface notification for today at a random minute between 2 PM and 4 PM.
    /// - Parameter snippet: A short text excerpt from the user's notes to display.
    func scheduleRandomResurface(snippet: String) {
        let center = UNUserNotificationCenter.current()
        center.removePendingNotificationRequests(withIdentifiers: [NeuronNotification.randomResurface])
        guard !snippet.isEmpty else { return }

        let content = UNMutableNotificationContent()
        content.title = "From your notes"
        // Truncate long snippets
        let display  = snippet.count > 120 ? String(snippet.prefix(117)) + "..." : snippet
        content.body  = display
        content.sound = .default
        content.userInfo = ["target": NotificationTarget.ask.rawValue]

        // Random fire time between 14:00 and 15:59
        let randomMinuteOffset = Int.random(in: 0..<120)   // 0–119 minutes after 14:00
        let fireHour   = 14 + randomMinuteOffset / 60       // 14 or 15
        let fireMinute = randomMinuteOffset % 60

        var comps = DateComponents()
        comps.hour   = fireHour
        comps.minute = fireMinute
        let trigger = UNCalendarNotificationTrigger(dateMatching: comps, repeats: true)
        let request = UNNotificationRequest(
            identifier: NeuronNotification.randomResurface,
            content: content,
            trigger: trigger
        )
        center.add(request)
    }

    // MARK: Test Notification (fires in ~5 seconds)

    func sendTestNotification() {
        let center = UNUserNotificationCenter.current()
        center.removePendingNotificationRequests(withIdentifiers: ["testNotification"])

        let content = UNMutableNotificationContent()
        content.title = "Test notification"
        content.body  = "Neuron notifications are working correctly."
        content.sound = .default

        let trigger = UNTimeIntervalNotificationTrigger(timeInterval: 5, repeats: false)
        let request = UNNotificationRequest(identifier: "testNotification", content: content, trigger: trigger)
        center.add(request)
    }

    // MARK: Cancel helpers

    func cancelMorningBriefing() {
        UNUserNotificationCenter.current().removePendingNotificationRequests(
            withIdentifiers: [NeuronNotification.morningBriefing]
        )
    }

    func cancelStreakReminder() {
        UNUserNotificationCenter.current().removePendingNotificationRequests(
            withIdentifiers: [NeuronNotification.streakReminder]
        )
    }

    func cancelSRSReminder() {
        UNUserNotificationCenter.current().removePendingNotificationRequests(
            withIdentifiers: [NeuronNotification.srsReminder]
        )
    }

    func cancelRandomResurface() {
        UNUserNotificationCenter.current().removePendingNotificationRequests(
            withIdentifiers: [NeuronNotification.randomResurface]
        )
    }

    func cancelAll() {
        UNUserNotificationCenter.current().removeAllPendingNotificationRequests()
    }

    // MARK: Schedule All

    /// Re-schedule all enabled notification types based on current AppSettings.
    func scheduleAll(userName: String, streak: Int, settings: AppSettings? = nil) {
        let s = settings ?? AppSettings.shared

        if s.notifyMorningBriefing {
            scheduleMorningBriefing(userName: userName, hour: s.morningBriefingHour)
        } else {
            cancelMorningBriefing()
        }

        if s.notifyStreakReminder {
            scheduleStreakReminder(streak: streak)
        } else {
            cancelStreakReminder()
        }

        // SRS and resurface are scheduled dynamically when content is loaded;
        // cancel them here if their toggle is off.
        if !s.notifySRSReminder {
            cancelSRSReminder()
        }
        if !s.notifyRandomResurface {
            cancelRandomResurface()
        }
    }

    // MARK: Legacy convenience (keeps existing call sites working)

    @available(*, deprecated, renamed: "scheduleAll(userName:streak:settings:)")
    func scheduleAll(userName: String, streak: Int) {
        scheduleAll(userName: userName, streak: streak, settings: AppSettings.shared)
    }
}
