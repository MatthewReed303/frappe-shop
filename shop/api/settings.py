import frappe
from frappe.utils import cint, flt

from shop.api import only_managers

EDITABLE = (
	"store_name",
	"store_logo",
	"enable_cod",
	"allow_out_of_stock",
	"prices_include_tax",
	"tax_template",
	"flat_shipping_rate",
	"free_shipping_above",
	"shipping_account",
	"low_stock_threshold",
	"price_list",
	"show_prices_with_tax",
	"other_currencies_with_tax",
	"export_tax_template",
	"shipping_item",
	"card_fee_item",
	"enable_bank_transfer",
	"bank_transfer_days",
	"conversion_fee_item",
	"transfer_fee_item",
	"bank_charges_account",
	"invoice_paid_orders",
	"cancel_unpaid_orders",
	"online_payment_days",
	"order_naming_series",
	"payment_reminder_hours",
	"return_window_days",
	"quote_valid_days",
	"send_shipping_emails",
	"require_terms",
	"terms_and_conditions",
	"terms_url",
	"trade_customer_group",
	"email_statements",
	"allow_quote_requests",
)
CHECKS = {
	"enable_cod",
	"allow_out_of_stock",
	"prices_include_tax",
	"show_prices_with_tax",
	"other_currencies_with_tax",
	"enable_bank_transfer",
	"invoice_paid_orders",
	"cancel_unpaid_orders",
	"send_shipping_emails",
	"require_terms",
	"email_statements",
	"allow_quote_requests",
}
AMOUNTS = {
	"flat_shipping_rate",
	"free_shipping_above",
}
COUNTS = {
	"low_stock_threshold",
	"bank_transfer_days",
	"online_payment_days",
	"payment_reminder_hours",
	"return_window_days",
	"quote_valid_days",
}
LOCATION_FIELDS = (
	"warehouse",
	"display_name",
	"fulfillment_provider",
	"ship_from_address",
	"auto_send",
	"show_stock",
	"allow_pickup",
	"pickup_instructions",
)
GATEWAY_FIELDS = (
	"payment_gateway",
	"label",
	"fee_label",
	"fee_percent",
	"fee_fixed",
	"fee_percent_international",
	"fee_fixed_international",
)
BANK_ACCOUNT_FIELDS = (
	"bank_account",
	"label",
	"branch_code_label",
	"conversion_fee_percent",
	"local_transfer_fee",
	"swift_transfer_fee",
	"instructions",
)


@frappe.whitelist()
def get_settings() -> dict:
	only_managers()
	settings = frappe.get_doc("Shop Settings")
	payload = {field: settings.get(field) for field in EDITABLE}
	payload.update(
		{
			# ERPNext's own settings, shown here because every web order uses them
			"new_customer_group": frappe.db.get_single_value("Selling Settings", "customer_group"),
			"round_order_totals": not cint(frappe.db.get_single_value("Global Defaults", "disable_rounded_total")),
			"company": settings.company,
			"currency": settings.currency,
			# what ERPNext's company is set up with: its accounts' currency and its country
			"company_currency": frappe.get_cached_value("Company", settings.company, "default_currency")
			if settings.company
			else None,
			"company_country": frappe.get_cached_value("Company", settings.company, "country")
			if settings.company
			else None,
			"active_theme": settings.active_theme,
			"gateway_accounts": frappe.get_all(
				"Payment Gateway Account", fields=["name", "payment_gateway", "currency"]
			),
			"fulfillment_locations": [
				{
					**{field: row.get(field) for field in LOCATION_FIELDS},
					"warning": settings.ship_from_warning(row),
				}
				for row in settings.fulfillment_locations
			],
			"addresses": company_addresses(settings.company),
			"payment_gateways": [
				{field: row.get(field) for field in GATEWAY_FIELDS} for row in settings.payment_gateways
			],
			"tax_templates": frappe.get_all("Sales Taxes and Charges Template", pluck="name"),
			"income_accounts": frappe.get_all(
				"Account",
				filters={"company": settings.company, "is_group": 0, "root_type": "Income"},
				pluck="name",
			),
			"price_lists": frappe.get_all("Price List", filters={"selling": 1}, pluck="name"),
			"service_items": frappe.get_all(
				"Item",
				filters={"is_stock_item": 0, "is_sales_item": 1, "disabled": 0, "has_variants": 0},
				pluck="name",
			),
			"terms_records": frappe.get_all(
				"Terms and Conditions", filters={"selling": 1, "disabled": 0}, pluck="name"
			),
			"customer_groups": frappe.get_all("Customer Group", filters={"is_group": 0}, pluck="name"),
			"bank_accounts": [{field: row.get(field) for field in BANK_ACCOUNT_FIELDS} for row in settings.bank_accounts],
			"company_bank_accounts": company_bank_accounts(settings.company),
			"expense_accounts": frappe.get_all(
				"Account",
				filters={"company": settings.company, "is_group": 0, "root_type": "Expense"},
				pluck="name",
			),
			"fulfillment_providers": fulfillment_providers(),
			"warehouses": [
				{"value": row.name, "label": row.warehouse_name or row.name}
				for row in frappe.get_all(
					"Warehouse",
					filters={"company": settings.company, "is_group": 0},
					fields=["name", "warehouse_name"],
				)
			],
		}
	)
	return payload


