import os
from pathlib import Path

import requests

BASE = "https://analyst-assessment-production.up.railway.app/api/v1"
BELLHAVEN_PARENT_ID = "0015QAPLGS3FVYEEEM"


def _token():
    """API token from the BH_API_TOKEN environment variable (used by the
    scheduled job), or from a local token.txt file (used on your computer).
    Keeping it out of the code means it never ends up in the repo."""
    token = os.environ.get("BH_API_TOKEN")
    if token:
        return token.strip()
    token_file = Path(__file__).with_name("token.txt")
    if token_file.exists():
        return token_file.read_text().strip()
    raise RuntimeError("No API token: set BH_API_TOKEN or create token.txt")


HEADERS = {"Authorization": f"Bearer {_token()}"}


def fetch_accounts():
    accounts, seen, page = [], set(), 1
    while True:
        r = requests.get(f"{BASE}/accounts", headers=HEADERS,
                         params={"page": page, "page_size": 100}, timeout=30)
        r.raise_for_status()
        body = r.json()
        new = [a for a in body["data"] if a["account_id"] not in seen]
        if not new:
            break
        seen.update(a["account_id"] for a in new)
        accounts.extend(new)
        if len(accounts) >= body["total"]:
            break
        page += 1
    return accounts


def update_account(account_id, fields):
    r = requests.patch(f"{BASE}/accounts/{account_id}", headers=HEADERS,
                       json=fields, timeout=30)
    if not r.ok:
        raise RuntimeError(f"PATCH failed ({r.status_code}): {r.text[:300]}")
    return r.json()


def find_created(fields):
    """An account we already created with this name + street
    (protects against creating the same account twice)."""
    for a in fetch_accounts():
        if (a.get("created_by_candidate") and a["name"] == fields["name"]
                and a["billing_street"] == fields["billing_street"]):
            return a["account_id"]
    return None


def create_account(fields):
    existing = find_created(fields)
    if existing:
        return existing
    r = requests.post(f"{BASE}/accounts", headers=HEADERS, json=fields, timeout=30)
    if not r.ok:
        raise RuntimeError(f"POST failed ({r.status_code}): {r.text[:300]}")
    body = r.json()
    record = body.get("data", body) if isinstance(body, dict) else body
    if isinstance(record, dict) and record.get("account_id"):
        return record["account_id"]
    if isinstance(record, str) and record.startswith("001"):
        return record
    new_id = find_created(fields)
    if new_id:
        return new_id
    raise RuntimeError(f"Account created, but its id wasn't in the response: {str(body)[:300]}")
