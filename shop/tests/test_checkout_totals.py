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
	"terms_and_conditions", "terms_checkbox_note", "business_buyer_label", "flat_shipping_rate", "free_shipping_above", "enable_cod",
	"payment_gateway_account",
)

TEST_PROVIDER = "shop.tests.test_checkout_totals.test_rates"


def test_rates(context):
	"""A shipping app: one courier service, 12.00 before tax; none to Antarctica."""
	if context.address.country == "Antarctica":
		return {"options": [], "messages": ["We don't ship there"]}
	return [{"id": "test:courier", "label": "Test Courier", "amount": 12.0, "tax_included": False}]


def rates_with_tax(context):
	"""A shipping app whose prices include the store's tax: 13.80 is 12.00 plus 15%."""
	return [{"id": "test:taxed", "label": "Taxed Courier", "amount": 13.80, "tax_included": True}]


def company_bank_account(company: str, currency: str) -> str:
	"""An ERPNext company Bank Account in a currency, with test details, as a store would set up."""
	name = f"_Test Shop {currency}"
	if frappe.db.exists("Bank Account", {"account_name": name, "company": company}):
		return frappe.db.get_value("Bank Account", {"account_name": name, "company": company})
	if not frappe.db.exists("Bank", "_Test Shop Bank"):
		frappe.get_doc({"doctype": "Bank", "bank_name": "_Test Shop Bank", "swift_number": "TESTXX22"}).insert(ignore_permissions=True)
	parent = frappe.db.get_value("Account", {"company": company, "account_type": "Bank", "is_group": 1}) or frappe.db.get_value(
		"Account", {"company": company, "root_type": "Asset", "is_group": 1}
	)
	account = frappe.db.get_value("Account", {"company": company, "account_name": name})
	if not account:
		account = frappe.get_doc(
			{"doctype": "Account", "account_name": name, "company": company, "parent_account": parent,
			 "account_type": "Bank", "account_currency": currency}
		).insert(ignore_permissions=True).name
	return frappe.get_doc(
		{"doctype": "Bank Account", "account_name": name, "bank": "_Test Shop Bank", "is_company_account": 1,
		 "company": company, "account": account, "branch_code": "2", "bank_account_no": "3"}
	).insert(ignore_permissions=True).name


