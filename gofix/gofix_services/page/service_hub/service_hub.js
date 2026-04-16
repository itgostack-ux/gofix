frappe.pages["service-hub"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Service Hub"),
		single_column: true,
	});
	wrapper.service_hub = new ServiceHub(page);
};

frappe.pages["service-hub"].refresh = function (wrapper) {
	wrapper.service_hub && wrapper.service_hub.refresh();
};

class ServiceHub {
	constructor(page) {
		this.page = page;
		this._timer = null;
		this._setup_controls();
		this._setup_container();
		this.refresh();
		this._start_auto_refresh();
	}

	_setup_controls() {
		this.company_field = this.page.add_field({
			fieldname: "company", label: __("Company"),
			fieldtype: "Link", options: "Company",
			default: frappe.defaults.get_user_default("Company"),
			change: () => this.refresh(),
		});
		this.store_field = this.page.add_field({
			fieldname: "store", label: __("Store / Warehouse"),
			fieldtype: "Link", options: "Warehouse",
			get_query: () => {
				const company = this.company_field?.get_value();
				const filters = { is_group: 0 };
				if (company) filters.company = company;
				return { filters };
			},
			change: () => this.refresh(),
		});
		this.from_date_field = this.page.add_field({
			fieldname: "from_date", label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.month_start(),
			change: () => this.refresh(),
		});
		this.to_date_field = this.page.add_field({
			fieldname: "to_date", label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.month_end(),
			change: () => this.refresh(),
		});
		this.page.add_button(__("Refresh"), () => this.refresh(), { icon: "refresh" });
	}

	_setup_container() {
		this.$root = $(`<div class="hub-root"></div>`).appendTo(this.page.body);
	}

	refresh() {
		const company = this.company_field?.get_value() || "";
		const store = this.store_field?.get_value() || "";
		const from_date = this.from_date_field?.get_value() || "";
		const to_date = this.to_date_field?.get_value() || "";
		this.$root.html(`<div class="hub-loading"><i class="fa fa-spinner fa-spin"></i> ${__("Loading Service Hub...")}</div>`);
		frappe.xcall("gofix.gofix_services.page.service_hub.service_hub_api.get_service_hub_data",
			{ company, store, from_date, to_date })
			.then((data) => this._render(data))
			.catch(() => {
				this.$root.html(`<div class="hub-loading text-danger">${__("Failed to load data. Please try again.")}</div>`);
			});
	}

	_start_auto_refresh() {
		this._timer = setInterval(() => this.refresh(), 60000);
		$(this.page.parent).on("remove", () => clearInterval(this._timer));
	}

	_render(data) {
		this.$root.empty();
		this._render_header();
		this._render_pipeline(data.pipeline || []);
		this._render_kpis(data.kpis || []);
		this._render_actions();
		this._render_intelligence(data.ai_insights || [], data.financial_control || {});
		this._render_tables(data);
	}

	_render_header() {
		this.$root.append(`
			<div class="hub-header">
				<div>
					<div class="hub-title"><i class="fa fa-wrench"></i> ${__("Service Hub")}</div>
				<div class="hub-subtitle">${__('Service lifecycle: Intake → Accepted → In Service → Completed / Not Repairable → Delivered → Invoiced')}</div>
				</div>
				<div class="hub-auto-badge">
					<span class="pulse-dot"></span> ${__("Live · Auto-refreshes every 60s")}
				</div>
			</div>
		`);
	}

	_render_pipeline(steps) {
		const arrow = `<div class="hub-flow-connector">
			<svg width="32" height="24" viewBox="0 0 32 24" fill="none" stroke="currentColor"
				stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
				<path d="M4 12H24M18 6l6 6-6 6"/>
			</svg>
		</div>`;
		const nodes = steps.map((s, i) => {
			const node = `
				<div class="hub-flow-node" data-step="${s.key}">
					<div class="hub-flow-badge" style="background:${s.color}">${s.count}</div>
					<div class="hub-flow-meta">
						<i class="fa fa-${s.icon}"></i>
						<span class="hub-flow-name">${__(s.label)}</span>
					</div>
					<div class="hub-flow-sub">${s.sub || ""}</div>
				</div>`;
			return i < steps.length - 1 ? node + arrow : node;
		}).join("");
		this.$root.append(`
			<div class="hub-section">
				<h5 class="hub-section-title"><i class="fa fa-random"></i> ${__("Service Pipeline")}</h5>
				<div class="hub-flow-wrap"><div class="hub-flow">${nodes}</div></div>
			</div>
		`);
	}

