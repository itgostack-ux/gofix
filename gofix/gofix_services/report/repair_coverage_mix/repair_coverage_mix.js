// Copyright (c) 2026, GoStack and contributors
// For license information, please see license.txt

const repair_coverage_mix_active_company = () => {
	const lock = window.ch_erp15 && window.ch_erp15.company_lock;
	if (lock && typeof lock.active_company === "function") {
		return lock.active_company() || "";
	}
	return frappe.defaults.get_user_default("Company") || frappe.defaults.get_user_default("company") || "";
};

const repair_coverage_mix_store_query = () => ({
	query: "gofix.gofix_services.store_context.warehouse_query",
	filters: {
		company: frappe.query_report.get_filter_value("company") || repair_coverage_mix_active_company(),
	},
});

frappe.query_reports["Repair Coverage Mix"] = {
	filters: [
		...gofix_scope_filters(),
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.month_start(),
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.now_date(),
		},
		{
			fieldname: "source_warehouse",
			label: __("Source Warehouse"),
			fieldtype: "Link",
			options: "Warehouse",
			get_query: repair_coverage_mix_store_query,
		},
	],

	formatter(value, row, column, data, default_formatter) {
		const formatted = default_formatter(value, row, column, data);
		if (!data) return formatted;

		// Colour the row by which pocket pays, matching the Ops Hub badges so
		// the same three words mean the same three things on both screens.
		if (column.fieldname === "coverage") {
			const tint = {
				"In-Warranty": "#166534",
				"VAS Claim": "#92400e",
				"Non-Warranty": "#475569",
				"Unclassified": "#b91c1c",
			}[data.coverage];
			if (tint) return `<span style="color:${tint};font-weight:600">${formatted}</span>`;
		}

		// A ticket with no category is one the classifier never ran on. That is
		// a data-quality signal, not a paid repair, so it is called out rather
		// than sitting quietly in the table.
		if (data.coverage === "Unclassified" && column.fieldname === "jobs" && value) {
			return `<span style="color:#b91c1c;font-weight:600">${formatted}</span>`;
		}

		// Cost we carry is the number this report exists to surface.
		if (column.fieldname === "total_cost" && data.coverage === "In-Warranty" && value) {
			return `<span style="color:#b91c1c">${formatted}</span>`;
		}
		return formatted;
	},
};
