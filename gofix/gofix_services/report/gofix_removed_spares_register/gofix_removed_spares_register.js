// Copyright (c) 2026, GoStack and contributors
// For license information, please see license.txt

frappe.query_reports["GoFix Removed Spares Register"] = {
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
			label: __("Location (Store / Hub)"),
			fieldtype: "Link",
			options: "Warehouse",
			get_query: () => ({ filters: { is_group: 0, disabled: 0 } }),
		},
		{
			fieldname: "zone",
			label: __("Zone"),
			fieldtype: "Link",
			options: "CH Store Zone",
		},
		{
			fieldname: "spare_category",
			label: __("Spare Category"),
			fieldtype: "Link",
			options: "CH Category",
		},
		{
			fieldname: "condition",
			label: __("Condition"),
			fieldtype: "Select",
			options: ["", "Good", "Faulty", "Damaged", "Scrap"].join("\n"),
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
			fieldname: "missing_only",
			label: __("Missing Details Only"),
			fieldtype: "Check",
			default: 0,
		},
	],

	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (data && ["removed_part_serial", "removed_part_condition"].includes(column.fieldname) && String(data[column.fieldname] || "").includes("⚠")) {
			value = `<span style="color:var(--red-600);font-weight:600">${value}</span>`;
		}
		if (column.fieldname === "removed_part_condition" && data && data.removed_part_condition === "Faulty") {
			value = `<span class="indicator-pill orange">${value}</span>`;
		}
		return value;
	},
};
