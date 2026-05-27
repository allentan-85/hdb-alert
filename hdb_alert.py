#!/usr/bin/env python3
"""
HDB Resale Transaction Alert
Monitors data.gov.sg for new HDB resale transactions registered in the last 6 months
and sends an email alert when new entries are found.
Filters: Only shows leases that commenced from 2022 onwards.
Sorting: Latest transactions first, with most recent within same month on top.
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
STREET_NAME           = os.getenv("HDB_STREET_NAME", "BEDOK SOUTH RD")
FLAT_TYPE             = os.getenv("HDB_FLAT_TYPE", "4 ROOM")
HDB_TOWN              = os.getenv("HDB_TOWN", "BEDOK")
MONTHS_BACK           = int(os.getenv("RESALE_REGISTRATION_MONTHS", "6"))  # Last 6 months
MIN_LEASE_YEAR        = int(os.getenv("MIN_LEASE_YEAR", "2022"))  # Only leases from 2022+
FROM_EMAIL            = os.getenv("GMAIL_ADDRESS", "")        # your Gmail address
APP_PASSWORD          = os.getenv("GMAIL_APP_PASSWORD", "")   # Gmail App Password
TO_EMAIL              = os.getenv("ALERT_TO_EMAIL", FROM_EMAIL)
STATE_FILE            = os.getenv("STATE_FILE", "last_seen.json")

# FIXED: Modern active data.gov.sg resource ID for HDB resale flat prices
RESOURCE_ID   = "d_8b84c4ee58e3cfc0ece0d773c8ca6abc"
API_BASE      = "https://data.gov.sg/api/action/datastore_search"

# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def get_cutoff_date() -> str:
    """Calculate the cutoff date for 'last N months' filter.
    Returns date in YYYY-MM format for API filtering."""
    today = datetime.now()
    cutoff = today - timedelta(days=30 * MONTHS_BACK)
    return cutoff.strftime("%Y-%m")


def fetch_transactions(limit: int = 200) -> list[dict]:
    """Fetch latest transactions matching the configured criteria."""
    cutoff_month = get_cutoff_date()
    
    params = {
        "resource_id": RESOURCE_ID,
        "filters": json.dumps({
            "flat_type": FLAT_TYPE,
            "street_name": STREET_NAME,
            "town": HDB_TOWN,
        }),
        "limit": limit,
    }
    resp = requests.get(API_BASE, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        raise RuntimeError(f"API returned failure: {data}")
    
    records = data["result"]["records"]
    
    # Filter transactions:
    # 1. Only from the last N months
    # 2. Only leases that commenced from MIN_LEASE_YEAR onwards
    filtered = [
        r for r in records 
        if r.get("month", "") >= cutoff_month
        and int(r.get("lease_commence_date", "0")) >= MIN_LEASE_YEAR
    ]
    
    # FIXED: Re-sorted to guarantee that the latest month and highest _id (newest entry) are strictly on top
    filtered = sorted(
        filtered, 
        key=lambda x: (x.get("month", ""), int(x.get("_id", 0))),
        reverse=True
    )
    
    return filtered


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


def build_email_body(new_txns: list[dict]) -> tuple[str, str, str]:
    """Returns (subject, plain_text, html) for the alert email."""
    count = len(new_txns)
    subject = (
        f"🏠 HDB Alert: {count} New Transaction{'s' if count > 1 else ''} — "
        f"{FLAT_TYPE} @ {STREET_NAME}"
    )

    # Plain text layout
    lines = [
        f"New HDB Resale Transaction{'s' if count > 1 else ''} Detected\n",
        f"Criteria: {FLAT_TYPE} along {STREET_NAME} (Lease {MIN_LEASE_YEAR} onwards)\n",
        "-" * 60
    ]
    for t in new_txns:
        lines.append(
            f"📍 Month: {t.get('month')} | Block: {t.get('block')} | "
            f"Floor: {t.get('storey_range')} | Price: {format_price(t.get('resale_price'))} | "
            f"Lease Start: {t.get('lease_commence_date')}"
        )
    plain_text = "\n".join(lines)

    # HTML Email template styling
    html = f"""
    <html>
    <body style="font-family: Arial, sans-serif; color: #333; line-height: 1.6;">
        <h2 style="color: #d32f2f; border-bottom: 2px solid #d32f2f; padding-bottom: 8px;">
            🏠 {count} New HDB Resale Transaction{'s' if count > 1 else ''} Found!
        </h2>
        <p><strong>Target Watchlist:</strong> {FLAT_TYPE} flats at <span style="color:#d32f2f;">{STREET_NAME}</span> (Lease year {MIN_LEASE_YEAR}+).</p>
        
        <table border="1" cellpadding="10" cellspacing="0" style="border-collapse: collapse; width: 100%; border: 1px solid #ddd;">
            <thead>
                <tr style="background-color: #f5f5f5; text-align: left;">
                    <th>Registration Month</th>
                    <th>Block</th>
                    <th>Storey Range</th>
                    <th>Resale Price</th>
                    <th>Lease Commencement</th>
                    <th>Floor Area</th>
                </tr>
            </thead>
            <tbody>
    """
    for t in new_txns:
        html += f"""
                <tr>
                    <td><strong>{t.get('month')}</strong></td>
                    <td>{t.get('block')}</td>
                    <td>{t.get('storey_range')}</td>
                    <td style="color: #2e7d32; font-weight: bold;">{format_price(t.get('resale_price'))}</td>
                    <td>{t.get('lease_commence_date')}</td>
                    <td>{t.get('floor_area_sqm')} sqm</td>
                </tr>
        """
    html += """
            </tbody>
        </table>
        <br>
        <p style="font-size: 12px; color: #777;">This alert was automatically generated by your GitHub Actions workflow tracker.</p>
    </body>
    </html>
    """
    return subject, plain_text, html


def send_email(subject: str, plain_text: str, html_text: str) -> None:
    if not FROM_EMAIL or not APP_PASSWORD:
        print("⚠️ Email credentials missing. Skipping notification.")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = FROM_EMAIL
    msg["To"] = TO_EMAIL

    msg.attach(MIMEText(plain_text, "plain"))
    msg.attach(MIMEText(html_text, "html"))

    with smtplib.SMTP("smtp.gmail.com", 587) as smtp:
        smtp.starttls()
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
    print(f"  Reg     : Last {MONTHS_BACK} months (from {cutoff_month})")

    try:
        txns = fetch_transactions()
    except Exception as e:
        print(f"❌ Failed to fetch data: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"  Fetched : {len(txns)} records from API (after filtering)")

    state    = load_state()
    seen_ids = set(state.get("seen_ids", []))
    new_txns = [t for t in txns if str(t["_id"]) not in seen_ids]

    print(f"  New     : {len(new_txns)} unseen transaction(s)")

    if new_txns:
        subject, plain, html = build_email_body(new_txns)
        try:
            send_email(subject, plain, html)
        except Exception as e:
            print(f"❌ Failed to send email: {e}", file=sys.stderr)
            # Do not update state if email failed so it re-attempts next time
            sys.exit(1)
        
        # Save both old and newly parsed IDs to persistent state
        updated_ids = list(seen_ids.union([str(t["_id"]) for t in txns]))
        save_state(updated_ids)
    else:
        print("ℹ️ No new transactions — nothing to send.")


if __name__ == "__main__":
    main()
