"""Records the store tests share: tax templates, service items, bank accounts, terms."""

import frappe


def service(name: str, company: str) -> str:
	if not frappe.db.exists("Item", name):
		frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": name,
				"item_name": name,
				"item_group": "All Item Groups",
				"stock_uom": "Nos",
				"is_stock_item": 0,
				"is_sales_item": 1,
			}
		).insert(ignore_permissions=True)
	return name


def template(company: str, title: str, rate: float, included: int) -> str:
	name = frappe.db.exists("Sales Taxes and Charges Template", {"title": title, "company": company})
	if name:
		return name
	account = frappe.db.get_value("Account", {"company": company, "account_type": "Tax", "is_group": 0})
	doc = frappe.get_doc(
		{
			"doctype": "Sales Taxes and Charges Template",
			"title": title,
			"company": company,
			"taxes": [
				{
					"charge_type": "On Net Total",
					"account_head": account,
					"rate": rate,
					"description": f"VAT {rate:g}%",
					"included_in_print_rate": included,
				}
			],
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.name


def company_bank_account(company: str, currency: str) -> str:
	"""An ERPNext company Bank Account in a currency, with test details, as a store would set up."""
	name = f"_Test Shop {currency}"
	if frappe.db.exists("Bank Account", {"account_name": name, "company": company}):
		return frappe.db.get_value("Bank Account", {"account_name": name, "company": company})
	if not frappe.db.exists("Bank", "_Test Shop Bank"):
		frappe.get_doc(
			{"doctype": "Bank", "bank_name": "_Test Shop Bank", "swift_number": "TESTXX22"}
		).insert(ignore_permissions=True)
	parent = frappe.db.get_value(
		"Account", {"company": company, "account_type": "Bank", "is_group": 1}
	) or frappe.db.get_value("Account", {"company": company, "root_type": "Asset", "is_group": 1})
	account = frappe.db.get_value("Account", {"company": company, "account_name": name})
	if not account:
		account = (
			frappe.get_doc(
				{
					"doctype": "Account",
					"account_name": name,
					"company": company,
					"parent_account": parent,
					"account_type": "Bank",
					"account_currency": currency,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)
	return (
		frappe.get_doc(
			{
				"doctype": "Bank Account",
				"account_name": name,
				"bank": "_Test Shop Bank",
				"is_company_account": 1,
				"company": company,
				"account": account,
				"branch_code": "2",
				"bank_account_no": "3",
			}
		)
		.insert(ignore_permissions=True)
		.name
	)


def terms_record() -> str:
	"""An ERPNext Terms and Conditions record for the store, using Jinja as ERPNext allows."""
	if not frappe.db.exists("Terms and Conditions", "_Test Store Terms"):
		frappe.get_doc(
			{
				"doctype": "Terms and Conditions",
				"title": "_Test Store Terms",
				"selling": 1,
				"terms": "<p>Terms for {{ customer_name }}</p>",
			}
		).insert(ignore_permissions=True)
	return "_Test Store Terms"
