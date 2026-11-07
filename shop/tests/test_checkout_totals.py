"""Checkout totals worked out by ERPNext: tax in or on top of prices, exports, coupons, shipping,
card and conversion fees, other currencies, and what happens once an order is placed."""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, flt, nowdate

from shop.storefront import cart, checkout, payment, totals
from shop.tests.utils import company_bank_account, service, template, terms_record

BUYER = {"email": "totals.buyer@example.com", "full_name": "Totals Buyer", "phone": "021000000"}
ITEM = "SHOP-DEMO-003"
SETTINGS_FIELDS = (
	"prices_include_tax",
	"show_prices_with_tax",
	"tax_template",
	"export_tax_template",
	"shipping_item",
	"shipping_account",
	"card_fee_item",
	"card_fee_percent",
	"card_fee_fixed",
	"card_fee_percent_international",
	"card_fee_fixed_international",
	"enable_bank_transfer",
	"conversion_fee_item",
	"bank_transfer_days",
	"require_terms",
	"terms_url",
	"terms_and_conditions",
	"terms_checkbox_note",
	"business_buyer_label",
	"flat_shipping_rate",
	"free_shipping_above",
	"enable_cod",
	"payment_gateway_account",
	"invoice_paid_orders",
	"cancel_unpaid_orders",
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


def inactive_rates(context):
	return None


def line(quote, kind):
	return [row["amount"] for row in totals.summary_lines(quote.order)[0] if row["kind"] == kind]


def grand(quote) -> float:
	"""The order total before any rounding, for checking the sums."""
	return flt(quote.order.grand_total)


def fee_row(quote, item):
	return next(row for row in quote.order.items if row.item_code == item)


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
		self.other = "United States" if self.home != "United States" else "Australia"
		self.foreign = "USD" if self.company_currency != "USD" else "EUR"
		self.export_template = template(self.company, "Test VAT 0 export", 0, 0)
		self.set(
			prices_include_tax=1,
			show_prices_with_tax=0,
			tax_template=template(self.company, "Test VAT 15 included", 15, 1),
			export_tax_template=self.export_template,
			shipping_item=service("TEST-SHIPPING", self.company),
			card_fee_item=service("TEST-CARD-FEE", self.company),
			conversion_fee_item=service("TEST-CONVERSION", self.company),
			card_fee_percent=2.65,
			card_fee_fixed=0.30,
			card_fee_percent_international=3.5,
			card_fee_fixed_international=0.30,
			enable_bank_transfer=1,
			bank_transfer_days=7,
			enable_cod=1,
			require_terms=0,
			flat_shipping_rate=0,
			free_shipping_above=0,
			invoice_paid_orders=0,
			cancel_unpaid_orders=0,
		)
		settings = frappe.get_doc("Shop Settings")
		settings.bank_accounts = []
		settings.append(
			"bank_accounts",
			{"bank_account": company_bank_account(self.company, self.company_currency), "label": "Home"},
		)
		settings.append(
			"bank_accounts",
			{
				"bank_account": company_bank_account(self.company, self.foreign),
				"label": "Foreign",
				"conversion_fee_percent": 1,
				"instructions": "Pay by ACH",
			},
		)
		settings.save(ignore_permissions=True)
		frappe.clear_cache(doctype="Shop Settings")
		for source, target, rate in (
			(self.company_currency, self.foreign, 0.5),
			(self.foreign, self.company_currency, 2.0),
		):
			if not frappe.db.exists("Currency Exchange", {"from_currency": source, "to_currency": target}):
				frappe.get_doc(
					{
						"doctype": "Currency Exchange",
						"date": add_days(nowdate(), -1),
						"from_currency": source,
						"to_currency": target,
						"exchange_rate": rate,
						"for_selling": 1,
					}
				).insert(ignore_permissions=True)
		self.price = flt(
			frappe.db.get_value(
				"Item Price", {"item_code": ITEM, "price_list": settings.price_list}, "price_list_rate"
			)
		)
		self.home_address = {
			"address_line1": "5 High Street",
			"city": "Town",
			"pincode": "9016",
			"country": self.home,
		}
		self.abroad_address = {
			"address_line1": "1 Ice Shelf",
			"city": "Base",
			"pincode": "0001",
			"country": self.other,
		}

	def tearDown(self):
		settings = frappe.get_doc("Shop Settings")
		settings.update(self.original)
		settings.bank_accounts = []
		for row in self.original_banks:
			settings.append(
				"bank_accounts",
				{k: row[k] for k in ("bank_account", "label", "conversion_fee_percent", "instructions")},
			)
		settings.save(ignore_permissions=True)
		frappe.clear_cache(doctype="Shop Settings")

	def set(self, **values):
		frappe.db.set_single_value("Shop Settings", values)
		frappe.clear_cache(doctype="Shop Settings")

	def free_coupon(self) -> str:
		"""A coupon for 100% off, as ERPNext's coupons are set up."""
		code = "TESTFREE100"
		if not frappe.db.exists("Coupon Code", {"coupon_code": code}):
			rule = frappe.get_doc(
				{
					"doctype": "Pricing Rule",
					"title": "Test free order",
					"apply_on": "Transaction",
					"selling": 1,
					"coupon_code_based": 1,
					"rate_or_discount": "Discount Percentage",
					"discount_percentage": 100,
					"company": self.company,
					"currency": self.company_currency,
				}
			).insert(ignore_permissions=True)
			frappe.get_doc(
				{
					"doctype": "Coupon Code",
					"coupon_name": code,
					"coupon_code": code,
					"coupon_type": "Promotional",
					"pricing_rule": rule.name,
				}
			).insert(ignore_permissions=True)
		return code

	def basket(self, qty=2, item=ITEM):
		cart.add_item(item, qty=qty)
		return cart.resolve_cart()

	def without_tax(self):
		"""A store whose prices exclude tax, which is added on top."""
		self.set(prices_include_tax=0, tax_template=template(self.company, "Test VAT 15 added", 15, 0))

	def place(self, **kwargs):
		kwargs.setdefault("payment_method", "bank")
		kwargs.setdefault("address", self.home_address)
		return checkout.place_order(customer=BUYER, **kwargs)["sales_order"]

	def pay(self, name, amount=None):
		from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

		entry = get_payment_entry("Sales Order", name, party_amount=amount)
		entry.reference_no, entry.reference_date = "TEST", nowdate()
		entry.insert(ignore_permissions=True)
		entry.submit()
		return entry

	# --- totals ---------------------------------------------------------------

	def test_card_fee_covers_the_provider_fee_on_the_final_total(self):
		quote = totals.compute(self.basket(), self.home_address, None, "gateway")
		fee = fee_row(quote, "TEST-CARD-FEE")
		self.assertEqual(grand(quote), flt(self.price * 2 + fee.amount, 2))
		# what the store keeps of the fee, after its tax, pays the provider's charge on the total
		self.assertAlmostEqual(fee.net_amount, 0.0265 * grand(quote) + 0.30, delta=0.01)
		self.assertGreaterEqual(fee.net_amount, 0.0265 * grand(quote) + 0.30 - 0.005)

	def test_bank_transfer_has_no_card_fee(self):
		quote = totals.compute(self.basket(), self.home_address, None, "bank")
		self.assertEqual(quote.fees, [])
		self.assertEqual(grand(quote), flt(self.price * 2, 2))

	def test_quote_and_order_are_the_same_erpnext_totals(self):
		with patch("frappe.get_hooks", side_effect=hooks_with([TEST_PROVIDER])):
			quote = totals.compute(self.basket(), self.home_address, None, "bank")
			order = frappe.get_doc("Sales Order", self.place(expected_total=quote.total))
		self.assertEqual(totals.order_total(order), quote.total)
		self.assertEqual([row.item_code for row in order.items], [row.item_code for row in quote.order.items])

	def test_export_takes_included_tax_off_and_uses_the_export_template(self):
		quote = totals.compute(self.basket(1), self.abroad_address, None, "bank", self.company_currency)
		self.assertTrue(quote.export)
		self.assertEqual(quote.tax_template, self.export_template)
		self.assertEqual(quote.order.items[0].rate, flt(self.price / 1.15, 2))
		self.assertIn("Tax-free export", totals.payload(quote)["notes"])
		card = totals.compute(cart.resolve_cart(), self.abroad_address, None, "gateway")
		self.assertAlmostEqual(fee_row(card, "TEST-CARD-FEE").amount, 0.035 * grand(card) + 0.30, delta=0.01)

	def test_a_shopping_cart_tax_rule_comes_before_the_export_template(self):
		rule_template = template(self.company, "Test VAT 5 rule", 5, 0)
		rule = frappe.get_doc(
			{
				"doctype": "Tax Rule",
				"tax_type": "Sales",
				"company": self.company,
				"use_for_shopping_cart": 1,
				"shipping_country": self.other,
				"sales_tax_template": rule_template,
				"priority": 1,
			}
		).insert(ignore_permissions=True)
		self.addCleanup(frappe.delete_doc, "Tax Rule", rule.name, force=True)
		quote = totals.compute(self.basket(1), self.abroad_address, None, "bank", self.company_currency)
		self.assertEqual(quote.tax_template, rule_template)

	def test_foreign_currency_bank_transfer(self):
		quote = totals.compute(self.basket(1), self.abroad_address, None, "bank", self.foreign)
		self.assertEqual(quote.currency, self.foreign)
		goods = flt(self.price / 1.15 * 0.5, 2)
		self.assertAlmostEqual(line(quote, "subtotal")[0], goods, delta=0.01)
		self.assertEqual([fee.kind for fee in quote.fees], ["conversion_fee"])
		self.assertAlmostEqual(fee_row(quote, "TEST-CONVERSION").net_amount, goods * 0.01, delta=0.01)
		name = self.place(address=self.abroad_address, currency=self.foreign)
		order = frappe.get_doc("Sales Order", name)
		self.assertEqual(order.currency, self.foreign)
		self.assertEqual(totals.order_total(order), quote.total)
		self.assertEqual(order.contact_email, BUYER["email"])
		info = payment.bank_instructions(order)
		self.assertEqual(info["reference"], order.name)
		branch = "ACH routing number" if self.foreign == "USD" else "Branch code"
		self.assertIn(f"{branch}: 2", info["details"])
		self.assertIn("Account number: 3", info["details"])
		self.assertIn("BIC/SWIFT: TESTXX22", info["details"])
		self.assertIn("Pay by ACH", info["details"])

	def test_coupon_is_charged_as_quoted_when_tax_is_added(self):
		self.without_tax()
		self.basket(1, "SHOP-DEMO-011")
		cart.apply_coupon("WELCOME10")
		quote = totals.compute(cart.resolve_cart(), self.home_address, None, "bank")
		order = frappe.get_doc("Sales Order", self.place(expected_total=quote.total))
		self.assertEqual(totals.order_total(order), quote.total)
		self.assertEqual(order.apply_discount_on, "Net Total")
		self.assertAlmostEqual(order.discount_amount, flt(order.total * 0.1, 2), delta=0.01)

	def test_flat_shipping_on_the_shipping_account_is_charged_as_quoted(self):
		self.without_tax()
		account = frappe.db.get_value(
			"Account", {"company": self.company, "account_type": "Chargeable", "is_group": 0}
		) or frappe.db.get_value("Account", {"company": self.company, "root_type": "Income", "is_group": 0})
		self.set(shipping_item=None, shipping_account=account, flat_shipping_rate=10)
		quote = totals.compute(self.basket(1), self.home_address, None, "bank")
		self.assertEqual(line(quote, "shipping"), [10.0])
		order = frappe.get_doc("Sales Order", self.place(expected_total=quote.total))
		self.assertEqual(totals.order_total(order), quote.total)

	def test_an_item_tax_template_is_charged_as_erpnext_charges_it(self):
		self.without_tax()
		tax_account = frappe.db.get_value(
			"Account", {"company": self.company, "account_type": "Tax", "is_group": 0}
		)
		title = "_Test Shop Zero Rated"
		if not frappe.db.exists("Item Tax Template", {"title": title, "company": self.company}):
			frappe.get_doc(
				{
					"doctype": "Item Tax Template",
					"title": title,
					"company": self.company,
					"taxes": [{"tax_type": tax_account, "tax_rate": 0}],
				}
			).insert(ignore_permissions=True)
		item_tax = frappe.db.get_value("Item Tax Template", {"title": title, "company": self.company})
		item = frappe.get_doc("Item", "SHOP-DEMO-011")
		item.append("taxes", {"item_tax_template": item_tax})
		item.save(ignore_permissions=True)
		self.basket(1)
		quote = totals.compute(self.basket(1, "SHOP-DEMO-011"), self.home_address, None, "bank")
		taxed = sum(row.net_amount for row in quote.order.items if row.item_code == ITEM)
		self.assertAlmostEqual(line(quote, "tax")[0], taxed * 0.15, delta=0.01)
		order = frappe.get_doc("Sales Order", self.place(expected_total=quote.total))
		self.assertEqual(totals.order_total(order), quote.total)

	def test_rounded_total_is_what_the_customer_pays(self):
		from frappe.custom.doctype.property_setter.property_setter import make_property_setter

		# ERPNext's Global Defaults sets this default on each transaction type
		make_property_setter(
			"Sales Order", "disable_rounded_total", "default", 0, "Text", validate_fields_for_doctype=False
		)
		self.addCleanup(frappe.clear_cache, doctype="Sales Order")
		quote = totals.compute(self.basket(1), self.home_address, None, "gateway")
		self.assertEqual(quote.total, flt(quote.order.rounded_total))
		shown = totals.payload(quote)
		self.assertAlmostEqual(sum(row["amount"] for row in shown["lines"]), quote.total, delta=0.001)

	# --- shipping ----------------------------------------------------------------

	def test_provider_rates_are_charged_as_quoted_with_tax(self):
		with patch("frappe.get_hooks", side_effect=hooks_with([TEST_PROVIDER])):
			quote = totals.compute(self.basket(1), self.home_address, None, "bank")
			self.assertEqual(quote.shipping["id"], "test:courier")
			self.assertEqual(line(quote, "shipping"), [13.80])  # 12.00 plus 15% tax, as prices include tax
			order = frappe.get_doc("Sales Order", self.place(shipping_option="test:courier"))
		shipping_line = [row for row in order.items if row.item_code == "TEST-SHIPPING"]
		self.assertEqual(shipping_line[0].rate, 13.80)
		self.assertIn("test:courier", order.shop_shipping_option)

	def test_prices_without_tax_get_tax_added_and_the_invoice_matches(self):
		self.without_tax()
		self.set(invoice_paid_orders=1)
		with patch("frappe.get_hooks", side_effect=hooks_with([TEST_PROVIDER])):
			quote = totals.compute(self.basket(1), self.home_address, "test:courier", "bank")
			self.assertEqual(line(quote, "subtotal"), [flt(self.price, 2)])
			self.assertEqual(line(quote, "shipping"), [12.0])
			self.assertEqual(line(quote, "tax"), [flt((self.price + 12.0) * 0.15, 2)])
			name = self.place(shipping_option="test:courier", expected_total=quote.total)
		order = frappe.get_doc("Sales Order", name)
		rows = {row.item_code: row for row in order.items}
		self.assertEqual(rows["TEST-SHIPPING"].rate, 12.0)
		self.assertEqual(rows["TEST-SHIPPING"].description, "Test Courier")
		with patch("frappe.sendmail"):
			self.pay(name)
		invoice = frappe.get_doc("Sales Invoice", payment.order_invoice(name))
		self.assertEqual(
			{row.item_code: row.rate for row in invoice.items},
			{ITEM: flt(self.price, 2), "TEST-SHIPPING": 12.0},
		)
		self.assertEqual(totals.order_total(invoice), quote.total)
		self.assertEqual(flt(invoice.outstanding_amount), 0)

	def test_lines_can_be_shown_with_tax(self):
		self.without_tax()
		self.set(show_prices_with_tax=1)
		with patch("frappe.get_hooks", side_effect=hooks_with([TEST_PROVIDER])):
			quote = totals.compute(self.basket(1), self.home_address, "test:courier", "bank")
		shown = totals.payload(quote)
		self.assertNotIn("tax", [row["kind"] for row in shown["lines"]])
		self.assertEqual(flt(sum(row["amount"] for row in shown["lines"]), 2), quote.total)
		self.assertEqual([row["amount"] for row in shown["lines"] if row["kind"] == "shipping"], [13.80])
		self.assertIn(totals.money(line(quote, "tax")[0], quote.currency), " ".join(shown["notes"]))
		self.assertEqual(
			[option["formatted"] for option in shown["shipping_options"]],
			[totals.money(13.80, quote.currency)],
		)

	def test_prices_are_shown_as_the_price_list_has_them_unless_shown_with_tax(self):
		from frappe.utils import fmt_money

		from shop.storefront import pricing

		self.without_tax()
		home = self.company_currency
		frappe.set_user("Guest")
		self.addCleanup(frappe.set_user, "Administrator")
		self.assertEqual(pricing.display_price(100), f"{fmt_money(100, currency=home)} {home}")
		self.assertIsNone(pricing.display_price_without_tax(100))
		self.set(show_prices_with_tax=1)
		self.assertEqual(pricing.display_price(100), f"{fmt_money(115, currency=home)} {home} incl. VAT")
		self.assertEqual(
			pricing.display_price_without_tax(100), f"{fmt_money(100, currency=home)} {home} excl. VAT"
		)
		self.with_currency_cookie(self.foreign)
		rate = pricing.display_rate(self.foreign)
		self.assertEqual(
			pricing.display_price(100), f"≈ {fmt_money(100 * rate, currency=self.foreign)} {self.foreign}"
		)
		self.assertIsNone(pricing.display_price_without_tax(100))

	def test_currency_code_is_shown_only_when_the_store_has_several(self):
		from frappe.utils import fmt_money

		from shop.storefront import pricing

		settings = frappe.get_doc("Shop Settings")
		settings.bank_accounts = settings.bank_accounts[:1]
		settings.save(ignore_permissions=True)
		frappe.clear_cache(doctype="Shop Settings")
		self.assertEqual(pricing.display_amount(100), fmt_money(100, currency=self.company_currency))

	def test_carrier_price_with_tax_goes_on_the_order_without_it(self):
		self.without_tax()
		with patch(
			"frappe.get_hooks", side_effect=hooks_with(["shop.tests.test_checkout_totals.rates_with_tax"])
		):
			quote = totals.compute(self.basket(1), self.home_address, None, "bank")
			self.assertEqual(line(quote, "shipping"), [12.0])
			self.assertEqual(grand(quote), flt((self.price + 12.0) * 1.15, 2))
			export = totals.compute(
				cart.resolve_cart(), self.abroad_address, None, "bank", self.company_currency
			)
		self.assertEqual(line(export, "shipping"), [12.0])
		self.assertEqual(export.order.items[0].rate, flt(self.price, 2))
		self.assertEqual(line(export, "tax"), [])

	def test_no_rates_means_no_order_and_says_why(self):
		with patch("frappe.get_hooks", side_effect=hooks_with([TEST_PROVIDER])):
			self.basket(1)
			address = dict(self.abroad_address, country="Antarctica")
			quote = totals.compute(cart.resolve_cart(), address, None, "bank")
			self.assertIsNone(quote.shipping)
			self.assertEqual(quote.shipping_messages, ["We don't ship there"])
			with self.assertRaises(frappe.ValidationError):
				self.place(address=address)

	def test_switched_off_provider_means_free_shipping(self):
		with patch(
			"frappe.get_hooks", side_effect=hooks_with(["shop.tests.test_checkout_totals.inactive_rates"])
		):
			quote = totals.compute(self.basket(1), self.home_address, None, "bank")
			self.assertEqual(quote.shipping["amount"], 0)

	def test_free_shipping_threshold_follows_the_coupon(self):
		account = frappe.db.get_value(
			"Account", {"company": self.company, "root_type": "Income", "is_group": 0}
		)
		self.set(
			shipping_item=None,
			shipping_account=account,
			flat_shipping_rate=10,
			free_shipping_above=self.price,
		)
		quote = totals.compute(self.basket(1), self.home_address, None, "bank")
		self.assertEqual(line(quote, "shipping"), [])
		cart.apply_coupon("WELCOME10")
		quote = totals.compute(cart.resolve_cart(), self.home_address, None, "bank")
		self.assertEqual(line(quote, "shipping"), [10.0])

	# --- order rules ---------------------------------------------------------------

	def test_terms_must_be_accepted_and_are_recorded(self):
		from shop.storefront import terms

		self.set(require_terms=1, terms_url="", terms_and_conditions=terms_record())
		self.basket(1)
		with self.assertRaises(frappe.ValidationError):
			self.place()
		order = frappe.get_doc("Sales Order", self.place(accept_terms=1, business_buyer=1))
		self.assertEqual(order.shop_terms_version, terms.version())
		self.assertTrue(order.shop_terms_version.startswith("_Test Store Terms ("))
		self.assertTrue(order.shop_terms_accepted_on)
		# ERPNext fills in the terms' text for the order when it prints it
		self.assertEqual(order.tc_name, "_Test Store Terms")
		order.set_missing_terms()
		self.assertIn(f"Terms for {order.customer_name}", order.terms)
		self.assertTrue(terms.url().endswith("/terms"))
		self.assertEqual(order.shop_business_buyer, 1)

	def test_changed_total_stops_the_order(self):
		self.basket(1)
		with self.assertRaises(frappe.ValidationError):
			self.place(expected_total=1)

	def test_a_cart_is_ordered_once(self):
		self.basket(1)
		basket = cart.resolve_cart()
		self.place()
		with self.assertRaises(frappe.ValidationError):
			checkout.lock_cart(basket)

	def test_a_product_without_a_price_cant_be_ordered(self):
		price = frappe.db.get_value(
			"Item Price", {"item_code": ITEM, "price_list": frappe.get_cached_doc("Shop Settings").price_list}
		)
		frappe.db.set_value("Item Price", price, "price_list_rate", 0)
		self.addCleanup(frappe.db.set_value, "Item Price", price, "price_list_rate", self.price)
		self.basket(1)
		with self.assertRaises(frappe.ValidationError):
			self.place()

	def test_a_guest_must_sign_in_to_order_for_an_account(self):
		from shop.storefront import accounts

		email = "has.account@example.com"
		if not frappe.db.exists("User", email):
			accounts.register(full_name="Has Account", email=email, accept_terms=1)
		self.basket(1)
		frappe.set_user("Guest")
		self.addCleanup(frappe.set_user, "Administrator")
		with self.assertRaises(frappe.ValidationError):
			checkout.place_order(
				customer={"email": email, "full_name": "Someone"},
				address=self.home_address,
				payment_method="cod",
			)

	def test_unpaid_orders_are_cancelled_only_when_the_store_wants_it(self):
		self.basket(1)
		name = self.place()
		frappe.db.set_value("Sales Order", name, "shop_payment_due", add_days(nowdate(), -1))
		payment.cancel_overdue_orders()
		self.assertEqual(frappe.db.get_value("Sales Order", name, "docstatus"), 1)
		self.set(cancel_unpaid_orders=1)
		with patch("frappe.sendmail") as sendmail, patch("frappe.db.commit"):
			payment.cancel_overdue_orders()
		self.assertEqual(frappe.db.get_value("Sales Order", name, "docstatus"), 2)
		self.assertIn([BUYER["email"]], [call.kwargs["recipients"] for call in sendmail.call_args_list])

	def test_unpaid_bank_order_is_not_cancelled_before_its_due_date(self):
		self.basket(1)
		name = self.place()
		self.assertFalse(payment.cancel_if_unpaid(name))

	def test_store_emails_are_editable_email_templates(self):
		from shop.storefront import emails

		emails.ensure_templates()
		for name, *_rest in emails.TEMPLATES.values():
			self.assertTrue(frappe.db.exists("Email Template", name), name)
		template_doc = frappe.get_doc("Email Template", "Shop: Order Cancelled")
		template_doc.subject = "Order {{ doc.name }} was cancelled ({{ store_name }})"
		template_doc.save(ignore_permissions=True)
		self.basket(1)
		name = self.place()
		frappe.db.set_value("Sales Order", name, "shop_payment_due", add_days(nowdate(), -1))
		with patch("frappe.sendmail") as sendmail:
			payment.cancel_if_unpaid(name)
		kwargs = sendmail.call_args.kwargs
		self.assertTrue(kwargs["subject"].startswith(f"Order {name} was cancelled ("))
		self.assertEqual((kwargs["reference_doctype"], kwargs["reference_name"]), ("Sales Order", name))
		emails.ensure_templates()  # an edited template is never overwritten
		self.assertEqual(
			frappe.db.get_value("Email Template", "Shop: Order Cancelled", "subject"), template_doc.subject
		)

	def test_shopper_text_is_escaped_in_emails(self):
		from shop.storefront import emails

		doc = frappe._dict(doctype="Lead", name="X", company_name="<b>Evil</b> Ltd", country="")
		_subject, body = emails.render("trade_application_declined", doc, reason="<script>x</script>")
		self.assertNotIn("<script>", body)
		self.assertNotIn("<b>Evil</b>", body)

	def test_paid_bank_order_is_not_cancelled(self):
		self.basket(1)
		name = self.place()
		self.pay(name)
		frappe.db.set_value("Sales Order", name, "shop_payment_due", add_days(nowdate(), -1))
		self.assertTrue(payment.is_paid(frappe.get_doc("Sales Order", name)))
		self.assertFalse(payment.cancel_if_unpaid(name))

	def test_paid_order_is_invoiced_when_the_store_invoices_paid_orders(self):
		self.basket(1)
		name = self.place()
		with patch("frappe.sendmail"):
			self.pay(name)
		self.assertFalse(payment.order_invoice(name))
		self.set(invoice_paid_orders=1)
		self.basket(1)
		name = self.place()
		self.assertFalse(payment.order_invoice(name))  # not before payment
		with patch("frappe.sendmail"):
			entry = self.pay(name)
		invoice = frappe.get_doc("Sales Invoice", payment.order_invoice(name))
		self.assertEqual(invoice.docstatus, 1)
		self.assertEqual(flt(invoice.outstanding_amount), 0)
		self.assertEqual(invoice.status, "Paid")
		self.assertEqual([a.reference_name for a in invoice.advances], [entry.name])

	def test_part_paid_order_is_not_invoiced(self):
		self.set(invoice_paid_orders=1)
		self.basket(1)
		name = self.place()
		with patch("frappe.sendmail"):
			self.pay(name, amount=1)
		self.assertFalse(payment.order_invoice(name))

	def test_a_free_order_is_paid_when_placed(self):
		self.set(invoice_paid_orders=1)
		self.basket(1)
		cart.apply_coupon(self.free_coupon())
		self.assertEqual(totals.compute(cart.resolve_cart(), self.home_address, None, "gateway").fees, [])
		with patch("frappe.sendmail"):
			name = self.place()
		order = frappe.get_doc("Sales Order", name)
		self.assertEqual(totals.order_total(order), 0)
		self.assertTrue(payment.is_paid(order))
		self.assertTrue(payment.order_invoice(name))

	def test_invoice_pdf_needs_the_customer_or_the_order_link(self):
		from shop.storefront import accounts

		self.set(invoice_paid_orders=1)
		self.basket(1)
		name = self.place()
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
			self.assertEqual(
				get_print.call_args.kwargs["print_format"],
				frappe.get_meta("Sales Invoice").default_print_format or None,
			)
		finally:
			frappe.set_user("Administrator")

	def test_overseas_bank_transfer_suggests_the_buyers_currency(self):
		quote = totals.compute(self.basket(1), self.abroad_address, None, "bank")
		self.assertEqual(
			quote.currency, self.foreign if self.other == "United States" else self.company_currency
		)

	def with_currency_cookie(self, currency):
		from werkzeug.test import EnvironBuilder
		from werkzeug.wrappers import Request

		builder = EnvironBuilder(path="/store/products", headers={"Cookie": f"shop_currency={currency}"})
		frappe.local.request = Request(builder.get_environ())
		self.addCleanup(
			lambda: delattr(frappe.local, "request") if hasattr(frappe.local, "request") else None
		)
		if hasattr(frappe.local, "shop_display_rates"):
			delattr(frappe.local, "shop_display_rates")

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
		quote = totals.compute(basket, self.home_address, None, "bank")
		self.assertEqual(quote.currency, self.foreign)
		online = totals.payload(totals.compute(basket, self.home_address, None, "gateway"))
		self.assertEqual(online["currency"], self.company_currency)
		self.assertTrue(any(self.foreign in note for note in online["notes"]))

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
		frappe.set_user(email)
		try:
			if hasattr(frappe.local, "shop_session_customer"):
				delattr(frappe.local, "shop_session_customer")
			frappe.local.cookie_manager = frappe.auth.CookieManager()
			pricing.set_currency(self.foreign)
			party = pricing.session_customer()
			self.assertEqual(frappe.db.get_value("Customer", party, "default_currency"), self.foreign)
			self.assertEqual(frappe.local.cookie_manager.cookies["shop_currency"]["value"], self.foreign)
			frappe.clear_cache(doctype="Customer")
			self.assertEqual(pricing.display_currency(), self.foreign)
		finally:
			frappe.set_user("Administrator")

	def test_charge_lines_are_not_products(self):
		self.assertTrue(totals.is_charge_item("TEST-CARD-FEE"))
		self.assertFalse(totals.is_charge_item(ITEM))

	def test_tax_short_names(self):
		self.assertEqual(totals.tax_name("GST @ 15.0"), "GST")
		self.assertEqual(totals.tax_name("VAT 20% (included)"), "VAT")
		self.assertEqual(totals.tax_name("Sales Tax 8.25%"), "Sales Tax")


original_hooks = frappe.get_hooks


def hooks_with(providers):
	def get_hooks(hook=None, *args, **kwargs):
		if hook == "shop_shipping_rate_providers":
			return providers
		return original_hooks(hook, *args, **kwargs)

	return get_hooks
