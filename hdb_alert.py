#!/usr/bin/env python3
"""
HDB Resale Transaction Alert
Monitors data.gov.sg for new HDB resale transactions registered in the last 6 months
and sends an email alert when new entries are found.
Filters : Only shows leases that commenced from MIN_LEASE_YEAR onwards.
Sorting : Latest month first. Within same month, highest _id (most recently registered) on top.
Schedule: Runs every 12 hours.
"""

import requests
import json
import smtplib
import os
import sys
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta

# ─────────────────────────────────────────────
# CONFIGURATION — edit these or set as env vars
# ─────────────────────────────────────────────
STREET_NAME   = os.getenv("HDB_STREET_NAME", "BEDOK SOUTH RD")
FLAT_TYPE     = os.getenv("HDB_FLAT_TYPE", "4 ROOM")
HDB_TOWN      = os.getenv("HDB_TOWN", "BEDOK")
MONTHS_BACK   = int(os.getenv("RESALE_REGISTRATION_MONTHS", "6"))
MIN_LEASE_YEAR= int(os.getenv("MIN_LEASE_YEAR", "2022"))
FROM_EMAIL    = os.getenv("GMAIL_ADDRESS", "")
APP_PASSWORD  = os.getenv("GMAIL_APP_PASSWORD", "")
TO_EMAIL      = os.getenv("ALERT_TO_EMAIL", FROM_EMAIL)
STATE_FILE    = os.getenv("STATE_FILE", "last_seen.json")

RESOURCE_ID   = "f1765b54-a209-4718-8d38-a39237f502b3"
API_BASE      = "https://data.gov.sg/api/action/datastore_search"

# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def get_cutoff_date() -> str:
    today = datetime.now()
    cutoff = today - timedelta(days=30 * MONTHS_BACK)
    return cutoff.strftime("%Y-%m")


def fetch_all_matching(limit: int = 500) -> list[dict]:
    """
    Fetch ALL transactions matching street/flat/town.
    We fetch a large limit so nothing is missed before filtering.
    Returns records sorted: month DESC, then _id DESC (newest registration first).
    """
    cutoff_month = get_cutoff_date()

    params = {
        "resource_id": RESOURCE_ID,
        "filters": json.dumps({
            "flat_type":   FLAT_TYPE,
            "street_name": STREET_NAME,
            "town":        HDB_TOWN,
        }),
        "sort":  "_id desc",   # ← Sort by registration ID so newest come first
        "limit": limit,
    }

    print(f"  Fetching from API (limit={limit}, sort=_id desc)...")
    resp = requests.get(API_BASE, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        raise RuntimeError(f"API returned failure: {data}")

    records = data["result"]["records"]
    print(f"  API returned {len(records)} raw records")

    # ── Apply filters ──────────────────────────────────────────────────
    filtered = []
    skipped_month = 0
    skipped_lease = 0

    for r in records:
        month      = r.get("month", "")
        lease_year = r.get("lease_commence_date", "0")

        if month < cutoff_month:
            skipped_month += 1
            continue

        try:
            if int(lease_year) < MIN_LEASE_YEAR:
                skipped_lease += 1
                continue
        except (ValueError, TypeError):
            skipped_lease += 1
            continue

        filtered.append(r)

    print(f"  Skipped (too old month): {skipped_month}")
    print(f"  Skipped (lease < {MIN_LEASE_YEAR}): {skipped_lease}")
    print(f"  After filter: {len(filtered)} records")

    # ── Final sort: month DESC, then _id DESC within same month ────────
    filtered.sort(
        key=lambda x: (x.get("month", ""), int(x.get("_id", 0))),
        reverse=True
    )

    return filtered


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {"seen_ids": []}


def save_state(seen_ids: list) -> None:
    with open(STATE_FILE, "w") as f:
        json.dump(
            {
                "seen_ids":     seen_ids,
                "last_checked": datetime.now().isoformat(),
            },
            f,
            indent=2,
        )


def format_price(price_str: str) -> str:
    try:
        return f"${int(float(price_str)):,}"
    except (ValueError, TypeError):
        return str(price_str)


def build_email_body(new_txns: list[dict]) -> tuple[str, str, str]:
    count        = len(new_txns)
    cutoff_month = get_cutoff_date()

    subject = (
        f"🏠 HDB Alert: {count} new transaction{'s' if count > 1 else ''} — "
        f"{FLAT_TYPE} @ {STREET_NAME}"
    )

    # ── Already sorted correctly (month DESC, _id DESC) ────────────────
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
            f"Resale $   : {format_price(t.get('resale_price', '0'))}",
            "─" * 70,
        ]
    plain = "\n".join(lines)

    rows = ""
    for t in new_txns:
        rows += f"""
        <tr>
          <td><strong>{t.get('month', '')}</strong></td>
          <td>Blk {t.get('block', '')} {STREET_NAME}</td>
          <td>{t.get('storey_range', '')}</td>
          <td>{t.get('floor_area_sqm', '')} sqm</td>
          <td>{t.get('lease_commence_date', '')}</td>
          <td><strong>{format_price(t.get('resale_price', '0'))}</strong></td>
        </tr>"""

    html = f"""
    <html><body style="font-family:sans-serif;color:#222;">
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
            <th>Month ↓ (Latest)</th>
            <th>Block</th>
            <th>Storey</th>
            <th>Area</th>
            <th>Lease Start</th>
            <th>Resale Price</th>
          </tr>
        </thead>
        <tbody>{rows}</tbody>
      </table>
      <p style="font-size:12px;color:#888;margin-top:24px;">
        Source: <a href="https://data.gov.sg">data.gov.sg</a> HDB Resale Prices dataset.<br>
        Note: HDB data is updated approximately monthly.
      </p>
    </body></html>"""

    return subject, plain, html


