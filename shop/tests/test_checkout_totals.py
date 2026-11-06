"""Checkout totals: tax included in prices, exports, shipping providers, card fees, bank transfers
in other currencies, terms acceptance and unpaid order cancellation."""

import hashlib
import hmac
import time
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, flt, nowdate

from shop.payments import stripe_checkout
from shop.storefront import cart, checkout, payment, shipping, totals

BUYER = {"email": "totals.buyer@example.com", "full_name": "Totals Buyer", "phone": "021000000"}
ITEM = "SHOP-DEMO-003"
SETTINGS_FIELDS = (
	"prices_include_tax", "tax_template", "export_tax_template", "shipping_item", "card_fee_item",
	"card_fee_percent", "card_fee_fixed", "card_fee_percent_international", "card_fee_fixed_international",
	"enable_bank_transfer", "conversion_fee_item", "bank_transfer_days", "require_terms", "terms_url",
	"terms_version", "business_buyer_label", "flat_shipping_rate", "free_shipping_above", "enable_cod",
	"payment_gateway_account",
)

TEST_PROVIDER = "shop.tests.test_checkout_totals.test_rates"


def test_rates(context):
	"""A shipping app: one courier service, 12.00 before tax; none to Antarctica."""
	if context.address.country == "Antarctica":
		return {"options": [], "messages": ["We don't ship there"]}
	return [{"id": "test:courier", "label": "Test Courier", "amount": 12.0, "tax_included": False}]


def inactive_rates(context):
	return None


def service(name: str, company: str) -> str:
	if not frappe.db.exists("Item", name):
		frappe.get_doc(
			{"doctype": "Item", "item_code": name, "item_name": name, "item_group": "All Item Groups",
			 "stock_uom": "Nos", "is_stock_item": 0, "is_sales_item": 1}
		).insert(ignore_permissions=True)
	return name


def template(company: str, title: str, rate: float, included: int) -> str:
	name = frappe.db.exists("Sales Taxes and Charges Template", {"title": title, "company": company})
	if name:
		return name
	account = frappe.db.get_value("Account", {"company": company, "account_type": "Tax", "is_group": 0})
	doc = frappe.get_doc(
		{"doctype": "Sales Taxes and Charges Template", "title": title, "company": company,
		 "taxes": [{"charge_type": "On Net Total", "account_head": account, "rate": rate,
					"description": f"VAT {rate:g}%", "included_in_print_rate": included}]}
	)
	doc.insert(ignore_permissions=True)
	return doc.name


