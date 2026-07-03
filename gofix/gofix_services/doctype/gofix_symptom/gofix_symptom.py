# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class GoFixSymptom(Document):
	def validate(self) -> None:
		# Only one "Not-sure / expert check" symptom per device type — the tablet
		# uses this flag to enforce mutual exclusivity, so more than one would
		# make the client rule ambiguous.
		if self.is_expert_check:
			existing = frappe.db.exists(
				"GoFix Symptom",
				{
					"device_type": self.device_type,
					"is_expert_check": 1,
					"name": ("!=", self.name or ""),
				},
			)
			if existing:
				frappe.throw(
					_("A \"Not sure / expert check\" symptom already exists for {0}: {1}").format(
						self.device_type, existing
					)
				)
		if self.is_other:
			existing = frappe.db.exists(
				"GoFix Symptom",
				{
					"device_type": self.device_type,
					"is_other": 1,
					"name": ("!=", self.name or ""),
				},
			)
			if existing:
				frappe.throw(
					_("An \"Other\" symptom already exists for {0}: {1}").format(
						self.device_type, existing
					)
				)
