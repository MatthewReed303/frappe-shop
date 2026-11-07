import frappe
from frappe.utils import cint, flt

from shop.api import only_managers

EDITABLE = (
	"store_name",
	"store_logo",
	"enable_cod",
	"payment_gateway_account",
	"allow_out_of_stock",
	"prices_include_tax",
	"tax_template",
	"flat_shipping_rate",
	"free_shipping_above",
	"shipping_account",
	"low_stock_threshold",
	"price_list",
	"default_warehouse",
	"fulfillment_provider",
	"auto_send_to_fulfillment",
	"show_prices_with_tax",
	"export_tax_template",
	"shipping_item",
	"online_payment_label",
	"card_fee_percent",
	"card_fee_fixed",
	"card_fee_percent_international",
	"card_fee_fixed_international",
	"enable_bank_transfer",
	"bank_transfer_days",
	"invoice_paid_orders",
	"cancel_unpaid_orders",
	"online_payment_days",
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
	"auto_send_to_fulfillment",
	"show_prices_with_tax",
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
	"card_fee_percent",
	"card_fee_fixed",
	"card_fee_percent_international",
	"card_fee_fixed_international",
}
COUNTS = {"low_stock_threshold", "bank_transfer_days", "online_payment_days"}


@frappe.whitelist()
def get_settings() -> dict:
	only_managers()
	settings = frappe.get_doc("Shop Settings")
	payload = {field: settings.get(field) for field in EDITABLE}
	payload.update(
		{
			"company": settings.company,
			"currency": settings.currency,
			"active_theme": settings.active_theme,
			"gateway_accounts": frappe.get_all(
				"Payment Gateway Account", fields=["name", "payment_gateway", "currency"]
			),
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
			"has_bank_accounts": bool(settings.get("bank_accounts")),
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
	settings.save(ignore_permissions=True)
	return get_settings()
