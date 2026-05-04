"""Google Calendar ingester.

Fetches events from all your calendars (multiple accounts supported).
Stores events as structured text chunks with attendees, dates, and descriptions.
"""
from datetime import datetime, timezone, timedelta
from .base import Document


def _strip_html(html: str) -> str:
    import re
    text = re.sub(r"<[^>]+>", " ", html or "")
    return re.sub(r"\s+", " ", text).strip()


def _format_attendees(attendees: list[dict]) -> str:
    names = []
    for a in attendees or []:
        name = a.get("displayName") or a.get("email", "")
        if name:
            names.append(name)
    return ", ".join(names[:10]) if names else ""


class GoogleCalendarIngester:
    def __init__(self, credentials, account_label: str = "primary"):
        from googleapiclient.discovery import build
        self.service = build("calendar", "v3", credentials=credentials, cache_discovery=False)
        self.account_label = account_label

    def ingest(self, days_past: int = 180, days_future: int = 90) -> list[Document]:
        now = datetime.now(timezone.utc)
        time_min = (now - timedelta(days=days_past)).isoformat()
        time_max = (now + timedelta(days=days_future)).isoformat()

        docs = []
        calendars = self._list_calendars()
        print(f"  [{self.account_label}] {len(calendars)} calendars")

        for cal in calendars:
            cal_id = cal["id"]
            cal_name = cal.get("summary", cal_id)
            try:
                events = self._fetch_events(cal_id, time_min, time_max)
                for event in events:
                    doc = self._event_to_doc(event, cal_name)
                    if doc:
                        docs.append(doc)
            except Exception as e:
                print(f"    Calendar '{cal_name}': {e}")

        return docs

    def _list_calendars(self) -> list[dict]:
        result = self.service.calendarList().list().execute()
        return result.get("items", [])

    def _fetch_events(self, cal_id: str, time_min: str, time_max: str) -> list[dict]:
        events = []
        page_token = None
        while True:
            resp = self.service.events().list(
                calendarId=cal_id,
                timeMin=time_min,
                timeMax=time_max,
                singleEvents=True,
                orderBy="startTime",
                maxResults=500,
                pageToken=page_token,
            ).execute()
            events.extend(resp.get("items", []))
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return events

    def _event_to_doc(self, event: dict, cal_name: str) -> Document | None:
        title = event.get("summary", "Untitled Event")
        status = event.get("status", "")
        if status == "cancelled":
            return None

        start = event.get("start", {})
        end   = event.get("end", {})
        date_str = start.get("dateTime", start.get("date", ""))[:10]
        start_time = start.get("dateTime", start.get("date", ""))
        end_time   = end.get("dateTime",   end.get("date", ""))

        description = _strip_html(event.get("description", "") or "")
        location = event.get("location", "")
        attendees = _format_attendees(event.get("attendees", []))
        organizer = (event.get("organizer") or {}).get("displayName") or \
                    (event.get("organizer") or {}).get("email", "")
        recurrence = "recurring" if event.get("recurringEventId") else "one-time"
        html_link = event.get("htmlLink", "")

        parts = [f"Event: {title}", f"Date: {start_time} → {end_time}"]
        if location:
            parts.append(f"Location: {location}")
        if attendees:
            parts.append(f"Attendees: {attendees}")
        if organizer:
            parts.append(f"Organizer: {organizer}")
        if description:
            parts.append(f"Description: {description[:800]}")
        parts.append(f"Calendar: {cal_name} | Type: {recurrence}")

        content = "\n".join(parts)
        if len(content) < 30:
            return None

        uid = event.get("iCalUID") or event.get("id", "")
        return Document(
            id=f"gcal_{uid}",
            content=content,
            source="calendar",
            title=f"{title} ({date_str})",
            metadata={
                "type": "event",
                "date": date_str,
                "calendar": cal_name,
                "account": self.account_label,
                "url": html_link,
                "recurrence": recurrence,
            },
        )
