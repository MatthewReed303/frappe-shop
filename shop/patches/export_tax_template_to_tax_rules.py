"""Shop Settings had an Export Tax Template for orders shipped abroad. Taxes are now picked by
ERPNext Tax Rules ("Use for Shopping Cart"); make the two rules it stood for."""

import frappe


def execute():
	old = frappe.db.sql(
		"select value from `tabSingles` where doctype = 'Shop Settings' and field = 'export_tax_template'"
	)
	export_template = old[0][0] if old else None
	company, home_template = frappe.db.get_value("Shop Settings", None, ["company", "tax_template"])
	if export_template and company and home_template:
		from shop.storefront.totals import ensure_tax_rules

		ensure_tax_rules(company, home_template, export_template)
	frappe.db.sql("delete from `tabSingles` where doctype = 'Shop Settings' and field = 'export_tax_template'")
