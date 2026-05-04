# iOS Shortcut Setup for Jimmy

Two shortcuts are described below. Both require your Mac running the Jimmy server
to be on the same Wi-Fi network as your iPhone. Find your Mac's local IP address
with: `ipconfig getifaddr en0` (typically something like 192.168.1.X).

Replace `YOUR_MAC_IP` with that address in all URLs below.

---

## Shortcut 1: Capture Current Safari URL

Saves the URL of the page you are currently viewing in Safari to Jimmy.

### Steps (in the Shortcuts app)

1. Open the **Shortcuts** app on iOS.
2. Tap **+** to create a new shortcut. Name it "Capture to Jimmy".
3. Add action: **Get Current URL** (from the Safari Web group).
4. Add action: **Get Contents of URL**
   - URL: `http://YOUR_MAC_IP:7700/ingest/url`
   - Method: **POST**
   - Headers: `Content-Type` = `application/json`
   - Request Body: **JSON**
     - Key: `url` / Value: **Current URL** (tap the variable picker)
5. Add action: **Show Notification**
   - Title: `Jimmy`
   - Body: `Captured!`
6. Tap **Done**.

### Add to Share Sheet (optional)

In the shortcut's settings (tap the info icon), enable **Show in Share Sheet**.
This lets you share any URL from any app directly to Jimmy.

---

## Shortcut 2: Share Any URL (Share Sheet)

Works from the iOS Share Sheet in any app (Safari, Chrome, Reeder, etc.).

### Steps

1. Create a new shortcut named "Save to Jimmy".
2. Add action: **Receive** — set input type to **URLs**, check "Show in Share Sheet".
3. Add action: **Get Contents of URL**
   - URL: `http://YOUR_MAC_IP:7700/ingest/url`
   - Method: **POST**
   - Headers: `Content-Type` = `application/json`
   - Request Body: **JSON**
     - Key: `url` / Value: **Shortcut Input** (tap variable picker)
4. Add action: **Show Notification**
   - Title: `Jimmy`
   - Body: `Saved!`
5. Tap **Done**.

---

## Shortcut 3: Voice Memo (Record + Send)

Records a voice memo and sends it to Jimmy for transcription and ingestion.

### Steps

1. Create a new shortcut named "Jimmy Voice Memo".
2. Add action: **Record Audio** — saves recording to variable `Recording`.
3. Add action: **Get Contents of URL**
   - URL: `http://YOUR_MAC_IP:7700/ingest/voice`
   - Method: **POST**
   - Headers: leave empty (multipart is set automatically)
   - Request Body: **Form**
     - Key: `file` / Value: **Recording** (tap variable picker)
     - Key: `title` / Value: (leave empty for auto-generated title)
4. Add action: **Get Value for Key** from the previous result
   - Key: `title`
5. Add action: **Show Notification**
   - Title: `Jimmy — Voice Memo Saved`
   - Body: the value from step 4.
6. Tap **Done**.

### Add to Home Screen

In the shortcut's settings, tap "Add to Home Screen" for one-tap recording.

---

## Finding Your Mac's IP

Run in Terminal on your Mac:

```
ipconfig getifaddr en0
```

Or go to System Settings > Wi-Fi > Details > IP Address.

If the IP changes (DHCP), consider setting a static IP or using your Mac's
Bonjour hostname: `YOUR_MACBOOK_NAME.local` (e.g., `ralphs-macbook.local`).
Test with: `ping ralphs-macbook.local`

---

## Bookmarklet (Browser)

Drag this link to your bookmarks bar. Clicking it on any page sends the URL to Jimmy.

```
javascript:(function(){fetch('http://YOUR_MAC_IP:7700/ingest/url',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url:location.href,title:document.title})}).then(r=>r.json()).then(()=>{const n=document.createElement('div');n.style.cssText='position:fixed;top:20px;right:20px;background:#2563eb;color:white;padding:12px 18px;border-radius:8px;font-family:system-ui;font-size:14px;z-index:999999;box-shadow:0 4px 12px rgba(0,0,0,0.15)';n.textContent='Saved to Jimmy';document.body.appendChild(n);setTimeout(()=>n.remove(),2500);});})();
```

Replace `YOUR_MAC_IP` with your Mac's actual local IP before saving.
