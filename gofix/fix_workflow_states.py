"""
DEPRECATED — Use gofix.gofix_services.setup_workflow instead.
Run with: bench --site erpnext.local execute gofix.gofix_services.setup_workflow.setup_service_order_workflow

This file delegates to the canonical setup script to avoid duplication.
"""
from gofix.gofix_services.setup_workflow import setup_service_order_workflow


def execute():
    """Delegate to canonical setup script."""
    setup_service_order_workflow()
