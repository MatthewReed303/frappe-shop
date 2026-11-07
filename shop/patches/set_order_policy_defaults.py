import frappe


def execute():
	"""Stores made before the return window, quote validity and payment reminder were settings keep
	what they did: 14 days, 30 days and one hour."""
	for field, value in (("return_window_days", 14), ("quote_valid_days", 30), ("payment_reminder_hours", 1)):
		if not frappe.db.exists("Singles", {"doctype": "Shop Settings", "field": field}):
			frappe.db.set_single_value("Shop Settings", field, value)
