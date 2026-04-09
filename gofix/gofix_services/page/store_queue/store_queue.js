frappe.pages["store-queue"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Store Queue"),
		single_column: true,
	});
	page.main.html(`<div id="store-queue-app"></div>`);
	new StoreQueue(page);
};

class StoreQueue {
	constructor(page) {
		this.page = page;
		this.wrapper = page.main.find("#store-queue-app");
		this.warehouse = "";
		this.stage_filter = "all";
		this.search = "";
		this.data = { requests: [], summary: {} };
		this.init();
	}

	async init() {
		const ctx = await frappe.xcall(
			"gofix.gofix_services.page.store_queue.store_queue.get_store_context"
		);
		this.warehouses = ctx.warehouses || [];
		this.warehouse = ctx.default_warehouse || "";
		this.render_toolbar();
		this.render_layout();
		this.load_queue();
	}

	render_toolbar() {
		this.page.clear_fields();

		this.wh_field = this.page.add_field({
			label: __("Store"),
			fieldtype: "Select",
			fieldname: "warehouse",
			options: ["", ...this.warehouses],
			default: this.warehouse,
			change: () => {
				this.warehouse = this.wh_field.get_value();
				this.load_queue();
			},
		});

		this.search_field = this.page.add_field({
			label: __("Search"),
			fieldtype: "Data",
			fieldname: "search",
			change: () => {
				this.search = this.search_field.get_value();
				this.load_queue();
			},
		});

		this.page.set_secondary_action(__("Refresh"), () => this.load_queue(), "refresh");
	}

	render_layout() {
		this.wrapper.html(`
			<div class="store-queue-container">
				<div class="sq-summary-bar"></div>
				<div class="sq-stage-tabs"></div>
				<div class="sq-request-list"></div>
				<div class="sq-detail-panel" style="display:none;"></div>
			</div>
		`);
	}

	async load_queue() {
		const data = await frappe.xcall(
			"gofix.gofix_services.page.store_queue.store_queue.get_queue",
			{
				warehouse: this.warehouse,
				search: this.search,
				stage_filter: this.stage_filter,
			}
		);
		this.data = data;
		this.render_summary(data.summary);
		this.render_list(data.requests);
	}

