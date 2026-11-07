"""Trade account applications are now ERPNext Leads; the Shop Trade Application doctype is gone."""

import frappe


def execute():
	if frappe.db.exists("DocType", "Shop Trade Application"):
		frappe.delete_doc("DocType", "Shop Trade Application", force=True, ignore_missing=True)
