"""SQLite-backed contacts database for Jimmy CRM."""
import sqlite3
import json
import hashlib
from pathlib import Path
from datetime import date, datetime, timedelta
from typing import Optional

from ..config import JIMMY_DATA_DIR

DB_PATH = JIMMY_DATA_DIR / "contacts.db"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """Create tables if they don't exist."""
    conn = _conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS contacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            first_name TEXT NOT NULL,
            last_name TEXT NOT NULL DEFAULT '',
            preferred_name TEXT DEFAULT '',
            company TEXT DEFAULT '',
            role TEXT DEFAULT '',
            industry TEXT DEFAULT '',
            sub_industry TEXT DEFAULT '',
            seniority TEXT DEFAULT '',
            city TEXT DEFAULT '',
            region TEXT DEFAULT '',
            email TEXT DEFAULT '',
            phone TEXT DEFAULT '',
            linkedin_url TEXT DEFAULT '',
            closeness INTEGER DEFAULT 0 CHECK(closeness BETWEEN 0 AND 5),
            starred INTEGER DEFAULT 0,
            warmth_last_contact TEXT DEFAULT '',
            reachability TEXT DEFAULT '',
            tier TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            source TEXT DEFAULT 'manual',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS affiliations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL
        );

        CREATE TABLE IF NOT EXISTS contact_affiliations (
            contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
            affiliation_id INTEGER NOT NULL REFERENCES affiliations(id) ON DELETE CASCADE,
            PRIMARY KEY (contact_id, affiliation_id)
        );

        CREATE INDEX IF NOT EXISTS idx_contacts_name ON contacts(last_name, first_name);
        CREATE INDEX IF NOT EXISTS idx_contacts_company ON contacts(company);
        CREATE INDEX IF NOT EXISTS idx_contacts_region ON contacts(region);
        CREATE INDEX IF NOT EXISTS idx_contacts_closeness ON contacts(closeness);
        CREATE INDEX IF NOT EXISTS idx_contacts_source ON contacts(source);

        CREATE TABLE IF NOT EXISTS interactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
            type TEXT NOT NULL DEFAULT 'note',
            body TEXT DEFAULT '',
            interaction_date TEXT NOT NULL DEFAULT (date('now')),
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_interactions_contact ON interactions(contact_id, interaction_date DESC);
    """)
    # Add starred column if missing
    try:
        conn.execute("ALTER TABLE contacts ADD COLUMN starred INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass  # already exists
    # Seed common affiliations
    SEED = [
        "Columbia", "FIJI", "Flatbush", "Yavneh", "Chabad", "TAU",
        "Datadog", "Cypris", "Securent", "family",
        "Syrian Jewish", "Persian Jewish", "Ashkenazi",
        "NYC", "Tri-State", "Israel",
    ]
    for a in SEED:
        conn.execute("INSERT OR IGNORE INTO affiliations(name) VALUES(?)", (a,))
    conn.commit()
    conn.close()


def _ensure_affiliation(conn: sqlite3.Connection, name: str) -> int:
    """Get or create affiliation, return id."""
    name = name.strip()
    row = conn.execute("SELECT id FROM affiliations WHERE name=? COLLATE NOCASE", (name,)).fetchone()
    if row:
        return row["id"]
    cur = conn.execute("INSERT INTO affiliations(name) VALUES(?)", (name,))
    return cur.lastrowid


def add_contact(data: dict) -> int:
    """Insert contact + affiliations. Returns contact id."""
    conn = _conn()
    affiliations = data.pop("affiliations", [])
    cols = [k for k in data if k != "id"]
    vals = [data[k] for k in cols]
    placeholders = ",".join(["?"] * len(cols))
    col_str = ",".join(cols)
    cur = conn.execute(f"INSERT INTO contacts({col_str}) VALUES({placeholders})", vals)
    cid = cur.lastrowid
    for aff in affiliations:
        aid = _ensure_affiliation(conn, aff)
        conn.execute("INSERT OR IGNORE INTO contact_affiliations(contact_id,affiliation_id) VALUES(?,?)", (cid, aid))
    conn.commit()
    conn.close()
    return cid


def update_contact(contact_id: int, data: dict):
    """Update contact fields + affiliations."""
    conn = _conn()
    affiliations = data.pop("affiliations", None)
    if data:
        data["updated_at"] = datetime.now().isoformat()
        sets = ",".join(f"{k}=?" for k in data)
        vals = list(data.values()) + [contact_id]
        conn.execute(f"UPDATE contacts SET {sets} WHERE id=?", vals)
    if affiliations is not None:
        conn.execute("DELETE FROM contact_affiliations WHERE contact_id=?", (contact_id,))
        for aff in affiliations:
            aid = _ensure_affiliation(conn, aff)
            conn.execute("INSERT OR IGNORE INTO contact_affiliations(contact_id,affiliation_id) VALUES(?,?)", (contact_id, aid))
    conn.commit()
    conn.close()


def delete_contact(contact_id: int):
    conn = _conn()
    conn.execute("DELETE FROM contacts WHERE id=?", (contact_id,))
    conn.commit()
    conn.close()


def get_contact(contact_id: int) -> Optional[dict]:
    conn = _conn()
    row = conn.execute("SELECT * FROM contacts WHERE id=?", (contact_id,)).fetchone()
    if not row:
        conn.close()
        return None
    c = dict(row)
    c["affiliations"] = _get_affiliations(conn, contact_id)
    conn.close()
    return c


def _get_affiliations(conn: sqlite3.Connection, contact_id: int) -> list[str]:
    rows = conn.execute(
        "SELECT a.name FROM affiliations a "
        "JOIN contact_affiliations ca ON ca.affiliation_id=a.id "
        "WHERE ca.contact_id=?", (contact_id,)
    ).fetchall()
    return [r["name"] for r in rows]


def query_contacts(
    q: str = "",
    affiliations: list[str] | None = None,
    region: str = "",
    city: str = "",
    industry: str = "",
    company: str = "",
    min_closeness: int = 0,
    max_closeness: int = 5,
    tier: str = "",
    source: str = "",
    stale_days: int = 0,
    starred: int = -1,
    missing_info: bool = False,
    never_contacted: bool = False,
    sort: str = "closeness",
    limit: int = 200,
) -> list[dict]:
    """Multi-filter contact query. All filters are AND'd."""
    conn = _conn()
    where = ["1=1"]
    params: list = []

    if q:
        where.append("(first_name LIKE ? OR last_name LIKE ? OR preferred_name LIKE ? OR company LIKE ? OR notes LIKE ?)")
        pat = f"%{q}%"
        params.extend([pat] * 5)
    if region:
        where.append("region LIKE ?")
        params.append(f"%{region}%")
    if city:
        where.append("city LIKE ?")
        params.append(f"%{city}%")
    if industry:
        where.append("industry LIKE ?")
        params.append(f"%{industry}%")
    if company:
        where.append("company LIKE ?")
        params.append(f"%{company}%")
    if min_closeness > 0:
        where.append("closeness >= ?")
        params.append(min_closeness)
    if max_closeness < 5:
        where.append("closeness <= ?")
        params.append(max_closeness)
    if tier:
        where.append("tier = ?")
        params.append(tier)
    if source:
        where.append("source = ?")
        params.append(source)
    if stale_days > 0:
        where.append("(warmth_last_contact = '' OR warmth_last_contact < date('now', ?))")
        params.append(f"-{stale_days} days")
    if starred >= 0:
        where.append("starred = ?")
        params.append(starred)
    if missing_info:
        where.append("(email = '' OR email IS NULL) AND (linkedin_url = '' OR linkedin_url IS NULL)")
    if never_contacted:
        where.append("(warmth_last_contact = '' OR warmth_last_contact IS NULL)")

    # Affiliation filter: contact must have ALL specified affiliations
    aff_params = []
    aff_join = ""
    if affiliations:
        for i, aff in enumerate(affiliations):
            alias = f"ca{i}"
            aff_join += (
                f" JOIN contact_affiliations {alias} ON {alias}.contact_id=c.id "
                f"JOIN affiliations a{i} ON a{i}.id={alias}.affiliation_id AND a{i}.name=? COLLATE NOCASE "
            )
            aff_params.append(aff)

    order_map = {
        "closeness": "closeness DESC, last_name, first_name",
        "name": "last_name, first_name",
        "last_contact": "CASE WHEN warmth_last_contact='' THEN '0000' ELSE warmth_last_contact END DESC",
        "recent": "c.created_at DESC",
        "staleness": "CASE WHEN warmth_last_contact='' THEN '0000' ELSE warmth_last_contact END ASC",
    }
    order = order_map.get(sort, order_map["closeness"])
    sql = f"SELECT DISTINCT c.* FROM contacts c {aff_join} WHERE {' AND '.join(where)} ORDER BY {order} LIMIT ?"
    all_params = aff_params + params + [limit]

    rows = conn.execute(sql, all_params).fetchall()
    results = []
    for row in rows:
        c = dict(row)
        c["affiliations"] = _get_affiliations(conn, c["id"])
        results.append(c)
    conn.close()
    return results


