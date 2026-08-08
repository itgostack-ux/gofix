"""Sync imported CH Customer Address rows into ERPNext Address."""

import frappe


def execute():
	# Superseded by retire_ch_customer_address, which migrates every Billing,
	# Shipping, and Both row before removing the duplicate child DocType.
	return
