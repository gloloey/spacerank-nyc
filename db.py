"""
db.py — SpaceRank NYC: Postgres persistence for leads + search analytics
=========================================================================
Backs two things that used to be ephemeral (see app.py's /api/leads docstring
for the prior state): tenant leads, and anonymous search-behavior events
(which areas/types get searched, how many results they returned) used to
compute the admin stats panel (conversion rate, top areas, top landlords).

HONEST-PERSISTENCE DESIGN: every function here degrades to a safe no-op
if DATABASE_URL isn't set (local dev without a DB, or CI) — insert_lead()
returns False, fetch_leads()/fetch_stats() return empty/None. Callers keep
their existing fallback behavior (the stdout log in app.py) either way, so
nothing breaks in an environment with no database configured.

One short-lived connection per call, not a pooled connection held in memory
— correct for serverless (Vercel functions are stateless between
invocations), and Neon's own connection pooler (reachable through the
pooled DATABASE_URL Vercel injects) absorbs the per-call connect cost.
"""

import os

try:
    from dotenv import load_dotenv
    load_dotenv(".env.local")
except ImportError:
    pass   # production: Vercel injects env vars directly, no .env file exists

import psycopg
from psycopg.types.json import Json

DATABASE_URL = os.environ.get("DATABASE_URL")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    lead_id TEXT PRIMARY KEY,
    received_at TIMESTAMPTZ NOT NULL,
    first_name TEXT NOT NULL DEFAULT '',
    last_name TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL,
    phone TEXT NOT NULL DEFAULT '',
    company TEXT NOT NULL DEFAULT '',
    tenant_type TEXT NOT NULL DEFAULT '',
    message TEXT NOT NULL DEFAULT '',
    interested_in TEXT NOT NULL DEFAULT '',
    landlord TEXT NOT NULL DEFAULT '',
    search JSONB NOT NULL DEFAULT '{}'::jsonb
);
-- migration for a table created before the first_name/last_name/phone/
-- tenant_type split (pre-launch, only test leads existed — safe to drop
-- the old single "name" column rather than carry a legacy fallback).
ALTER TABLE leads DROP COLUMN IF EXISTS name;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS first_name TEXT NOT NULL DEFAULT '';
ALTER TABLE leads ADD COLUMN IF NOT EXISTS last_name TEXT NOT NULL DEFAULT '';
ALTER TABLE leads ADD COLUMN IF NOT EXISTS phone TEXT NOT NULL DEFAULT '';
ALTER TABLE leads ADD COLUMN IF NOT EXISTS tenant_type TEXT NOT NULL DEFAULT '';
CREATE TABLE IF NOT EXISTS search_events (
    id BIGSERIAL PRIMARY KEY,
    happened_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    property_type TEXT,
    areas TEXT[],
    used_term BOOLEAN NOT NULL DEFAULT false,
    used_anchor BOOLEAN NOT NULL DEFAULT false,
    landlord_style TEXT,
    result_count INTEGER
);
-- Saved search alerts: token IS the unsubscribe key (long random string,
-- never guessable) as well as the primary key — no separate id needed.
CREATE TABLE IF NOT EXISTS saved_searches (
    token TEXT PRIMARY KEY,
    email TEXT NOT NULL,
    params JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_notified_at TIMESTAMPTZ,
    active BOOLEAN NOT NULL DEFAULT true
);
-- Every listing key ever seen, keyed by a stable identity (landlord +
-- building + suite + source URL). tools/run_search_alerts.py inserts new
-- keys each week (ON CONFLICT DO NOTHING) and treats whatever it just
-- inserted as "new since last week" — this table IS that memory, since
-- spaces_clean.csv itself is fully rebuilt from scratch every refresh and
-- carries no history of its own.
CREATE TABLE IF NOT EXISTS known_listings (
    listing_key TEXT PRIMARY KEY,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

_schema_ready = False


def _connect():
    """Never raises — a bad or unreachable DATABASE_URL must degrade to
    "no database", not 500 a tenant-facing endpoint like /api/leads."""
    if not DATABASE_URL:
        return None
    try:
        return psycopg.connect(DATABASE_URL, connect_timeout=5)
    except Exception:
        return None


def _ensure_schema(conn):
    global _schema_ready
    if _schema_ready:
        return
    with conn.cursor() as cur:
        cur.execute(_SCHEMA)
    conn.commit()
    _schema_ready = True


def insert_lead(record: dict) -> bool:
    """record is the same dict app.py already builds for the stdout log.
    Returns False (never raises) if there's no DB or the insert fails —
    the caller always has the log as a fallback, so a DB hiccup must never
    turn into a 500 for the tenant submitting the form."""
    conn = _connect()
    if conn is None:
        return False
    try:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO leads (lead_id, received_at, first_name, last_name, email,
                       phone, company, tenant_type, message, interested_in, landlord, search)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (lead_id) DO NOTHING""",
                (record["lead_id"], record["received_at"], record["first_name"],
                 record["last_name"], record["email"], record["phone"],
                 record["company"], record["tenant_type"], record["message"],
                 record["interested_in"], record["landlord"],
                 Json(record["search"])),
            )
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        conn.close()


def log_search_event(req, result_count: int) -> None:
    """Fired once per real /api/match call (not /api/count, which fires on
    every filter tweak for the live preview and would drown out signal).
    No PII — only the shape of the search, never the tenant's free text."""
    conn = _connect()
    if conn is None:
        return
    try:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO search_events (property_type, areas, used_term,
                       used_anchor, landlord_style, result_count)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (req.property_type, req.areas or None, bool(req.description),
                 req.anchor is not None, req.landlord_style, result_count),
            )
        conn.commit()
    except Exception:
        pass   # analytics must never break a real search
    finally:
        conn.close()


