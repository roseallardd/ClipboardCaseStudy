import hashlib
import json
import re
from collections import Counter
from datetime import date
from difflib import SequenceMatcher

from crm import BELLHAVEN_PARENT_ID as BH, fetch_accounts
from scraper import scrape

TODAY = date.today().isoformat()

# ---------- Normalizing so "1120 West Main Street" == "1120 W Main St" ----------
ABBREV = {"street": "st", "avenue": "ave", "road": "rd", "boulevard": "blvd",
          "drive": "dr", "lane": "ln", "court": "ct", "place": "pl",
          "square": "sq", "parkway": "pkwy", "highway": "hwy", "suite": "ste",
          "north": "n", "south": "s", "east": "e", "west": "w",
          "northwest": "nw", "northeast": "ne", "southwest": "sw", "southeast": "se"}
NAME_SYNONYMS = {"rehabilitation": "rehab", "centre": "center", "healthcare": "health care"}


def norm_street(s):
    s = re.sub(r"[^a-z0-9 ]", " ", (s or "").lower())
    return " ".join(ABBREV.get(w, w) for w in s.split())


def norm_name(s):
    s = re.sub(r"[^a-z0-9 ]", " ", (s or "").lower().replace("&", " and "))
    return " ".join(NAME_SYNONYMS.get(w, w) for w in s.split() if w != "the")


def sim(a, b):
    return SequenceMatcher(None, a, b).ratio()


def snap(a):
    """The CRM facts a reviewer needs to see."""
    return {"account_id": a["account_id"], "name": a["name"],
            "parent": a["parent_name"] or "(no parent)",
            "address": f"{a['billing_street']}, {a['billing_city']} {a['billing_state']} {a['billing_zip']}",
            "status": a["status"], "care_type": a["care_type"],
            "lifetime_revenue": a["lifetime_revenue"], "outstanding_ar": a["outstanding_ar"],
            "note": a["note"]}


def add_note(a, text):
    return f"{a['note']} | {text}" if a["note"] else text


CARE_MAP = {"Short-Term Rehabilitation & Nursing": "Skilled Nursing",
            "Memory Support": "Memory Care",
            "Assisted Living": "Assisted Living",
            "Independent Living": "Independent Living"}


def care_types(c):
    """Website offerings translated into the CRM's care_type vocabulary."""
    return [CARE_MAP.get(o, o) for o in c["care_offerings"]]


def care_type_for(c):
    types = care_types(c)
    return types[0] if types else ""


# ---------- Proposal record, with a stable id for safe re-runs ----------
def proposal(ptype, summary, account=None, community=None,
             changes=None, create=None, evidence=None):
    changes, create = changes or {}, create or {}
    # The id ignores notes (they contain dates) so the same decision always gets the same id
    key = json.dumps([ptype, account["account_id"] if account else community["slug"],
                      {k: v for k, v in changes.items() if k != "note"},
                      {k: v for k, v in create.items() if k != "note"}], sort_keys=True)
    return {"id": hashlib.sha1(key.encode()).hexdigest()[:12], "type": ptype,
            "summary": summary,
            "account_id": account["account_id"] if account else None,
            "account_name": account["name"] if account else None,
            "community": community["name"] if community else None,
            "changes": changes, "create": create, "evidence": evidence or {}}


# ---------- Matching ----------
def find_matches(c, pool):
    st, city = norm_street(c["street"]), c["city"].lower()
    exact = [a for a in pool if norm_street(a["billing_street"]) == st
             and (a["billing_zip"] == c["zip"] or a["billing_city"].lower() == city)]
    if exact:
        return exact, "same street address"
    # Fallback, only inside the same city (avoids Carlisle vs New Carlisle mistakes)
    fuzzy = [a for a in pool
             if a["billing_state"] == c["state"] and a["billing_city"].lower() == city
             and (sim(norm_street(a["billing_street"]), st) >= 0.85
                  or sim(norm_name(a["name"]), norm_name(c["name"])) >= 0.85)]
    if fuzzy:
        return fuzzy, "same city + similar street or name (lower confidence)"
    return [], None


