/**
 * Quick Intake — POS-style rapid walk-in Service Request creation
 *
 * Minimal-click flow:
 *   1. Scan/enter Serial → auto-fills device, brand, warranty
 *   2. Select/create customer (phone search)
 *   3. Pick issue category, describe fault, note accessories
 *   4. Submit → Service Request created + printed token
 */
frappe.pages["quick-intake"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Quick Intake"),
		single_column: true,
	});
	page.main.html(`<div id="quick-intake-app"></div>`);
	new QuickIntake(page);
};

const QI_API = "gofix.gofix_services.page.quick_intake.quick_intake";

class QuickIntake {
	constructor(page) {
		this.page = page;
		this.wrapper = page.main.find("#quick-intake-app");
		this.form_data = {};
		this.init();
	}

	async init() {
		this.ctx = await frappe.xcall(`${QI_API}.get_intake_context`);
		this.form_data.source_warehouse = this.ctx.default_warehouse;
		this.form_data.company = this.ctx.company;
		this.render();
		this.bind_events();
		// Auto-focus serial field
		setTimeout(() => this.wrapper.find("#qi-serial").focus(), 200);
	}

	render() {
		const wh_options = this.ctx.warehouses.map(
			(w) => `<option value="${w}" ${w === this.ctx.default_warehouse ? "selected" : ""}>${w}</option>`
		).join("");

		this.wrapper.html(`
		<style>
			.qi-container { max-width: 800px; margin: 0 auto; padding: 16px; }
			.qi-card { background: #fff; border: 1px solid #e2e8f0; border-radius: 8px; padding: 20px; margin-bottom: 16px; }
			.qi-card h6 { font-weight: 600; color: #334155; margin-bottom: 14px; border-bottom: 1px solid #f1f5f9; padding-bottom: 8px; }
			.qi-row { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
			.qi-field { margin-bottom: 10px; }
			.qi-field label { font-size: 12px; font-weight: 500; color: #64748b; display: block; margin-bottom: 3px; }
			.qi-field input, .qi-field select, .qi-field textarea { width: 100%; padding: 7px 10px; border: 1px solid #cbd5e1; border-radius: 6px; font-size: 13px; }
			.qi-field input:focus, .qi-field select:focus, .qi-field textarea:focus { outline: none; border-color: #3b82f6; box-shadow: 0 0 0 2px rgba(59,130,246,0.15); }
			.qi-serial-result { background: #f0fdf4; border: 1px solid #86efac; border-radius: 6px; padding: 12px; margin-top: 8px; font-size: 12px; display: none; }
			.qi-serial-result.warning { background: #fef3c7; border-color: #fbbf24; }
			.qi-serial-result.error { background: #fef2f2; border-color: #fca5a5; }
			.qi-submit-bar { position: sticky; bottom: 0; background: #fff; border-top: 1px solid #e2e8f0; padding: 12px 20px; display: flex; justify-content: space-between; align-items: center; }
			.qi-badge { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 11px; font-weight: 600; }
			.qi-badge.green { background: #dcfce7; color: #166534; }
			.qi-badge.red { background: #fee2e2; color: #991b1b; }
			.qi-badge.orange { background: #fff7ed; color: #9a3412; }
			.qi-customer-results { max-height: 180px; overflow-y: auto; border: 1px solid #e2e8f0; border-radius: 6px; margin-top: 4px; display: none; }
			.qi-customer-results .qi-cust-row { padding: 8px 12px; cursor: pointer; font-size: 12px; border-bottom: 1px solid #f1f5f9; }
			.qi-customer-results .qi-cust-row:hover { background: #f8fafc; }
			.qi-success { text-align: center; padding: 40px 20px; }
			.qi-success h3 { color: #16a34a; }
		</style>

		<div class="qi-container">
			<!-- Section 1: Device -->
			<div class="qi-card">
				<h6>📱 Device</h6>
				<div class="qi-row">
					<div class="qi-field" style="grid-column: span 2;">
						<label>Serial No / IMEI <small class="text-muted">(scan or type)</small></label>
						<input type="text" id="qi-serial" placeholder="Scan barcode or enter IMEI…" autocomplete="off">
						<div id="qi-serial-result" class="qi-serial-result"></div>
					</div>
				</div>
				<div class="qi-row">
					<div class="qi-field">
						<label>Device Item *</label>
						<input type="text" id="qi-device-item" placeholder="Search item…" autocomplete="off">
						<input type="hidden" id="qi-device-item-code">
					</div>
					<div class="qi-field">
						<label>Brand</label>
						<input type="text" id="qi-brand" readonly>
					</div>
				</div>
				<div id="qi-warranty-badge"></div>
			</div>

			<!-- Section 2: Customer -->
			<div class="qi-card">
				<h6>👤 Customer</h6>
				<div class="qi-row">
					<div class="qi-field" style="position:relative;">
						<label>Search by Phone / Name</label>
						<input type="text" id="qi-customer-search" placeholder="Phone or name…" autocomplete="off">
						<div id="qi-customer-results" class="qi-customer-results"></div>
					</div>
					<div class="qi-field">
						<label>Customer *</label>
						<input type="text" id="qi-customer-name" readonly>
						<input type="hidden" id="qi-customer">
					</div>
				</div>
				<div class="qi-row">
					<div class="qi-field">
						<label>Mobile *</label>
						<input type="text" id="qi-phone" maxlength="15">
					</div>
					<div class="qi-field">
						<label>Email</label>
						<input type="email" id="qi-email">
					</div>
				</div>
			</div>

			<!-- Section 3: Issue -->
			<div class="qi-card">
				<h6>🔧 Issue Details</h6>
				<div class="qi-row">
					<div class="qi-field">
						<label>Issue Category</label>
						<input type="text" id="qi-issue-category" placeholder="Search issue category…" autocomplete="off">
					</div>
					<div class="qi-field">
						<label>Priority</label>
						<select id="qi-priority">
							<option value="Low">Low</option>
							<option value="Medium" selected>Medium</option>
							<option value="High">High</option>
							<option value="Urgent">Urgent</option>
						</select>
					</div>
				</div>
				<div class="qi-field">
					<label>Fault Description *</label>
					<textarea id="qi-issue-desc" rows="2" placeholder="Customer's complaint…"></textarea>
				</div>
				<div class="qi-row">
					<div class="qi-field">
						<label>Physical Condition</label>
						<textarea id="qi-condition" rows="1" placeholder="Scratches, dents, screen cracks…"></textarea>
					</div>
					<div class="qi-field">
						<label>Accessories Received</label>
						<input type="text" id="qi-accessories" placeholder="Charger, cable, box…">
					</div>
				</div>
				<div class="qi-row">
					<div class="qi-field">
						<label>Backup Info</label>
						<input type="text" id="qi-backup" placeholder="e.g. No backup taken by customer">
					</div>
					<div class="qi-field">
						<label>Password / Pattern</label>
						<input type="text" id="qi-password" placeholder="Lock screen password">
					</div>
				</div>
			</div>

			<!-- Section 4: Store -->
			<div class="qi-card">
				<h6>🏪 Store</h6>
				<div class="qi-field">
					<label>Source Warehouse *</label>
					<select id="qi-warehouse">${wh_options}</select>
				</div>
			</div>

			<!-- Submit Bar -->
			<div class="qi-submit-bar">
				<button class="btn btn-default" id="qi-reset">Reset</button>
				<button class="btn btn-primary btn-lg" id="qi-submit" style="min-width: 200px;">
					<i class="fa fa-check"></i> Create Service Request
				</button>
			</div>
		</div>
		`);
	}

