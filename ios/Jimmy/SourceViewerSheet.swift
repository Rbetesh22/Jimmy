import SwiftUI
import UIKit
import SafariServices

// MARK: - Source Viewer Sheet

struct SourceViewerSheet: View {
    let source: SourceChunk
    @Environment(\.dismiss) private var dismiss
    @State private var mode: ViewerMode

    enum ViewerMode { case text, web }

    private var hasURL: Bool { source.url?.isEmpty == false }
    private var isPDF: Bool { source.url?.lowercased().hasSuffix(".pdf") == true }
    private var sourceURL: URL? { source.url.flatMap { URL(string: $0) } }

    init(source: SourceChunk) {
        self.source = source
        // Open the actual file/page immediately if a URL exists
        _mode = State(initialValue: source.url?.isEmpty == false ? .web : .text)
    }

    var body: some View {
        NavigationStack {
            ZStack {
                if mode == .web, let url = sourceURL {
                    SafariView(url: url)
                        .ignoresSafeArea()
                } else {
                    textReader
                }
            }
            .background(Color(UIColor.systemGroupedBackground))
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { toolbarContent }
        }
    }

    // MARK: - Toolbar

    @ToolbarContentBuilder
    private var toolbarContent: some ToolbarContent {
        ToolbarItem(placement: .topBarLeading) {
            Button {
                dismiss()
            } label: {
                Image(systemName: "xmark")
                    .font(.system(size: 13, weight: .bold))
                    .foregroundStyle(Color(UIColor.secondaryLabel))
                    .frame(width: 28, height: 28)
                    .background(Color(UIColor.tertiarySystemFill))
                    .clipShape(Circle())
            }
        }

        ToolbarItem(placement: .principal) {
            VStack(spacing: 1) {
                Text(source.title ?? "Source")
                    .font(.system(size: 14, weight: .semibold))
                    .lineLimit(1)
                if let src = source.source {
                    Text(src.capitalized)
                        .font(.system(size: 11))
                        .foregroundStyle(.tertiary)
                }
            }
        }

        ToolbarItem(placement: .topBarTrailing) {
            HStack(spacing: 4) {
                if mode == .web {
                    Button { mode = .text } label: {
                        Image(systemName: "doc.text")
                            .foregroundStyle(Color(hex: "#0071e3"))
                            .font(.system(size: 15))
                    }
                } else if hasURL {
                    Button { mode = .web } label: {
                        Image(systemName: "safari")
                            .foregroundStyle(Color(hex: "#0071e3"))
                            .font(.system(size: 15))
                    }
                }
                if let url = sourceURL {
                    ShareLink(item: url) {
                        Image(systemName: "square.and.arrow.up")
                            .foregroundStyle(Color(hex: "#0071e3"))
                            .font(.system(size: 15))
                    }
                }
            }
        }
    }

    // MARK: - Text Reader

    private var textReader: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {

                // Source header card
                HStack(spacing: 10) {
                    Text(source.icon ?? sourceEmoji)
                        .font(.system(size: 22))
                    VStack(alignment: .leading, spacing: 3) {
                        if let src = source.source {
                            Text(src.replacingOccurrences(of: "_", with: " ").capitalized)
                                .font(.system(size: 10, weight: .semibold))
                                .foregroundStyle(.tertiary)
                                .tracking(0.5)
                                .textCase(.uppercase)
                        }
                        if let title = source.title {
                            Text(title)
                                .font(.system(size: 15, weight: .semibold))
                                .foregroundStyle(.primary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    Spacer()
                }
                .padding(14)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Color(UIColor.secondarySystemGroupedBackground))
                .clipShape(RoundedRectangle(cornerRadius: 12))

                // Open / download actions
                if let url = sourceURL {
                    Button { mode = .web } label: {
                        HStack(spacing: 8) {
                            Image(systemName: isPDF ? "doc.richtext" : "safari")
                                .font(.system(size: 14))
                            Text(isPDF ? "Open PDF" : "Open in viewer")
                                .font(.system(size: 14, weight: .medium))
                            Spacer()
                            Image(systemName: "chevron.right")
                                .font(.system(size: 12, weight: .semibold))
                                .foregroundStyle(.tertiary)
                        }
                        .foregroundStyle(Color(hex: "#0071e3"))
                        .padding(14)
                        .background(Color(hex: "#0071e3").opacity(0.07))
                        .clipShape(RoundedRectangle(cornerRadius: 10))
                        .overlay(
                            RoundedRectangle(cornerRadius: 10)
                                .stroke(Color(hex: "#0071e3").opacity(0.18), lineWidth: 1)
                        )
                    }
                    .buttonStyle(.plain)

                    // Also offer to download/share the raw URL
                    ShareLink(item: url) {
                        HStack(spacing: 8) {
                            Image(systemName: "arrow.down.circle")
                                .font(.system(size: 14))
                            Text("Save / Share file")
                                .font(.system(size: 14, weight: .medium))
                            Spacer()
                        }
                        .foregroundStyle(.secondary)
                        .padding(14)
                        .background(Color(UIColor.secondarySystemGroupedBackground))
                        .clipShape(RoundedRectangle(cornerRadius: 10))
                    }
                    .buttonStyle(.plain)
                }

                // Only show extracted text for sources without a URL (notes, voice memos, etc.)
                if !hasURL {
                    Divider()
                    if let text = source.full_text ?? source.excerpt, !text.isEmpty {
                        Text(renderMarkdown(text))
                            .font(.system(size: 15))
                            .foregroundStyle(.primary)
                            .lineSpacing(5)
                            .textSelection(.enabled)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    } else {
                        Text("No preview available.")
                            .font(.system(size: 14))
                            .foregroundStyle(.tertiary)
                            .frame(maxWidth: .infinity)
                            .padding(.top, 32)
                    }
                }
            }
            .padding(16)
            .padding(.bottom, 32)
        }
    }

    private var sourceEmoji: String {
        switch source.source?.lowercased() {
        case "canvas":       return "🎓"
        case "gmail":        return "✉️"
        case "note":         return "📝"
        case "apple_notes":  return "📓"
        case "notion":       return "🗒️"
        case "github":       return "💻"
        case "youtube":      return "📺"
        case "readwise":     return "📖"
        case "granola":      return "🎙️"
        case "url":          return "🌐"
        default:             return "📄"
        }
    }
}

// MARK: - Safari View (SFSafariViewController wrapper)

struct SafariView: UIViewControllerRepresentable {
    let url: URL

    func makeUIViewController(context: Context) -> SFSafariViewController {
        let cfg = SFSafariViewController.Configuration()
        cfg.entersReaderIfAvailable = false
        cfg.barCollapsingEnabled = true
        let vc = SFSafariViewController(url: url, configuration: cfg)
        vc.preferredBarTintColor = .systemBackground
        vc.preferredControlTintColor = UIColor(red: 0, green: 113/255, blue: 227/255, alpha: 1)
        return vc
    }

    func updateUIViewController(_ vc: SFSafariViewController, context: Context) {}
}

