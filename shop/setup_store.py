"""Set a store up on ERPNext's own records: charge items and their income accounts, the online
payment account, home and export taxes, trade Customer Group and discounts, the default
Customer Group and monthly statements. Running it again keeps what exists."""

import frappe
from frappe import _
from frappe.utils import add_months, get_last_day, nowdate

CHARGE_ITEMS = (
	# Shop Settings field, item code, item name, income account
	("shipping_item", "STORE-SHIPPING", "Shipping", "Freight Income"),
	("card_fee_item", "STORE-CARD-FEE", "Card fee", "Card Fee Income"),
	("conversion_fee_item", "STORE-CURRENCY-CONVERSION", "Currency conversion", "Currency Conversion Income"),
	("transfer_fee_item", "STORE-TRANSFER-FEE", "Bank transfer fee", "Bank Transfer Fee Income"),
)


def set_up(
	company: str | None = None,
	*,
	price_list: str | None = None,
	warehouse: str | None = None,
	account_numbers: dict | None = None,
	export_zero_rated: bool = True,
	trade_group: str | None = None,
	trade_payment_terms: str | None = None,
	trade_discounts: dict | None = None,
	default_customer_group: str | None = None,
	statements: bool = True,
):
	"""Set the store up. account_numbers maps account names (e.g. "Freight Income") to numbers, for
	charts that number their accounts. trade_discounts maps Item Group -> discount %."""
	from shop.api.onboarding import fill_commerce_defaults

	settings = frappe.get_doc("Shop Settings")
	settings.company = company or settings.company
	settings.price_list = price_list or settings.price_list
	settings.default_warehouse = warehouse or settings.default_warehouse
	fill_commerce_defaults(settings)
	company_doc = frappe.get_doc("Company", settings.company)
	settings.currency = settings.currency or company_doc.default_currency
	numbers = account_numbers or {}

	for field, code, name, account_name in CHARGE_ITEMS:
		if settings.get(field):
			continue
		income = account(
			company_doc, account_name, income_parent(company_doc), "Income Account", numbers.get(account_name)
		)
		settings.set(field, charge_item(company_doc, code, name, income))

	gateway = stripe_gateway_account(company_doc, numbers.get("Stripe Clearing"))
	if gateway and not settings.payment_gateways:
		settings.append(
			"payment_gateways",
			{
				"payment_gateway": frappe.db.get_value("Payment Gateway Account", gateway, "payment_gateway"),
				"label": _("Pay by card"),
				"fee_label": _("Card fee"),
			},
		)

	settings.tax_template = settings.tax_template or default_sales_taxes(company_doc.name)
	if export_zero_rated and settings.tax_template and not settings.get("export_tax_template"):
		settings.export_tax_template = zero_rated_template(company_doc, settings.tax_template)

	if trade_group:
		settings.trade_customer_group = customer_group(trade_group, trade_payment_terms, trade_discounts)
	if default_customer_group:
		set_default_customer_group(default_customer_group)
	settings.save(ignore_permissions=True)

	if statements and settings.get("trade_customer_group"):
		if not settings.get("email_statements"):
			frappe.db.set_single_value("Shop Settings", "email_statements", 1)
		statement_run(
			company_doc.name, settings.trade_customer_group, settings.store_name or company_doc.company_name
		)
	return settings


# --- accounts and items -----------------------------------------------------------------


def income_parent(company) -> str:
	"""Where new income accounts go: beside the company's default income account."""
	if company.default_income_account:
		return frappe.db.get_value("Account", company.default_income_account, "parent_account")
	return frappe.db.get_value(
		"Account",
		{"company": company.name, "root_type": "Income", "is_group": 1},
		"name",
		order_by="lft desc",
	)


def account(
	company, name: str, parent: str, account_type: str, number: str | None = None, currency: str | None = None
) -> str:
	"""The company's account with this name, made under `parent` if it doesn't exist."""
	existing = frappe.db.get_value("Account", {"company": company.name, "account_name": name, "is_group": 0})
	if existing:
		return existing
	return (
		frappe.get_doc(
			{
				"doctype": "Account",
				"account_name": name,
				"account_number": number,
				"parent_account": parent,
				"company": company.name,
				"account_type": account_type,
				"account_currency": currency or company.default_currency,
			}
		)
		.insert(ignore_permissions=True)
		.name
	)