	bind_events() {
		const w = this.wrapper;
		const self = this;

		// Serial lookup (debounced)
		let serial_timer;
		w.find("#qi-serial").on("input", function () {
			clearTimeout(serial_timer);
			const val = $(this).val().trim();
			if (val.length < 3) { w.find("#qi-serial-result").hide(); return; }
			serial_timer = setTimeout(() => self.lookup_serial(val), 400);
		});

		// Customer search (debounced)
		let cust_timer;
		w.find("#qi-customer-search").on("input", function () {
			clearTimeout(cust_timer);
			const val = $(this).val().trim();
			if (val.length < 3) { w.find("#qi-customer-results").hide(); return; }
			cust_timer = setTimeout(() => self.search_customer(val), 400);
		});

		// Device item link
		w.find("#qi-device-item").on("focus", function () {
			const val = $(this).val();
			frappe.call({
				method: "frappe.client.get_list",
				args: { doctype: "Item", filters: { item_name: ["like", `%${val}%`], disabled: 0 }, fields: ["name", "item_name", "brand"], limit_page_length: 10 },
				callback: () => {},
			});
		});

		// Create Frappe link controls for autocomplete
		this._setup_link_field("#qi-device-item", "Item", (item) => {
			w.find("#qi-device-item-code").val(item.value);
			w.find("#qi-device-item").val(item.description || item.value);
			w.find("#qi-brand").val(item.brand || "");
			self.form_data.device_item = item.value;
			self.form_data.device_item_name = item.description || item.value;
			self.form_data.brand = item.brand || "";
		});

		this._setup_link_field("#qi-issue-category", "Issue Category", (item) => {
			self.form_data.issue_category = item.value;
		});

		// Submit
		w.find("#qi-submit").on("click", () => self.submit_intake());

		// Reset
		w.find("#qi-reset").on("click", () => {
			self.form_data = { source_warehouse: self.ctx.default_warehouse, company: self.ctx.company };
			self.render();
			self.bind_events();
			setTimeout(() => self.wrapper.find("#qi-serial").focus(), 200);
		});
	}

