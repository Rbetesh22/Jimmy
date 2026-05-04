"""Whoop health data ingester — pulls recovery, sleep, strain, and workouts via API."""
import json
import threading
import webbrowser
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from .base import Document, _h
from ..config import JIMMY_DATA_DIR

TOKEN_PATH = JIMMY_DATA_DIR / "whoop_token.json"
AUTH_URL   = "https://api.prod.whoop.com/oauth/oauth2/auth"
TOKEN_URL  = "https://api.prod.whoop.com/oauth/oauth2/token"
API_BASE   = "https://api.prod.whoop.com/developer/v1"
REDIRECT   = "http://localhost:8089/callback"
SCOPES     = "offline read:recovery read:cycles read:sleep read:workout read:profile read:body_measurement"


# ── OAuth2 helpers ───────────────────────────────────────────────────────────

def _save_token(data: dict):
    TOKEN_PATH.parent.mkdir(exist_ok=True)
    TOKEN_PATH.write_text(json.dumps(data))


def _load_token() -> dict | None:
    if TOKEN_PATH.exists():
        try:
            return json.loads(TOKEN_PATH.read_text())
        except Exception:
            pass
    return None


def _refresh_token(client_id: str, client_secret: str, tok: dict) -> dict:
    resp = httpx.post(TOKEN_URL, data={
        "grant_type": "refresh_token",
        "refresh_token": tok["refresh_token"],
        "client_id": client_id,
        "client_secret": client_secret,
    })
    resp.raise_for_status()
    new_tok = resp.json()
    if "refresh_token" not in new_tok:
        new_tok["refresh_token"] = tok["refresh_token"]
    _save_token(new_tok)
    return new_tok


def _auth_flow(client_id: str, client_secret: str) -> dict:
    """Interactive OAuth2 authorization code flow — opens browser, captures callback."""
    auth_params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": REDIRECT,
        "scope": SCOPES,
    }
    auth_url = AUTH_URL + "?" + urlencode(auth_params)

    code_holder: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            params = parse_qs(urlparse(self.path).query)
            code = params.get("code", [""])[0]
            if code:
                code_holder.append(code)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"<h2>Whoop connected! You can close this tab.</h2>")
            else:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"<h2>Authorization failed.</h2>")

        def log_message(self, *_):
            pass

    server = HTTPServer(("localhost", 8089), Handler)
    print(f"\nOpening Whoop authorization in your browser…")
    print(f"If it doesn't open, visit:\n  {auth_url}\n")
    webbrowser.open(auth_url)

    while not code_holder:
        server.handle_request()

    server.server_close()
    code = code_holder[0]

    resp = httpx.post(TOKEN_URL, data={
        "grant_type": "authorization_code",
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": REDIRECT,
    })
    resp.raise_for_status()
    tok = resp.json()
    _save_token(tok)
    return tok


# ── API client ───────────────────────────────────────────────────────────────

class WhoopClient:
    def __init__(self, client_id: str, client_secret: str):
        self.client_id = client_id
        self.client_secret = client_secret
        tok = _load_token()
        if not tok:
            tok = _auth_flow(client_id, client_secret)
        else:
            # Refresh if expired (Whoop tokens last ~1 hour)
            try:
                tok = _refresh_token(client_id, client_secret, tok)
            except Exception:
                tok = _auth_flow(client_id, client_secret)
        self._token = tok

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token['access_token']}"}

    def _get_all(self, endpoint: str, params: dict | None = None) -> list[dict]:
        """Paginate through all results from a Whoop list endpoint."""
        url = f"{API_BASE}{endpoint}"
        results = []
        p = dict(params or {})
        p["limit"] = 25

        with httpx.Client(timeout=20) as client:
            while url:
                resp = client.get(url, headers=self._headers(), params=p)
                if resp.status_code == 401:
                    self._token = _refresh_token(self.client_id, self.client_secret, self._token)
                    resp = client.get(url, headers=self._headers(), params=p)
                resp.raise_for_status()
                data = resp.json()
                records = data.get("records", [])
                results.extend(records)
                next_token = data.get("next_token")
                if not next_token or not records:
                    break
                p["nextToken"] = next_token
                url = f"{API_BASE}{endpoint}"

        return results

    def profile(self) -> dict:
        with httpx.Client(timeout=10) as c:
            r = c.get(f"{API_BASE}/user/profile/basic", headers=self._headers())
            r.raise_for_status()
            return r.json()

    def cycles(self, days: int = 90) -> list[dict]:
        start = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        return self._get_all("/cycle", {"start": start})

    def recoveries(self, days: int = 90) -> list[dict]:
        start = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        return self._get_all("/recovery", {"start": start})

    def sleeps(self, days: int = 90) -> list[dict]:
        start = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        return self._get_all("/activity/sleep", {"start": start})

    def workouts(self, days: int = 90) -> list[dict]:
        start = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        return self._get_all("/activity/workout", {"start": start})


