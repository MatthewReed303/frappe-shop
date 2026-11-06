"""Store customers made before this were left without a Customer Primary Contact, so ERPNext
had no email for them. Use their first linked contact that has an email."""

import frappe


def execute():
	from shop.storefront.checkout import set_primary_contact

	for customer in frappe.get_all("Customer", filters={"customer_primary_contact": ["is", "not set"]}, pluck="name"):
		contact = frappe.db.sql(
			"""select c.name from `tabContact` c join `tabDynamic Link` l on l.parent = c.name
			where l.link_doctype = 'Customer' and l.link_name = %s and l.parenttype = 'Contact'
			and ifnull(c.email_id, '') != '' order by c.creation limit 1""",
			customer,
		)
		if contact:
			set_primary_contact(customer, frappe.get_doc("Contact", contact[0][0]))
