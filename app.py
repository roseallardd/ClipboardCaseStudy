import json
from datetime import datetime, timezone

from flask import Flask, redirect, render_template_string, request, url_for

from crm import create_account, update_account
from pipeline import db

app = Flask(__name__)


def execute(p):
    """Carry out an approved proposal against the CRM."""
    if p["create"]:
        new_id = create_account(p["create"])
        if p["type"] == "chow":
            update_account(p["account_id"], {"chow_current_account": new_id})
            return f"Created {new_id} under Bellhaven; set chow_current_account on {p['account_id']}"
        return f"Created {new_id}"
    update_account(p["account_id"], p["changes"])
    return f"Updated {p['account_id']}: {', '.join(p['changes'])}"


@app.route("/")
def index():
    status = request.args.get("status", "pending")
    con = db()
    rows = con.execute("SELECT * FROM proposals WHERE status = ? ORDER BY type, summary",
                       (status,)).fetchall()
    counts = dict(con.execute("SELECT status, COUNT(*) FROM proposals GROUP BY status").fetchall())
    items = [{**dict(r), "p": json.loads(r["payload"])} for r in rows]
    return render_template_string(PAGE, items=items, status=status, counts=counts)


@app.post("/decide/<pid>")
def decide(pid):
    action, comment = request.form["action"], request.form.get("comment", "")
    con = db()
    row = con.execute("SELECT * FROM proposals WHERE id = ? AND status IN ('pending', 'failed')",
                      (pid,)).fetchone()
    if row:
        if action == "approve":
            try:
                result, status = execute(json.loads(row["payload"])), "approved"
            except Exception as e:
                result, status = f"FAILED: {e}", "failed"
        else:
            result, status = "Rejected by reviewer; no CRM change", "rejected"
        con.execute("UPDATE proposals SET status = ?, decided_at = ?, reviewer_comment = ?, "
                    "result = ? WHERE id = ?",
                    (status, datetime.now(timezone.utc).isoformat(timespec="seconds"),
                     comment, result, pid))
        con.commit()
    return redirect(url_for("index", status=request.args.get("back", "pending")))

@app.post("/reopen/<pid>")
def reopen(pid):
    """Send a rejected proposal back to the queue (a reviewer changed their mind)."""
    con = db()
    row = con.execute("SELECT result FROM proposals WHERE id = ? AND status = 'rejected'",
                      (pid,)).fetchone()
    if row:
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        con.execute("UPDATE proposals SET status = 'pending', decided_at = NULL, result = ? "
                    "WHERE id = ?", (f"{row['result']} | Reopened {stamp}", pid))
        con.commit()
    return redirect(url_for("index", status=request.args.get("back", "rejected")))

PAGE = """<!doctype html><html><head><title>Bellhaven CRM Review</title><style>
body{font-family:system-ui,sans-serif;max-width:1100px;margin:24px auto;padding:0 16px;color:#222}
nav a{margin-right:16px}
.card{border:1px solid #ddd;border-radius:8px;padding:14px 16px;margin:14px 0}
.badge{display:inline-block;padding:2px 8px;border-radius:10px;font-size:12px;font-weight:600;color:#fff;text-transform:uppercase;background:#666}
.update{background:#2f6fb3}.reparent{background:#8a4fbf}.chow{background:#c0392b}
.duplicate{background:#d68910}.create{background:#1e8449}.stale{background:#7f8c8d}
table{border-collapse:collapse;margin:8px 0}td,th{border:1px solid #ddd;padding:4px 8px;text-align:left;font-size:14px}
pre{background:#f6f6f6;padding:10px;border-radius:6px;font-size:12px;overflow-x:auto}
.result{font-size:14px;color:#444}button{padding:6px 14px;margin-left:6px;cursor:pointer}
</style></head><body>
<h1>Bellhaven CRM review queue</h1>
<nav>{% for s in ['pending','approved','rejected','failed','superseded'] %}
<a href="/?status={{s}}">{% if s == status %}<b>{% endif %}{{ s|capitalize }} ({{ counts.get(s, 0) }}){% if s == status %}</b>{% endif %}</a>
{% endfor %}</nav>
{% if not items %}<p>Nothing here.</p>{% endif %}
{% for it in items %}{% set p = it.p %}
<div class="card">
  <span class="badge {{ p.type }}">{{ p.type }}</span>
  <strong>{{ p.account_name or p.community }}</strong>
  {% if p.account_id %}<code>{{ p.account_id }}</code>{% endif %}
  <p>{{ p.summary }}</p>
  {% if p.changes %}<table><tr><th>Field</th><th>New value</th></tr>
    {% for k, v in p.changes.items() %}<tr><td>{{ k }}</td><td>{{ v }}</td></tr>{% endfor %}</table>{% endif %}
  {% if p.create %}<p><b>New account to create:</b></p><table>
    {% for k, v in p.create.items() %}<tr><td>{{ k }}</td><td>{{ v }}</td></tr>{% endfor %}</table>{% endif %}
  <details open><summary>Evidence</summary><pre>{{ p.evidence | tojson(indent=2) }}</pre></details>
  {% if it.status in ['pending', 'failed'] %}
  <form method="post" action="/decide/{{ it.id }}?back={{ status }}">
    <input name="comment" placeholder="Reviewer comment (optional)" size="50">
    <button name="action" value="approve">Approve</button>
    <button name="action" value="reject">Reject</button>
  </form>{% endif %}
  {% if it.status == 'rejected' %}
  <form method="post" action="/reopen/{{ it.id }}?back={{ status }}">
    <button>Reopen</button>
  </form>{% endif %}
  {% if it.result %}<p class="result"><b>Result:</b> {{ it.result }}
    {% if it.reviewer_comment %} · <b>Comment:</b> {{ it.reviewer_comment }}{% endif %}
    · {{ it.decided_at }}</p>{% endif %}
</div>{% endfor %}
</body></html>"""

if __name__ == "__main__":
    app.run(debug=True)