def list_affiliations() -> list[str]:
    conn = _conn()
    rows = conn.execute("SELECT name FROM affiliations ORDER BY name").fetchall()
    conn.close()
    return [r["name"] for r in rows]


def contact_stats() -> dict:
    conn = _conn()
    total = conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
    by_source = {r[0]: r[1] for r in conn.execute("SELECT source, COUNT(*) FROM contacts GROUP BY source").fetchall()}
    by_region = {r[0]: r[1] for r in conn.execute("SELECT region, COUNT(*) FROM contacts WHERE region!='' GROUP BY region").fetchall()}
    avg_closeness = conn.execute("SELECT AVG(closeness) FROM contacts WHERE closeness>0").fetchone()[0] or 0
    stale = conn.execute(
        "SELECT COUNT(*) FROM contacts WHERE warmth_last_contact!='' AND warmth_last_contact < date('now','-180 days')"
    ).fetchone()[0]
    conn.close()
    return {
        "total": total,
        "by_source": by_source,
        "by_region": by_region,
        "avg_closeness": round(avg_closeness, 1),
        "stale_6mo": stale,
    }


def recalc_closeness():
    """Recalculate closeness scores based on real signals, not bulk assignment."""
    conn = _conn()
    contacts = conn.execute("SELECT id, first_name, last_name, email, phone, linkedin_url, company, role, source, notes, warmth_last_contact, starred FROM contacts").fetchall()

    # Count how many sources each person appears in (by name)
    from collections import Counter
    name_sources = Counter()
    name_ids = {}
    for c in contacts:
        key = f"{(c['first_name'] or '').lower().strip()}|{(c['last_name'] or '').lower().strip()}"
        if key == '|':
            continue
        name_sources[key] += 1
        name_ids.setdefault(key, []).append(c['id'])

    for c in contacts:
        cid = c['id']
        key = f"{(c['first_name'] or '').lower().strip()}|{(c['last_name'] or '').lower().strip()}"
        score = 0

        # Check affiliations
        affs = [r[0] for r in conn.execute(
            "SELECT a.name FROM affiliations a JOIN contact_affiliations ca ON ca.affiliation_id=a.id WHERE ca.contact_id=?", (cid,)
        ).fetchall()]

        # Family -> 5
        if 'family' in affs:
            score = 5
        # Manual source (user typed them) -> at least 3
        elif c['source'] == 'manual':
            score = 3
        else:
            # Has phone -> strong signal (you have their number)
            if c['phone']:
                score += 2
            # Has email -> decent signal
            if c['email']:
                score += 1
            # Has LinkedIn URL -> some connection
            if c['linkedin_url']:
                score += 1
            # Multiple sources -> real relationship
            if name_sources.get(key, 1) >= 2:
                score += 1
            # Has company + role -> real professional contact
            if c['company'] and c['role']:
                score += 1
            # Recent warmth -> active relationship
            if c['warmth_last_contact'] and c['warmth_last_contact'] > (date.today() - timedelta(days=90)).isoformat():
                score += 1
            # Starred by user -> strong intent signal
            if c['starred'] == 1:
                score += 2
            # Cap at 5
            score = min(score, 5)

        conn.execute("UPDATE contacts SET closeness=? WHERE id=?", (score, cid))

    conn.commit()
    conn.close()


