# Bellhaven CRM Sync

A daily pipeline that keeps Bellhaven Senior Living's facility accounts in the CRM in sync with the communities listed on Bellhaven's website. It scrapes the website, matches each community to a CRM account, classifies what needs to change, and queues each change for a human reviewer. **Nothing writes to the CRM without approval.**

## End state of the CRM

All 35 communities on Bellhaven's website now map to exactly one active CRM account under the Bellhaven parent, with one deliberate exception (Ashtabula, explained below).

| Outcome | Count | Accounts |
|---|---|---|
| Already correct, no change | 16 | e.g. Goshen, Akron, Toledo, Defiance |
| Updated (rename / address / zip) | 8 | Grove City, Sycamore Ridge, Dayton, Ashland, Chagrin Falls (was "Riverbend Manor Care Center"), Willow Creek (was "Sunny Acres Retirement Home"), Chesterton (was "Chesterton Senior Commons"), Portsmouth zip |
| Re-parented to Bellhaven | 4 | Kettering (was "Kettering Care Centre" under Harborview), Lima (Harborview), Zanesville (was "Cedar Trail of Zanesville"), Findlay (had no parent) |
| CHOW (change of ownership) | 3 | Tiffin, Marietta (Cedar Trail → Bellhaven), Sandusky (Bellhaven → Millstone) |
| Duplicates marked Inactive | 6 | Old copies of Port Clinton, Erie, Monroe (×2), Kettering, Owosso |
| New accounts created | 4 | Batavia, Union Square, Carlisle PA, Amberly Manor |
| Flagged Needs Review | 2 | Alliance, Coldwater |
| Rejected by reviewer | 1 | Ashtabula address change |

Total: 28 proposals, 27 approved, 1 rejected.

## How it works

```
scraper.py  ->  match.py  ->  pipeline.py  ->  decisions.db  ->  app.py  ->  CRM API
(website)      (classify)     (daily job)      (review queue)    (approve)    (writes)
```

1. **`scraper.py`** follows the community directory's "Next" links across all pages, and also checks the homepage. Findlay is linked only from the homepage, not from the directory, so a directory-only scrape would have missed it. It reads each community page for name, address, city, state, zip, care offerings, administrator, and phone.
2. **`match.py`** pulls all CRM accounts (paginated), matches each community to an account, and produces proposals with their evidence.
3. **`pipeline.py`** is the daily job. It runs the matcher and stores new proposals in `decisions.db` (SQLite).
4. **`app.py`** is a local Flask review app. Each card shows the proposed change and the supporting evidence (the website listing, the CRM snapshot, match method, and SOP check). Approve calls the CRM API; reject records the decision and a comment.
5. **`crm.py`** handles API access: paginated reads, `PATCH` updates, and `POST` creates.

## Matching approach

**Address first, name second.** Facilities get renamed when they change hands, but street addresses rarely change. Addresses are normalized before comparing ("1120 West Main Street" and "1120 W Main St" both become "1120 w main st").

1. **Exact match:** same normalized street, plus the same zip or city.
2. **Fallback, within the same city only:** a similar street or similar name (similarity ≥ 0.85). Restricting this to the same city avoids false matches like *Carlisle, PA* vs. *New Carlisle, OH*, or website *Amberly Manor* (Hudson, OH) vs. CRM *Amberly Care Center* (Grand Rapids, MI). Ashtabula was found this way, since its CRM address was a PO Box.
3. **Search every account, not just Bellhaven's children.** This is how Kettering and Zanesville were found: they sat under other parents with non-Bellhaven names.

Parent accounts, accounts already marked as duplicates, and old CHOW records are excluded from matching, so re-runs don't re-match them.

## Classification and decisions

**Update.** The account is under Bellhaven, but its name, address, zip, status, or care type differs from the website. The website is treated as the source of truth. Formatting-only differences (e.g. "Road" vs. "Rd") are ignored to avoid noise. Website care offerings are translated into the CRM's vocabulary ("Short-Term Rehabilitation & Nursing" → Skilled Nursing, "Memory Support" → Memory Care).

**Re-parent vs. CHOW (the billing SOP).** When an account needs a different parent:
- If `lifetime_revenue > 0` **and** `outstanding_ar > 0`, the old account is left untouched, a new account is created under the correct parent, and the old account's `chow_current_account` points to it (**Tiffin**, **Marietta**).
- Otherwise, the existing account is re-parented directly. **Lima** has $47,000 revenue but $0 AR, so it was re-parented directly.

**Sandusky (sold away from Bellhaven).** Sandusky was under Bellhaven but no longer appears on the website, and an account for "Millstone Care of Sandusky" exists at the same address. It has $130,000 revenue and $5,200 AR, so per the SOP its account was left as is. Since the new owner's account already exists, `chow_current_account` points to that existing account instead of creating another one, which would have just been a new duplicate.

**Duplicates.** When several accounts share a community's address, one survivor is kept. The others get `duplicate_of_account` set to the survivor's id, status Inactive, and an explanatory note. The survivor rule: (1) the account with billing history, (2) then the one already under the correct parent, (3) then the one that already matches the website exactly. Every losing copy here had $0 revenue and $0 AR, so no billing history was affected. Port Clinton, Erie, and Monroe each had a leftover record under their previous owner (Harborview or Cedar Trail). For **Kettering**, both copies were identical ($0, same address, neither under Bellhaven), so the survivor choice was effectively a tie.