	_setup_link_field(selector, doctype, on_select) {
		const $input = this.wrapper.find(selector);
		$input.on("input", frappe.utils.debounce(async function () {
			const txt = $(this).val().trim();
			if (txt.length < 2) return;
			const results = await frappe.call({
				method: "frappe.client.get_list",
				args: {
					doctype: doctype,
					filters: [[doctype, "name", "like", `%${txt}%`]],
					or_filters: [[doctype, doctype === "Item" ? "item_name" : "name", "like", `%${txt}%`]],
					fields: doctype === "Item" ? ["name", "item_name as description", "brand"] : ["name"],
					limit_page_length: 8,
				},
			});
			const items = (results.message || []);
			if (!items.length) return;

			// Simple dropdown
			let $dd = $input.next(".qi-link-dropdown");
			if (!$dd.length) {
				$dd = $(`<div class="qi-link-dropdown" style="position:absolute;z-index:10;background:#fff;border:1px solid #e2e8f0;border-radius:6px;max-height:160px;overflow-y:auto;width:100%;"></div>`);
				$input.after($dd);
				$input.parent().css("position", "relative");
			}
			$dd.html(items.map((r) =>
				`<div class="qi-cust-row" data-value="${r.name}" data-desc="${r.description || r.name}" data-brand="${r.brand || ''}">${r.description || r.name} <small class="text-muted">(${r.name})</small></div>`
			).join("")).show();

			$dd.find(".qi-cust-row").on("click", function () {
				const val = $(this).data("value");
				const desc = $(this).data("desc");
				const brand = $(this).data("brand");
				$input.val(desc || val);
				$dd.hide();
				on_select({ value: val, description: desc, brand: brand });
			});
		}, 300));

		// Hide dropdown on blur
		$input.on("blur", () => setTimeout(() => $input.next(".qi-link-dropdown").hide(), 200));
	}

	async lookup_serial(serial_no) {
		const result = await frappe.xcall(`${QI_API}.search_serial`, { serial_no });
		const el = this.wrapper.find("#qi-serial-result");

		if (!result.found) {
			el.html(`Serial not found. A new one will be created on submit.`).removeClass("warning").addClass("error").show();
			return;
		}

		// Auto-fill
		this.wrapper.find("#qi-device-item").val(result.item_name);
		this.wrapper.find("#qi-device-item-code").val(result.item_code);
		this.wrapper.find("#qi-brand").val(result.brand);
		this.form_data.device_item = result.item_code;
		this.form_data.device_item_name = result.item_name;
		this.form_data.brand = result.brand;
		this.form_data.serial_no = serial_no;

		let html = `<b>${result.item_name}</b> (${result.brand})<br>`;
		html += `Warranty: <span class="qi-badge ${result.warranty_status === "Under Warranty" ? "green" : "red"}">${result.warranty_status}</span>`;
		if (result.warranty_plan) html += ` — ${result.warranty_plan}`;
		if (result.warranty_expiry) html += ` (exp: ${result.warranty_expiry})`;

		let css = "";
		if (result.open_requests && result.open_requests.length) {
			html += `<br><span class="qi-badge orange">⚠ ${result.open_requests.length} open request(s)</span>`;
			result.open_requests.forEach((r) => {
				html += `<br>&nbsp;&nbsp;→ <a href="/app/service-request/${r.name}">${r.name}</a> ${r.issue_category || ""} (${r.status})`;
			});
			css = "warning";
		}

		el.html(html).removeClass("error warning").addClass(css || "").show();

		// Warranty badge
		this.wrapper.find("#qi-warranty-badge").html(
			`<span class="qi-badge ${result.warranty_status === "Under Warranty" ? "green" : "red"}">${result.warranty_status}</span>`
		);
	}

