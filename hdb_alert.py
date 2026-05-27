#!/usr/bin/env python3
"""Email alerts for newly posted HDB resale transactions."""

from __future__ import annotations

import argparse
import datetime as dt
import email.message
import json
import os
import smtplib
import ssl
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


RESOURCE_ID = "d_8b84c4ee58e3cfc0ece0d773c8ca6abc"
DATASTORE_URL = "https://data.gov.sg/api/action/datastore_search"
DEFAULT_MAIL_TO = "tyuehlun85@gmail.com"

DEFAULT_FILTERS = {
    "town": "BEDOK",
    "flat_type": "4 ROOM",
    "street_name": "BEDOK SOUTH ROAD",
    "lease_commence_date": "2022",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check HDB resale transactions and email when new matches are posted."
    )
    parser.add_argument(
        "--state-file",
        default="hdb-resale-alert/state.json",
        help="JSON file used to remember already-seen transaction IDs.",
    )
    parser.add_argument(
        "--months",
        type=int,
        default=int(os.getenv("HDB_ALERT_MONTHS", "6")),
        help="Only consider transactions from this many calendar months back.",
    )
    parser.add_argument(
        "--send-on-first-run",
        action="store_true",
        default=os.getenv("HDB_ALERT_SEND_ON_FIRST_RUN", "").lower() in {"1", "true", "yes"},
        help="Send an email for existing matches when the state file does not exist yet.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=os.getenv("HDB_ALERT_DRY_RUN", "").lower() in {"1", "true", "yes"},
        help="Print the email body instead of sending mail.",
    )
    return parser.parse_args()


def months_back_start(today: dt.date, months: int) -> str:
    year = today.year
    month = today.month - months + 1
    while month <= 0:
        month += 12
        year -= 1
    return f"{year:04d}-{month:02d}"


def fetch_transactions(filters: dict[str, str], start_month: str) -> list[dict[str, Any]]:
    query = {
        "resource_id": RESOURCE_ID,
        "limit": "500",
        "filters": json.dumps(filters, separators=(",", ":")),
        "sort": "month desc,_id desc",
    }
    url = f"{DATASTORE_URL}?{urllib.parse.urlencode(query)}"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "hdb-resale-alert/1.0",
        },
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))

    if not payload.get("success"):
        raise RuntimeError(f"data.gov.sg request failed: {payload}")

    records = payload["result"]["records"]
    recent = [record for record in records if record.get("month", "") >= start_month]
    return sorted(
        recent,
        key=lambda record: (record.get("month", ""), int(record.get("_id", 0))),
        reverse=True,
    )