**New accounts.** Created under Bellhaven when no account exists at the address and no similar name exists in the same city. For **Union Square**, Juniper has a "Union Square Senior Living" in New Albany, but at a different street (240 Market St vs. 118 Union Square Dr), so it was treated as a different building and not merged. The reviewer sees that nearby account in the evidence.

**Stale (under Bellhaven, not on the website).** If another owner's account exists at the same address, the facility was sold and is handled as CHOW or duplicate (see Sandusky). If not, as with **Alliance** and **Coldwater**, it's set to **Needs Review** with a note rather than Inactive. There's no evidence of whether they closed or were sold, so a human should verify rather than the pipeline guessing.

### Judgment call: Ashtabula (rejected)

The proposal replaced Ashtabula's `billing_street` of "PO Box 517" with the website's "3156 W Prospect Rd". I rejected it: it's a billing field, and a PO Box can be a legitimate billing mailing address, so I'd confirm with the billing team before overwriting it. The account is still correctly matched and under the right parent. The rejection is logged with that comment, and the daily run won't propose it again. It can be reopened in the app if billing confirms.

## Review app

- Tabs for Pending, Approved, Rejected, Failed, and Superseded.
- Each card shows the change, the new account to create (if any), and the full evidence.
- **Approve** executes the change against the API and records the result. **Reject** records the decision with an optional comment. Failed writes show the API error and can be retried.
- **Reopen** sends a rejected proposal back to Pending, keeping the history.
- CHOW approvals run as one unit: create the new account, then set `chow_current_account` on the old one. Creates check for an account already created with the same name and street first, so a retry can't create a duplicate.

## Re-run safety

Each proposal gets a stable id: a hash of its type, its target account (or community), and its field changes. Notes are excluded from the hash because they contain dates. On every run:

- **New id:** added to the queue.
- **Pending or failed:** evidence refreshed, not duplicated.
- **Rejected:** skipped forever (until a reviewer reopens it).
- **Approved:** normally never regenerated, because the CRM now reflects it. If it *is* regenerated, the CRM drifted back (for example, someone reverted the fix), so it's re-queued for review.
- **Pending but no longer produced** (someone fixed the CRM by hand): marked Superseded.

After the review, a second run produced **New: 0** and re-proposed nothing. In the demo, I simulated a rep reverting the Sycamore Ridge name (`revert_for_demo.py`), and the next run re-queued exactly that one item.

## Schedule

`.github/workflows/daily-sync.yml` runs the pipeline daily at 11:17 UTC (about 3am Pacific, before the sales day), plus a manual "Run workflow" button. It reads the API token from the `BH_API_TOKEN` repository secret, only adds proposals to the queue (never writes to the CRM), and commits `decisions.db` back to the repo so decisions persist between runs. A concurrency group prevents overlapping runs.

Equivalent cron entry for a server:

```
17 3 * * * cd /opt/bellhaven && python3 pipeline.py >> pipeline.log 2>&1
```

## Running it locally

```
pip install -r requirements.txt
# Put the API token in token.txt (not committed), or set BH_API_TOKEN
python pipeline.py      # scrape, match, queue proposals
python app.py           # review app at http://127.0.0.1:5000
```

## Limitations and what I'd do next

- **Proposals are per account.** A re-parent that also includes a rename is approved or rejected as one unit. Per-field approval would give reviewers finer control.
- **Care type is a single CRM value,** while some communities offer several services. I keep the CRM value if it's any of the website's offerings.
- **No undo for approved changes.** I'd store before-values at approval time to support a one-click revert. Creates can't be undone because the API has no delete, but they could be marked Inactive.
- **Sold-facility detection takes the first other-owner account at the address.** With several, it should flag for review instead.
- **State lives in a SQLite file committed by the scheduled job.** In production I'd use a shared database so the review app and the job always see the same queue.
- **Contacts:** each community page lists an administrator and phone, and the API has contacts endpoints. Syncing those is a natural next step.
- **Tests:** I'd add unit tests for address normalization, the SOP decision, and the survivor rule.

## Time spent

About **2.5 hours**, including setting up Python on my machine and learning the tooling.

## How I used AI

I used Claude throughout as a pair programmer and to keep the work organized into clear steps. Claude wrote most of the code. My part was directing the work and checking it:

- I ran every step on my machine and fed the real output back, which shaped each next step. For example, the paginated API, the website's page 2, and the homepage-only Findlay listing all came from inspecting actual output.
- Before approving anything, I reviewed the evidence for each proposal, including the duplicates' billing history, the stale accounts' addresses, and the Union Square near-match.
- I verified writes in the CRM browser, starting with a single low-risk test approval (the Portsmouth zip) before approving the rest.
- I made the judgment calls: rejecting the Ashtabula billing address change, and asking for the Reopen feature so a reviewer can revisit a rejection.
- I questioned changes in direction, like which proposal to use for the demo rejection, rather than following along blindly.

## Files

| File | Purpose |
|---|---|
| `scraper.py` | Scrapes all communities from the website |
| `match.py` | Matching, classification, and SOP logic |
| `pipeline.py` | Daily job: runs the matcher and updates the review queue |
| `app.py` | Local review app (Flask) |
| `crm.py` | CRM API client |
| `decisions.db` | Review queue and decision history (SQLite) |
| `.github/workflows/daily-sync.yml` | Daily schedule |
| `revert_for_demo.py` | Simulates CRM drift for the demo |
| `requirements.txt` | Python dependencies |