def terms_record() -> str:
	"""An ERPNext Terms and Conditions record for the store, using Jinja as ERPNext allows."""
	if not frappe.db.exists("Terms and Conditions", "_Test Store Terms"):
		frappe.get_doc(
			{"doctype": "Terms and Conditions", "title": "_Test Store Terms", "selling": 1,
			 "terms": "<p>Terms for {{ customer_name }}</p>"}
		).insert(ignore_permissions=True)
	return "_Test Store Terms"


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
		settings.append("bank_accounts", {"bank_account": company_bank_account(self.company, self.company_currency), "label": "Home"})
		settings.append("bank_accounts", {"bank_account": company_bank_account(self.company, self.foreign), "label": "Foreign",
										  "conversion_fee_percent": 1, "instructions": "Pay by ACH"})
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
			settings.append("bank_accounts", {k: row[k] for k in ("bank_account", "label", "conversion_fee_percent", "instructions")})
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
		# the details come from the ERPNext Bank Account, Bank and the account's currency
		branch = "ACH routing number" if self.foreign == "USD" else "Branch code"
		self.assertIn(f"{branch}: 2", info["details"])
		self.assertIn("Account number: 3", info["details"])
		self.assertIn("BIC/SWIFT: TESTXX22", info["details"])
		self.assertIn("Pay by ACH", info["details"])

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

	# --- prices without tax ------------------------------------------------------------

	def without_tax(self):
		self.set(prices_include_tax=0, tax_template=template(self.company, "Test VAT 15 added", 15, 0))

	def test_prices_without_tax_get_tax_added_on_every_line_and_the_invoice_matches(self):
		self.without_tax()
		with patch("frappe.get_hooks", side_effect=hooks_with([TEST_PROVIDER])):
			quote = totals.compute(self.basket(1), self.home_address, "test:courier", "card")
			self.assertEqual(quote.subtotal, flt(self.price, 2))
			self.assertEqual(quote.shipping.customer_amount, 12.0)  # the carrier's price, tax added below
			fee = quote.fees[0].amount
			lines = flt(self.price + 12.0 + fee, 2)
			self.assertEqual(quote.tax_added, flt(lines * 0.15, 2))
			self.assertEqual(quote.total, flt(lines + quote.tax_added, 2))
			# the fee (its tax is passed on) pays the provider's charge on the whole total
			self.assertAlmostEqual(fee, 0.0265 * quote.total + 0.30, delta=0.01)
			# the buyer sees every line with tax, adding up to the total, and the tax it includes
			shown = totals.payload(quote)
			self.assertNotIn("tax", [line["kind"] for line in shown["lines"]])
			self.assertEqual(flt(sum(line["amount"] for line in shown["lines"]), 2), quote.total)
			self.assertEqual([l["amount"] for l in shown["lines"] if l["kind"] == "shipping"], [13.80])
			self.assertIn(f"{totals.money(quote.tax_added, quote.currency)}", " ".join(shown["notes"]))
			self.assertEqual([o["formatted"] for o in shown["shipping_options"]], [totals.money(13.80, quote.currency)])

			bank = totals.compute(cart.resolve_cart(), self.home_address, "test:courier", "bank")
			name = checkout.place_order(customer=BUYER, address=self.home_address, payment_method="bank",
										shipping_option="test:courier")["sales_order"]
		order = frappe.get_doc("Sales Order", name)
		rows = {row.item_code: row for row in order.items}
		self.assertEqual(rows[ITEM].rate, flt(self.price, 2))
		self.assertEqual(rows["TEST-SHIPPING"].rate, 12.0)
		self.assertEqual(rows["TEST-SHIPPING"].description, "Test Courier")
		self.assertEqual(flt(order.total_taxes_and_charges, 2), bank.tax_added)
		self.assertEqual(flt(order.grand_total, 2), bank.total)
		with patch("frappe.sendmail"):
			self.pay(name)
		invoice = frappe.get_doc("Sales Invoice", payment.order_invoice(name))
		self.assertEqual({row.item_code: row.rate for row in invoice.items}, {ITEM: flt(self.price, 2), "TEST-SHIPPING": 12.0})
		self.assertEqual(flt(invoice.total_taxes_and_charges, 2), bank.tax_added)
		self.assertEqual(flt(invoice.grand_total, 2), bank.total)
		self.assertEqual(flt(invoice.outstanding_amount), 0)

	def test_store_currency_shows_price_with_tax_and_without_abroad_shows_it_without(self):
		from frappe.utils import fmt_money

		from shop.storefront import pricing

		self.without_tax()
		home = self.company_currency
		# as a guest: the store's pages are public
		frappe.set_user("Guest")
		self.addCleanup(frappe.set_user, "Administrator")
		self.assertEqual(pricing.display_price(100), f"{fmt_money(115, currency=home)} {home} incl. VAT")
		self.assertEqual(pricing.display_price_without_tax(100), f"{fmt_money(100, currency=home)} {home} excl. VAT")
		self.with_currency_cookie(self.foreign)
		rate = pricing.display_rate(self.foreign)
		self.assertEqual(pricing.display_price(100), f"≈ {fmt_money(100 * rate, currency=self.foreign)} {self.foreign}")
		self.assertIsNone(pricing.display_price_without_tax(100))

	def test_carrier_price_with_tax_goes_on_the_order_without_it(self):
		self.without_tax()
		with patch("frappe.get_hooks", side_effect=hooks_with(["shop.tests.test_checkout_totals.rates_with_tax"])):
			quote = totals.compute(self.basket(1), self.home_address, None, "bank")
			self.assertEqual(quote.shipping.customer_amount, 12.0)
			self.assertEqual(quote.total, flt((self.price + 12.0) * 1.15, 2))
			export = totals.compute(cart.resolve_cart(), self.abroad_address, None, "bank", self.company_currency)
			self.assertEqual(export.shipping.customer_amount, 12.0)
			self.assertEqual(export.order_items[0].rate, flt(self.price, 2))  # nothing to take off
			self.assertEqual(export.tax_added, 0)
			# an export is shown without tax, as it is charged
			self.assertEqual([l["amount"] for l in totals.payload(export)["lines"] if l["kind"] == "shipping"], [12.0])

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
		from shop.storefront import terms

		self.set(require_terms=1, terms_url="", terms_and_conditions=terms_record())
		self.basket(1)
		with self.assertRaises(frappe.ValidationError):
			checkout.place_order(customer=BUYER, address=self.home_address, payment_method="bank")
		result = checkout.place_order(customer=BUYER, address=self.home_address, payment_method="bank",
									  accept_terms=1, business_buyer=1)
		order = frappe.get_doc("Sales Order", result["sales_order"])
		self.assertEqual(order.shop_terms_version, terms.version())
		self.assertTrue(order.shop_terms_version.startswith("_Test Store Terms ("))
		self.assertTrue(order.shop_terms_accepted_on)
		# the ERPNext terms go on the order; ERPNext fills in their text for the order when printing it
		self.assertEqual(order.tc_name, "_Test Store Terms")
		order.set_missing_terms()
		self.assertIn(f"Terms for {order.customer_name}", order.terms)
		self.assertTrue(terms.url().endswith("/terms"))  # the store's own terms page
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

	def pay(self, name, amount=None):
		from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

		entry = get_payment_entry("Sales Order", name, party_amount=amount)
		entry.reference_no, entry.reference_date = "TEST", nowdate()
		entry.insert(ignore_permissions=True)
		entry.submit()
		return entry

	def test_paid_order_is_invoiced_with_the_payment_set_against_it(self):
		self.basket(1)
		name = checkout.place_order(customer=BUYER, address=self.home_address, payment_method="bank")["sales_order"]
		self.assertFalse(payment.order_invoice(name))  # not invoiced before payment
		with patch("frappe.sendmail"):
			entry = self.pay(name)
		invoice = frappe.get_doc("Sales Invoice", payment.order_invoice(name))
		self.assertEqual(invoice.docstatus, 1)
		self.assertEqual(flt(invoice.outstanding_amount), 0)
		self.assertEqual(invoice.status, "Paid")
		self.assertEqual([a.reference_name for a in invoice.advances], [entry.name])

	def test_part_paid_order_is_not_invoiced(self):
		self.basket(1)
		name = checkout.place_order(customer=BUYER, address=self.home_address, payment_method="bank")["sales_order"]
		with patch("frappe.sendmail"):
			self.pay(name, amount=1)
		self.assertFalse(payment.order_invoice(name))

	def test_invoice_pdf_needs_the_customer_or_the_order_link(self):
		from shop.storefront import accounts

		self.basket(1)
		result = checkout.place_order(customer=BUYER, address=self.home_address, payment_method="bank")
		name = result["sales_order"]
		with patch("frappe.sendmail"):
			self.pay(name)
		invoice = payment.order_invoice(name)
		token = frappe.db.get_value("Shop Cart", {"sales_order": name}, "token")
		frappe.set_user("Guest")
		try:
			with self.assertRaises(frappe.PermissionError):
				accounts.invoice_pdf(invoice)
			with self.assertRaises(frappe.PermissionError):
				accounts.invoice_pdf(invoice, order=name, token="wrong")
			with patch("frappe.get_print", return_value=b"%PDF") as get_print:
				accounts.invoice_pdf(invoice, order=name, token=token)
			self.assertEqual(get_print.call_args.kwargs["print_format"], frappe.get_meta("Sales Invoice").default_print_format or None)
		finally:
			frappe.set_user("Administrator")

	def test_overseas_bank_transfer_suggests_the_buyers_currency(self):
		quote = totals.compute(self.basket(1), self.abroad_address, None, "bank")
		self.assertEqual(quote.currency, self.foreign if self.other == "United States" else self.company_currency)

	def with_currency_cookie(self, currency):
		from werkzeug.test import EnvironBuilder
		from werkzeug.wrappers import Request

		builder = EnvironBuilder(path="/store/products", headers={"Cookie": f"shop_currency={currency}"})
		frappe.local.request = Request(builder.get_environ())
		self.addCleanup(lambda: delattr(frappe.local, "request") if hasattr(frappe.local, "request") else None)
		for name in ("shop_display_rates",):
			if hasattr(frappe.local, name):
				delattr(frappe.local, name)

	def test_prices_show_the_store_currency_by_default(self):
		from shop.storefront import pricing

		self.assertEqual(pricing.display_currencies(), [self.company_currency, self.foreign])
		self.assertTrue(pricing.display_amount(100).endswith(f" {self.company_currency}"))
		self.assertNotIn("≈", pricing.display_amount(100))

	def test_picked_currency_shows_approximate_prices_and_leads_bank_transfer(self):
		from shop.storefront import pricing

		basket = self.basket(1)
		self.with_currency_cookie(self.foreign)
		shown = pricing.display_amount(100)
		self.assertTrue(shown.startswith("≈ ") and shown.endswith(f" {self.foreign}"), shown)
		# checkout charges the real amounts: bank transfer starts in the picked currency
		quote = totals.compute(basket, self.home_address, None, "bank")
		self.assertEqual(quote.currency, self.foreign)
		card = totals.payload(totals.compute(basket, self.home_address, None, "card"))
		self.assertEqual(card["currency"], self.company_currency)
		self.assertTrue(any(self.foreign in note for note in card["notes"]))

	def test_unknown_currency_cookie_is_ignored(self):
		from shop.storefront import pricing

		self.with_currency_cookie("XYZ")
		self.assertEqual(pricing.display_currency(), self.company_currency)
		with self.assertRaises(frappe.ValidationError):
			pricing.set_currency("XYZ")

	def test_signed_in_customer_keeps_the_currency_as_billing_currency(self):
		from shop.storefront import accounts, pricing

		email = "currency.keeper@example.com"
		if not frappe.db.exists("User", email):
			accounts.register(full_name="Currency Keeper", email=email, accept_terms=1)
		customer = frappe.db.get_value("Contact", {"email_id": email}, "name")
		frappe.set_user(email)
		try:
			for name in ("shop_session_customer",):
				if hasattr(frappe.local, name):
					delattr(frappe.local, name)
			frappe.local.cookie_manager = frappe.auth.CookieManager()
			pricing.set_currency(self.foreign)
			party = pricing.session_customer()
			self.assertEqual(frappe.db.get_value("Customer", party, "default_currency"), self.foreign)
			self.assertEqual(frappe.local.cookie_manager.cookies["shop_currency"]["value"], self.foreign)
			# without the cookie (another browser) the Billing Currency still applies
			frappe.clear_cache(doctype="Customer")
			self.assertEqual(pricing.display_currency(), self.foreign)
		finally:
			frappe.set_user("Administrator")

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
