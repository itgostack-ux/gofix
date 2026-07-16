// Copyright (c) 2026, GoStack and contributors
// For license information, please see license.txt

frappe.query_reports["GoFix Ticket Status by Location"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
		},
		{
			fieldname: "location",
			label: __("Location (Store)"),
			fieldtype: "Link",
			options: "Warehouse",
			get_query: () => ({
				filters: { is_group: 0, disabled: 0 },
			}),
		},
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.add_months(frappe.datetime.get_today(), -1),
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
		},
		{
			fieldname: "status",
			label: __("Status"),
			fieldtype: "Select",
			options: [
				"", "Draft", "Open", "In Service", "Waiting for Parts",
				"Ready for Delivery", "Completed", "Invoiced", "Delivered",
				"Cancelled", "Rejected",
			].join("\n"),
		},
		{
			fieldname: "include_closed",
			label: __("Include Cancelled / Rejected"),
			fieldtype: "Check",
			default: 0,
		},
	],

	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "status" && data) {
			const s = String(data.status || "");
			let color = "orange";
			if (s.startsWith("Completed") || s.startsWith("Invoiced") || s.startsWith("Delivered")) color = "green";
			else if (s.startsWith("Cancelled") || s.startsWith("Rejected")) color = "red";
			value = `<span class="indicator-pill ${color}">${value}</span>`;
		}
		if (column.fieldname === "qc_status" && data && data.qc_status) {
			const color = data.qc_status === "Pass" ? "green" : data.qc_status === "Fail" ? "red" : "orange";
			value = `<span class="indicator-pill ${color}">${value}</span>`;
		}
		if (column.fieldname === "days_open" && data && data.days_open > 7) {
			value = `<span style="color:var(--red-600);font-weight:600">${value}</span>`;
		}
		return value;
	},
};