def get_triage_batch(offset: int = 0, limit: int = 20) -> list[dict]:
    """Get contacts for triage, prioritizing those with more data (worth rating)."""
    conn = _conn()
    # Prioritize contacts with real data, haven't been starred yet
    rows = conn.execute("""
        SELECT * FROM contacts
        WHERE starred = 0
        ORDER BY
            CASE WHEN source='manual' THEN 0
                 WHEN phone != '' THEN 1
                 WHEN email != '' AND linkedin_url != '' THEN 2
                 WHEN email != '' OR linkedin_url != '' THEN 3
                 WHEN company != '' THEN 4
                 ELSE 5
            END,
            closeness DESC,
            last_name, first_name
        LIMIT ? OFFSET ?
    """, (limit, offset)).fetchall()
    results = []
    for r in rows:
        c = dict(r)
        c["affiliations"] = _get_affiliations(conn, c["id"])
        results.append(c)
    conn.close()
    return results


def triage_remaining() -> int:
    conn = _conn()
    count = conn.execute("SELECT COUNT(*) FROM contacts WHERE starred = 0").fetchone()[0]
    conn.close()
    return count


def triage_stats() -> dict:
    """Return triage progress: total, starred, dismissed, untriaged."""
    conn = _conn()
    total = conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
    starred = conn.execute("SELECT COUNT(*) FROM contacts WHERE starred = 1").fetchone()[0]
    dismissed = conn.execute("SELECT COUNT(*) FROM contacts WHERE starred = -1").fetchone()[0]
    untriaged = conn.execute("SELECT COUNT(*) FROM contacts WHERE starred = 0").fetchone()[0]
    conn.close()
    return {"total": total, "starred": starred, "dismissed": dismissed, "untriaged": untriaged, "triaged": starred + dismissed}


