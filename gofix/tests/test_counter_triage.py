"""The counter triage must be useful, honest, and impossible to mis-price."""

import frappe

from gofix.ai.triage import triage

_CO = "GOFIX SOLUTIONS PRIVATE LIMITED"
_results = []


def _check(label, cond, detail=""):
	_results.append(("PASS" if cond else "FAIL", label, str(detail)[:70]))


def _t(text, brand="Apple", model=""):
	return triage(description=text, brand=brand, device_model=model, company=_CO)


def run_all():
	_results.clear()

	# ── It reads the symptom the customer actually described ─────────────
	for text, expected in (
		("charging very slow and gets hot near the port", "Charging & Power"),
		("screen flickers and touch dead in lower third", "Screen & Display"),
		("phone fell in water and will not switch on", "Water Damage"),
		("speaker muffled and mic not working on calls", "Speaker & Mic"),
		("battery drains within 2 hours", "Battery"),
		("camera app opens but rear photos are blurred", "Camera"),
	):
		r = _t(text)
		found = [i["category"] for i in r.get("issues") or []]
		_check(f"'{text[:38]}' → {expected}", expected in found, found)

	# ── It refuses to guess ───────────────────────────────────────────────
	# This is the property that keeps it trusted. An earlier version answered
	# "it is not working" with a category and a price, because one past repair
	# on that model shared a common word.
	for vague in ("it is not working", "asdkjh qwe zxc", "device has a problem"):
		r = _t(vague)
		_check(f"'{vague}' is not placed", r.get("source") == "unplaced", r.get("source"))
		_check(f"'{vague}' quotes no price", not (r.get("price") or {}).get("total"),
		       r.get("price"))
		_check(f"'{vague}' offers questions instead",
		       bool(r.get("ask_the_customer")), r.get("ask_the_customer"))

	r = _t("bad")
	_check("too short to act on", r.get("ready") is False, r.get("reason"))

	# ── History supports a finding; it can never be the whole of one ──────
	r = _t("it is not working", model="Smart Phones-iOS Phones-Apple-Apple iPhone 13 Pro")
	_check("a well-worn model does not make a vague fault confident",
	       r.get("source") == "unplaced", r.get("source"))

	# ── Every category and solution comes from the masters ───────────────
	valid_cats = set(frappe.get_all("Issue Category", pluck="name"))
	valid_sols = set(frappe.get_all("Repair Solution", pluck="name"))
	r = _t("screen cracked and battery swollen after a drop")
	_check("categories are real masters",
	       all(i["category"] in valid_cats for i in r["issues"]),
	       [i["category"] for i in r["issues"]])
	_check("solutions are real masters",
	       all(s["name"] in valid_sols for s in r["solutions"]),
	       [s["name"] for s in r["solutions"]])

	# ── The price is the rules engine's, never a guess ───────────────────
	if r.get("price"):
		from gofix.gofix_services.doctype.gofix_pricing_rule.gofix_pricing_rule import (
			calculate_estimate_from_rules,
		)
		quoted = calculate_estimate_from_rules(
			issue_categories=[i["category"] for i in r["issues"]],
			solutions=[{"repair_solution": s["name"], "issue_category": s["issue_category"]}
			           for s in r["solutions"][:4]],
			brand="Apple", company=_CO)
		_check("the price is exactly what the pricing engine says",
		       round(r["price"]["total"], 2) == round(quoted.get("estimate_total") or 0, 2),
		       f'{r["price"]["total"]} vs {quoted.get("estimate_total")}')

	# ── It always says it is indicative ──────────────────────────────────
	_check("it never presents itself as a quote",
	       "Indicative" in (r.get("disclaimer") or "") or "Could not place" in (r.get("disclaimer") or ""),
	       r.get("disclaimer"))

	# ── It works with the AI switched off, which it currently is ─────────
	enabled = frappe.db.get_single_value("CH AI Settings", "enabled")
	_check("useful with AI disabled" if not enabled else "AI is enabled",
	       bool(_t("screen cracked").get("issues")), f"ai_enabled={enabled}")

	for status, label, detail in _results:
		print(f"{status}  {label:<62} {detail}")
	failed = sum(1 for s, _l, _d in _results if s == "FAIL")
	print(f"TOTAL: {len(_results) - failed} passed, {failed} failed")
	return {"passed": len(_results) - failed, "failed": failed}