def site_updates(a, c):
    """Fields where the CRM disagrees with the website (ignores formatting-only differences)."""
    ch = {}
    if a["name"] != c["name"]:
        ch["name"] = c["name"]
    if norm_street(a["billing_street"]) != norm_street(c["street"]):
        ch["billing_street"] = c["street"]
    for field, val in [("billing_city", c["city"]), ("billing_state", c["state"]),
                       ("billing_zip", c["zip"])]:
        if (a[field] or "").strip().lower() != val.lower():
            ch[field] = val
    if a["status"] != "Active":
        ch["status"] = "Active"
    if a["care_type"] not in care_types(c):
        ch["care_type"] = care_type_for(c)
    return ch


def survivor_rank(a, c):
    # Billing history first (billing needs that record), then already-correct parent,
    # then the record that already matches the website exactly
    return (a["lifetime_revenue"], a["outstanding_ar"], a["parent_id"] == BH,
            a["billing_street"] == c["street"], a["name"] == c["name"])


def build_proposals():
    communities = scrape()
    accounts = fetch_accounts()
    parent_ids = {a["parent_id"] for a in accounts if a["parent_id"]}
    # Facilities only: skip parent companies, already-marked duplicates, and old CHOW records
    pool = [a for a in accounts
            if a["account_id"] not in parent_ids
            and "(parent account)" not in a["name"].lower()
            and not a["duplicate_of_account"] and not a["chow_current_account"]]

    proposals, matched, confirmed = [], set(), []
    for c in communities:
        site = {"url": c["url"], "name": c["name"],
                "address": f"{c['street']}, {c['city']} {c['state']} {c['zip']}",
                "care_offerings": c["care_offerings"]}
        new_fields = {"name": c["name"], "parent_id": BH, "billing_street": c["street"],
                      "billing_city": c["city"], "billing_state": c["state"],
                      "billing_zip": c["zip"], "care_type": care_type_for(c),
                      "phone": c["phone"], "status": "Active"}
        matches, method = find_matches(c, pool)

        # 1. Not in the CRM at all
        if not matches:
            proposals.append(proposal(
                "create", f"New community, no CRM account found: {c['name']}",
                community=c,
                create={**new_fields, "note": f"Created {TODAY} from Bellhaven website ({c['url']})"},
                                evidence={"website": site,
                          "search": "No account at this address, and no similar name in this city",
                          "other_accounts_in_same_city": [
                              snap(x) for x in accounts
                              if x["billing_city"].lower() == c["city"].lower()
                              and x["billing_state"] == c["state"]]}))
            continue

        ranked = sorted(matches, key=lambda a: survivor_rank(a, c), reverse=True)
        a = ranked[0]
        matched.update(m["account_id"] for m in matches)

        # 2. Duplicates: every extra copy points to the survivor
        for loser in ranked[1:]:
            proposals.append(proposal(
                "duplicate", f"Duplicate of {a['name']} ({a['account_id']})",
                account=loser, community=c,
                changes={"duplicate_of_account": a["account_id"], "status": "Inactive",
                         "note": add_note(loser, f"{TODAY}: duplicate of {a['account_id']} (same address)")},
                evidence={"website": site, "survivor": snap(a), "duplicate": snap(loser),
                          "survivor_rule": "Keep the copy with billing history; then correct parent; then exact website match"}))

        ev = {"website": site, "crm": snap(a), "match_method": method,
              "name_similarity": round(sim(norm_name(a["name"]), norm_name(c["name"])), 2)}

        # 3. Wrong or missing parent -> apply the SOP
        if a["parent_id"] != BH:
            old_parent = a["parent_name"] or "(no parent)"
            if a["lifetime_revenue"] > 0 and a["outstanding_ar"] > 0:
                proposals.append(proposal(
                    "chow",
                    f"CHOW: {old_parent} -> Bellhaven. Revenue ${a['lifetime_revenue']:,} and "
                    f"AR ${a['outstanding_ar']:,}, so old account is preserved and a new one is created",
                    account=a, community=c,
                    changes={"chow_current_account": "<id of new account>"},
                    create={**new_fields, "note": f"Created {TODAY} via CHOW; replaces {a['account_id']} (formerly under {old_parent})"},
                    evidence={**ev, "sop": "Revenue > 0 AND AR > 0: do not re-parent; create new + set chow_current_account"}))
            else:
                ch = site_updates(a, c)
                ch["parent_id"] = BH
                ch["note"] = add_note(a, f"{TODAY}: re-parented from {old_parent} to Bellhaven (listed on {c['url']})")
                proposals.append(proposal(
                    "reparent",
                    f"Move from {old_parent} to Bellhaven (revenue ${a['lifetime_revenue']:,}, "
                    f"AR ${a['outstanding_ar']:,}, so a direct re-parent is allowed)",
                    account=a, community=c, changes=ch,
                    evidence={**ev, "sop": "Not both revenue > 0 and AR > 0: re-parent directly"}))
            continue

        # 4. Right parent, but stale details
        ch = site_updates(a, c)
        if ch:
            proposals.append(proposal("update", "Update to match website: " + ", ".join(ch),
                                      account=a, community=c, changes=ch, evidence=ev))
        else:
            confirmed.append(f"{c['name']}  <->  {a['name']} ({a['account_id']})")

    # 5. Under Bellhaven in the CRM, but not on the website
        # 5. Under Bellhaven in the CRM, but not on the website
    for a in pool:
        if a["parent_id"] != BH or a["account_id"] in matched:
            continue
        # Is another owner's account at the same address? Then it was sold, not closed.
        new_owner = [o for o in pool if o is not a and o["parent_id"] and o["parent_id"] != BH
                     and o["billing_street"] and o["billing_zip"] == a["billing_zip"]
                     and norm_street(o["billing_street"]) == norm_street(a["billing_street"])]
        ev = {"crm": snap(a), "other_accounts_at_same_address": [snap(o) for o in new_owner],
              "website": "Not found in any of the scraped communities"}
        if new_owner:
            o = new_owner[0]
            if a["lifetime_revenue"] > 0 and a["outstanding_ar"] > 0:
                proposals.append(proposal(
                    "chow",
                    f"Sold to {o['parent_name']}. Revenue ${a['lifetime_revenue']:,} and AR "
                    f"${a['outstanding_ar']:,}, so the old account stays as is and points to the "
                    f"new owner's existing account {o['name']} ({o['account_id']})",
                    account=a, changes={"chow_current_account": o["account_id"]},
                    evidence={**ev, "sop": "Revenue > 0 AND AR > 0: old account untouched. "
                              "New owner's account already exists, so link to it instead of creating one."}))
            else:
                proposals.append(proposal(
                    "duplicate", f"Sold to {o['parent_name']}; duplicate of {o['name']} ({o['account_id']})",
                    account=a,
                    changes={"duplicate_of_account": o["account_id"], "status": "Inactive",
                             "note": add_note(a, f"{TODAY}: off Bellhaven website; now under {o['parent_name']} as {o['account_id']}")},
                    evidence=ev))
        elif a["status"] != "Needs Review":
            proposals.append(proposal(
                "stale", f"Under Bellhaven in CRM but not on the website: {a['name']}",
                account=a,
                changes={"status": "Needs Review",
                         "note": add_note(a, f"{TODAY}: no longer listed on Bellhaven website; ownership needs verification")},
                evidence=ev))

    return proposals, confirmed, accounts


if __name__ == "__main__":
    proposals, confirmed, accounts = build_proposals()
    with open("proposals.json", "w", encoding="utf-8") as f:
        json.dump(proposals, f, indent=2)

    print(f"\n=== CONFIRMED, no change needed: {len(confirmed)} ===")
    for line in confirmed:
        print("  ", line)
    for t in ["update", "reparent", "chow", "duplicate", "create", "stale"]:
        group = [p for p in proposals if p["type"] == t]
        print(f"\n=== {t.upper()}: {len(group)} ===")
        for p in group:
            who = p["account_name"] or p["community"]
            print(f"  [{p['id']}] {who}: {p['summary']}")
            if p["changes"]:
                print("        changes:", {k: v for k, v in p["changes"].items() if k != "note"})

    print("\n=== CRM care_type values (for comparing with the website) ===")
    print(Counter(a["care_type"] for a in accounts))