# ── Formatting helpers ───────────────────────────────────────────────────────

def _fmt_min(ms: int | None) -> str:
    if not ms:
        return "unknown"
    mins = int(ms / 1000 / 60)
    h, m = divmod(mins, 60)
    return f"{h}h {m}m" if h else f"{m}m"


def _date(iso: str | None) -> str:
    if not iso:
        return ""
    return iso[:10]


SPORT_NAMES = {
    0: "Running", 1: "Cycling", 16: "Baseball", 17: "Basketball", 18: "Rowing",
    19: "Fencing", 20: "Field Hockey", 21: "Football", 22: "Golf", 24: "Ice Hockey",
    25: "Lacrosse", 27: "Rugby", 28: "Sailing", 29: "Skiing", 30: "Soccer",
    31: "Softball", 32: "Squash", 33: "Swimming", 34: "Tennis", 35: "Track & Field",
    36: "Volleyball", 37: "Water Polo", 38: "Wrestling", 39: "Boxing", 42: "Dance",
    43: "Pilates", 44: "Yoga", 45: "Weightlifting", 47: "Cross Country Skiing",
    48: "Functional Fitness", 49: "Duathlon", 51: "Gymnastics", 52: "Hiking",
    53: "Horseback Riding", 55: "Kayaking", 56: "Martial Arts", 57: "Mountain Biking",
    59: "Powerlifting", 60: "Rock Climbing", 61: "Paddleboarding", 62: "Triathlon",
    63: "Walking", 64: "Surfing", 65: "Elliptical", 66: "Stairmaster",
    70: "Meditation", 71: "Other", 126: "HIIT", 127: "Strength Training",
    230: "Pickleball", 231: "Racquetball",
}


# ── Ingester ─────────────────────────────────────────────────────────────────

