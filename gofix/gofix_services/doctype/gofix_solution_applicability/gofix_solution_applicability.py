# Copyright (c) 2026, GoFix and contributors

"""One applicability rule on a Repair Solution.

A row is a *positive* rule: the solution applies to a device when every column
the row fills in matches that device. An empty column means "any". A solution
with no rows at all is universal — that is the default, so nothing in an
existing catalogue changes until someone declares a restriction.

This is the same shape as a SAP task-list-to-equipment assignment or a Field
Service Work Type tied to a Product family: applicability is its own many-to-many
relation, not a field on the part or on the price.
"""

from frappe.model.document import Document


class GoFixSolutionApplicability(Document):
	# No validate() here on purpose: Frappe does not run a child DocType's own
	# validate, so the guard against a row that narrows nothing lives in
	# RepairSolution._clean_applicability().
	pass