def fetch_leads(limit: int = 200) -> list:
    conn = _connect()
    if conn is None:
        return []
    try:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                """SELECT lead_id, received_at, first_name, last_name, email, phone,
                          company, tenant_type, message, interested_in, landlord, search
                   FROM leads ORDER BY received_at DESC LIMIT %s""", (limit,))
            cols = [d.name for d in cur.description]
            rows = cur.fetchall()
        return [dict(zip(cols, row)) for row in rows]
    finally:
        conn.close()


def fetch_stats() -> dict | None:
    """None means "no database configured" (distinct from a real zero-state)
    so the admin endpoint can honestly say 503 rather than fake empty stats."""
    conn = _connect()
    if conn is None:
        return None
    try:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM search_events")
            total_searches = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM leads")
            total_leads = cur.fetchone()[0]
            cur.execute(
                """SELECT area, COUNT(*) AS n FROM search_events, unnest(areas) AS area
                   GROUP BY area ORDER BY n DESC LIMIT 10""")
            top_areas = [{"area": r[0], "count": r[1]} for r in cur.fetchall()]
            cur.execute("SELECT AVG(result_count) FROM search_events")
            avg_results = cur.fetchone()[0]
            cur.execute(
                """SELECT landlord, COUNT(*) AS n FROM leads
                   WHERE landlord <> '' GROUP BY landlord ORDER BY n DESC LIMIT 10""")
            top_landlords = [{"landlord": r[0], "count": r[1]} for r in cur.fetchall()]
        return {
            "total_searches": total_searches,
            "total_leads": total_leads,
            "conversion_rate": (total_leads / total_searches) if total_searches else None,
            "avg_results_per_search": float(avg_results) if avg_results is not None else None,
            "top_areas": top_areas,
            "top_landlords_by_interest": top_landlords,
        }
    finally:
        conn.close()


def fetch_public_pulse(days: int = 30) -> dict | None:
    """The PUBLIC, anonymous cousin of fetch_stats() — no lead/PII data,
    only aggregate counts already stripped of anything personal in
    search_events. None means "no database", same convention as fetch_stats,
    so the caller can distinguish "not configured" from "genuinely zero"."""
    conn = _connect()
    if conn is None:
        return None
    try:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                """SELECT COUNT(*) FROM search_events
                   WHERE happened_at > now() - (%s || ' days')::interval""", (days,))
            total = cur.fetchone()[0]
            cur.execute(
                """SELECT area, COUNT(*) AS n FROM search_events, unnest(areas) AS area
                   WHERE happened_at > now() - (%s || ' days')::interval
                   GROUP BY area ORDER BY n DESC LIMIT 3""", (days,))
            top_areas = [r[0] for r in cur.fetchall()]
            cur.execute(
                """SELECT property_type, COUNT(*) AS n FROM search_events
                   WHERE happened_at > now() - (%s || ' days')::interval
                   GROUP BY property_type ORDER BY n DESC LIMIT 1""", (days,))
            row = cur.fetchone()
            top_type = row[0] if row else None
            cur.execute(
                """SELECT AVG(result_count) FROM search_events
                   WHERE happened_at > now() - (%s || ' days')::interval""", (days,))
            avg_results = cur.fetchone()[0]
        return {
            "days": days,
            "total_searches": total,
            "top_areas": top_areas,
            "top_property_type": top_type,
            "avg_results_per_search": round(float(avg_results), 1) if avg_results is not None else None,
        }
    finally:
        conn.close()


def insert_saved_search(token: str, email: str, params: dict) -> bool:
    conn = _connect()
    if conn is None:
        return False
    try:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO saved_searches (token, email, params)
                   VALUES (%s, %s, %s)""", (token, email, Json(params)))
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        conn.close()


def deactivate_saved_search(token: str) -> bool:
    """Returns True iff a matching active row was actually found and
    turned off — lets the unsubscribe endpoint give an honest response
    rather than a blanket "done" for a bogus/already-used token."""
    conn = _connect()
    if conn is None:
        return False
    try:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE saved_searches SET active = false
                   WHERE token = %s AND active = true""", (token,))
            changed = cur.rowcount > 0
        conn.commit()
        return changed
    except Exception:
        return False
    finally:
        conn.close()


def fetch_active_saved_searches() -> list:
    """Used only by tools/run_search_alerts.py (a standalone script, not a
    web request) — no limit/pagination needed at this project's scale."""
    conn = _connect()
    if conn is None:
        return []
    try:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT token, email, params FROM saved_searches WHERE active = true")
            rows = cur.fetchall()
        return [{"token": r[0], "email": r[1], "params": r[2]} for r in rows]
    finally:
        conn.close()


def mark_search_notified(token: str) -> None:
    conn = _connect()
    if conn is None:
        return
    try:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("UPDATE saved_searches SET last_notified_at = now() WHERE token = %s", (token,))
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


def mark_new_listing_keys(keys: list) -> list:
    """Inserts every key not already known (ON CONFLICT DO NOTHING) and
    returns exactly the ones that were actually new — i.e. weren't already
    in the table before this call. That's the whole "what's new this week"
    mechanism: a plain existence diff against a persisted memory, since the
    freshly-rebuilt CSV itself has no concept of history."""
    conn = _connect()
    if conn is None:
        return []
    try:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT listing_key FROM known_listings")
            already_known = {r[0] for r in cur.fetchall()}
            new_keys = [k for k in keys if k not in already_known]
            if new_keys:
                cur.executemany(
                    "INSERT INTO known_listings (listing_key) VALUES (%s) ON CONFLICT DO NOTHING",
                    [(k,) for k in new_keys])
        conn.commit()
        return new_keys
    except Exception:
        return []
    finally:
        conn.close()