class TestCheckoutTotals(IntegrationTestCase):
	def setUp(self):
		frappe.db.delete("Shop Cart")
		if hasattr(frappe.local, "request"):
			del frappe.local.request
		settings = frappe.get_doc("Shop Settings")
		self.original = {field: settings.get(field) for field in SETTINGS_FIELDS}
		self.original_banks = [row.as_dict() for row in settings.bank_accounts]
		self.company = settings.company
		self.company_currency = frappe.get_cached_value("Company", self.company, "default_currency")
		self.home = frappe.get_cached_value("Company", self.company, "country")
		self.abroad = "Antarctica" if self.home != "Antarctica" else "New Zealand"
		self.other = "United States" if self.home != "United States" else "Australia"
		self.foreign = "USD" if self.company_currency != "USD" else "EUR"
		self.set(
			prices_include_tax=1,
			tax_template=template(self.company, "Test VAT 15 included", 15, 1),
			export_tax_template=template(self.company, "Test VAT 0 export", 0, 0),
			shipping_item=service("TEST-SHIPPING", self.company),
			card_fee_item=service("TEST-CARD-FEE", self.company),
			conversion_fee_item=service("TEST-CONVERSION", self.company),
			card_fee_percent=2.65, card_fee_fixed=0.30,
			card_fee_percent_international=3.5, card_fee_fixed_international=0.30,
			enable_bank_transfer=1, bank_transfer_days=7, enable_cod=1,
			require_terms=0, flat_shipping_rate=0, free_shipping_above=0,
		)
		settings = frappe.get_doc("Shop Settings")
		settings.bank_accounts = []
		settings.append("bank_accounts", {"currency": self.company_currency, "label": "Home", "account_details": "Acct: 1"})
		settings.append("bank_accounts", {"currency": self.foreign, "label": "Foreign", "conversion_fee_percent": 1,
										  "account_details": "Routing: 2\nAccount: 3"})
		settings.save(ignore_permissions=True)
		frappe.clear_cache(doctype="Shop Settings")
		if not frappe.db.exists("Currency Exchange", {"from_currency": self.company_currency, "to_currency": self.foreign}):
			frappe.get_doc({"doctype": "Currency Exchange", "date": add_days(nowdate(), -1),
							"from_currency": self.company_currency, "to_currency": self.foreign,
							"exchange_rate": 0.5, "for_selling": 1}).insert(ignore_permissions=True)
		if not frappe.db.exists("Currency Exchange", {"from_currency": self.foreign, "to_currency": self.company_currency}):
			frappe.get_doc({"doctype": "Currency Exchange", "date": add_days(nowdate(), -1),
							"from_currency": self.foreign, "to_currency": self.company_currency,
							"exchange_rate": 2.0, "for_selling": 1}).insert(ignore_permissions=True)
		self.price = flt(frappe.db.get_value("Item Price", {"item_code": ITEM, "price_list": settings.price_list}, "price_list_rate"))
		self.home_address = {"address_line1": "5 High Street", "city": "Town", "pincode": "9016", "country": self.home}
		self.abroad_address = {"address_line1": "1 Ice Shelf", "city": "Base", "pincode": "0001", "country": self.other}

	def tearDown(self):
		settings = frappe.get_doc("Shop Settings")
		settings.update(self.original)
		settings.bank_accounts = []
		for row in self.original_banks:
			settings.append("bank_accounts", {k: row[k] for k in ("currency", "label", "conversion_fee_percent", "account_details")})
		settings.save(ignore_permissions=True)
		frappe.clear_cache(doctype="Shop Settings")

	def set(self, **values):
		frappe.db.set_single_value("Shop Settings", values)
		frappe.clear_cache(doctype="Shop Settings")

	def basket(self, qty=2):
		cart.add_item(ITEM, qty=qty)
		return cart.resolve_cart()

	# --- totals ---------------------------------------------------------------

	def test_card_fee_covers_the_provider_fee_on_the_final_total(self):
		quote = totals.compute(self.basket(), self.home_address, None, "card")
		goods = self.price * 2
		fee = quote.fees[0].amount
		self.assertEqual(quote.total, flt(goods + fee, 2))
		# the fee, less its own tax, pays the provider's charge on the whole total (to the cent)
		provider_charge = 0.0265 * quote.total + 0.30
		self.assertAlmostEqual(fee / 1.15, provider_charge, delta=0.01)
		self.assertGreaterEqual(fee / 1.15, provider_charge - 0.005)
		self.assertAlmostEqual(quote.tax_included, quote.total - quote.total / 1.15, delta=0.01)

	def test_bank_transfer_has_no_card_fee(self):
		quote = totals.compute(self.basket(), self.home_address, None, "bank")
		self.assertEqual(quote.fees, [])
		self.assertEqual(quote.total, flt(self.price * 2, 2))

	def test_export_takes_included_tax_off_and_uses_the_export_template(self):
		quote = totals.compute(self.basket(1), self.abroad_address, None, "bank", self.company_currency)
		self.assertTrue(quote.export)
		self.assertEqual(quote.order_items[0].rate, flt(self.price / 1.15, 2))
		self.assertEqual(quote.tax_template, frappe.get_cached_doc("Shop Settings").export_tax_template)
		card = totals.compute(cart.resolve_cart(), self.abroad_address, None, "card")
		self.assertAlmostEqual(card.fees[0].amount, 0.035 * card.total + 0.30, delta=0.01)

	def test_foreign_currency_bank_transfer(self):
		quote = totals.compute(self.basket(1), self.abroad_address, None, "bank", self.foreign)
		self.assertEqual(quote.currency, self.foreign)
		converted = flt(flt(self.price / 1.15 * quote.exchange_rate, 2), 2)
		self.assertEqual(quote.subtotal, converted)
		self.assertEqual(quote.fees[0].kind, "conversion_fee")
		self.assertEqual(quote.fees[0].amount, totals.round_up(converted * 0.01))
		result = checkout.place_order(customer=BUYER, address=self.abroad_address, payment_method="bank", currency=self.foreign)
		order = frappe.get_doc("Sales Order", result["sales_order"])
		self.assertEqual(order.currency, self.foreign)
		self.assertEqual(flt(order.grand_total, 2), quote.total)
		self.assertEqual(order.shop_payment_method, "bank")
		self.assertEqual(order.contact_email, BUYER["email"])
		info = payment.bank_instructions(order)
		self.assertEqual(info["reference"], order.name)
		self.assertIn("Routing: 2", info["details"])

	# --- shipping ----------------------------------------------------------------

	def test_provider_rates_are_charged_as_quoted_with_tax(self):
		with patch("frappe.get_hooks", side_effect=hooks_with([TEST_PROVIDER])):
			quote = totals.compute(self.basket(1), self.home_address, None, "bank")
			self.assertEqual(quote.shipping["id"], "test:courier")
			self.assertEqual(quote.shipping.customer_amount, 13.80)  # 12.00 plus 15% tax, as prices include tax
			result = checkout.place_order(customer=BUYER, address=self.home_address, payment_method="bank",
										  shipping_option="test:courier")
			order = frappe.get_doc("Sales Order", result["sales_order"])
			shipping_line = [row for row in order.items if row.item_code == "TEST-SHIPPING"]
			self.assertEqual(shipping_line[0].rate, 13.80)
			self.assertIn("test:courier", order.shop_shipping_option)

	def test_no_rates_means_no_order_and_says_why(self):
		with patch("frappe.get_hooks", side_effect=hooks_with([TEST_PROVIDER])):
			self.basket(1)
			address = dict(self.abroad_address, country="Antarctica")
			quote = totals.compute(cart.resolve_cart(), address, None, "bank")
			self.assertIsNone(quote.shipping)
			self.assertEqual(quote.shipping_messages, ["We don't ship there"])
			with self.assertRaises(frappe.ValidationError):
				checkout.place_order(customer=BUYER, address=address, payment_method="bank")

	def test_switched_off_provider_means_free_shipping(self):
		with patch("frappe.get_hooks", side_effect=hooks_with(["shop.tests.test_checkout_totals.inactive_rates"])):
			quote = totals.compute(self.basket(1), self.home_address, None, "bank")
			self.assertEqual(quote.shipping["amount"], 0)

	# --- order rules ---------------------------------------------------------------

	def test_terms_must_be_accepted_and_are_recorded(self):
		self.set(require_terms=1, terms_url="/terms", terms_version="v1")
		self.basket(1)
		with self.assertRaises(frappe.ValidationError):
			checkout.place_order(customer=BUYER, address=self.home_address, payment_method="bank")
		result = checkout.place_order(customer=BUYER, address=self.home_address, payment_method="bank",
									  accept_terms=1, business_buyer=1)
		order = frappe.get_doc("Sales Order", result["sales_order"])
		self.assertEqual(order.shop_terms_version, "v1")
		self.assertTrue(order.shop_terms_accepted_on)
		self.assertEqual(order.shop_business_buyer, 1)

	def test_changed_total_stops_the_order(self):
		self.basket(1)
		with self.assertRaises(frappe.ValidationError):
			checkout.place_order(customer=BUYER, address=self.home_address, payment_method="bank", expected_total=1)

	def test_unpaid_bank_order_is_cancelled_after_its_due_date(self):
		self.basket(1)
		name = checkout.place_order(customer=BUYER, address=self.home_address, payment_method="bank")["sales_order"]
		self.assertFalse(payment.cancel_if_unpaid(name))  # not due yet
		frappe.db.set_value("Sales Order", name, "shop_payment_due", add_days(nowdate(), -1))
		with patch("frappe.sendmail") as sendmail:
			self.assertTrue(payment.cancel_if_unpaid(name))
		self.assertEqual(frappe.db.get_value("Sales Order", name, "docstatus"), 2)
		self.assertEqual(sendmail.call_args.kwargs["recipients"], [BUYER["email"]])

	def test_paid_bank_order_is_not_cancelled(self):
		from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

		self.basket(1)
		name = checkout.place_order(customer=BUYER, address=self.home_address, payment_method="bank")["sales_order"]
		entry = get_payment_entry("Sales Order", name)
		entry.reference_no, entry.reference_date = "TEST", nowdate()
		entry.insert(ignore_permissions=True)
		entry.submit()
		frappe.db.set_value("Sales Order", name, "shop_payment_due", add_days(nowdate(), -1))
		order = frappe.get_doc("Sales Order", name)
		self.assertTrue(payment.is_paid(order))
		self.assertFalse(payment.cancel_if_unpaid(name))

	def test_overseas_bank_transfer_suggests_the_buyers_currency(self):
		quote = totals.compute(self.basket(1), self.abroad_address, None, "bank")
		self.assertEqual(quote.currency, self.foreign if self.other == "United States" else self.company_currency)

	def test_charge_lines_are_not_products(self):
		self.assertTrue(totals.is_charge_item("TEST-CARD-FEE"))
		self.assertFalse(totals.is_charge_item(ITEM))


