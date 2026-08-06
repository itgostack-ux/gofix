"""Classify legacy Service Request history by business event type.

Before the repair model consolidation, lifecycle changes and Ops Hub stage
changes shared the same child table without a discriminator.  Keep the table
as the ticket audit stream, but make the two state machines explicit.
"""

import frappe


OPS_STAGE_VALUES = (
	"draft",
	"analysis",
	"confirm",
	"solutions",
	"assign",
	"repair",
	"qc",
	"invoice",
	"rework",
	"done",
	"closed",
	"Analysis",
	"Customer Confirmation",
	"Solution Assignment",
	"Technician Assignment",
	"Repair",
	"Quality Control",
	"Invoice",
	"Rework",
)


def execute():
	if not frappe.db.table_exists("GoFix Status Log") or not frappe.db.has_column(
		"GoFix Status Log", "event_type"
	):
		return

	# New and unclassified history is lifecycle history by default.  Distinctive
	# Ops Hub stage values are then promoted to the operational state machine.
	frappe.db.sql(
		"""
		UPDATE `tabGoFix Status Log`
		   SET event_type = 'Lifecycle'
		 WHERE COALESCE(event_type, '') = ''
		"""
	)
	placeholders = ", ".join(["%s"] * len(OPS_STAGE_VALUES))
	frappe.db.sql(
		f"""
		UPDATE `tabGoFix Status Log`
		   SET event_type = 'Operations Stage'
		 WHERE from_status IN ({placeholders})
		    OR to_status IN ({placeholders})
		""",
		OPS_STAGE_VALUES + OPS_STAGE_VALUES,
	)