def send_email(subject: str, plain: str, html: str) -> None:
    if not FROM_EMAIL or not APP_PASSWORD:
        raise ValueError("GMAIL_ADDRESS and GMAIL_APP_PASSWORD must be set.")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = FROM_EMAIL
    msg["To"]      = TO_EMAIL
    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(html,  "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(FROM_EMAIL, APP_PASSWORD)
        smtp.sendmail(FROM_EMAIL, TO_EMAIL, msg.as_string())

    print(f"✅ Email sent to {TO_EMAIL}")


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main() -> None:
    cutoff_month = get_cutoff_date()
    print(f"[{datetime.now().isoformat()}] Checking HDB transactions …")
    print(f"  Town    : {HDB_TOWN}")
    print(f"  Street  : {STREET_NAME}")
    print(f"  Flat    : {FLAT_TYPE}")
    print(f"  Lease   : {MIN_LEASE_YEAR}+")
    print(f"  Cutoff  : {cutoff_month}")

    try:
        txns = fetch_all_matching()
    except Exception as e:
        print(f"❌ Failed to fetch data: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"  Total   : {len(txns)} records after filter")

    # ── Load state and detect NEW transactions ─────────────────────────
    state    = load_state()
    seen_ids = set(str(x) for x in state.get("seen_ids", []))

    print(f"  Known   : {len(seen_ids)} previously seen IDs")

    new_txns = [t for t in txns if str(t["_id"]) not in seen_ids]
    print(f"  New     : {len(new_txns)} unseen transaction(s)")

    if new_txns:
        subject, plain, html = build_email_body(new_txns)
        try:
            send_email(subject, plain, html)
        except Exception as e:
            print(f"❌ Failed to send email: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        print("  No new transactions — nothing to send.")

    # ── Save ALL current IDs as seen (not just new ones) ───────────────
    # This prevents re-alerting on next run
    all_ids = [str(t["_id"]) for t in txns]
    save_state(all_ids)
    print(f"  Saved   : {len(all_ids)} IDs to state file")


if __name__ == "__main__":
    main()