def charge_item(company, code: str, name: str, income_account: str) -> str:
	"""A non-stock service item for a charge line on orders, booked to its income account."""
	doc = frappe.get_doc("Item", code) if frappe.db.exists("Item", code) else frappe.new_doc("Item")
	if doc.is_new():
		doc.update(
			{
				"item_code": code,
				"item_name": name,
				"description": name,
				"item_group": "Services"
				if frappe.db.exists("Item Group", "Services")
				else frappe.db.get_value("Item Group", {"is_group": 0}, "name"),
				"stock_uom": "Nos",
				"is_stock_item": 0,
				"is_sales_item": 1,
				"is_purchase_item": 0,
				"include_item_in_manufacturing": 0,
			}
		)
	defaults = [row for row in doc.get("item_defaults") or [] if row.company == company.name]
	if not defaults:
		doc.append("item_defaults", {"company": company.name, "income_account": income_account})
	elif not defaults[0].income_account:
		defaults[0].income_account = income_account
	elif not doc.is_new():
		return doc.name  # set up already; keep the owner's choices
	doc.save(ignore_permissions=True) if not doc.is_new() else doc.insert(ignore_permissions=True)
	return doc.name


def stripe_gateway_account(company, number: str | None = None) -> str | None:
	"""The Payment Gateway Account for Stripe, paid into a "Stripe Clearing" bank account, when a
	Stripe gateway has been set up (its keys are entered in the desk)."""
	gateway = frappe.db.get_value("Payment Gateway", {"gateway_settings": "Stripe Settings"})
	if not gateway:
		return None
	bank_parent = frappe.db.get_value(
		"Account", {"company": company.name, "account_type": "Bank", "is_group": 0}, "parent_account"
	)
	if not bank_parent:
		return None
	clearing = account(company, "Stripe Clearing", bank_parent, "Bank", number)
	existing = frappe.db.get_value(
		"Payment Gateway Account", {"payment_gateway": gateway, "currency": company.default_currency}
	)
	if existing:
		return existing
	return (
		frappe.get_doc(
			{
				"doctype": "Payment Gateway Account",
				"payment_gateway": gateway,
				"payment_account": clearing,
				"currency": company.default_currency,
				"is_default": 1,
			}
		)
		.insert(ignore_permissions=True)
		.name
	)


# --- taxes and customers -----------------------------------------------------------------


def default_sales_taxes(company: str) -> str | None:
	"""The company's default Sales Taxes and Charges Template (ERPNext makes one per country)."""
	return frappe.db.get_value(
		"Sales Taxes and Charges Template", {"company": company, "is_default": 1, "disabled": 0}
	) or frappe.db.get_value("Sales Taxes and Charges Template", {"company": company, "disabled": 0})


def zero_rated_template(company, home_template: str) -> str:
	"""A 0% copy of the home taxes for goods shipped abroad, on the same tax account."""
	name = f"Export zero-rated - {company.abbr}"
	if frappe.db.exists("Sales Taxes and Charges Template", name):
		return name
	account_head = frappe.db.get_value(
		"Sales Taxes and Charges",
		{"parent": home_template, "parenttype": "Sales Taxes and Charges Template"},
		"account_head",
	)
	tax = frappe.db.get_value("Account", account_head, "account_name") if account_head else _("Tax")
	return (
		frappe.get_doc(
			{
				"doctype": "Sales Taxes and Charges Template",
				"title": "Export zero-rated",
				"company": company.name,
				"taxes": [
					{
						"charge_type": "On Net Total",
						"account_head": account_head,
						"rate": 0,
						"description": f"{tax} 0% (exported goods)",
					}
				],
			}
		)
		.insert(ignore_permissions=True)
		.name
	)


def customer_group(name: str, payment_terms: str | None = None, discounts: dict | None = None) -> str:
	"""A Customer Group for trade customers with its default Payment Terms, and an ERPNext Pricing
	Rule for each Item Group discount (made once; changes made in the desk are kept)."""
	if not frappe.db.exists("Customer Group", name):
		frappe.get_doc(
			{
				"doctype": "Customer Group",
				"customer_group_name": name,
				"parent_customer_group": "All Customer Groups",
			}
		).insert(ignore_permissions=True)
	group = frappe.get_doc("Customer Group", name)
	if payment_terms and not group.payment_terms:
		group.payment_terms = payment_terms
		group.save(ignore_permissions=True)
	for item_group, percent in (discounts or {}).items():
		group_discount(name, item_group, percent)
	return name


def group_discount(customer_group: str, item_group: str, percent: float) -> str:
	"""An ERPNext Pricing Rule: percent off an Item Group for a Customer Group."""
	title = f"{customer_group}: {item_group}"
	existing = frappe.db.get_value("Pricing Rule", {"title": title, "customer_group": customer_group})
	if existing:
		return existing
	rule = frappe.get_doc(
		{
			"doctype": "Pricing Rule",
			"title": title,
			"apply_on": "Item Group",
			"item_groups": [{"item_group": item_group}],
			"applicable_for": "Customer Group",
			"customer_group": customer_group,
			"selling": 1,
			"rate_or_discount": "Discount Percentage",
			"discount_percentage": percent,
			"company": frappe.db.get_single_value("Shop Settings", "company"),
		}
	)
	return rule.insert(ignore_permissions=True).name


