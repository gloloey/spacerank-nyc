"""
email_notify.py — SpaceRank NYC: lead notification emails
===========================================================
Two emails per lead: one to the SpaceRank team (full details, so a real
inquiry is never missed), one confirming receipt to the tenant/broker who
submitted it. Sent via Resend's plain REST API (https://resend.com) using
`requests` — already a runtime dependency, so no new package is needed for
something this small.

HONEST-PERSISTENCE PATTERN (same as db.py): every function here degrades to
a silent no-op if RESEND_API_KEY isn't set, or if the Resend API call fails
for any reason. A dead email provider must never turn a tenant's form
submission into a 500 — the stdout log + Postgres write in app.py already
guarantee the lead itself is never lost; email is a notification on top,
not the source of truth.

SETUP (one-time, needs Gabriel's own account — same pattern as DATABASE_URL
and the admin key): sign up free at resend.com, verify sending from either
your own domain (spaceranknyc.com) or skip that and use their built-in
onboarding@resend.dev sender for testing, then set RESEND_API_KEY as an
env var (Vercel + local .env.local). Nothing else in this file needs to
change — ADMIN_NOTIFY_EMAILS is separately configurable without touching
code, exactly so more recipients can be added easily.
"""

import os

try:
    from dotenv import load_dotenv
    load_dotenv(".env.local")
except ImportError:
    pass

import requests

RESEND_API_KEY = os.environ.get("RESEND_API_KEY")
# Resend's shared testing sender works immediately with zero DNS setup;
# swap to a verified spaceranknyc.com address once that domain's sending
# records are set up in the Resend dashboard.
RESEND_FROM = os.environ.get("RESEND_FROM", "SpaceRank NYC <onboarding@resend.dev>")
# Not a secret — a recipient list, not a credential — so a sane default
# ships in code (rule: credentials need an env var, plain config doesn't).
# Add more addresses any time via the env var, no code change needed.
ADMIN_NOTIFY_EMAILS = [
    e.strip() for e in os.environ.get("ADMIN_NOTIFY_EMAILS", "gabriel@plus972group.com").split(",")
    if e.strip()
]

_API_URL = "https://api.resend.com/emails"


def _send(to: list, subject: str, html: str) -> bool:
    if not RESEND_API_KEY:
        return False
    try:
        r = requests.post(_API_URL,
                          headers={"Authorization": f"Bearer {RESEND_API_KEY}",
                                   "Content-Type": "application/json"},
                          json={"from": RESEND_FROM, "to": to, "subject": subject, "html": html},
                          timeout=8)
        return r.status_code < 300
    except Exception:
        return False


def _esc(s) -> str:
    return (str(s or "")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _search_summary_html(search: dict) -> str:
    if not search:
        return "<p style=\"color:#5a6478\">No search context attached.</p>"
    rows = "".join(f"<tr><td style='padding:2px 10px 2px 0;color:#5a6478'>{_esc(k)}</td>"
                   f"<td style='padding:2px 0;color:#0b1324'>{_esc(v)}</td></tr>"
                   for k, v in search.items())
    return f"<table style='font-size:13px;border-collapse:collapse'>{rows}</table>"


def send_admin_notification(lead: dict) -> bool:
    """lead is the same record dict app.py already builds (log + DB)."""
    if not ADMIN_NOTIFY_EMAILS:
        return False
    full_name = f"{lead.get('first_name', '')} {lead.get('last_name', '')}".strip() or lead.get("name", "")
    html = f"""
    <div style="font-family:-apple-system,Segoe UI,Arial,sans-serif;max-width:560px;margin:0 auto">
      <h2 style="font-size:18px;margin:0 0 4px">New lead — SpaceRank NYC</h2>
      <p style="color:#5a6478;font-size:13px;margin:0 0 18px">Ref {_esc(lead.get('lead_id', ''))} · {_esc(lead.get('received_at', ''))}</p>
      <table style="font-size:14px;border-collapse:collapse;width:100%;margin-bottom:16px">
        <tr><td style="padding:4px 10px 4px 0;color:#5a6478;width:120px">Name</td><td style="padding:4px 0;font-weight:600">{_esc(full_name)}</td></tr>
        <tr><td style="padding:4px 10px 4px 0;color:#5a6478">Email</td><td style="padding:4px 0"><a href="mailto:{_esc(lead.get('email'))}">{_esc(lead.get('email'))}</a></td></tr>
        <tr><td style="padding:4px 10px 4px 0;color:#5a6478">Phone</td><td style="padding:4px 0">{_esc(lead.get('phone')) or '&mdash;'}</td></tr>
        <tr><td style="padding:4px 10px 4px 0;color:#5a6478">Company</td><td style="padding:4px 0">{_esc(lead.get('company')) or '&mdash;'}</td></tr>
        <tr><td style="padding:4px 10px 4px 0;color:#5a6478">I am a…</td><td style="padding:4px 0">{_esc(lead.get('tenant_type')) or '&mdash;'}</td></tr>
        <tr><td style="padding:4px 10px 4px 0;color:#5a6478">Interested in</td><td style="padding:4px 0;font-weight:600">{_esc(lead.get('interested_in')) or '&mdash;'}</td></tr>
        <tr><td style="padding:4px 10px 4px 0;color:#5a6478">Landlord</td><td style="padding:4px 0">{_esc(lead.get('landlord')) or '&mdash;'}</td></tr>
      </table>
      <p style="font-size:13px;color:#5a6478;margin:0 0 4px">Message</p>
      <p style="font-size:14px;white-space:pre-wrap;margin:0 0 18px">{_esc(lead.get('message')) or '&mdash;'}</p>
      <p style="font-size:13px;color:#5a6478;margin:0 0 4px">Search context</p>
      {_search_summary_html(lead.get('search') or {})}
      <p style="font-size:11px;color:#868da0;margin-top:22px">Also visible in the admin dashboard at /admin.</p>
    </div>"""
    return _send(ADMIN_NOTIFY_EMAILS,
                 f"New lead: {lead.get('interested_in') or 'SpaceRank NYC inquiry'}", html)


def send_tenant_confirmation(lead: dict) -> bool:
    if not lead.get("email"):
        return False
    first_name = lead.get("first_name") or (lead.get("name") or "").split(" ")[0] or "there"
    interested = lead.get("interested_in") or "your search on SpaceRank NYC"
    html = f"""
    <div style="font-family:-apple-system,Segoe UI,Arial,sans-serif;max-width:520px;margin:0 auto;color:#0b1324">
      <h2 style="font-size:19px;margin:0 0 14px">We've got your request, {_esc(first_name)}.</h2>
      <p style="font-size:14px;line-height:1.6;color:#3a4358">
        Thanks for reaching out about <b>{_esc(interested)}</b> through SpaceRank NYC.
        The ownership-side leasing contact for this space has your details, and
        we'll follow up shortly to help move things along.
      </p>
      <p style="font-size:14px;line-height:1.6;color:#3a4358">
        No broker in the middle — this goes straight to the people who actually
        own or manage the space.
      </p>
      <p style="font-size:13px;color:#868da0;margin-top:26px">
        — The SpaceRank NYC team<br>
        <a href="https://spaceranknyc.com" style="color:#2456e6">spaceranknyc.com</a>
      </p>
    </div>"""
    return _send([lead["email"]], f"Your request about {interested} — SpaceRank NYC", html)
