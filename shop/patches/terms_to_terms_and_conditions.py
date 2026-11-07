"""Shop Settings kept the terms as free text with a typed version. They now live in an ERPNext
Terms and Conditions record that Shop Settings links to; move existing text there."""

import frappe


def execute():
	old = dict(
		frappe.db.sql(
			"select field, value from `tabSingles` where doctype = 'Shop Settings' and field in ('terms_text', 'store_name')"
		)
	)
	text = (old.get("terms_text") or "").strip()
	if not text or frappe.db.get_single_value("Shop Settings", "terms_and_conditions"):
		return
	title = f"{old.get('store_name') or 'Store'} Terms and Conditions"
	if not frappe.db.exists("Terms and Conditions", title):
		frappe.get_doc(
			{"doctype": "Terms and Conditions", "title": title, "terms": text, "selling": 1}
		).insert(ignore_permissions=True)
	frappe.db.set_single_value("Shop Settings", "terms_and_conditions", title)
	frappe.db.sql("delete from `tabSingles` where doctype = 'Shop Settings' and field in ('terms_text', 'terms_version')")
