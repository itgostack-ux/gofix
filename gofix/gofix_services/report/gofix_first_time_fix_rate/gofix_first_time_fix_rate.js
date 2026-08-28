// Copyright (c) 2026, GoStack and contributors

const gofix_first_time_fix_rate_store_query = () => ({
	query: "gofix.gofix_services.store_context.warehouse_query",
	filters: {
		company: frappe.query_report.get_filter_value("company"),
	},
});

frappe.query_reports["GoFix First Time Fix Rate"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
		},
		{
			fieldname: "from_date",
			label: __("From"),
			fieldtype: "Date",
			default: frappe.datetime.add_months(frappe.datetime.get_today(), -3),
		},
		{
			fieldname: "to_date",
			label: __("To"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
		},
		{
			fieldname: "warehouse",
			label: __("Store"),
			fieldtype: "Link",
			options: "Warehouse",
			get_query: gofix_first_time_fix_rate_store_query,
		},
		{
			fieldname: "group_by",
			label: __("Group By"),
			fieldtype: "Select",
			options: ["Store", "Technician", "Issue Category"],
			default: "Store",
		},
	],

	formatter(value, row, column, data, default_formatter) {
		const out = default_formatter(value, row, column, data);
		if (column.fieldname === "ftfr" && data) {
			// 90% is the line most repair chains hold themselves to.
			const rate = parseFloat(data.ftfr) || 0;
			const colour = rate >= 90 ? "#2C6B4C" : rate >= 80 ? "#96590A" : "#A63A2E";
			return `<span style="color:${colour};font-weight:600">${out}</span>`;
		}
		return out;
	},
};
