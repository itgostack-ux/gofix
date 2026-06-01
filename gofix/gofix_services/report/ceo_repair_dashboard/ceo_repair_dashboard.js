// Copyright (c) 2026, GoStack and contributors
// For license information, please see license.txt

frappe.query_reports["CEO Repair Dashboard"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
			get_query: () => ({
				filters: [["Company", "name", "like", "GoFix%"]]
			}),
		},
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
		}
	],
};