def set_default_customer_group(name: str) -> None:
	"""New customers (store sign-ups and checkouts) join this group, as Selling Settings sets it;
	customers left without a group join it too."""
	if not frappe.db.exists("Customer Group", name):
		return
	if not frappe.db.get_single_value("Selling Settings", "customer_group"):
		selling = frappe.get_doc("Selling Settings")
		selling.customer_group = name
		selling.save(ignore_permissions=True)
	for customer in frappe.get_all("Customer", filters={"customer_group": ["is", "not set"]}, pluck="name"):
		doc = frappe.get_doc("Customer", customer)
		doc.customer_group = name
		doc.save(ignore_permissions=True)


# --- statements -------------------------------------------------------------------------------

LETTER_HEAD = """{% set c = frappe.db.get_value("Company", doc.company, ["company_name", "email", "phone_no", "website", "tax_id"], as_dict=True) or {} %}
{% set link = frappe.db.get_value("Dynamic Link", {"link_doctype": "Company", "link_name": doc.company, "parenttype": "Address"}, "parent") %}
{% set a = frappe.db.get_value("Address", link, ["address_line1", "address_line2", "city", "pincode", "country"], as_dict=True) if link else None %}
<table style="width: 100%; border-collapse: collapse; font-size: 12px;">
	<tr>
		<td style="vertical-align: top; width: 60%;">
			<div style="font-size: 16px; font-weight: bold; margin-bottom: 4px;">{{ store_name }}</div>
			<div>{{ c.company_name }}</div>
			{% if a %}<div>{{ a.address_line1 or "" }}{% if a.address_line2 %}, {{ a.address_line2 }}{% endif %}</div>
			<div>{{ a.city or "" }} {{ a.pincode or "" }}, {{ a.country or "" }}</div>{% endif %}
			{% if c.tax_id %}<div>{{ _("Tax ID") }} {{ c.tax_id }}</div>{% endif %}
		</td>
		<td style="vertical-align: top; text-align: right;">
			{% if c.email %}<div>{{ c.email }}</div>{% endif %}
			{% if c.phone_no %}<div>{{ c.phone_no }}</div>{% endif %}
			{% if c.website %}<div>{{ c.website }}</div>{% endif %}
		</td>
	</tr>
</table>"""


def statement_letter_head(store_name: str) -> str:
	"""A letterhead for statements: the store and company name, address, tax ID and contacts.
	ERPNext's standard letterheads are made for transactions and leave the address off."""
	name = f"{store_name} Statement"
	if not frappe.db.exists("Letter Head", name):
		frappe.get_doc(
			{
				"doctype": "Letter Head",
				"letter_head_name": name,
				"source": "HTML",
				"content": LETTER_HEAD.replace("{{ store_name }}", frappe.utils.escape_html(store_name)),
			}
		).insert(ignore_permissions=True)
	return name


def statement_run(company: str, group: str, store_name: str) -> str | None:
	"""ERPNext's Process Statement Of Accounts emailing the group a statement each month, once the
	group has a customer. The store keeps its customer list in step with the group
	(shop.storefront.statements.sync_customer)."""
	# the group's run, whatever the store was called when it was made
	existing = frappe.db.get_value(
		"Process Statement Of Accounts",
		{"company": company, "customer_collection": "Customer Group", "collection_name": group},
	)
	if existing:
		return existing
	name = f"{store_name} statements"
	if frappe.db.exists("Process Statement Of Accounts", name):
		return name
	month_end = get_last_day(nowdate())
	doc = frappe.new_doc("Process Statement Of Accounts")
	doc.update(
		{
			"__newname": name,
			"company": company,
			"report": "General Ledger",
			"customer_collection": "Customer Group",
			"collection_name": group,
			"enable_auto_email": 1,
			"frequency": "Monthly",
			"filter_duration": 1,
			"start_date": month_end,
			"to_date": month_end,
			"from_date": add_months(month_end, -1),
			"include_ageing": 1,
			"ageing_based_on": "Due Date",
			"orientation": "Portrait",
			"primary_mandatory": 1,
			"letter_head": statement_letter_head(store_name),
			"sender": frappe.db.get_value("Email Account", {"default_outgoing": 1, "enable_outgoing": 1}),
			"subject": f"Your {store_name} statement to {{{{ doc.to_date }}}}",
			"body": (
				"<p>Hello {{ customer.customer_name }},</p>"
				f"<p>Your {store_name} statement from {{{{ doc.from_date }}}} to {{{{ doc.to_date }}}} is attached. "
				"It lists your invoices and payments, and anything still owing.</p>"
				"<p>If anything doesn't look right, just reply to this email.</p>"
			),
			"pdf_name": f"{store_name} statement {{{{ doc.to_date }}}}",
		}
	)
	from shop.storefront.statements import run_customers

	customers = run_customers(group, 1)
	if not customers:
		# ERPNext's run needs a customer; it is made when the first one joins the group
		return None
	doc.set("customers", customers)
	doc.insert(ignore_permissions=True)
	return name
