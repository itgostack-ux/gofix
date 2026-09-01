app_name = "gofix"
app_title = "GoFix"
app_publisher = "GoStack"
app_description = "Repairs & Services"
app_email = "contact@gostack.in"
app_license = "custom"

boot_session = "gofix.boot.boot_session"
required_apps = ["frappe/erpnext"]

# Jinja helpers available to print formats (Repair Charge Sheet timeline)
jinja = {
	"methods": [
		"gofix.gofix_services.page.gofix_ops_hub.gofix_ops_hub.get_repair_history",
	]
}

fixtures = [
    {
        "dt": "Role",
        "filters": [
            ["name", "in", ["Service Manager", "Service Engineer", "Service Viewer"]]
        ]
    },
    {
        "dt": "Workflow State",
        "filters": [
            ["name", "in", [
                "Draft", "Submitted", "Work in Progress",
                "QC Awaiting", "QC Pass", "QC Fail",
                "Not Repairable", "Customer Cancelled", "Closed"
            ]]
        ]
    },
    {
        "dt": "Workflow Action Master",
        "filters": [
            ["name", "in", [
                "Submit", "Start Work", "Complete Job",
                "Mark Not Repairable", "Customer Cancelled",
                "QC Pass", "QC Fail", "Rework", "Close"
            ]]
        ]
    },
    {
        "dt": "Workflow",
        "filters": [
            ["name", "in", ["Service Order Workflow"]]
        ]
    },
]

# Old apps-screen route lives on in bookmarks/history — redirect it.
website_redirects = [{"source": "/GoFix", "target": "/desk/gofix"}]

add_to_apps_screen = [
	{
		"name": "gofix",
		"logo": "/assets/gofix/icon.svg",
		"title": "GoFix",
		"route": "/desk/gofix",
		"has_permission": "gofix.gofix.utils.has_app_permission"
	}
]

# GoGizmo App Switcher top nav removed — it hid the top-right Desk controls and
# caused navigation confusion. Navigate via the standard Desk sidebar/workspaces.
app_include_css = ["/assets/gofix/css/gofix.css"]
app_include_js = []

doctype_list_js = {
    "Sales Order": "public/js/sales_order_list.js",
}

doctype_js = {
    "Material Request": "public/js/material_request_gofix.js",
}

after_install = "gofix.setup.install.after_install"
after_migrate = [
	"gofix.setup.permissions.ensure_default_permissions",
	"gofix.setup.permissions.ignore_user_permissions_on_service_locations",
	"gofix.setup.sales_order_custom_fields.create_sales_order_custom_fields",
    "gofix.setup.notifications.create_notifications",
    "gofix.setup.workflow.ensure_service_order_workflow",
    "gofix.setup.employee_custom_fields.create_employee_custom_fields",
    "gofix.setup.service_request_ops_fields.create_service_request_ops_fields",
    "gofix.setup.sales_invoice_custom_fields.create_sales_invoice_custom_fields",
    "gofix.setup.competitive_ops_fields.create_competitive_ops_fields",
    "gofix.setup.material_request_custom_fields.create_material_request_custom_fields",
	"gofix.setup.company_custom_fields.create_company_custom_fields",
	"gofix.setup.service_billing_setup.ensure_service_billing_setup",
	"gofix.setup.pos_setup.ensure_gofix_business_dates",
    "gofix.setup.item_custom_fields.create_item_custom_fields",
    "gofix.setup.compliance_fields.create_compliance_fields",
    "gofix.setup.maturity_fields.create_maturity_fields",
    "gofix.setup.accessory_masters.ensure_accessory_masters",
    # Last: force-imports workspace JSON fixtures whose content changed, so it
    # must run after any other hook that wires links into those workspaces.
    "gofix.setup.workspace_sync.sync_workspaces",
]

permission_query_conditions = {
	"Service Request": "gofix.security.get_service_request_query",
	"Job Assignment":  "gofix.security.get_job_assignment_query",
	"GoFix Token": "gofix.security.get_gofix_token_query",
}