class WhoopIngester:
    def __init__(self, client_id: str, client_secret: str):
        self.client = WhoopClient(client_id, client_secret)

    def ingest(self, days: int = 90) -> list[Document]:
        docs = []
        print("Fetching Whoop recovery data…")
        docs.extend(self._ingest_daily_summary(days))
        print("Fetching Whoop workouts…")
        docs.extend(self._ingest_workouts(days))
        print("Fetching Whoop sleep data…")
        docs.extend(self._ingest_sleep(days))
        print("Building Whoop trends summary…")
        docs.extend(self._ingest_trends(days))
        return docs

    def _ingest_daily_summary(self, days: int) -> list[Document]:
        """One document per day combining cycle + recovery data."""
        try:
            cycles = self.client.cycles(days)
            recoveries = {_date(r.get("created_at")): r for r in self.client.recoveries(days)}
        except Exception as e:
            print(f"  Warning: {e}")
            return []

        docs = []
        for cycle in cycles:
            score = cycle.get("score") or {}
            date = _date(cycle.get("start"))
            if not date:
                continue

            strain = score.get("strain")
            kilojoules = score.get("kilojoule")
            avg_hr = score.get("average_heart_rate")
            max_hr = score.get("max_heart_rate")

            rec = recoveries.get(date, {})
            rec_score_obj = rec.get("score") or {}
            recovery_score = rec_score_obj.get("recovery_score")
            hrv = rec_score_obj.get("hrv_rmssd_milli")
            rhr = rec_score_obj.get("resting_heart_rate")
            spo2 = rec_score_obj.get("spo2_percentage")
            skin_temp = rec_score_obj.get("skin_temp_celsius")
            sleep_perf = rec_score_obj.get("sleep_performance_percentage")

            lines = [f"Whoop Daily Summary — {date}"]
            if recovery_score is not None:
                status = "green" if recovery_score >= 67 else "yellow" if recovery_score >= 34 else "red"
                lines.append(f"Recovery: {int(recovery_score)}% ({status})")
            if hrv is not None:
                lines.append(f"HRV: {hrv:.1f} ms")
            if rhr is not None:
                lines.append(f"Resting HR: {int(rhr)} bpm")
            if strain is not None:
                lines.append(f"Day Strain: {strain:.1f}/21")
            if sleep_perf is not None:
                lines.append(f"Sleep Performance: {int(sleep_perf)}%")
            if avg_hr is not None:
                lines.append(f"Avg HR: {int(avg_hr)} bpm")
            if max_hr is not None:
                lines.append(f"Max HR: {int(max_hr)} bpm")
            if kilojoules is not None:
                lines.append(f"Energy Burned: {int(kilojoules)} kJ ({int(kilojoules * 0.239):.0f} kcal)")
            if spo2 is not None:
                lines.append(f"SpO2: {spo2:.1f}%")
            if skin_temp is not None:
                lines.append(f"Skin Temp: {skin_temp:.1f}°C")

            content = "\n".join(lines)
            if len(content) < 60:
                continue

            docs.append(Document(
                id=f"whoop_day_{date}",
                content=content,
                source="whoop",
                title=f"Whoop Day: {date}",
                metadata={
                    "date": date,
                    "type": "health_daily",
                    "recovery_score": recovery_score,
                    "hrv": hrv,
                    "resting_hr": rhr,
                    "strain": strain,
                },
            ))
        return docs

    def _ingest_workouts(self, days: int) -> list[Document]:
        try:
            workouts = self.client.workouts(days)
        except Exception:
            return []

        docs = []
        for w in workouts:
            score = w.get("score") or {}
            date = _date(w.get("start"))
            sport_id = w.get("sport_id", 71)
            sport = SPORT_NAMES.get(sport_id, f"Sport {sport_id}")
            strain = score.get("strain")
            avg_hr = score.get("average_heart_rate")
            max_hr = score.get("max_heart_rate")
            kilojoules = score.get("kilojoule")
            dur_ms = None
            if w.get("start") and w.get("end"):
                try:
                    s = datetime.fromisoformat(w["start"].replace("Z", "+00:00"))
                    e = datetime.fromisoformat(w["end"].replace("Z", "+00:00"))
                    dur_ms = int((e - s).total_seconds() * 1000)
                except Exception:
                    pass

            # Heart rate zones (percentages of time)
            zones = score.get("zone_duration") or {}
            zone_parts = []
            for z_name, z_key in [("Zone 1", "zone_zero_milli"), ("Zone 2", "zone_one_milli"),
                                    ("Zone 3", "zone_two_milli"), ("Zone 4", "zone_three_milli"),
                                    ("Zone 5", "zone_four_milli"), ("Zone 6", "zone_five_milli")]:
                z_ms = zones.get(z_key)
                if z_ms and dur_ms:
                    pct = int(z_ms / dur_ms * 100)
                    if pct > 2:
                        zone_parts.append(f"{z_name}: {pct}%")

            lines = [f"Whoop Workout — {sport} on {date}"]
            if dur_ms:
                lines.append(f"Duration: {_fmt_min(dur_ms)}")
            if strain is not None:
                lines.append(f"Strain: {strain:.1f}/21")
            if avg_hr is not None:
                lines.append(f"Avg HR: {int(avg_hr)} bpm")
            if max_hr is not None:
                lines.append(f"Max HR: {int(max_hr)} bpm")
            if kilojoules is not None:
                lines.append(f"Energy: {int(kilojoules)} kJ ({int(kilojoules * 0.239):.0f} kcal)")
            if zone_parts:
                lines.append("HR Zones: " + ", ".join(zone_parts))

            content = "\n".join(lines)
            uid = _h(f"{date}_{sport}_{strain}")
            docs.append(Document(
                id=f"whoop_workout_{uid}",
                content=content,
                source="whoop",
                title=f"Whoop Workout: {sport} ({date})",
                metadata={
                    "date": date,
                    "type": "workout",
                    "sport": sport,
                    "strain": strain,
                    "duration_min": int(dur_ms / 60000) if dur_ms else None,
                },
            ))
        return docs

    def _ingest_sleep(self, days: int) -> list[Document]:
        try:
            sleeps = self.client.sleeps(days)
        except Exception:
            return []

        docs = []
        for s in sleeps:
            score = s.get("score") or {}
            date = _date(s.get("start"))
            stages = score.get("stage_summary") or {}
            total_ms = stages.get("total_in_bed_time_milli")
            awake_ms = stages.get("total_awake_time_milli")
            light_ms = stages.get("total_light_sleep_time_milli")
            slow_ms  = stages.get("total_slow_wave_sleep_time_milli")
            rem_ms   = stages.get("total_rem_sleep_time_milli")
            disturbances = stages.get("disturbance_count")
            efficiency = score.get("sleep_efficiency_percentage")
            latency = score.get("sleep_latency")
            consistency = score.get("sleep_consistency_percentage")
            respiratory_rate = score.get("respiratory_rate")
            perf = score.get("sleep_performance_percentage")

            lines = [f"Whoop Sleep — {date}"]
            if total_ms:
                lines.append(f"Total in bed: {_fmt_min(total_ms)}")
            if awake_ms and total_ms:
                sleep_ms = total_ms - awake_ms
                lines.append(f"Actual sleep: {_fmt_min(sleep_ms)}")
            if light_ms:
                lines.append(f"Light sleep: {_fmt_min(light_ms)}")
            if slow_ms:
                lines.append(f"Slow wave (deep): {_fmt_min(slow_ms)}")
            if rem_ms:
                lines.append(f"REM: {_fmt_min(rem_ms)}")
            if efficiency is not None:
                lines.append(f"Sleep efficiency: {efficiency:.1f}%")
            if perf is not None:
                lines.append(f"Sleep performance: {int(perf)}%")
            if disturbances is not None:
                lines.append(f"Disturbances: {disturbances}")
            if latency is not None:
                lines.append(f"Time to sleep: {_fmt_min(latency)}")
            if consistency is not None:
                lines.append(f"Sleep consistency: {consistency:.1f}%")
            if respiratory_rate is not None:
                lines.append(f"Respiratory rate: {respiratory_rate:.1f} breaths/min")

            content = "\n".join(lines)
            uid = _h(f"sleep_{date}")
            docs.append(Document(
                id=f"whoop_sleep_{uid}",
                content=content,
                source="whoop",
                title=f"Whoop Sleep: {date}",
                metadata={"date": date, "type": "sleep", "efficiency": efficiency, "performance": perf},
            ))
        return docs

    def _ingest_trends(self, days: int) -> list[Document]:
        """Create a rolling summary document covering trends across the period."""
        try:
            recoveries = self.client.recoveries(days)
        except Exception:
            return []

        if not recoveries:
            return []

        scores = [r.get("score") or {} for r in recoveries]
        rec_scores  = [s["recovery_score"] for s in scores if s.get("recovery_score") is not None]
        hrv_vals    = [s["hrv_rmssd_milli"] for s in scores if s.get("hrv_rmssd_milli") is not None]
        rhr_vals    = [s["resting_heart_rate"] for s in scores if s.get("resting_heart_rate") is not None]
        sleep_perfs = [s["sleep_performance_percentage"] for s in scores if s.get("sleep_performance_percentage") is not None]

        def _avg(lst): return sum(lst) / len(lst) if lst else None
        def _pct_green(lst): return int(sum(1 for x in lst if x >= 67) / len(lst) * 100) if lst else None

        avg_rec = _avg(rec_scores)
        pct_green = _pct_green(rec_scores)
        avg_hrv = _avg(hrv_vals)
        avg_rhr = _avg(rhr_vals)
        avg_sleep_perf = _avg(sleep_perfs)

        period = f"last {days} days"
        lines = [f"Whoop Health Trends — {period}"]
        lines.append(f"Period: {days} days of data ({len(recoveries)} recovery records)")
        if avg_rec is not None:
            lines.append(f"Average recovery: {avg_rec:.0f}%")
        if pct_green is not None:
            lines.append(f"Green recovery days: {pct_green}% of days")
        if avg_hrv is not None:
            lines.append(f"Average HRV: {avg_hrv:.1f} ms")
        if avg_rhr is not None:
            lines.append(f"Average resting HR: {avg_rhr:.0f} bpm")
        if avg_sleep_perf is not None:
            lines.append(f"Average sleep performance: {avg_sleep_perf:.0f}%")

        # Recent week vs full period comparison
        if len(rec_scores) >= 7:
            recent7 = rec_scores[-7:]
            all_avg = _avg(rec_scores[:-7]) if len(rec_scores) > 7 else None
            lines.append(f"\nLast 7 days average recovery: {_avg(recent7):.0f}%")
            if all_avg is not None:
                diff = _avg(recent7) - all_avg
                trend = "up" if diff > 3 else "down" if diff < -3 else "stable"
                lines.append(f"Trend vs prior period: {trend} ({diff:+.0f}%)")

        content = "\n".join(lines)
        return [Document(
            id=f"whoop_trends_{days}d",
            content=content,
            source="whoop",
            title=f"Whoop Health Trends ({days} days)",
            metadata={"type": "health_trends", "days": days, "avg_recovery": avg_rec},
        )]
