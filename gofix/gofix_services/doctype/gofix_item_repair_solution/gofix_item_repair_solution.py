# Copyright (c) 2026, GoFix and contributors

from frappe.model.document import Document


class GoFixItemRepairSolution(Document):
	"""Which repair a spare Item serves.

	The Item is the master: these rows are mirrored into ``Solution Spare
	Mapping`` so the repair flow can look the relationship up from either side.
	"""

	pass
