# Copyright (c) 2026, GoFix and contributors

"""The repair-operation master.

Identity is the ``solution_code``, never the label. A repair catalogue is keyed
by a stable code everywhere it is done seriously — SAP standard operations, an
FSL Work Type, an OEM labour-operation code — because the label is a display
string that gets reworded, translated and reused, while the code is what the
service Item (``GFR-<code>``), the pricing rules, the job cards and every
historical invoice line are anchored to.

Keying on the label instead made "Display Change" a name that could exist
exactly once in the whole system, so a solution legitimately needed under two
Issue Categories could not be created at all.
"""

import re

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cstr

CODE_MAX_LEN = 40

# Words that carry no distinguishing weight in a repair label. Dropped only
# while abbreviating an over-long name, and never all of them — a code still
# has to be readable on a job card.
_FILLER = ("AND", "THE", "FOR", "WITH", "OF")


def slugify_code(text: str) -> str:
	"""``Screen / Display Change`` -> ``SCREEN-DISPLAY-CHANGE``."""
	slug = re.sub(r"[^A-Z0-9]+", "-", cstr(text).upper()).strip("-")
	if len(slug) <= CODE_MAX_LEN:
		return slug
	# Too long: drop filler words, then truncate on a word boundary so the code
	# never ends mid-word.
	parts = [p for p in slug.split("-") if p not in _FILLER] or slug.split("-")
	out = ""
	for part in parts:
		candidate = f"{out}-{part}" if out else part
		if len(candidate) > CODE_MAX_LEN:
			break
		out = candidate
	return out or slug[:CODE_MAX_LEN].rstrip("-")


def make_unique_code(text: str, exclude: str | None = None) -> str:
	"""A free ``solution_code`` derived from ``text``.

	Collisions get a numeric suffix (``SCREEN-CHANGE-2``) rather than being
	rejected — the caller quick-creating from the Ops Hub has no way to invent
	a code and should not be stopped from working.
	"""
	base = slugify_code(text) or "SOLUTION"
	candidate = base
	n = 1
	while True:
		clash = frappe.db.get_value("Repair Solution", {"solution_code": candidate}, "name")
		if not clash or clash == exclude:
			return candidate
		n += 1
		suffix = f"-{n}"
		candidate = f"{base[:CODE_MAX_LEN - len(suffix)].rstrip('-')}{suffix}"


class RepairSolution(Document):
	def before_naming(self):
		# The Ops Hub quick-create supplies a label and nothing else, so the
		# code is derived here rather than made a data-entry burden.
		self.solution_code = slugify_code(self.solution_code) if self.solution_code else ""
		if not self.solution_code:
			self.solution_code = make_unique_code(self.solution_name)

	def validate(self):
		self.solution_name = " ".join(cstr(self.solution_name).split())
		if not self.solution_name:
			frappe.throw(_("Solution Name is required."), title=_("Validation Error"))

		self.solution_code = slugify_code(self.solution_code)
		if not self.solution_code:
			frappe.throw(_("Solution Code is required."), title=_("Validation Error"))

		self._validate_unique_label()
		self._clean_applicability()

	def _clean_applicability(self):
		"""Drop applicability rows that narrow nothing, and de-duplicate.

		A row with every column blank matches every device, so an accidental empty
		grid row would silently turn a deliberately restricted repair back into a
		universal one — the exact opposite of what the person adding it meant.

		This lives on the parent because Frappe does not run a child DocType's own
		``validate``; only the parent's is called.
		"""
		kept, seen = [], set()
		for row in self.get("applies_to") or []:
			key = (
				row.device_category or "", row.device_sub_category or "",
				row.device_brand or "", row.device_model or "",
			)
			if not any(key) or key in seen:
				continue
			seen.add(key)
			row.idx = len(kept) + 1
			kept.append(row)
		if len(kept) != len(self.get("applies_to") or []):
			self.set("applies_to", kept)

	def _validate_unique_label(self):
		"""One label per Issue Category.

		The label is free to repeat ACROSS categories — that is the whole point
		of the re-key — but repeating it *within* one category gives the person
		picking a solution two identical-looking options and no way to choose.
		"""
		clash = frappe.db.get_value(
			"Repair Solution",
			{
				"solution_name": self.solution_name,
				"issue_category": self.issue_category,
				"name": ("!=", self.name or ""),
			},
			["name", "solution_code"],
			as_dict=True,
		)
		if clash:
			frappe.throw(
				_("{0} already has a solution called <b>{1}</b> ({2}). "
				  "Use that one, or give this repair a distinct name.").format(
					frappe.bold(self.issue_category), self.solution_name, clash.solution_code
				),
				title=_("Duplicate Solution"),
			)