def company_addresses(company: str | None) -> list[dict]:
	"""Addresses parcels can be sent from: the company's and its warehouses'."""
	if not company:
		return []
	from shop.storefront.stock import warehouse_address

	names = frappe.get_all(
		"Dynamic Link",
		filters={"link_doctype": "Company", "link_name": company, "parenttype": "Address"},
		pluck="parent",
	)
	for warehouse in frappe.get_all("Warehouse", filters={"company": company, "is_group": 0}, pluck="name"):
		address = warehouse_address(warehouse)
		if address:
			names.append(address)
	return [
		{"value": row.name, "label": f"{row.address_title or row.name}, {row.city or ''}".strip(", ")}
		for row in frappe.get_all(
			"Address",
			filters={"name": ["in", list(dict.fromkeys(names)) or [""]], "disabled": 0},
			fields=["name", "address_title", "city"],
		)
	]


def company_bank_accounts(company: str) -> list[dict]:
	"""The company's ERPNext Bank Accounts, with the currency customers would pay in and whether
	their Bank has a SWIFT code for international transfers."""
	accounts = []
	for row in frappe.get_all(
		"Bank Account",
		filters={"company": company, "is_company_account": 1, "disabled": 0},
		fields=["name", "account", "bank"],
	):
		accounts.append(
			{
				"name": row.name,
				"currency": frappe.get_cached_value("Account", row.account, "account_currency")
				if row.account
				else None,
				"swift": bool(row.bank and frappe.get_cached_value("Bank", row.bank, "swift_number")),
			}
		)
	return accounts


def fulfillment_providers() -> list[dict]:
	from shop.fulfillment.provider import available

	return available()


@frappe.whitelist(methods=["POST"])
def save_settings(payload: dict) -> dict:
	only_managers()
	settings = frappe.get_doc("Shop Settings")
	for field in EDITABLE:
		if field not in payload:
			continue
		value = payload[field]
		if field in CHECKS:
			value = 1 if value else 0
		elif field in AMOUNTS:
			value = flt(value)
		elif field in COUNTS:
			value = cint(value)
		settings.set(field, value)
	if "fulfillment_locations" in payload:
		settings.set(
			"fulfillment_locations",
			[
				{field: row.get(field) for field in LOCATION_FIELDS}
				for row in payload["fulfillment_locations"] or []
				if row.get("warehouse")
			],
		)
	if "payment_gateways" in payload:
		settings.set(
			"payment_gateways",
			[
				{field: row.get(field) for field in GATEWAY_FIELDS}
				for row in payload["payment_gateways"] or []
				if row.get("payment_gateway")
			],
		)
	if "bank_accounts" in payload:
		settings.set(
			"bank_accounts",
			[
				{field: row.get(field) for field in BANK_ACCOUNT_FIELDS}
				for row in payload["bank_accounts"] or []
				if row.get("bank_account")
			],
		)
	settings.save(ignore_permissions=True)
	save_erpnext_settings(payload)
	return get_settings()


def save_erpnext_settings(payload: dict) -> None:
	if "new_customer_group" in payload:
		selling = frappe.get_single("Selling Settings")
		selling.customer_group = payload["new_customer_group"]
		selling.save(ignore_permissions=True)
	if "round_order_totals" in payload:
		defaults = frappe.get_single("Global Defaults")
		defaults.disable_rounded_total = 0 if payload["round_order_totals"] else 1
		defaults.save(ignore_permissions=True)


@frappe.whitelist(methods=["POST"])
def create_service_item(item_name: str) -> str:
	"""A service Item (not stocked) for a charge the store adds to orders, such as a fee or shipping."""
	from shop.api.products import default_item_group, unique_item_code

	only_managers()
	group = "Services" if frappe.db.exists("Item Group", "Services") else default_item_group()
	item = frappe.get_doc(
		{
			"doctype": "Item",
			"item_code": unique_item_code(item_name),
			"item_name": item_name,
			"item_group": group,
			"stock_uom": "Nos",
			"is_stock_item": 0,
			"is_sales_item": 1,
			"description": item_name,
		}
	).insert(ignore_permissions=True)
	return item.name
