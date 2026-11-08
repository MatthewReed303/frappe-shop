import frappe
from frappe import _
from frappe.model.document import Document

from shop.routes import STOREFRONT_SEGMENTS, get_prefix, normalize_prefix


class ShopSettings(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF
		from shop.shop.doctype.shop_bank_account.shop_bank_account import ShopBankAccount
		from shop.shop.doctype.shop_theme_page.shop_theme_page import ShopThemePage

		active_theme: DF.Data | None
		allow_out_of_stock: DF.Check
		allow_quote_requests: DF.Check
		applied_route_prefix: DF.Data | None
		auto_send_to_fulfillment: DF.Check
		bank_accounts: DF.Table[ShopBankAccount]
		bank_charges_account: DF.Link | None
		bank_transfer_days: DF.Int
		business_buyer_label: DF.SmallText | None
		cancel_unpaid_orders: DF.Check
		card_fee_fixed: DF.Currency
		card_fee_fixed_international: DF.Currency
		card_fee_item: DF.Link | None
		card_fee_percent: DF.Percent
		card_fee_percent_international: DF.Percent
		company: DF.Link | None
		conversion_fee_item: DF.Link | None
		currency: DF.Link | None
		default_warehouse: DF.Link | None
		email_statements: DF.Check
		enable_bank_transfer: DF.Check
		enable_cod: DF.Check
		export_tax_template: DF.Link | None
		flat_shipping_rate: DF.Currency
		free_shipping_above: DF.Currency
		fulfillment_provider: DF.Data | None
		invoice_paid_orders: DF.Check
		low_stock_threshold: DF.Int
		onboarding_complete: DF.Check
		online_payment_days: DF.Int
		online_payment_label: DF.Data | None
		order_naming_series: DF.Data | None
		other_currencies_with_tax: DF.Check
		payment_gateway_account: DF.Link | None
		payment_reminder_hours: DF.Int
		price_list: DF.Link | None
		prices_include_tax: DF.Check
		quote_valid_days: DF.Int
		require_terms: DF.Check
		return_window_days: DF.Int
		route_prefix: DF.Data | None
		send_shipping_emails: DF.Check
		shipping_account: DF.Link | None
		shipping_item: DF.Link | None
		show_prices_with_tax: DF.Check
		store_logo: DF.AttachImage | None
		store_name: DF.Data | None
		tax_template: DF.Link | None
		terms_and_conditions: DF.Link | None
		terms_checkbox_note: DF.SmallText | None
		terms_url: DF.Data | None
		theme_pages: DF.Table[ShopThemePage]
		trade_customer_group: DF.Link | None
		transfer_fee_item: DF.Link | None
	# end: auto-generated types

	def validate(self):
		self.validate_order_naming_series()
		self.route_prefix = normalize_prefix(self.route_prefix)
		first_segment = self.route_prefix.split("/")[0] if self.route_prefix else ""
		if first_segment in STOREFRONT_SEGMENTS:
			frappe.throw(
				_("Route Prefix can't start with {0}, the storefront already uses that path.").format(
					frappe.bold(first_segment)
				)
			)

	def validate_order_naming_series(self):
		if not self.order_naming_series:
			return
		options = (frappe.get_meta("Sales Order").get_field("naming_series").options or "").split("\n")
		if self.order_naming_series not in options:
			frappe.throw(
				_("Add {0} to Sales Order in Document Naming Settings first.").format(
					frappe.bold(self.order_naming_series)
				)
			)

	def on_update(self):
		# the storefront reads the cached settings, which Frappe refreshes only after on_update
		self.clear_cache()
		if self.has_value_changed("require_terms"):
			from shop.install import enable_customer_signup

			enable_customer_signup()
		if not self.active_theme:
			return
		if get_prefix() != normalize_prefix(self.applied_route_prefix):
			from shop.themes import apply_route_prefix

			apply_route_prefix()
