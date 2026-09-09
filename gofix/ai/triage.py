"""Answer the customer's first question at the counter, not a day later.

Every customer asks the same two things when they hand over a phone: *how much*
and *how long*. Today nobody at the counter can answer either. The price only
exists after a technician runs Analysis, which the status log says takes a mean
of 13.5 hours -- and 15 of 97 repairs are then rejected once the number finally
arrives, after the customer has already gone home without their phone. That is
the most expensive silence in the business: the device was taken in, the bench
time was spent, and the job is thrown away at the end.

This gives the counter an answer while the customer is still standing there.

**AI classifies. The rules engine prices.** A language model is good at reading
"screen flickers and touch dead in the lower third after a drop, battery drains
fast" and saying that is Screen & Display plus Battery. It has no business
inventing a rupee figure. So the model only ever maps free text onto the
sixteen Issue Categories and the thirty-eight Repair Solutions the masters
already define; every number then comes from `calculate_estimate_from_rules`,
the same engine that prices the formal estimate. The counter and the technician
therefore quote from one source, and a model cannot mis-price a repair.

**It works with the AI switched off**, which matters because it is switched off
right now. Three sources, tried in order, each labelled in the result so the
counter knows how much to trust it:

  ``history``  what this exact fault on this exact model actually cost us
               before. The strongest evidence there is, and it needs no model.
  ``ai``       the language model, when one is configured.
  ``keyword``  the Issue Category descriptions, matched on words. Crude, always
               available, and better than an empty screen.
"""

from __future__ import annotations

import json
import re

import frappe
from frappe import _
from frappe.utils import cint, flt

# Never present a guess as a quote. The counter is told the band and the basis,
# and the formal estimate still comes after Analysis.
DISCLAIMER = _(
	"Indicative only — based on {basis}. The firm estimate follows inspection, "
	"and no work starts until the customer approves it."
)

_STOP = {
	"the", "and", "not", "was", "has", "have", "with", "for", "its", "but",
	"this", "that", "from", "after", "when", "while", "very", "some", "all",
	"phone", "device", "mobile", "customer", "issue", "problem", "please",
}


def _words(text: str) -> set:
	return {w for w in re.findall(r"[a-z]{3,}", (text or "").lower()) if w not in _STOP}


# ──────────────────────────────────────────────────────────────────────────
# Scoring. Every signal votes on the same sixteen categories.
# ──────────────────────────────────────────────────────────────────────────
#
# The first version of this asked three sources in turn and took the first
# answer. History won almost every time, and history -- ranked by how often we
# fix a category on that model -- is a POPULARITY PRIOR, not a diagnosis. Asked
# about "charging very slow and gets hot near the port" it returned Buttons &
# Keys and Camera, and never mentioned Charging & Power, whose description reads
# "Charging port, adapter / charger, slow or no charging". Asked "it is not
# working" it answered with the same confidence.
#
# A tool that is confidently wrong at the counter gets switched off within a
# week. So every signal now scores the same list, symptom words are the strongest
# because they are the customer's own evidence, and a repair we have done before
# only counts when the description actually echoes it. Nothing scoring means we
# say we could not place it.

# What each signal is worth. Symptom words beat model priors on purpose.
_W_KEYWORD = 3.0     # the customer's word appears in the category's own description
_W_AI = 4.0          # a model that read the sentence, when one is configured
_W_HISTORY_ECHO = 2.0  # we fixed this on this model AND the notes echo the words
_W_HISTORY_PRIOR = 0.4  # we fix this on this model a lot -- a hint, never a finding
_MIN_SCORE = 1.0     # below this we are guessing, and we say so