def add_interaction(contact_id: int, type: str = "note", body: str = "", interaction_date: str = "") -> int:
    conn = _conn()
    if not interaction_date:
        interaction_date = date.today().isoformat()
    cur = conn.execute(
        "INSERT INTO interactions(contact_id, type, body, interaction_date) VALUES(?,?,?,?)",
        (contact_id, type, body, interaction_date),
    )
    # Auto-update warmth_last_contact
    conn.execute(
        "UPDATE contacts SET warmth_last_contact = MAX(COALESCE(NULLIF(warmth_last_contact,''), '0000-01-01'), ?) WHERE id=?",
        (interaction_date, contact_id),
    )
    conn.commit()
    iid = cur.lastrowid
    conn.close()
    return iid


def get_interactions(contact_id: int, limit: int = 50) -> list[dict]:
    conn = _conn()
    rows = conn.execute(
        "SELECT * FROM interactions WHERE contact_id=? ORDER BY interaction_date DESC, created_at DESC LIMIT ?",
        (contact_id, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_interaction(interaction_id: int):
    conn = _conn()
    conn.execute("DELETE FROM interactions WHERE id=?", (interaction_id,))
    conn.commit()
    conn.close()


def dashboard_data() -> dict:
    conn = _conn()
    today = date.today().isoformat()

    # Warmth distribution
    hot = conn.execute("SELECT COUNT(*) FROM contacts WHERE warmth_last_contact >= date('now','-30 days')").fetchone()[0]
    warm = conn.execute("SELECT COUNT(*) FROM contacts WHERE warmth_last_contact < date('now','-30 days') AND warmth_last_contact >= date('now','-90 days')").fetchone()[0]
    cooling = conn.execute("SELECT COUNT(*) FROM contacts WHERE warmth_last_contact < date('now','-90 days') AND warmth_last_contact >= date('now','-180 days')").fetchone()[0]
    cold = conn.execute("SELECT COUNT(*) FROM contacts WHERE warmth_last_contact < date('now','-180 days') AND warmth_last_contact != ''").fetchone()[0]
    never = conn.execute("SELECT COUNT(*) FROM contacts WHERE warmth_last_contact = '' OR warmth_last_contact IS NULL").fetchone()[0]
    total = conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]

    # Reach-out suggestions: only starred contacts going stale
    starred_count = conn.execute("SELECT COUNT(*) FROM contacts WHERE starred = 1").fetchone()[0]
    reach_out_rows = []
    if starred_count > 0:
        reach_out_rows = conn.execute(
            "SELECT * FROM contacts WHERE starred = 1 AND warmth_last_contact != '' "
            "AND warmth_last_contact < date('now','-14 days') "
            "ORDER BY closeness DESC, warmth_last_contact ASC LIMIT 15"
        ).fetchall()
        # Fill with starred contacts that have never been contacted
        if len(reach_out_rows) < 10:
            existing_ids = {r["id"] for r in reach_out_rows}
            fill = conn.execute(
                "SELECT * FROM contacts WHERE starred = 1 "
                "AND (warmth_last_contact = '' OR warmth_last_contact IS NULL) "
                "ORDER BY closeness DESC LIMIT 10"
            ).fetchall()
            for r in fill:
                if r["id"] not in existing_ids:
                    reach_out_rows.append(r)
                    if len(reach_out_rows) >= 15:
                        break

    # Seed-randomize by today's date for consistency within a day
    import random
    seed = int(hashlib.md5(today.encode()).hexdigest()[:8], 16)
    rng = random.Random(seed)
    reach_list = [dict(r) for r in reach_out_rows]
    rng.shuffle(reach_list)
    reach_out = []
    for c in reach_list[:5]:
        days_since = (date.today() - date.fromisoformat(c["warmth_last_contact"])).days if c["warmth_last_contact"] else None
        c["days_since_contact"] = days_since
        c["affiliations"] = _get_affiliations(conn, c["id"])
        reach_out.append(c)

    # Smart segment counts
    segments = {}
    # FIJI in Finance
    segments["fiji_finance"] = conn.execute(
        "SELECT COUNT(DISTINCT c.id) FROM contacts c "
        "JOIN contact_affiliations ca ON ca.contact_id=c.id "
        "JOIN affiliations a ON a.id=ca.affiliation_id AND a.name='FIJI' "
        "WHERE c.industry LIKE '%finance%' OR c.industry LIKE '%banking%' OR c.industry LIKE '%invest%'"
    ).fetchone()[0]
    # Columbia close
    segments["columbia_close"] = conn.execute(
        "SELECT COUNT(DISTINCT c.id) FROM contacts c "
        "JOIN contact_affiliations ca ON ca.contact_id=c.id "
        "JOIN affiliations a ON a.id=ca.affiliation_id AND a.name='Columbia' "
        "WHERE c.closeness >= 3"
    ).fetchone()[0]
    # Going cold (90d+, closeness>=2)
    segments["going_cold"] = conn.execute(
        "SELECT COUNT(*) FROM contacts WHERE closeness >= 2 AND warmth_last_contact != '' "
        "AND warmth_last_contact < date('now','-90 days')"
    ).fetchone()[0]
    # Recently added (30d)
    segments["recently_added"] = conn.execute(
        "SELECT COUNT(*) FROM contacts WHERE created_at >= date('now','-30 days')"
    ).fetchone()[0]
    # Inner circle
    segments["inner_circle"] = conn.execute(
        "SELECT COUNT(*) FROM contacts WHERE closeness >= 4"
    ).fetchone()[0]
    # Missing contact info
    segments["missing_info"] = conn.execute(
        "SELECT COUNT(*) FROM contacts WHERE (email = '' OR email IS NULL) AND (linkedin_url = '' OR linkedin_url IS NULL)"
    ).fetchone()[0]
    # Family
    segments["family"] = conn.execute(
        "SELECT COUNT(DISTINCT c.id) FROM contacts c "
        "JOIN contact_affiliations ca ON ca.contact_id=c.id "
        "JOIN affiliations a ON a.id=ca.affiliation_id AND a.name='family'"
    ).fetchone()[0]

    by_source = {r[0]: r[1] for r in conn.execute("SELECT source, COUNT(*) FROM contacts GROUP BY source").fetchall()}
    affiliations_count = conn.execute("SELECT COUNT(DISTINCT a.name) FROM affiliations a JOIN contact_affiliations ca ON ca.affiliation_id=a.id").fetchone()[0]

    # Triage progress
    triaged = conn.execute("SELECT COUNT(*) FROM contacts WHERE starred != 0").fetchone()[0]
    dismissed = conn.execute("SELECT COUNT(*) FROM contacts WHERE starred = -1").fetchone()[0]

    conn.close()
    return {
        "total": total,
        "affiliations_count": affiliations_count,
        "by_source": by_source,
        "reach_out": reach_out,
        "warmth": {"hot": hot, "warm": warm, "cooling": cooling, "cold": cold, "never": never},
        "segments": segments,
        "starred_count": starred_count,
        "triaged": triaged,
        "dismissed": dismissed,
    }


# Init on import
init_db()
