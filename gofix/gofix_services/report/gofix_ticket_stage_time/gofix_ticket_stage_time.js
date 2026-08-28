// Copyright (c) 2026, GoStack and contributors
// For license information, please see license.txt

const gofix_ticket_stage_time_store_query = () => ({
	query: "gofix.gofix_services.store_context.warehouse_query",
	filters: {
		company: frappe.query_report.get_filter_value("company"),
	},
});

frappe.query_reports["GoFix Ticket Stage Time"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
		},
		{
			fieldname: "service_request",
			label: __("SR Number"),
			fieldtype: "Link",
			options: "Service Request",
		},
		{
			fieldname: "store",
			label: __("Store / Location"),
			fieldtype: "Link",
			options: "Warehouse",
			get_query: gofix_ticket_stage_time_store_query,
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
			fieldname: "open_only",
			label: __("Open Tickets Only"),
			fieldtype: "Check",
			default: 0,
		},
	],

	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (data && column.fieldname === "bottleneck" && data.bottleneck) {
			value = `<span style="color:var(--orange-600);font-weight:600">${value}</span>`;
		}
		if (data && column.fieldname === "current_stage") {
			value = `<span class="indicator-pill blue">${value}</span>`;
		}
		return value;
	},
};