	_render_kpis(kpis) {
		const cards = kpis.map((k) => {
			const val = k.fmt === "currency"
				? frappe.format(k.value, { fieldtype: "Currency" })
				: k.value;
			return `<div class="hub-kpi-card" style="--kpi-color:${k.color}" data-kpi="${k.key}">
				<div class="hub-kpi-value">${val}</div>
				<div class="hub-kpi-label">${__(k.label)}</div>
			</div>`;
		}).join("");
		this.$root.append(`
			<div class="hub-section">
				<h5 class="hub-section-title"><i class="fa fa-tachometer"></i> ${__("Key Metrics")}</h5>
				<div class="hub-kpi-grid">${cards}</div>
			</div>
		`);
	}

	_render_actions() {
		this.$root.append(`
			<div class="hub-section">
				<h5 class="hub-section-title"><i class="fa fa-bolt"></i> ${__("Quick Actions")}</h5>
				<div class="hub-actions-grid">
					<button class="hub-action-btn" onclick="frappe.set_route('List','Service Request',{decision:'Draft'})"><i class="fa fa-plus"></i> ${__("New Requests")}</button>
					<button class="hub-action-btn" onclick="frappe.set_route('List','Service Request',{decision:'Accepted'})"><i class="fa fa-check"></i> ${__("Accepted")}</button>
					<button class="hub-action-btn" onclick="frappe.set_route('List','Service Request',{decision:'In Service'})"><i class="fa fa-cogs"></i> ${__("In Service")}</button>
					<button class="hub-action-btn" onclick="frappe.set_route('List','Service Request',{decision:'Completed'})"><i class="fa fa-check-circle"></i> ${__("Completed")}</button>					<button class="hub-action-btn hub-action-nr" onclick="frappe.set_route('List','Service Request',{decision:'Rejected',repairability_status:['in',['Not Repairable','BER']]})"><i class="fa fa-ban"></i> ${__('Not Repairable')}</button>					<button class="hub-action-btn" onclick="frappe.set_route('app','gofix-ops-hub')"><i class="fa fa-dashboard"></i> ${__("GoFix Ops Hub")}</button>
					<button class="hub-action-btn" onclick="frappe.set_route('List','Issue Category')"><i class="fa fa-tags"></i> ${__("Issue Categories")}</button>
				</div>
			</div>
		`);
	}

	_render_intelligence(insights, financial) {
		const insightCards = insights.map((i) => `
			<div class="hub-insight-card hub-insight-${(i.severity || 'medium').toLowerCase()}">
				<div class="hub-insight-top">
					<span class="hub-badge hub-badge-${i.severity === 'High' ? 'red' : i.severity === 'Low' ? 'green' : 'yellow'}">${i.severity}</span>
					<span class="hub-insight-title">${i.title}</span>
				</div>
				<div class="hub-insight-detail">${i.detail}</div>
				${i.action ? `<div class="hub-insight-action">${i.action}</div>` : ""}
			</div>
		`).join("");

		const fc = financial;
		this.$root.append(`
			<div class="hub-section">
				<h5 class="hub-section-title"><i class="fa fa-brain"></i> ${__("AI Insights & Service Control")}</h5>
				<div class="hub-intel-grid">
					<div class="hub-intel-panel">${insightCards || '<div class="hub-empty">No insights</div>'}</div>
					<div class="hub-intel-panel">
						<div class="hub-mini-kpi-grid">
							<div class="hub-mini-kpi" style="--mini-color:#7c3aed">
								<div class="hub-mini-kpi-value">${fc.total_active || 0}</div>
								<div class="hub-mini-kpi-label">${__("Active Jobs")}</div>
							</div>
							<div class="hub-mini-kpi" style="--mini-color:#059669">
								<div class="hub-mini-kpi-value">${fc.completion_rate || "0%"}</div>
								<div class="hub-mini-kpi-label">${__("Completion Rate")}</div>
							</div>
							<div class="hub-mini-kpi" style="--mini-color:#f59e0b">
								<div class="hub-mini-kpi-value">${fc.avg_tat || "N/A"}</div>
								<div class="hub-mini-kpi-label">${__("Avg TAT (days)")}</div>
							</div>
							<div class="hub-mini-kpi" style="--mini-color:#3b82f6">
								<div class="hub-mini-kpi-value">${frappe.format(fc.revenue_mtd || 0, {fieldtype:"Currency"})}</div>
								<div class="hub-mini-kpi-label">${__("Service Revenue MTD")}</div>
							</div>
						</div>
					</div>
				</div>
			</div>
		`);
	}

