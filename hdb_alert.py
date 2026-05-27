#!/usr/bin/env python3
"""
HDB Resale Transaction Alert v3 — crash-proof edition
Monitors data.gov.sg for new HDB resale transactions (last N months)
and sends an email alert only when new entries are found.
Filters : Lease commenced from MIN_LEASE_YEAR onwards.
Sorting : Latest month first. Within same month, highest _id first (newest registration).
Schedule: Every 12 hours (9am and 9pm SGT).
"""

import requests
import json
import smtplib
import os
import sys
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta


# ─────────────────────────────────────────────────────────────────────
# SAFE HELPERS — never crash on empty/missing env vars
# ─────────────────────────────────────────────────────────────────────

def safe_int(value: str, default: int) -> int:
    """Convert string to int safely. Returns default if value is empty or invalid."""
    try:
        v = (value or "").strip()
        return int(v) if v else default
    except (ValueError, TypeError):
        return default


# ─────────────────────────────────────────────────────────────────────
# CONFIGURATION — read from GitHub Variables / Secrets
# ─────────────────────────────────────────────────────────────────────

STREET_NAME    = (os.getenv("HDB_STREET_NAME")    or "BEDOK SOUTH RD").strip()
FLAT_TYPE      = (os.getenv("HDB_FLAT_TYPE")      or "4 ROOM").strip()
HDB_TOWN       = (os.getenv("HDB_TOWN")           or "BEDOK").strip()
MONTHS_BACK    = safe_int(os.getenv("RESALE_REGISTRATION_MONTHS"), 6)
MIN_LEASE_YEAR = safe_int(os.getenv("MIN_LEASE_YEAR"), 2022)
FROM_EMAIL     = (os.getenv("GMAIL_ADDRESS")      or "").strip()
APP_PASSWORD   = (os.getenv("GMAIL_APP_PASSWORD") or "").strip()
TO_EMAIL       = (os.getenv("ALERT_TO_EMAIL")     or FROM_EMAIL).strip()
STATE_FILE     = (os.getenv("STATE_FILE")         or "last_seen.json").strip()

RESOURCE_ID    = "f1765b54-a209-4718-8d38-a39237f502b3"
API_BASE       = "https://data.gov.sg/api/action/datastore_search"


# ─────────────────────────────────────────────────────────────────────
# CORE FUNCTIONS
# ─────────────────────────────────────────────────────────────────────

def get_cutoff_date() -> str:
    """Return YYYY-MM for 'N months ago' — used to filter old transactions."""
    cutoff = datetime.now() - timedelta(days=30 * MONTHS_BACK)
    return cutoff.strftime("%Y-%m")


def safe_id(record: dict) -> int:
    """Extract _id as integer safely for sorting."""
    try:
        return int(record.get("_id") or 0)
    except (ValueError, TypeError):
        return 0