has_permission = {
	"Service Request": "gofix.security.has_service_request_permission",
	"Job Assignment":  "gofix.security.has_job_assignment_permission",
	"GoFix Token": "gofix.security.has_gofix_token_permission",
}

override_doctype_class = {
	"Sales Order": "gofix.overrides.sales_order.CustomSalesOrder"
}

doc_events = {
	# Item is the master of the service catalogue; these mirror it into GoFix.
	"Item": {
		"on_update": "gofix.catalogue_sync.sync_spare_mappings_from_item",
		"after_rename": "gofix.catalogue_sync.repoint_spare_mappings_on_rename",
	},
	"Repair Solution": {
		"validate": "gofix.catalogue_sync.validate_repair_solution",
		"on_update": "gofix.catalogue_sync.on_repair_solution_update",
	},
	"Solution Spare Mapping": {
		"validate": "gofix.catalogue_sync.validate_solution_spare_mapping",
	},
	"Sales Order": {
		"validate": "gofix.overrides.sales_order.validate_service_order_before_submit",
		"on_update": "gofix.overrides.sales_order.update_service_request_on_qc",
        "on_update_after_submit": "gofix.overrides.sales_order.update_service_request_on_qc",
		"on_submit": "gofix.overrides.sales_order.update_service_request_on_qc",
		"on_cancel": "gofix.overrides.sales_order.update_service_request_on_qc"
	},
	"Sales Invoice": {
		"before_insert": "gofix.overrides.sales_invoice.resolve_gofix_links",
		"on_submit": [
			"gofix.overrides.sales_invoice.update_service_request_on_invoice",
			"gofix.spare_lifecycle.on_sales_invoice_update",
		],
		# Outstanding changes here when a payment is allocated against the bill.
		"on_update_after_submit": "gofix.spare_lifecycle.on_sales_invoice_update",
		"on_cancel": "gofix.overrides.sales_invoice.update_service_request_on_invoice"
	},
	"Payment Entry": {
		"on_submit": "gofix.spare_lifecycle.on_payment_entry",
	},
	"Delivery Note": {
		"on_submit": "gofix.overrides.delivery_note.update_service_request_on_delivery",
		"on_cancel": "gofix.overrides.delivery_note.update_service_request_on_delivery"
	},
	"Purchase Order": {
		"validate": "gofix.customer_device_stock.block_customer_device_as_destination",
		"on_submit": "gofix.purchase_api.mark_spares_in_transit",
	},
	"Material Request": {
		"validate": "gofix.customer_device_stock.block_customer_device_as_destination",
	},
	"Purchase Receipt": {
		"validate": "gofix.customer_device_stock.block_customer_device_as_destination",
		# A draft receipt means the parts are AT the store; submitting it is what
		# puts them in stock. The ticket distinguishes the two.
		"after_insert": "gofix.purchase_api.mark_spares_delivered",
		"on_submit": "gofix.purchase_api.allocate_received_spares_to_tickets",
		"on_cancel": "gofix.purchase_api.unmark_spares_delivered",
		"on_trash": "gofix.purchase_api.unmark_spares_delivered",
	},
	"Stock Entry": {
		# A part transferred in is on the shelf as surely as one that was bought,
		# so a ticket waiting for it is released either way.
		"on_submit": "gofix.purchase_api.allocate_transferred_spares_to_tickets",
	},
	"Service Request": {
		"on_update": "gofix.gofix_services.whatsapp_notifications.on_service_request_update",
		"on_update_after_submit": [
			"gofix.gofix_services.doctype.service_request.service_request.ensure_service_order_on_accept",
			"gofix.spare_lifecycle.release_holds_on_dead_ticket",
		],
	},
}

scheduler_events = {
	"daily": [
		"gofix.gofix_services.doctype.service_request.service_request.flag_unclaimed_devices",
		"gofix.gofix_services.doctype.service_request.service_request.auto_expire_stale_requests",
		"gofix.gofix_services.api.expire_pending_estimates",
	],
	"cron": {
		"*/15 * * * *": [
			"gofix.gofix_services.doctype.gofix_sla_rule.gofix_sla_rule.check_gofix_sla_breach"
		]
	},
}
