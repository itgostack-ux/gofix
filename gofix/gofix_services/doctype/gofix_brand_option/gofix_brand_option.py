# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class GoFixBrandOption(Document):
	def validate(self) -> None:
		self._resolve_brand_link()

	def _resolve_brand_link(self) -> None:
		"""Every option must point at the common item-master Brand.

		The label (brand_name) stays customer-facing; ``brand`` is the
		canonical reference. Blank links are auto-resolved by exact label
		match (case-insensitive); anything unresolvable must be created in
		the Brand master first — the "Other" pseudo-option is the only row
		allowed to stay unlinked.
		"""

		label = (self.brand_name or "").strip()
		if label.lower() == "other":
			self.brand = None
			return
		if self.brand:
			return
		match = frappe.db.get_value("Brand", label, "name") or frappe.db.get_value(
			"Brand", {"brand": ("like", label)}, "name"
		)
		if match:
			self.brand = match
			return
		frappe.throw(
			_(
				"No Brand named {0} exists in the item master. Create it under "
				"Brand first (or link the matching Brand), so token reporting "
				"stays in sync with the catalogue."
			).format(frappe.bold(label)),
			title=_("Brand not in item master"),
		)
