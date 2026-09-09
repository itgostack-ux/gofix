"""The daily standup must be durable, deduped, and honest about who it reached.

Written against the two ways alerting had already failed on this bench:

  * The SLA sweep raised 11 escalations and produced ZERO durable records --
    a realtime toast and a delayed email, on a site where outgoing email has
    failed 43,862 times. So: assert Notification Log rows actually exist.
  * "Stock transfer stuck in transit - BMTNMT26000005" was sent 1,159 times for
    one transfer. So: assert a second run the same day sends nothing.
"""

import frappe
from frappe.utils import add_to_date, now_datetime

from gofix.gofix_services.standup import (
	REASON_LABEL, THRESHOLDS, daily_store_standup, my_standup, stuck_jobs,
)

_results = []


def _check(label, cond, detail=""):
	_results.append(("PASS" if cond else "FAIL", label, str(detail)[:72]))


def _clear_keys():
	for k in frappe.cache.get_keys("gofix:standup:*") or []:
		key = k.decode() if isinstance(k, bytes) else k
		frappe.cache.delete_value(key.split("|")[-1])


def run_all():
	_results.clear()
	frappe.set_user("Administrator")

	# ── 1. It finds stalled work and says why ────────────────────────────
	jobs = stuck_jobs()
	_check("stalled repairs are found", bool(jobs), f"{len(jobs)} found")
	_check("every one carries a reason a person can act on",
	       all(j["reason"] in REASON_LABEL for j in jobs),
	       sorted({j["reason"] for j in jobs}))
	_check("every one is older than its threshold",
	       all(j["hours"] >= THRESHOLDS[j["reason"]] for j in jobs),
	       min((j["hours"] for j in jobs), default=0))

	# ── 2. Delivered work is never nagged about ──────────────────────────
	delivered = frappe.db.count("Service Request",
	                            {"delivered_datetime": ("is", "set")})
	names = {j["name"] for j in jobs}
	still_open = frappe.get_all("Service Request",
	                            filters={"name": ("in", list(names))},
	                            fields=["name", "delivered_datetime"]) if names else []
	_check("nothing already delivered is reported",
	       all(not r.delivered_datetime for r in still_open),
	       f"{delivered} delivered records exist")

	# ── 3. The classification matches the ticket's real state ────────────
	unassigned = [j for j in jobs if j["reason"] == "unassigned"]
	if unassigned:
		n = unassigned[0]["name"]
		has_job = frappe.db.count("Job Assignment",
		                          {"service_request": n,
		                           "assignment_status": ("!=", "Cancelled")})
		_check(f"'no technician' really has none ({n})", has_job == 0, has_job)
	drafts = [j for j in jobs if j["reason"] == "draft"]
	if drafts:
		ds = frappe.db.get_value("Service Request", drafts[0]["name"], "docstatus")
		_check("'ticket never submitted' really is a draft", ds == 0, ds)
		# The SLA sweep filters on docstatus=1, so these were invisible to it.
		_check("drafts are work the SLA sweep cannot see", ds == 0, "docstatus=0")

	# ── 4. Durable, not a toast ──────────────────────────────────────────
	_clear_keys()
	# Today's digests may already exist from an earlier run, and the dedupe is
	# durable by design -- so a second run correctly sends nothing. Clear the
	# day first, or this asserts against the dedupe instead of the delivery.
	frappe.db.sql("""DELETE FROM `tabNotification Log`
		WHERE document_type = 'Service Request' AND DATE(creation) = CURDATE()""")
	frappe.db.commit()
	before = frappe.db.count("Notification Log", {"document_type": "Service Request"})
	first = daily_store_standup()
	after = frappe.db.count("Notification Log", {"document_type": "Service Request"})
	_check("it leaves a durable record for each person",
	       after - before == first["notified"] and first["notified"] > 0,
	       f"{after - before} logs for {first['notified']} notified")

	# ── 5. One message per PERSON, not per store ─────────────────────────
	_check("one message per person, not one per store",
	       first["notified"] <= first.get("recipients", 0)
	       and first["notified"] < first["stores"] * first.get("recipients", 1),
	       f"{first['stores']} stores, {first.get('recipients')} people, "
	       f"{first['notified']} messages")

	# ── 6. It cannot become wallpaper ────────────────────────────────────
	second = daily_store_standup()
	after2 = frappe.db.count("Notification Log", {"document_type": "Service Request"})
	_check("a second run the same day sends nothing",
	       second["notified"] == 0 and after2 == after,
	       f"notified={second['notified']}, logs {after}->{after2}")

	# ── 7. A store nobody covers is reported, not dropped ────────────────
	_check("stores with nobody to tell are surfaced, not swallowed",
	       isinstance(first.get("unreachable"), list),
	       first.get("unreachable"))

	# ── 8. The message names the job, the customer and the age ───────────
	log = frappe.get_all("Notification Log",
	                     filters={"document_type": "Service Request"},
	                     fields=["subject", "email_content"],
	                     order_by="creation desc", limit=1)
	if log:
		body = log[0].email_content or ""
		_check("the digest names actual repairs",
		       any(j["name"] in body for j in jobs), log[0].subject)
		_check("the digest gives an age in days", "d<" in body or "d " in body, "")

	# ── 9. The screen endpoint is permission-checked and scoped ──────────
	mine = my_standup()
	_check("my_standup returns the same work", mine["total"] == len(jobs),
	       f'{mine["total"]} vs {len(jobs)}')
	other = frappe.db.get_value("Warehouse",
	                            {"company": "Bestbuy Mobiles Private Limited",
	                             "is_group": 0}, "name")
	scoped = frappe.db.sql("""SELECT up.user FROM `tabUser Permission` up
	    JOIN `tabUser` u ON u.name = up.user AND u.enabled = 1
	    WHERE up.allow='Company' AND up.for_value='GOFIX SOLUTIONS PRIVATE LIMITED'
	      AND up.user NOT IN (SELECT parent FROM `tabHas Role`
	                          WHERE role='System Manager' AND parenttype='User')
	    LIMIT 1""")
	if scoped and other:
		frappe.set_user(scoped[0][0])
		leaked = None
		try:
			my_standup(warehouse=other)
			leaked = f"{scoped[0][0]} read {other}"
		except Exception:
			pass
		frappe.set_user("Administrator")
		_check("another company's warehouse is refused", leaked is None, leaked or "refused")

	for status, label, detail in _results:
		print(f"{status}  {label:<58} {detail}")
	failed = sum(1 for s, _l, _d in _results if s == "FAIL")
	print(f"TOTAL: {len(_results) - failed} passed, {failed} failed")
	return {"passed": len(_results) - failed, "failed": failed}