def _history_rows(device_model: str, brand: str) -> list:
	if not (device_model or brand):
		return []
	return frappe.db.sql(
		"""
		SELECT il.issue_category, COUNT(DISTINCT il.parent) jobs,
		       GROUP_CONCAT(DISTINCT IFNULL(il.description, '') SEPARATOR ' ') notes
		FROM `tabSR Issue Line` il
		JOIN `tabService Request` sr ON sr.name = il.parent
		WHERE IFNULL(il.issue_category, '') <> ''
		  AND (sr.device_model = %(model)s OR (%(model)s = '' AND sr.brand = %(brand)s))
		GROUP BY il.issue_category
		""",
		{"model": device_model or "", "brand": brand or ""},
		as_dict=True,
	)


def _past_outcomes(categories: list, device_model: str) -> dict:
	"""What these repairs actually cost and how long they actually took.

	Read off submitted invoices and real delivery times rather than the rate
	card, because what we charged predicts what we will charge better than what
	we meant to charge.
	"""
	if not categories:
		return {}
	rows = frappe.db.sql(
		"""
		SELECT si.net_total amount,
		       TIMESTAMPDIFF(HOUR, sr.creation, sr.delivered_datetime) hours
		FROM `tabService Request` sr
		JOIN `tabSales Invoice` si ON si.name = sr.service_invoice AND si.docstatus = 1
		WHERE EXISTS (SELECT 1 FROM `tabSR Issue Line` il
		              WHERE il.parent = sr.name AND il.issue_category IN %(cats)s)
		  AND (%(model)s = '' OR sr.device_model = %(model)s)
		""",
		{"cats": tuple(categories), "model": device_model or ""},
		as_dict=True,
	)
	amounts = sorted(flt(r.amount) for r in rows if flt(r.amount) > 0)
	hours = sorted(cint(r.hours) for r in rows if cint(r.hours) > 0)
	if not amounts:
		return {}
	mid = lambda xs: xs[len(xs) // 2]
	return {
		"jobs": len(amounts),
		"median_amount": mid(amounts),
		"low": amounts[0],
		"high": amounts[-1],
		"median_hours": mid(hours) if hours else 0,
	}


_SYSTEM = (
	"You are a service adviser at an Indian mobile repair chain. You are given a "
	"customer's description of a fault and the exact list of fault categories and "
	"repair solutions this company uses. Map the description onto that list.\n"
	"Rules:\n"
	"1. Only ever return categories and solution codes from the lists given. Never "
	"invent one.\n"
	"2. Never state or estimate a price or a duration. You are not asked for one.\n"
	"3. A description can map to more than one category; a customer often reports "
	"several faults in one sentence.\n"
	"4. If the description is too vague to place, return an EMPTY issues list and "
	"put what you would need to ask in ask_the_customer. Do not guess.\n"
	'Return JSON: {"issues":[{"category":str,"confidence":0-1,"why":str}],'
	'"solutions":[str],"ask_the_customer":[str]}'
)


def _ai_votes(text: str, brand: str, model: str, categories: list, solutions: list) -> dict:
	"""Ask the model, if one is configured. Absence is normal, not an error."""
	from ch_mg_reports.ai.providers import get_provider
	from ch_mg_reports.ai.providers.base import LLMError

	try:
		provider = get_provider()
	except LLMError:
		return {}
	except Exception:
		return {}

	cat_lines = "\n".join(f"- {c.name}: {c.description or c.category_name}" for c in categories)
	sol_lines = "\n".join(
		f"- {s.name} ({s.solution_name}) for {s.issue_category}" for s in solutions)
	user = (
		f"Device: {brand} {model}\n"
		f"Customer's words: {text}\n\n"
		f"Fault categories:\n{cat_lines}\n\nRepair solutions:\n{sol_lines}"
	)
	try:
		resp = provider.chat_json(system=_SYSTEM, user=user, temperature=0.1, max_tokens=700)
	except Exception:
		# The counter must never be blocked by an AI outage.
		frappe.log_error(frappe.get_traceback(), "gofix triage: provider")
		return {}

	data = resp.data if isinstance(resp.data, dict) else {}
	valid_cats = {c.name for c in categories}
	valid_sols = {s.name for s in solutions}
	return {
		"issues": [i for i in (data.get("issues") or []) if i.get("category") in valid_cats],
		"solutions": [s for s in (data.get("solutions") or []) if s in valid_sols][:6],
		"ask": [str(q)[:120] for q in (data.get("ask_the_customer") or [])][:3],
		"model": resp.model,
	}


def _score(text: str, brand: str, device_model: str, categories: list,
           all_solutions: list) -> dict:
	"""One scoreboard, every signal voting on it."""
	asked = _words(text)
	board = {c.name: {"score": 0.0, "why": [], "signals": set()} for c in categories}

	# The customer's own words against each category's description.
	for c in categories:
		hits = asked & _words(f"{c.category_name} {c.description or ''}")
		if hits:
			board[c.name]["score"] += _W_KEYWORD * min(len(hits), 3)
			board[c.name]["why"].append(_("mentions {0}").format(", ".join(sorted(hits)[:3])))
			board[c.name]["signals"].add("keyword")

	# What we have actually repaired on this model.
	for r in _history_rows(device_model, brand):
		if r.issue_category not in board:
			continue
		echo = asked & _words(r.notes)
		if echo:
			board[r.issue_category]["score"] += _W_HISTORY_ECHO
			board[r.issue_category]["why"].append(
				_("{0} past repair(s) on this model described it the same way").format(r.jobs))
			board[r.issue_category]["signals"].add("history")
		else:
			# A prior, deliberately too small to reach _MIN_SCORE alone.
			board[r.issue_category]["score"] += _W_HISTORY_PRIOR
			board[r.issue_category]["signals"].add("prior")

	ai = _ai_votes(text, brand, device_model, categories, all_solutions)
	for i in ai.get("issues") or []:
		cat = i["category"]
		board[cat]["score"] += _W_AI * max(flt(i.get("confidence")) or 0.5, 0.3)
		if i.get("why"):
			board[cat]["why"].append(str(i["why"])[:120])
		board[cat]["signals"].add("ai")

	# A category is only reported when the CUSTOMER'S OWN WORDS reached it --
	# a keyword hit, or a model that read the sentence. History corroborates a
	# finding; it can never be the whole of one. Without this rule "it is not
	# working" came back as Sensors & Biometrics at Rs 1,100, because one past
	# repair on that model happened to share a common word.
	direct = {"keyword", "ai"}
	ranked = sorted(
		((n, v) for n, v in board.items()
		 if v["score"] >= _MIN_SCORE and (v["signals"] & direct)),
		key=lambda kv: kv[1]["score"], reverse=True)[:4]

	return {
		"ranked": ranked,
		"ai_solutions": ai.get("solutions") or [],
		"ask": ai.get("ask") or [],
		"ai_model": ai.get("model"),
		"ai_ran": bool(ai),
	}


def _solutions_for(categories: list) -> list:
	if not categories:
		return []
	return frappe.get_all(
		"Repair Solution",
		filters={"issue_category": ("in", categories), "is_active": 1},
		fields=["name", "solution_name", "issue_category", "estimated_minutes",
		        "requires_spare"],
		order_by="estimated_minutes",
	)


@frappe.whitelist()
def triage(description=None, brand=None, device_model=None, device_item=None,
           company=None, warranty_status=None) -> dict:
	"""What is probably wrong, what it probably costs, and how sure we are.

	Called from the counter while the customer is still standing there. Returns a
	band and the reasoning behind it, never a quote -- see DISCLAIMER.
	"""
	frappe.has_permission("Service Request", "create", throw=True)

	text = (description or "").strip()
	brand = (brand or "").strip()
	device_model = (device_model or "").strip()
	if len(text) < 8:
		return {"ready": False,
		        "reason": _("Describe the fault in a few more words and this will "
		                    "suggest what it is likely to be.")}

	categories = frappe.get_all(
		"Issue Category", filters={"is_active": 1},
		fields=["name", "category_name", "description", "estimated_repair_hours"])
	all_solutions = frappe.get_all(
		"Repair Solution", filters={"is_active": 1},
		fields=["name", "solution_name", "issue_category"])

	scored = _score(text, brand, device_model, categories, all_solutions)
	ranked = scored["ranked"]

	# Nothing scored above the floor. Say so. The counter can still book the
	# device in under General Diagnosis, which is what that category is for --
	# but we do not dress a guess up as three confident findings.
	if not ranked:
		return {
			"ready": True,
			"confident": False,
			"source": "unplaced",
			"basis": _("nothing in the description matched a known fault"),
			"disclaimer": _("Could not place this fault from the description. Book it "
			                "in for diagnosis, or ask the customer the questions below."),
			"issues": [{"category": "General Diagnosis", "score": 0, "signals": [],
			            "why": [_("fault not recognised from the description")]}]
			           if any(c.name == "General Diagnosis" for c in categories) else [],
			"solutions": [], "price": {}, "history": {},
			"estimated_minutes": 0, "needs_parts": False,
			"ask_the_customer": scored["ask"] or [
				_("What was the device doing when the fault started?"),
				_("Has it been dropped, or been near water?"),
				_("Does it happen all the time, or only sometimes?"),
			],
		}

	picked = [n for n, _v in ranked]
	signals = set().union(*[v["signals"] for _n, v in ranked])
	source = ("ai" if "ai" in signals
	          else "history" if "history" in signals
	          else "keyword")
	basis = {
		"ai": _("the description, read against our fault catalogue"),
		"history": _("past repairs of this model that were described the same way"),
		"keyword": _("words in the description matched to our fault catalogue"),
	}[source]

	solutions = _solutions_for(picked)
	if scored["ai_solutions"]:
		preferred = set(scored["ai_solutions"])
		solutions.sort(key=lambda s: s.name not in preferred)

	# ── The money. From the rules engine, never from the model. ──────────
	price = {}
	if solutions:
		try:
			from gofix.gofix_services.doctype.gofix_pricing_rule.gofix_pricing_rule import (
				calculate_estimate_from_rules,
			)
			quoted = calculate_estimate_from_rules(
				issue_categories=picked,
				solutions=[{"repair_solution": s.name, "issue_category": s.issue_category}
				           for s in solutions[:4]],
				brand=brand,
				warranty_status=warranty_status,
				company=company,
				device_item=device_item,
			)
			price = {
				"labour": flt(quoted.get("labor_total")),
				"parts": flt(quoted.get("spare_total")),
				"total": flt(quoted.get("estimate_total")),
			}
		except Exception:
			frappe.log_error(frappe.get_traceback(), "gofix triage: pricing")

	# Two independent signals, or one strong one, before we call it confident.
	top = ranked[0][1]["score"]
	confident = top >= (_W_KEYWORD + _W_HISTORY_ECHO) or "ai" in ranked[0][1]["signals"]

	return {
		"ready": True,
		"confident": confident,
		"source": source,
		"basis": basis,
		"disclaimer": DISCLAIMER.format(basis=basis),
		"issues": [{"category": n, "score": round(v["score"], 1),
		            "why": v["why"][:2], "signals": sorted(v["signals"])}
		           for n, v in ranked],
		"solutions": [{"name": s.name, "label": s.solution_name,
		               "issue_category": s.issue_category,
		               "minutes": cint(s.estimated_minutes),
		               "needs_part": bool(s.requires_spare)} for s in solutions[:6]],
		"price": price,
		"history": _past_outcomes(picked, device_model),
		"estimated_minutes": sum(cint(s.estimated_minutes) for s in solutions[:4]),
		"needs_parts": any(s.requires_spare for s in solutions[:4]),
		"ask_the_customer": scored["ask"],
		"ai_used": scored["ai_ran"],
	}