	_render_tables(data) {
		const tabs = [
			{ key: "pending", label: __("Pending Intake"), count: (data.pending_intake || []).length },
			{ key: "in_service", label: __("In Service"), count: (data.in_service || []).length },
			{ key: "completed", label: __("Ready for Delivery"), count: (data.ready_delivery || []).length },
			{ key: "not_repairable", label: __("Not Repairable"), count: (data.not_repairable || []).length },
			{ key: "overdue", label: __("Overdue"), count: (data.overdue || []).length },
			{ key: "technicians", label: __("Technician Load"), count: (data.technician_load || []).length },
			{ key: "categories", label: __("Issue Breakdown"), count: (data.issue_breakdown || []).length },
		];
		const tabBtns = tabs.map((t, i) =>
			`<button class="hub-tab${i === 0 ? " active" : ""}" data-tab="${t.key}">
				${t.label} <span class="badge">${t.count}</span>
			</button>`
		).join("");

		this.$root.append(`
			<div class="hub-section">
				<h5 class="hub-section-title"><i class="fa fa-table"></i> ${__("Detail Tables")}</h5>
				<div class="hub-tabs">${tabBtns}</div>
				<div class="hub-tab-panel active" data-panel="pending">${this._table_pending(data.pending_intake || [])}</div>
				<div class="hub-tab-panel" data-panel="in_service">${this._table_in_service(data.in_service || [])}</div>
				<div class="hub-tab-panel" data-panel="completed">${this._table_ready(data.ready_delivery || [])}</div>
				<div class="hub-tab-panel" data-panel="not_repairable">${this._table_not_repairable(data.not_repairable || [])}</div>
				<div class="hub-tab-panel" data-panel="overdue">${this._table_overdue(data.overdue || [])}</div>
				<div class="hub-tab-panel" data-panel="technicians">${this._table_technicians(data.technician_load || [])}</div>
				<div class="hub-tab-panel" data-panel="categories">${this._table_categories(data.issue_breakdown || [])}</div>
			</div>
		`);

		this.$root.find(".hub-tab").on("click", (e) => {
			const key = $(e.currentTarget).data("tab");
			this.$root.find(".hub-tab").removeClass("active");
			$(e.currentTarget).addClass("active");
			this.$root.find(".hub-tab-panel").removeClass("active");
			this.$root.find(`[data-panel="${key}"]`).addClass("active");
		});
	}

	_lnk(dt, name) { return `<a href="/app/${frappe.router.slug(dt)}/${name}">${name}</a>`; }
	_badge(status) {
		const map = { "Draft": "grey", "Accepted": "blue", "In Service": "purple", "Completed": "green", "Delivered": "green", "Invoiced": "green", "Rejected": "red", "Cancelled": "grey", "Expired": "orange", "Not Repairable": "red", "BER": "red" };
		return `<span class="hub-badge hub-badge-${map[status] || "grey"}">${status}</span>`;
	}

	_table_pending(rows) {
		if (!rows.length) return `<div class="hub-empty"><i class="fa fa-check-circle"></i> ${__("No pending intake")}</div>`;
		return `<div class="hub-table-wrap"><table class="hub-table"><thead><tr>
			<th>${__("SR #")}</th><th>${__("Customer")}</th><th>${__("Device")}</th>
			<th>${__("Issue")}</th><th>${__("Priority")}</th><th>${__("Date")}</th><th>${__("Status")}</th>
		</tr></thead><tbody>${rows.map((r) => `<tr>
			<td>${this._lnk("Service Request", r.name)}</td>
			<td>${r.customer_name || r.customer || ""}</td>
			<td>${r.device_item || ""}</td>
			<td>${r.issue_category || ""}</td>
			<td>${r.priority || "-"}</td>
			<td>${frappe.datetime.str_to_user(r.creation)}</td>
			<td>${this._badge(r.decision || r.status)}</td>
		</tr>`).join("")}</tbody></table></div>`;
	}

	_table_in_service(rows) {
		if (!rows.length) return `<div class="hub-empty"><i class="fa fa-check-circle"></i> ${__("No items in service")}</div>`;
		return `<div class="hub-table-wrap"><table class="hub-table"><thead><tr>
			<th>${__("SR #")}</th><th>${__("Customer")}</th><th>${__("Device")}</th>
			<th>${__("Brand")}</th><th>${__("Technician")}</th><th>${__("Days In Service")}</th>
		</tr></thead><tbody>${rows.map((r) => `<tr>
			<td>${this._lnk("Service Request", r.name)}</td>
			<td>${r.customer_name || r.customer || ""}</td>
			<td>${r.device_item || ""}</td>
			<td>${r.brand || ""}</td>
			<td>${r.technician || "-"}</td>
			<td>${r.days_in_service || "-"}</td>
		</tr>`).join("")}</tbody></table></div>`;
	}