def fetch_all_matching() -> list[dict]:
    """
    Fetch all transactions matching street / flat type / town.
    Limit=500 ensures we capture everything before filtering.
    Returns list sorted: month DESC, then _id DESC (newest first within same month).
    """
    cutoff_month = get_cutoff_date()

    params = {
        "resource_id": RESOURCE_ID,
        "filters": json.dumps({
            "flat_type":   FLAT_TYPE,
            "street_name": STREET_NAME,
            "town":        HDB_TOWN,
        }),
        "sort":  "_id desc",
        "limit": 500,
    }

    print(f"  Querying API …")
    resp = requests.get(API_BASE, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    if not data.get("success"):
        raise RuntimeError(f"API error: {data}")

    records = data["result"]["records"]
    print(f"  Raw records from API  : {len(records)}")

    # ── Filter ────────────────────────────────────────────────────────
    filtered = []
    for r in records:
        month      = (r.get("month") or "").strip()
        lease_raw  = (r.get("lease_commence_date") or "0").strip()
        lease_year = safe_int(lease_raw, 0)

        if month < cutoff_month:
            continue                    # too old by registration month
        if lease_year < MIN_LEASE_YEAR:
            continue                    # lease too old
        filtered.append(r)

    print(f"  After filter          : {len(filtered)} "
          f"(month>={cutoff_month}, lease>={MIN_LEASE_YEAR})")

    # ── Sort: month DESC, then _id DESC ───────────────────────────────
    filtered.sort(key=lambda x: (x.get("month", ""), safe_id(x)), reverse=True)

    return filtered


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except Exception as e:
            print(f"  Warning: could not read state file ({e}). Starting fresh.")
    return {"seen_ids": []}


def save_state(all_ids: list) -> None:
    with open(STATE_FILE, "w") as f:
        json.dump(
            {"seen_ids": all_ids, "last_checked": datetime.now().isoformat()},
            f, indent=2,
        )


def fmt_price(val) -> str:
    try:
        return f"${int(float(val or 0)):,}"
    except (ValueError, TypeError):
        return str(val)


def build_email(new_txns: list[dict]) -> tuple[str, str, str]:
    count        = len(new_txns)
    cutoff_month = get_cutoff_date()
    subject      = (
        f"🏠 HDB Alert: {count} new transaction{'s' if count > 1 else ''} — "
        f"{FLAT_TYPE} @ {STREET_NAME}"
    )

    # Plain text
    lines = [
        f"New HDB Resale Transaction{'s' if count > 1 else ''} Detected\n",
        f"Town            : {HDB_TOWN}",
        f"Street          : {STREET_NAME}",
        f"Flat type       : {FLAT_TYPE}",
        f"Lease from year : {MIN_LEASE_YEAR}+",
        f"Registration    : Last {MONTHS_BACK} months (from {cutoff_month})",
        f"Checked         : {datetime.now().strftime('%d %b %Y %H:%M')}\n",
        "─" * 70,
    ]
    for t in new_txns:
        lines += [
            f"Month      : {t.get('month', 'N/A')}",
            f"Block      : {t.get('block', 'N/A')} {STREET_NAME}",
            f"Storey     : {t.get('storey_range', 'N/A')}",
            f"Floor Area : {t.get('floor_area_sqm', 'N/A')} sqm",
            f"Lease Start: {t.get('lease_commence_date', 'N/A')}",
            f"Resale $   : {fmt_price(t.get('resale_price'))}",
            "─" * 70,
        ]
    plain = "\n".join(lines)

    # HTML table rows
    rows = ""
    for t in new_txns:
        rows += (
            f"<tr>"
            f"<td><strong>{t.get('month','')}</strong></td>"
            f"<td>Blk {t.get('block','')} {STREET_NAME}</td>"
            f"<td>{t.get('storey_range','')}</td>"
            f"<td>{t.get('floor_area_sqm','')} sqm</td>"
            f"<td>{t.get('lease_commence_date','')}</td>"
            f"<td><strong>{fmt_price(t.get('resale_price'))}</strong></td>"
            f"</tr>"
        )

    html = f"""<html><body style="font-family:sans-serif;color:#222;">
<h2 style="color:#c0392b;">🏠 HDB Resale Alert</h2>
<p>
  <strong>Town:</strong> {HDB_TOWN} &nbsp;|&nbsp;
  <strong>Street:</strong> {STREET_NAME} &nbsp;|&nbsp;
  <strong>Type:</strong> {FLAT_TYPE} &nbsp;|&nbsp;
  <strong>Lease from:</strong> {MIN_LEASE_YEAR}+ &nbsp;|&nbsp;
  <strong>Registered:</strong> Last {MONTHS_BACK} months &nbsp;|&nbsp;
  <strong>Checked:</strong> {datetime.now().strftime('%d %b %Y %H:%M')}
</p>
<table border="1" cellpadding="8" cellspacing="0"
       style="border-collapse:collapse;width:100%;font-size:14px;">
  <thead style="background:#2c3e50;color:white;">
    <tr>
      <th>Month ↓ (Latest)</th><th>Block</th><th>Storey</th>
      <th>Area</th><th>Lease Start</th><th>Resale Price</th>
    </tr>
  </thead>
  <tbody>{rows}</tbody>
</table>
<p style="font-size:12px;color:#888;margin-top:24px;">
  Source: <a href="https://data.gov.sg">data.gov.sg</a> HDB Resale Prices.<br>
  Note: HDB data is updated approximately monthly.
</p>
</body></html>"""

    return subject, plain, html


def send_email(subject: str, plain: str, html: str) -> None:
    if not FROM_EMAIL or not APP_PASSWORD:
        raise ValueError(
            "GMAIL_ADDRESS and GMAIL_APP_PASSWORD must be set in GitHub Secrets."
        )
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = FROM_EMAIL
    msg["To"]      = TO_EMAIL
    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(html,  "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(FROM_EMAIL, APP_PASSWORD)
        smtp.sendmail(FROM_EMAIL, TO_EMAIL, msg.as_string())

    print(f"  ✅ Email sent → {TO_EMAIL}")


# ─────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────

def main() -> None:
    print(f"\n{'='*60}")
    print(f"[{datetime.now().isoformat()}] HDB Alert check starting")
    print(f"{'='*60}")
    print(f"  Street  : {STREET_NAME}")
    print(f"  Flat    : {FLAT_TYPE}")
    print(f"  Town    : {HDB_TOWN}")
    print(f"  Lease   : {MIN_LEASE_YEAR}+")
    print(f"  Months  : last {MONTHS_BACK} months")
    print(f"  Cutoff  : {get_cutoff_date()}")

    # ── Fetch ─────────────────────────────────────────────────────────
    try:
        txns = fetch_all_matching()
    except Exception as e:
        print(f"\n❌ Fetch failed: {e}", file=sys.stderr)
        sys.exit(1)

    if not txns:
        print("  No transactions matched filters.")
        save_state([])
        return

    # ── Detect new ────────────────────────────────────────────────────
    state    = load_state()
    seen_ids = set(str(x) for x in state.get("seen_ids", []))
    print(f"  Previously seen IDs : {len(seen_ids)}")

    new_txns = [t for t in txns if str(t.get("_id", "")) not in seen_ids]
    print(f"  New transactions    : {len(new_txns)}")

    # ── Alert ─────────────────────────────────────────────────────────
    if new_txns:
        try:
            subject, plain, html = build_email(new_txns)
            send_email(subject, plain, html)
        except Exception as e:
            print(f"\n❌ Email failed: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        print("  No new transactions — email not sent.")

    # ── Save state ────────────────────────────────────────────────────
    all_ids = [str(t.get("_id", "")) for t in txns]
    save_state(all_ids)
    print(f"  State saved         : {len(all_ids)} IDs")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
