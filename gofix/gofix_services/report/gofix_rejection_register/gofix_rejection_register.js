// Copyright (c) 2026, GoFix and contributors

frappe.query_reports["GoFix Rejection Register"] = {
	filters: [
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.add_months(frappe.datetime.get_today(), -3),
			reqd: 1,
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			reqd: 1,
		},
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
		},
		{
			fieldname: "source_warehouse",
			label: __("Store"),
			fieldtype: "Link",
			options: "Warehouse",
			get_query: () => ({ filters: { is_group: 0 } }),
		},
		{
			fieldname: "decision",
			label: __("Outcome"),
			fieldtype: "Select",
			options: ["", "Rejected", "Cancelled", "Expired", "Withdrawn"].join("\n"),
			description: __("Blank shows every ticket that was not serviced."),
		},
		{
			fieldname: "include_withdrawn",
			label: __("Include Customer Withdrawals"),
			fieldtype: "Check",
			default: 0,
			description: __("A withdrawal is the customer's decision, not a rejection — counted separately."),
		},
	],

	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "rejection_reason" && data && /not recorded/.test(data.rejection_reason || "")) {
			// A rejection with no reason is the one row a manager must chase.
			value = `<span style="color:var(--red-500,#e24c4c)">${value}</span>`;
		}
		if (column.fieldname === "decision" && data && data.decision === "Rejected") {
			value = `<b style="color:var(--red-500,#e24c4c)">${value}</b>`;
		}
		return value;
	},
};