	_table_ready(rows) {
		if (!rows.length) return `<div class="hub-empty"><i class="fa fa-check-circle"></i> ${__("None ready for delivery")}</div>`;
		return `<div class="hub-table-wrap"><table class="hub-table"><thead><tr>
			<th>${__("SR #")}</th><th>${__("Customer")}</th><th>${__("Device")}</th>
			<th>${__("Completed On")}</th><th class="text-right">${__("Est. Cost")}</th>
		</tr></thead><tbody>${rows.map((r) => `<tr>
			<td>${this._lnk("Service Request", r.name)}</td>
			<td>${r.customer_name || r.customer || ""}</td>
			<td>${r.device_item || ""}</td>
			<td>${r.completed_on ? frappe.datetime.str_to_user(r.completed_on) : "-"}</td>
			<td class="text-right">${frappe.format(r.estimated_cost || 0, {fieldtype:"Currency"})}</td>
		</tr>`).join("")}</tbody></table></div>`;
	}

	_table_overdue(rows) {
		if (!rows.length) return `<div class="hub-empty"><i class="fa fa-check-circle"></i> ${__("No overdue requests")}</div>`;
		return `<div class="hub-table-wrap"><table class="hub-table"><thead><tr>
			<th>${__("SR #")}</th><th>${__("Customer")}</th><th>${__("Device")}</th>
			<th>${__("Expected By")}</th><th>${__("Days Overdue")}</th><th>${__("Status")}</th>
		</tr></thead><tbody>${rows.map((r) => `<tr>
			<td>${this._lnk("Service Request", r.name)}</td>
			<td>${r.customer_name || r.customer || ""}</td>
			<td>${r.device_item || ""}</td>
			<td>${r.expected_completion_date ? frappe.datetime.str_to_user(r.expected_completion_date) : "-"}</td>
			<td class="text-danger">${r.days_overdue || "-"}</td>
			<td>${this._badge(r.decision || r.status)}</td>
		</tr>`).join("")}</tbody></table></div>`;
	}

	_table_technicians(rows) {
		if (!rows.length) return `<div class="hub-empty"><i class="fa fa-users"></i> ${__("No technician data")}</div>`;
		return `<div class="hub-table-wrap"><table class="hub-table"><thead><tr>
			<th>${__("Technician")}</th><th class="text-right">${__("Active Jobs")}</th>
			<th class="text-right">${__("Completed")}</th><th class="text-right">${__("Avg TAT (days)")}</th>
		</tr></thead><tbody>${rows.map((r) => `<tr>
			<td>${r.technician}</td>
			<td class="text-right">${r.active_jobs || 0}</td>
			<td class="text-right">${r.completed || 0}</td>
			<td class="text-right">${r.avg_tat || "-"}</td>
		</tr>`).join("")}</tbody></table></div>`;
	}

	_table_categories(rows) {
		if (!rows.length) return `<div class="hub-empty"><i class="fa fa-tags"></i> ${__("No category data")}</div>`;
		return `<div class="hub-table-wrap"><table class="hub-table"><thead><tr>
			<th>${__("Issue Category")}</th><th class="text-right">${__("Total")}</th>
			<th class="text-right">${__("Active")}</th><th class="text-right">${__("Completed")}</th>
		</tr></thead><tbody>${rows.map((r) => `<tr>
			<td>${r.issue_category || "Uncategorized"}</td>
			<td class="text-right">${r.total || 0}</td>
			<td class="text-right">${r.active || 0}</td>
			<td class="text-right">${r.completed || 0}</td>
		</tr>`).join("")}</tbody></table></div>`;
	}
	_table_not_repairable(rows) {
		if (!rows.length) return `<div class="hub-empty"><i class="fa fa-check-circle"></i> ${__('No not-repairable devices')}</div>`;
		return `<div class="hub-table-wrap"><table class="hub-table"><thead><tr>
			<th>${__('SR #')}</th><th>${__('Customer')}</th><th>${__('Device')}</th>
			<th>${__('Status')}</th><th>${__('Reason')}</th>
			<th class="text-center">${__('Pending Spares')}</th><th>${__('Date')}</th>
		</tr></thead><tbody>${rows.map((r) => `<tr>
			<td>${this._lnk('Service Request', r.name)}</td>
			<td>${r.customer_name || r.customer || ''}</td>
			<td>${r.device_item || ''}</td>
			<td>${this._badge(r.repairability_status || 'Rejected')}</td>
			<td>${r.rejection_reason || '-'}</td>
			<td class="text-center">${cint(r.pending_spares) ? '<span class="text-danger"><i class="fa fa-exclamation-triangle"></i> ' + r.pending_spares + '</span>' : '<i class="fa fa-check text-success"></i>'}</td>
			<td>${r.rejected_on ? frappe.datetime.str_to_user(r.rejected_on) : '-'}</td>
		</tr>`).join('')}</tbody></table></div>`;
	}}