class TestStripeWebhook(IntegrationTestCase):
	def sign(self, payload: bytes, secret: str, timestamp: int) -> str:
		digest = hmac.new(secret.encode(), f"{timestamp}.".encode() + payload, hashlib.sha256).hexdigest()
		return f"t={timestamp},v1={digest}"

	def test_signature(self):
		payload, now = b'{"type":"payment_intent.succeeded"}', int(time.time())
		self.assertTrue(stripe_checkout.valid_signature(payload, self.sign(payload, "whsec", now), "whsec"))
		self.assertFalse(stripe_checkout.valid_signature(payload, self.sign(payload, "other", now), "whsec"))
		self.assertFalse(stripe_checkout.valid_signature(payload + b" ", self.sign(payload, "whsec", now), "whsec"))
		self.assertFalse(stripe_checkout.valid_signature(payload, self.sign(payload, "whsec", now - 3600), "whsec"))
		self.assertFalse(stripe_checkout.valid_signature(payload, None, "whsec"))

	def test_minor_units(self):
		self.assertEqual(stripe_checkout.minor_units(293.29, "NZD"), 29329)
		self.assertEqual(stripe_checkout.minor_units(1000, "JPY"), 1000)


original_hooks = frappe.get_hooks


def hooks_with(providers):
	def get_hooks(hook=None, *args, **kwargs):
		if hook == "shop_shipping_rate_providers":
			return providers
		return original_hooks(hook, *args, **kwargs)

	return get_hooks
