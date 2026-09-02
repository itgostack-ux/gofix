# Copyright (c) 2026, GoFix and contributors

"""Two judgements the estimate should make for itself.

**A repair that comes back inside its own warranty is free.** Every field needed
to know that already existed — ``repair_warranty_expiry`` on the previous
ticket, ``previous_service_request`` and ``is_repeat_complaint`` on this one —
and nothing connected them to pricing. So it cut both ways: somebody had to
remember to zero the bill, and nothing stopped a customer being charged twice
for the same failure.

**A repair worth more than the device is worth flagging.** Nothing compared the
two, so a customer could be quoted correctly and absurdly at the same time —
₹7,000 to repair a ₹6,000 handset. Buyback already knows what used devices are
worth; this asks it.

Neither of these decides anything on its own. The warranty rule zeroes the
estimate and says why; the economic check raises a warning on the ticket. A
human still chooses whether to repair, replace, or write off.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, nowdate

# Above this share of the device's market value, a repair stops making sense.
# Deliberately a setting rather than a constant: an in-warranty flagship and a
# four-year-old budget handset do not share a threshold.
DEFAULT_BER_RATIO = 0.7


def warranty_rework_context(sr) -> dict:
	"""Is this ticket a return visit still covered by our own workmanship warranty?

	Returns ``{covered, previous, expiry, reason}``. ``covered`` is only true
	when a previous ticket is actually linked and its repair warranty has not
	run out — a repeat complaint about a different fault, or one that arrives
	after the warranty lapses, is ordinary paid work.
	"""
	out = {"covered": False, "previous": None, "expiry": None, "reason": "", "kind": None}

	# ── Same-part cover: the fitted spare is failing again inside its window ──
	# The strongest and most specific claim: a part WE fitted on a prior repair
	# is back on the bench for the SAME part, and its part-warranty window is
	# still open. This is granted regardless of whether a previous_service_request
	# link was set, by matching the current ticket's chosen spare(s) against the
	# parts covered on this serial.
	part = _same_part_rework(sr)
	if part:
		out.update(covered=True, kind="part", previous=part["service_request"],
		           expiry=part["expires_on"],
		           reason=_("Same part ({0}) failed again inside its warranty from repair {1} "
		                    "(to {2}).").format(part["covers"], part["service_request"], part["expires_on"]))
		return out

	# ── Workmanship cover: a linked prior repair whose labour warranty is live ──
	previous = sr.get("previous_service_request")
	if not previous:
		return out

	prev = frappe.db.get_value(
		"Service Request", previous,
		["name", "repair_warranty_expiry", "repair_warranty_days", "decision"],
		as_dict=True,
	)
	if not prev:
		return out

	out["previous"] = prev.name
	out["expiry"] = prev.repair_warranty_expiry

	if not prev.repair_warranty_expiry:
		out["reason"] = _("Previous repair {0} carries no workmanship warranty.").format(prev.name)
		return out

	if getdate(prev.repair_warranty_expiry) < getdate(nowdate()):
		out["reason"] = _("Workmanship warranty on {0} expired on {1}.").format(
			prev.name, frappe.utils.formatdate(prev.repair_warranty_expiry)
		)
		return out

	out.update(covered=True, kind="workmanship")
	out["reason"] = _(
		"Return visit within the workmanship warranty on repair {0}, which runs to {1}."
	).format(prev.name, frappe.utils.formatdate(prev.repair_warranty_expiry))
	return out


def _same_part_rework(sr) -> dict | None:
	"""The covered part on this serial that matches a spare chosen on THIS ticket.

	Reads live part cover from the warranty API (each entry carries the fitted
	item_code and its expiry) and intersects it with the spares the technician
	has chosen on this ticket. A match means the same part is failing again
	inside its warranty — the customer must not pay for it twice.
	"""
	serial = sr.get("serial_no")
	if not serial:
		return None
	chosen = {
		(r.get("spare_item") if hasattr(r, "get") else getattr(r, "spare_item", None))
		for r in (sr.get("spare_lines") or [])
	}
	chosen = {c for c in chosen if c}
	if not chosen:
		return None
	try:
		from ch_item_master.ch_item_master.warranty_api import _repair_and_part_coverage
	except ImportError:
		return None
	for cov in _repair_and_part_coverage(serial, sr.get("company")) or []:
		if cov.get("coverage_type") == "spare_warranty" and cov.get("item_code") in chosen:
			# Never a self-match: the covering repair must be a different ticket.
			if cov.get("service_request") and cov["service_request"] != sr.get("name"):
				return cov
	return None


def apply_warranty_rework(sr, labor: float, spare: float, total: float) -> tuple:
	"""Zero a covered rework, and record on the ticket why it was zeroed.

	Parts are zeroed along with labour. A screen that failed inside our warranty
	is our cost to carry, not the customer's to pay twice — the money comes back
	through the supplier claim, not through the customer.
	"""
	context = warranty_rework_context(sr)
	if not context["covered"]:
		return labor, spare, total, context

	if flt(total) > 0:
		sr.add_comment(
			"Info",
			_("Estimate zeroed: {0} Original quote was {1}.").format(
				context["reason"], frappe.utils.fmt_money(total, currency=_currency(sr))
			),
		)
	return 0.0, 0.0, 0.0, context


def _currency(sr) -> str:
	return (
		frappe.db.get_value("Company", sr.get("company"), "default_currency")
		or frappe.defaults.get_global_default("currency")
		or "INR"
	)


# ── economic repair ──────────────────────────────────────────────────────────

def device_market_value(device_item: str) -> float:
	"""Best available read on what the device is worth today.

	Buyback's own price list is the honest number — it is what the business will
	actually pay for one — so it is preferred over the sale price of a new unit,
	which would make almost every repair look economic.
	"""
	if not device_item:
		return 0.0

	for doctype, field, filters in (
		# What the trade says a used one is worth, then what a good one fetches
		# at trade-in, and only then the retail price of a new unit.
		("Buyback Price Master", "current_market_price", {"item_code": device_item}),
		("Buyback Price Master", "a_grade_iw_0_6", {"item_code": device_item}),
		("Item Price", "price_list_rate", {"item_code": device_item, "selling": 1}),
	):
		if not frappe.db.exists("DocType", doctype):
			continue
		try:
			value = frappe.db.get_value(doctype, filters, field)
		except Exception:
			continue
		if flt(value) > 0:
			return flt(value)

	return flt(frappe.db.get_value("Item", device_item, "standard_rate"))


def economic_repair_check(sr, total: float) -> dict:
	"""Compare the quote to the device's worth.

	Returns ``{checked, uneconomic, value, ratio, threshold}``. ``checked`` is
	false when the device has no known value, which is a reason to stay quiet
	rather than to guess.
	"""
	from gofix.config import get_float_setting

	threshold = get_float_setting("ber_cost_ratio", DEFAULT_BER_RATIO, minimum=0)
	value = device_market_value(sr.get("device_item"))
	if value <= 0 or flt(total) <= 0:
		return {"checked": False, "uneconomic": False, "value": value,
		        "ratio": 0.0, "threshold": threshold}

	ratio = flt(total) / value
	return {
		"checked": True,
		"uneconomic": ratio >= threshold,
		"value": value,
		"ratio": ratio,
		"threshold": threshold,
	}


def flag_if_uneconomic(sr, total: float) -> dict:
	"""Warn when the quote has outgrown the device. Never blocks.

	A customer is entitled to spend more than a phone is worth — on irreplaceable
	data, or a device they simply want back. What they are not entitled to is
	being kept in the dark about it, so this surfaces the comparison and leaves
	the decision where it belongs.
	"""
	check = economic_repair_check(sr, total)
	if not check["uneconomic"]:
		return check

	currency = _currency(sr)
	message = _(
		"This quote is {0} against an estimated device value of {1} — {2}% of what the "
		"device is worth. Discuss replacement with the customer before proceeding."
	).format(
		frappe.utils.fmt_money(total, currency=currency),
		frappe.utils.fmt_money(check["value"], currency=currency),
		int(round(check["ratio"] * 100)),
	)
	sr.add_comment("Info", _("Beyond economic repair: {0}").format(message))
	frappe.msgprint(message, title=_("Beyond Economic Repair"), indicator="orange")
	return check
