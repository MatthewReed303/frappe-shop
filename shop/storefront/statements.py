"""Customer statements, made by ERPNext's Process Statement Of Accounts.

statement_pdf   the signed-in customer downloads their statement from their store account
sync_customer   keeps each customer in the statement runs that email their Customer Group

A Process Statement Of Accounts run lists its customers in a table that ERPNext fills once
(Fetch Customers). For runs that email a Customer Group automatically, this keeps that table
up to date as customers join or leave the group, so a newly approved trade customer is not
missed. Runs set up any other way are left alone.
"""

import frappe
from frappe import _
from frappe.utils import add_months, getdate, nowdate

STATEMENT_MONTHS = 12
ROW_DOCTYPE = "Process Statement Of Accounts Customer"


def statement_doc(customers: list[str], company: str, from_date, to_date):
	"""An unsaved Process Statement Of Accounts for these customers, as ERPNext would run it."""
	doc = frappe.new_doc("Process Statement Of Accounts")
	doc.update(
		{
			"company": company,
			"report": "General Ledger",
			"from_date": from_date,
			"to_date": to_date,
			"orientation": "Portrait",
			"include_ageing": 1,
			"ageing_based_on": "Due Date",
			"include_break": 1,
			"categorize_by": "Categorize by Voucher (Consolidated)",
			"letter_head": letter_head_for(customers),
		}
	)
	for customer in customers:
		doc.append("customers", {"customer": customer, "customer_name": frappe.db.get_value("Customer", customer, "customer_name")})
	return doc


def letter_head_for(customers: list[str]) -> str | None:
	"""The letterhead of the statement run the customer is in, so the download matches the
	statements they are emailed; otherwise the default letterhead."""
	run = frappe.db.get_value(ROW_DOCTYPE, {"customer": ["in", customers or [""]], "parenttype": "Process Statement Of Accounts"}, "parent")
	letter_head = run and frappe.db.get_value("Process Statement Of Accounts", run, "letter_head")
	return letter_head or frappe.db.get_value("Letter Head", {"is_default": 1, "disabled": 0})


@frappe.whitelist(methods=["GET"])
def statement_pdf():
	"""The signed-in customer's statement for the last 12 months, with ageing."""
	from erpnext.accounts.doctype.process_statement_of_accounts.process_statement_of_accounts import (
		get_report_pdf,
	)

	from shop.storefront.checkout import elevated
	from shop.storefront.orders import session_customers

	customers = session_customers()
	if not customers:
		raise frappe.PermissionError
	company = frappe.get_cached_doc("Shop Settings").company
	to_date = getdate(nowdate())
	with elevated():
		pdf = get_report_pdf(statement_doc(customers, company, add_months(to_date, -STATEMENT_MONTHS), to_date))
	if not pdf:
		frappe.throw(_("There is nothing on your account yet."), title=_("No statement"))
	frappe.local.response.filename = f"Statement {to_date}.pdf"
	frappe.local.response.filecontent = pdf
	frappe.local.response.type = "pdf"


def sync_customer(doc, method=None) -> None:
	"""Customer on_update: add the customer to, or remove them from, the automatic statement
	runs for Customer Groups, matching the group they are in now."""
	if not frappe.db.table_exists("Process Statement Of Accounts"):
		return
	runs = frappe.get_all(
		"Process Statement Of Accounts",
		filters={"enable_auto_email": 1, "customer_collection": "Customer Group"},
		fields=["name", "collection_name", "primary_mandatory"],
	)
	for run in runs:
		groups = [run.collection_name, *frappe.db.get_descendants("Customer Group", run.collection_name)]
		belongs = (
			doc.customer_group in groups
			and not doc.disabled
			and (doc.email_id or not run.primary_mandatory)
		)
		row = frappe.db.get_value(ROW_DOCTYPE, {"parent": run.name, "customer": doc.name}, ["name", "primary_email"], as_dict=True)
		if belongs and not row:
			add_row(run.name, doc)
		elif belongs and row and row.primary_email != (doc.email_id or ""):
			frappe.db.set_value(ROW_DOCTYPE, row.name, "primary_email", doc.email_id or "")
		elif row and not belongs:
			# a direct delete: saving the run would refuse an empty customer list
			frappe.db.delete(ROW_DOCTYPE, {"name": row.name})


def add_row(run_name: str, customer) -> None:
	run = frappe.get_doc("Process Statement Of Accounts", run_name)
	row = run.append(
		"customers",
		{"customer": customer.name, "customer_name": customer.customer_name, "primary_email": customer.email_id or ""},
	)
	row.db_insert()
