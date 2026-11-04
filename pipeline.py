import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone

from match import build_proposals

DB = "decisions.db"


def db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE IF NOT EXISTS proposals (
        id TEXT PRIMARY KEY, type TEXT, summary TEXT, payload TEXT,
        status TEXT DEFAULT 'pending', first_seen TEXT, last_seen TEXT,
        decided_at TEXT, reviewer_comment TEXT, result TEXT)""")
    return con


REQUEUE_SQL = """
UPDATE proposals
SET status = 'pending', payload = ?, last_seen = ?, decided_at = NULL,
    result = result || ' | Re-queued: CRM drifted back after approval'
WHERE id = ?
"""


def run():
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    proposals, confirmed, _ = build_proposals()
    con = db()
    added = refreshed = skipped = requeued = 0

    for p in proposals:
        row = con.execute("SELECT status FROM proposals WHERE id = ?",
                          (p["id"],)).fetchone()
        payload = json.dumps(p)

        if row is None:
            # Brand new proposal
            con.execute(
                "INSERT INTO proposals "
                "(id, type, summary, payload, first_seen, last_seen) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (p["id"], p["type"], p["summary"], payload, now, now))
            added += 1
        elif row["status"] in ("pending", "failed"):
            # Still waiting for a decision: refresh its evidence
            con.execute(
                "UPDATE proposals SET payload = ?, last_seen = ? WHERE id = ?",
                (payload, now, p["id"]))
            refreshed += 1
        elif row["status"] == "approved":
            # Approved earlier, but the CRM no longer reflects it
            # (someone reverted it), so queue it again
            con.execute(REQUEUE_SQL, (payload, now, p["id"]))
            requeued += 1
        else:
            # Rejected: never re-proposed
            skipped += 1

    # Pending items the matcher no longer produces
    # (e.g. someone fixed the CRM by hand)
    ids = [p["id"] for p in proposals]
    placeholders = ",".join("?" * len(ids))
    superseded = con.execute(
        "UPDATE proposals SET status = 'superseded' "
        "WHERE status = 'pending' AND id NOT IN (" + placeholders + ")",
        ids).rowcount
    con.commit()

    by_type = dict(Counter(p["type"] for p in proposals))
    print("Run at", now)
    print("  Confirmed matches (no change needed):", len(confirmed))
    print("  Proposals this run by type:", by_type)
    print("  New:", added)
    print("  Still pending:", refreshed)
    print("  Re-queued (CRM drifted):", requeued)
    print("  Rejected, skipped:", skipped)
    print("  Superseded:", superseded)


if __name__ == "__main__":
    run()