	async search_customer(query) {
		const results = await frappe.xcall(`${QI_API}.search_customer`, { query });
		const el = this.wrapper.find("#qi-customer-results");

		if (!results.length) {
			el.html(`<div class="qi-cust-row text-muted">No match. <a href="#" class="qi-new-customer">Create new customer</a></div>`).show();
			el.find(".qi-new-customer").on("click", (e) => {
				e.preventDefault();
				frappe.new_doc("Customer", { customer_name: query });
			});
			return;
		}

		el.html(results.map((r) =>
			`<div class="qi-cust-row" data-customer="${frappe.utils.escape_html(r.customer)}" data-name="${frappe.utils.escape_html(r.customer_name || "")}" data-phone="${frappe.utils.escape_html(r.contact_number || "")}" data-email="${frappe.utils.escape_html(r.email || "")}">
				<b>${r.customer_name || r.customer}</b> <small class="text-muted">${r.contact_number || ""}</small>
			</div>`
		).join("")).show();

		el.find(".qi-cust-row").on("click", (e) => {
			const row = $(e.currentTarget);
			this.wrapper.find("#qi-customer").val(row.data("customer"));
			this.wrapper.find("#qi-customer-name").val(row.data("name") || row.data("customer"));
			this.wrapper.find("#qi-phone").val(row.data("phone"));
			this.wrapper.find("#qi-email").val(row.data("email"));
			this.form_data.customer = row.data("customer");
			this.form_data.customer_name = row.data("name");
			this.form_data.contact_number = row.data("phone");
			this.form_data.email = row.data("email");
			el.hide();
		});
	}

	async submit_intake() {
		// Gather form data
		const d = this.form_data;
		d.serial_no = this.wrapper.find("#qi-serial").val().trim() || d.serial_no || "";
		d.device_item = d.device_item || this.wrapper.find("#qi-device-item-code").val();
		d.customer = d.customer || this.wrapper.find("#qi-customer").val();
		d.contact_number = this.wrapper.find("#qi-phone").val().trim() || d.contact_number;
		d.email = this.wrapper.find("#qi-email").val().trim() || d.email;
		d.issue_category = d.issue_category || this.wrapper.find("#qi-issue-category").val().trim();
		d.issue_description = this.wrapper.find("#qi-issue-desc").val().trim();
		d.product_condition_desc = this.wrapper.find("#qi-condition").val().trim();
		d.accessories_received = this.wrapper.find("#qi-accessories").val().trim();
		d.backup_info = this.wrapper.find("#qi-backup").val().trim();
		d.password = this.wrapper.find("#qi-password").val().trim();
		d.priority = this.wrapper.find("#qi-priority").val();
		d.source_warehouse = this.wrapper.find("#qi-warehouse").val();

		// Basic validation
		if (!d.customer) { frappe.throw(__("Please select a customer")); return; }
		if (!d.device_item) { frappe.throw(__("Please select a device item")); return; }
		if (!d.contact_number) { frappe.throw(__("Phone number is required")); return; }
		if (!d.issue_description) { frappe.throw(__("Please describe the fault")); return; }

		this.wrapper.find("#qi-submit").prop("disabled", true).html('<i class="fa fa-spinner fa-spin"></i> Creating…');

		try {
			const result = await frappe.xcall(`${QI_API}.submit_intake`, { data: d });
			this.show_success(result.name);
		} catch (e) {
			this.wrapper.find("#qi-submit").prop("disabled", false).html('<i class="fa fa-check"></i> Create Service Request');
		}
	}

	show_success(sr_name) {
		this.wrapper.html(`
			<div class="qi-success">
				<div style="font-size:64px;color:#16a34a;">✓</div>
				<h3>${__("Service Request Created")}</h3>
				<h2 style="font-family:monospace;margin:16px 0;">${sr_name}</h2>
				<div style="margin:24px 0;">
					<a href="/app/service-request/${sr_name}" class="btn btn-primary btn-sm mr-2">
						View Request
					</a>
					<button class="btn btn-default btn-sm" id="qi-print-token">
						<i class="fa fa-print"></i> Print Token
					</button>
					<button class="btn btn-success btn-sm ml-2" id="qi-next">
						<i class="fa fa-plus"></i> Next Intake
					</button>
				</div>
			</div>
		`);

		this.wrapper.find("#qi-next").on("click", () => {
			this.form_data = { source_warehouse: this.ctx.default_warehouse, company: this.ctx.company };
			this.render();
			this.bind_events();
			setTimeout(() => this.wrapper.find("#qi-serial").focus(), 200);
		});

		this.wrapper.find("#qi-print-token").on("click", () => {
			frappe.set_route("print", "Service Request", sr_name);
		});
	}
}
