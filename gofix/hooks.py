app_name = "gofix"
app_title = "GoFix"
app_publisher = "GoStack"
app_description = "Repairs & Services"
app_email = "contact@gostack.in"
app_license = "custom"

boot_session = "gofix.boot.boot_session"
required_apps = ["frappe/erpnext"]

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

add_to_apps_screen = [
	{
		"name": "gofix",
		"logo": "/assets/gofix/icon.svg",
		"title": "GoFix",
		"route": "/desk/gofix-services",
		"has_permission": "gofix.gofix.utils.has_app_permission"
	}
]

app_include_css = ["/assets/gofix/css/gofix.css", "/assets/gofix/css/gogizmo_app_switcher.css"]
app_include_js = "/assets/gofix/js/gogizmo_app_switcher.js"

doctype_list_js = {
    "Sales Order": "public/js/sales_order_list.js"
}

after_install = "gofix.setup.install.after_install"
after_migrate = [
	"gofix.setup.sales_order_custom_fields.create_sales_order_custom_fields",
    "gofix.setup.notifications.create_notifications",
    "gofix.setup.workflow.ensure_service_order_workflow",
    "gofix.setup.employee_custom_fields.create_employee_custom_fields",
    "gofix.setup.service_request_ops_fields.create_service_request_ops_fields",
    "gofix.setup.sales_invoice_custom_fields.create_sales_invoice_custom_fields",
    "gofix.setup.competitive_ops_fields.create_competitive_ops_fields",
    "gofix.setup.material_request_custom_fields.create_material_request_custom_fields",
]

permission_query_conditions = {
	"Service Request": "gofix.security.get_service_request_query",
	"Job Assignment":  "gofix.security.get_job_assignment_query",
}

has_permission = {
	"Service Request": "gofix.security.has_service_request_permission",
	"Job Assignment":  "gofix.security.has_job_assignment_permission",
}

override_doctype_class = {
	"Sales Order": "gofix.overrides.sales_order.CustomSalesOrder"
}

doc_events = {
	"Sales Order": {
		"validate": "gofix.overrides.sales_order.validate_service_order_before_submit",
		"on_update": "gofix.overrides.sales_order.update_service_request_on_qc",
        "on_update_after_submit": "gofix.overrides.sales_order.update_service_request_on_qc",
		"on_submit": "gofix.overrides.sales_order.update_service_request_on_qc",
		"on_cancel": "gofix.overrides.sales_order.update_service_request_on_qc"
	},
	"Sales Invoice": {
		"before_insert": "gofix.overrides.sales_invoice.resolve_gofix_links",
		"on_submit": "gofix.overrides.sales_invoice.update_service_request_on_invoice",
		"on_cancel": "gofix.overrides.sales_invoice.update_service_request_on_invoice"
	},
	"Delivery Note": {
		"on_submit": "gofix.overrides.delivery_note.update_service_request_on_delivery",
		"on_cancel": "gofix.overrides.delivery_note.update_service_request_on_delivery"
	},
	"Service Request": {
		"on_update": "gofix.gofix_services.whatsapp_notifications.on_service_request_update",
		"on_update_after_submit": "gofix.gofix_services.doctype.service_request.service_request.ensure_service_order_on_accept",
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

