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
		if (column.fieldname === "timeline_btn" && data && data.name) {
			value = `<a href="#" onclick="gofix_ticket_timeline('${frappe.utils.escape_html(data.name)}'); return false;"
				style="font-weight:600">📈 ${__("View")}</a>`;
		}
		return value;
	},
};

// ── Per-ticket timeline dialog ──────────────────────────────────────────────

const GOFIX_TL_COLORS = [
	"#2563eb", "#16a34a", "#d97706", "#dc2626", "#7c3aed",
	"#0891b2", "#be185d", "#65a30d", "#b45309", "#4f46e5",
];

function gofix_tl_parse(dt) {
	return new Date(String(dt).replace(" ", "T"));
}

function gofix_tl_duration(ms) {
	if (ms < 0) ms = 0;
	const mins = Math.round(ms / 60000);
	if (mins < 1) return __("moments");
	if (mins < 60) return `${mins}m`;
	const hrs = Math.floor(mins / 60);
	if (hrs < 24) return `${hrs}h ${mins % 60}m`;
	const days = Math.floor(hrs / 24);
	return `${days}d ${hrs % 24}h`;
}

window.gofix_ticket_timeline = async function (sr_name) {
	let events;
	try {
		events = await frappe.xcall(
			"gofix.gofix_services.page.gofix_ops_hub.gofix_ops_hub.get_repair_history",
			{ sr_name }
		);
	} catch (e) {
		frappe.msgprint({ title: __("Timeline"), message: e.message || String(e), indicator: "red" });
		return;
	}
	if (!events || !events.length) {
		frappe.msgprint(__("No timeline events recorded for {0}.", [sr_name]));
		return;
	}

	const esc = frappe.utils.escape_html;
	const times = events.map((e) => gofix_tl_parse(e.at));
	const t0 = times[0];
	const tN = times[times.length - 1];
	const total = Math.max(tN - t0, 1);

	// ── Phase-duration bar: one segment per gap between consecutive events ──
	let segments = "";
	for (let i = 1; i < events.length; i++) {
		const gap = times[i] - times[i - 1];
		if (gap <= 0) continue;
		const pct = Math.max((gap / total) * 100, 0.8);
		const color = GOFIX_TL_COLORS[(i - 1) % GOFIX_TL_COLORS.length];
		const tip = `${esc(events[i - 1].title)} → ${esc(events[i].title)}: ${gofix_tl_duration(gap)}`;
		segments += `<div title="${tip}" style="width:${pct}%;background:${color};height:100%;"></div>`;
	}
	const bar = `
		<div style="margin-bottom:4px;font-size:12px;color:var(--text-muted);">
			${__("Total turnaround")}: <b style="color:var(--text-color)">${gofix_tl_duration(total)}</b>
			&nbsp;·&nbsp; ${frappe.datetime.str_to_user(events[0].at)} → ${frappe.datetime.str_to_user(events[events.length - 1].at)}
			&nbsp;·&nbsp; ${events.length} ${__("events")}
		</div>
		<div style="display:flex;height:18px;border-radius:9px;overflow:hidden;border:1px solid var(--border-color);margin-bottom:16px;">
			${segments || `<div style="width:100%;background:var(--control-bg)"></div>`}
		</div>
		<div style="font-size:11px;color:var(--text-muted);margin:-10px 0 14px;">
			${__("Hover a segment to see the phase and how long it took.")}
		</div>`;

	// ── Vertical event timeline ─────────────────────────────────────────────
	let rows = "";
	for (let i = 0; i < events.length; i++) {
		const ev = events[i];
		const color = i === 0 ? "var(--gray-500)" : GOFIX_TL_COLORS[(i - 1) % GOFIX_TL_COLORS.length];
		const gap = i > 0 ? `<span style="color:var(--text-muted);font-size:11px;">&nbsp;(+${gofix_tl_duration(times[i] - times[i - 1])})</span>` : "";
		const ref = ev.ref_name && ev.ref_doctype
			? `&nbsp;<a href="/app/${frappe.router.slug(ev.ref_doctype)}/${encodeURIComponent(ev.ref_name)}"
					style="font-size:11px;">${esc(ev.ref_name)}</a>`
			: "";
		const detail = ev.detail ? `<div style="color:var(--text-muted);font-size:12px;margin-top:1px;">${esc(ev.detail)}</div>` : "";
		rows += `
			<div style="display:flex;gap:12px;">
				<div style="display:flex;flex-direction:column;align-items:center;">
					<div style="width:12px;height:12px;border-radius:50%;background:${color};margin-top:3px;flex-shrink:0;"></div>
					${i < events.length - 1 ? `<div style="width:2px;flex:1;background:var(--border-color);min-height:14px;"></div>` : ""}
				</div>
				<div style="padding-bottom:14px;min-width:0;">
					<div style="font-size:11px;color:var(--text-muted);white-space:nowrap;">
						${frappe.datetime.str_to_user(ev.at)}${gap}
					</div>
					<div style="font-weight:600;font-size:13px;">${esc(ev.title)}${ref}</div>
					${detail}
				</div>
			</div>`;
	}

	const d = new frappe.ui.Dialog({
		title: __("Repair Timeline — {0}", [sr_name]),
		size: "large",
		fields: [{ fieldtype: "HTML", fieldname: "tl" }],
		primary_action_label: __("Open Ticket"),
		primary_action() {
			frappe.set_route("Form", "Service Request", sr_name);
			d.hide();
		},
	});
	d.fields_dict.tl.$wrapper.html(`<div style="max-height:65vh;overflow-y:auto;padding-right:6px;">${bar}${rows}</div>`);
	d.show();
};
