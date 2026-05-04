import SwiftUI
import UIKit
import AVFoundation

struct VoiceMemoSheet: View {
    @EnvironmentObject var api: APIClient
    @EnvironmentObject var settings: AppSettings
    @Environment(\.dismiss) private var dismiss

    @State private var recorder: AVAudioRecorder?
    @State private var isRecording = false
    @State private var audioURL: URL?
    @State private var recordingTime: TimeInterval = 0
    @State private var timer: Timer?
    @State private var isUploading = false
    @State private var result: String?
    @State private var transcriptPreview: String = ""
    @State private var errorMsg: String?
    @State private var title: String = ""

    private var timeString: String {
        let m = Int(recordingTime) / 60
        let s = Int(recordingTime) % 60
        return String(format: "%d:%02d", m, s)
    }

    var body: some View {
        NavigationStack {
            VStack(spacing: 32) {
                Spacer()

                // Recording indicator
                ZStack {
                    Circle()
                        .fill(isRecording ? Color.red.opacity(0.12) : Color(hex: "#0071e3").opacity(0.08))
                        .frame(width: 140, height: 140)
                        .scaleEffect(isRecording ? 1.1 : 1.0)
                        .animation(.easeInOut(duration: 0.8).repeatForever(autoreverses: true), value: isRecording)

                    Button(action: toggleRecording) {
                        ZStack {
                            Circle()
                                .fill(isRecording ? Color.red : Color(hex: "#0071e3"))
                                .frame(width: 80, height: 80)
                            Image(systemName: isRecording ? "stop.fill" : "mic.fill")
                                .font(.system(size: 30))
                                .foregroundStyle(.white)
                        }
                    }
                    .disabled(isUploading)
                }

                VStack(spacing: 8) {
                    Text(isRecording ? timeString : (audioURL != nil ? "Recording ready" : "Tap to record"))
                        .font(.system(size: 18, weight: .semibold))
                    Text(isRecording ? "Recording your voice memo..." : "Record what you learned today")
                        .font(.system(size: 14))
                        .foregroundStyle(.secondary)
                        .multilineTextAlignment(.center)
                }

                if audioURL != nil && !isRecording {
                    VStack(spacing: 12) {
                        TextField("Title (optional)", text: $title)
                            .padding(12)
                            .background(Color(UIColor.secondarySystemGroupedBackground))
                            .clipShape(RoundedRectangle(cornerRadius: 10))

                        Button(action: upload) {
                            HStack(spacing: 8) {
                                if isUploading {
                                    ProgressView().tint(.white)
                                } else {
                                    Image(systemName: "arrow.up.circle.fill")
                                        .font(.system(size: 16))
                                    Text("Save to Jimmy")
                                        .font(.system(size: 16, weight: .semibold))
                                }
                            }
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 14)
                            .background(Color(hex: "#0071e3"))
                            .foregroundStyle(.white)
                            .clipShape(RoundedRectangle(cornerRadius: 14))
                        }
                        .disabled(isUploading)
                    }
                    .padding(.horizontal, 24)
                }

                if let result = result {
                    VStack(alignment: .leading, spacing: 8) {
                        Label(result, systemImage: "checkmark.circle.fill")
                            .font(.system(size: 14))
                            .foregroundStyle(Color.green)
                            .multilineTextAlignment(.center)

                        if !transcriptPreview.isEmpty {
                            Text(transcriptPreview)
                                .font(.system(size: 13))
                                .foregroundStyle(.secondary)
                                .lineSpacing(3)
                                .padding(12)
                                .background(Color(UIColor.secondarySystemGroupedBackground))
                                .clipShape(RoundedRectangle(cornerRadius: 10))
                        }
                    }
                    .padding(.horizontal, 24)
                }
                if let err = errorMsg {
                    Label(err, systemImage: "xmark.circle.fill")
                        .font(.system(size: 14))
                        .foregroundStyle(Color.red)
                        .multilineTextAlignment(.center)
                        .padding(.horizontal, 24)
                }

                Spacer()
            }
            .padding()
            .background(Color(hex: "f5f0e8").ignoresSafeArea())
            .navigationTitle("Voice Memo")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }
                }
            }
        }
        .onDisappear { stopTimer() }
    }

    private func toggleRecording() {
        if isRecording {
            stopRecording()
        } else {
            startRecording()
        }
    }

    private func startRecording() {
        let session = AVAudioSession.sharedInstance()
        session.requestRecordPermission { granted in
            guard granted else { return }
            DispatchQueue.main.async {
                let url = FileManager.default.temporaryDirectory.appendingPathComponent("jimmy_voice_\(Date().timeIntervalSince1970).m4a")
                let settings: [String: Any] = [
                    AVFormatIDKey: Int(kAudioFormatMPEG4AAC),
                    AVSampleRateKey: 44100,
                    AVNumberOfChannelsKey: 1,
                    AVEncoderAudioQualityKey: AVAudioQuality.high.rawValue
                ]
                try? session.setCategory(.record, mode: .default)
                try? session.setActive(true)
                recorder = try? AVAudioRecorder(url: url, settings: settings)
                recorder?.record()
                audioURL = url
                isRecording = true
                recordingTime = 0
                result = nil
                errorMsg = nil
                startTimer()
                UIImpactFeedbackGenerator(style: .medium).impactOccurred()
            }
        }
    }

    private func stopRecording() {
        recorder?.stop()
        isRecording = false
        stopTimer()
        UIImpactFeedbackGenerator(style: .light).impactOccurred()
    }

    private func startTimer() {
        timer = Timer.scheduledTimer(withTimeInterval: 1.0, repeats: true) { _ in
            recordingTime += 1
        }
    }

    private func stopTimer() {
        timer?.invalidate()
        timer = nil
    }

    private func upload() {
        guard let url = audioURL, let data = try? Data(contentsOf: url) else { return }
        isUploading = true
        let memoTitle = title.isEmpty
            ? "Voice Memo — \(Date().formatted(.dateTime.month().day().hour().minute()))"
            : title
        Task {
            do {
                let response = try await api.ingestVoiceAudioWithResponse(
                    audioData: data,
                    filename: "voice_memo.m4a",
                    title: memoTitle
                )
                await MainActor.run {
                    isUploading = false
                    result = "Saved: \"\(response.title ?? memoTitle)\""
                    transcriptPreview = response.transcript ?? response.cleaned_transcript ?? ""
                    audioURL = nil
                    recordingTime = 0
                    self.title = ""
                    settings.totalNotesAdded += 1
                    UINotificationFeedbackGenerator().notificationOccurred(.success)
                }
            } catch {
                await MainActor.run {
                    isUploading = false
                    errorMsg = error.localizedDescription
                    UINotificationFeedbackGenerator().notificationOccurred(.error)
                }
            }
        }
    }
}
