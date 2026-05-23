#!/usr/bin/env python3
"""
HDB Resale Transaction Alert
Monitors data.gov.sg for new 4-Room flat transactions at Bedok South Rd
and sends an email alert when new entries are found.
"""

import requests
import json
import smtplib
import os
import sys
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

# ─────────────────────────────────────────────
# CONFIGURATION — edit these or set as env vars
# ─────────────────────────────────────────────
STREET_NAME   = os.getenv("HDB_STREET_NAME", "BEDOK SOUTH RD")
FLAT_TYPE     = os.getenv("HDB_FLAT_TYPE", "4 ROOM")
FROM_EMAIL    = os.getenv("GMAIL_ADDRESS", "")        # your Gmail address
APP_PASSWORD  = os.getenv("GMAIL_APP_PASSWORD", "")   # Gmail App Password (not your login password)
TO_EMAIL      = os.getenv("ALERT_TO_EMAIL", FROM_EMAIL)  # who receives the alert (defaults to yourself)
STATE_FILE    = os.getenv("STATE_FILE", "last_seen.json")

# data.gov.sg resource ID for HDB resale flat prices
RESOURCE_ID   = "f1765b54-a209-4718-8d38-a39237f502b3"
API_BASE      = "https://data.gov.sg/api/action/datastore_search"

# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def fetch_transactions(limit: int = 20) -> list[dict]:
    """Fetch latest transactions for the configured street + flat type."""
    params = {
        "resource_id": RESOURCE_ID,
        "filters": json.dumps({"flat_type": FLAT_TYPE, "street_name": STREET_NAME}),
        "sort": "month desc",
        "limit": limit,
    }
    resp = requests.get(API_BASE, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        raise RuntimeError(f"API returned failure: {data}")
    return data["result"]["records"]


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"seen_ids": []}


def save_state(seen_ids: list) -> None:
    with open(STATE_FILE, "w") as f:
        json.dump(
            {"seen_ids": seen_ids, "last_checked": datetime.now().isoformat()},
            f,
            indent=2,
        )


def format_price(price_str: str) -> str:
    try:
        return f"${int(float(price_str)):,}"
    except (ValueError, TypeError):
        return price_str


def build_email_body(new_txns: list[dict]) -> tuple[str, str]:
    """Returns (plain_text, html) for the alert email."""
    count = len(new_txns)
    subject = (
        f"🏠 HDB Alert: {count} new transaction{'s' if count > 1 else ''} — "
        f"{FLAT_TYPE} @ {STREET_NAME}"
    )

    # Plain text
    lines = [
        f"New HDB Resale Transaction{'s' if count > 1 else ''} Detected\n",
        f"Location : {STREET_NAME}",
        f"Flat type: {FLAT_TYPE}",
        f"Checked  : {datetime.now().strftime('%d %b %Y %H:%M')}\n",
        "─" * 50,
    ]
    for t in new_txns:
        lines += [
            f"Month      : {t.get('month', 'N/A')}",
            f"Block      : {t.get('block', 'N/A')} {STREET_NAME}",
            f"Storey     : {t.get('storey_range', 'N/A')}",
            f"Floor Area : {t.get('floor_area_sqm', 'N/A')} sqm",
            f"Lease      : {t.get('lease_commence_date', 'N/A')} (commence)",
            f"Resale $   : {format_price(t.get('resale_price', '0'))}",
            "─" * 50,
        ]
    plain = "\n".join(lines)

    # HTML
    rows = ""
    for t in new_txns:
        rows += f"""
        <tr>
          <td>{t.get('month', '')}</td>
          <td>Blk {t.get('block', '')} {STREET_NAME}</td>
          <td>{t.get('storey_range', '')}</td>
          <td>{t.get('floor_area_sqm', '')} sqm</td>
          <td>{t.get('lease_commence_date', '')}</td>
          <td><strong>{format_price(t.get('resale_price', '0'))}</strong></td>
        </tr>"""

    html = f"""
    <html><body style="font-family:sans-serif;color:#222;">
      <h2 style="color:#c0392b;">🏠 HDB Resale Alert</h2>
      <p><strong>Street:</strong> {STREET_NAME} &nbsp;|&nbsp;
         <strong>Type:</strong> {FLAT_TYPE} &nbsp;|&nbsp;
         <strong>Checked:</strong> {datetime.now().strftime('%d %b %Y %H:%M')}</p>
      <table border="1" cellpadding="8" cellspacing="0"
             style="border-collapse:collapse;width:100%;font-size:14px;">
        <thead style="background:#2c3e50;color:white;">
          <tr>
            <th>Month</th><th>Block</th><th>Storey</th>
            <th>Area</th><th>Lease Start</th><th>Resale Price</th>
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
        raise ValueError(
            "GMAIL_ADDRESS and GMAIL_APP_PASSWORD environment variables must be set."
        )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = FROM_EMAIL
    msg["To"]      = TO_EMAIL
    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(FROM_EMAIL, APP_PASSWORD)
        smtp.sendmail(FROM_EMAIL, TO_EMAIL, msg.as_string())

    print(f"✅ Email sent to {TO_EMAIL}")


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main() -> None:
    print(f"[{datetime.now().isoformat()}] Checking HDB transactions …")
    print(f"  Street   : {STREET_NAME}")
    print(f"  Flat type: {FLAT_TYPE}")

    try:
        txns = fetch_transactions()
    except Exception as e:
        print(f"❌ Failed to fetch data: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"  Fetched  : {len(txns)} records from API")

    state    = load_state()
    seen_ids = set(state.get("seen_ids", []))
    new_txns = [t for t in txns if str(t["_id"]) not in seen_ids]

    print(f"  New      : {len(new_txns)} unseen transaction(s)")

    if new_txns:
        subject, plain, html = build_email_body(new_txns)
        try:
            send_email(subject, plain, html)
        except Exception as e:
            print(f"❌ Failed to send email: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        print("  No new transactions — nothing to send.")

    # Save all fetched IDs so we don't re-alert next run
    all_ids = [str(t["_id"]) for t in txns]
    save_state(all_ids)
    print("  State saved.")


if __name__ == "__main__":
    main()