	render_summary(summary) {
		if (!summary) return;
		const stages = [
			{ key: "new_request", label: "New", color: "blue" },
			{ key: "awaiting_analysis", label: "Analysis", color: "orange" },
			{ key: "awaiting_approval", label: "Approval", color: "yellow" },
			{ key: "approved", label: "Approved", color: "green" },
			{ key: "in_repair", label: "Repair", color: "purple" },
			{ key: "qc", label: "QC", color: "cyan" },
			{ key: "ready_invoice", label: "Invoice", color: "green" },
			{ key: "ready_delivery", label: "Delivery", color: "darkgreen" },
			{ key: "not_repairable", label: "Not Repairable", color: "red" },
		];

		const bar = this.wrapper.find(".sq-summary-bar");
		bar.html(`
			<div class="sq-summary" style="display:flex;gap:8px;flex-wrap:wrap;padding:10px 0;">
				${stages
					.map(
						(s) => `
					<div class="sq-badge ${this.stage_filter === s.key ? "active" : ""}"
						data-stage="${s.key}"
						style="cursor:pointer;padding:4px 12px;border-radius:16px;
						background:var(--bg-${s.color}, #eee);font-size:12px;
						border:${this.stage_filter === s.key ? "2px solid var(--primary)" : "1px solid #ddd"};">
						${s.label}: <b>${summary[s.key] || 0}</b>
					</div>
				`
					)
					.join("")}
				<div class="sq-badge ${this.stage_filter === "all" ? "active" : ""}"
					data-stage="all"
					style="cursor:pointer;padding:4px 12px;border-radius:16px;
					background:#f5f5f5;font-size:12px;
					border:${this.stage_filter === "all" ? "2px solid var(--primary)" : "1px solid #ddd"};">
					All: <b>${summary.total || 0}</b>
				</div>
			</div>
		`);

		bar.find(".sq-badge").on("click", (e) => {
			this.stage_filter = $(e.currentTarget).data("stage");
			this.load_queue();
		});
	}

	render_list(requests) {
		const list_el = this.wrapper.find(".sq-request-list");

		if (!requests || !requests.length) {
			list_el.html(`<div class="text-muted text-center p-5">${__("No requests found")}</div>`);
			return;
		}

		const priority_colors = { Urgent: "red", High: "orange", Medium: "blue", Low: "grey" };

		list_el.html(`
			<table class="table table-hover" style="font-size:13px;">
				<thead>
					<tr>
						<th>${__("Request")}</th>
						<th>${__("Customer")}</th>
						<th>${__("Device")}</th>
						<th>${__("Issue")}</th>
						<th>${__("Stage")}</th>
						<th>${__("Priority")}</th>
						<th>${__("Days")}</th>
						<th>${__("Actions")}</th>
					</tr>
				</thead>
				<tbody>
					${requests
						.map(
							(r) => `
					<tr class="sq-row" data-name="${r.name}" style="cursor:pointer;">
						<td><a href="/app/service-request/${r.name}">${r.name}</a></td>
						<td>${r.customer_name || ""}<br><small class="text-muted">${r.contact_number || ""}</small></td>
						<td>${r.device_item_name || ""}<br><small class="text-muted">${r.serial_no || ""}</small></td>
						<td>${r.issue_category || ""}</td>
						<td><span class="indicator-pill">${r.queue_stage_label}</span></td>
						<td><span style="color:${priority_colors[r.priority] || "grey"}">${r.priority || ""}</span></td>
						<td>${r.days_open || 0}d</td>
						<td>
							${r.queue_stage === "new_request" ? `<button class="btn btn-xs btn-primary sq-accept" data-name="${r.name}">${__("Accept")}</button>` : ""}
							${r.queue_stage === "awaiting_approval" ? `<span class="text-warning">${__("Pending")}</span>` : ""}
							${r.queue_stage === "ready_invoice" ? `<button class="btn btn-xs btn-success sq-invoice" data-name="${r.name}">${__("Invoice")}</button>` : ""}
						</td>
					</tr>
					`
						)
						.join("")}
				</tbody>
			</table>
		`);

		// Event: Accept
		list_el.find(".sq-accept").on("click", async (e) => {
			e.stopPropagation();
			const name = $(e.currentTarget).data("name");
			await frappe.xcall(
				"gofix.gofix_services.page.store_queue.store_queue.quick_accept",
				{ service_request: name }
			);
			frappe.show_alert({ message: __("Request Accepted"), indicator: "green" });
			this.load_queue();
		});

		// Event: Row click → detail panel
		list_el.find(".sq-row").on("click", (e) => {
			if ($(e.target).is("a, button")) return;
			const name = $(e.currentTarget).data("name");
			this.show_detail(name);
		});
	}

	async show_detail(sr_name) {
		const detail = await frappe.xcall(
			"gofix.gofix_services.page.store_queue.store_queue.get_request_detail",
			{ sr_name }
		);
		const panel = this.wrapper.find(".sq-detail-panel");
		panel.show();

		const ev_rows = (detail.estimate_versions || [])
			.map(
				(ev) => `
			<tr>
				<td>V${ev.version_number}</td>
				<td>${frappe.format(ev.estimate_amount, { fieldtype: "Currency" })}</td>
				<td><span class="indicator-pill ${ev.status === "Customer Approved" ? "green" : ev.status === "Customer Rejected" ? "red" : "orange"}">${ev.status}</span></td>
				<td>${ev.reason_for_revision || ""}</td>
				<td>${ev.created_at ? frappe.datetime.prettyDate(ev.created_at) : ""}</td>
			</tr>
		`
			)
			.join("");

		panel.html(`
			<div style="border:1px solid #ddd;border-radius:8px;padding:16px;margin-top:12px;background:#fff;">
				<div style="display:flex;justify-content:space-between;align-items:center;">
					<h5>${detail.name} — ${detail.customer_name}</h5>
					<button class="btn btn-xs btn-default sq-close-detail">&times;</button>
				</div>
				<div class="row mt-3">
					<div class="col-md-4">
						<p><b>Device:</b> ${detail.device_item_name} (${detail.brand})</p>
						<p><b>Serial:</b> ${detail.serial_no}</p>
						<p><b>Issue:</b> ${detail.issue_category}</p>
						<p><b>Warranty:</b> ${detail.warranty_status} ${detail.warranty_plan_name ? "— " + detail.warranty_plan_name : ""}</p>
					</div>
					<div class="col-md-4">
						<p><b>Status:</b> ${detail.decision}</p>
						<p><b>Repairability:</b> ${detail.repairability_status || "Pending"}</p>
						<p><b>Estimate:</b> ${frappe.format(detail.estimated_cost, { fieldtype: "Currency" })} (V${detail.latest_estimate_version || 0})</p>
						<p><b>Paused:</b> ${detail.repair_paused ? detail.repair_pause_reason : "No"}</p>
					</div>
					<div class="col-md-4">
						<p><b>Source:</b> ${detail.source_warehouse}</p>
						<p><b>Current:</b> ${detail.current_processing_location || detail.source_warehouse}</p>
						<p><b>Service Order:</b> ${detail.service_order ? `<a href="/app/sales-order/${detail.service_order}">${detail.service_order}</a>` : "—"}</p>
						<p><b>Invoice:</b> ${detail.service_invoice ? `<a href="/app/sales-invoice/${detail.service_invoice}">${detail.service_invoice}</a>` : "—"}</p>
					</div>
				</div>
				${ev_rows ? `
				<h6 class="mt-3">Estimate History</h6>
				<table class="table table-sm" style="font-size:12px;">
					<thead><tr><th>Ver</th><th>Amount</th><th>Status</th><th>Reason</th><th>Date</th></tr></thead>
					<tbody>${ev_rows}</tbody>
				</table>
				` : ""}
			</div>
		`);

		panel.find(".sq-close-detail").on("click", () => panel.hide());
	}
}
