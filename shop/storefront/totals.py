"""Checkout totals, worked out by ERPNext on the Sales Order the checkout would place."""

import math

import frappe
from frappe import _
from frappe.utils import flt, fmt_money

from shop.storefront import shipping as shipping_module

GATEWAY = "gateway"
BANK = "bank"
ACCOUNT = "account"
COD = "cod"


def settings():
	return frappe.get_cached_doc("Shop Settings")


def company_currency() -> str:
	return frappe.get_cached_value("Company", settings().company, "default_currency")


def company_country() -> str:
	return frappe.get_cached_value("Company", settings().company, "country")


def tax_template_for(address: frappe._dict | None) -> str | None:
	"""The sales tax template for an order going to this address: the ERPNext Tax Rule for the
	shopping cart that matches it, else the store's Export Tax Template for orders shipped abroad,
	else the store's Tax Template."""
	from erpnext.accounts.doctype.tax_rule.tax_rule import get_tax_template
	from frappe.utils import nowdate

	from shop.storefront import pricing

	s = settings()
	country = (address or {}).get("country") or company_country()
	args = {"tax_type": "Sales", "company": s.company, "use_for_shopping_cart": 1,
			"shipping_country": country, "billing_country": country}
	customer = pricing.session_customer()
	if customer:
		group, category = frappe.get_cached_value("Customer", customer, ["customer_group", "tax_category"])
		args.update(customer_group=group, tax_category=category or "")
	rule = get_tax_template(nowdate(), args)
	if rule:
		return rule
	if is_export(address) and s.get("export_tax_template"):
		return s.export_tax_template
	return s.tax_template


def is_export(address: frappe._dict | None) -> bool:
	return bool(address and address.get("country") and address.country != company_country())


def gateway_available() -> bool:
	return bool(settings().payment_gateway_account)


def payment_methods(export: bool = False) -> list[dict]:
	s = settings()
	methods = []
	if gateway_available():
		methods.append(
			{"method": GATEWAY, "label": s.get("online_payment_label") or _("Pay Online"), "fee_note": card_fee_note(export)}
		)
	if s.get("enable_bank_transfer") and s.get("bank_accounts"):
		methods.append({"method": BANK, "label": _("Bank transfer"), "fee_note": ""})
	if s.enable_cod:
		methods.append({"method": COD, "label": _("Cash on Delivery"), "fee_note": ""})
	terms = on_account_terms()
	if terms:
		methods.insert(0, {"method": ACCOUNT, "label": _("Pay on account (invoice)"), "fee_note": terms})
	return methods


def on_account_terms(customer: str | None = None) -> str | None:
	"""The customer's payment terms, when they may pay on account (the signed-in shopper's
	customer by default)."""
	from shop.storefront.pricing import session_customer

	customer = customer or session_customer()
	if not customer or not frappe.get_meta("Customer").has_field("shop_allow_on_account"):
		return None
	allowed, terms = frappe.db.get_value("Customer", customer, ["shop_allow_on_account", "payment_terms"])
	return terms if allowed and terms else None


def card_fee_rates(export: bool) -> tuple[float, float]:
	s = settings()
	if not s.get("card_fee_item"):
		return 0.0, 0.0
	if export and (s.get("card_fee_percent_international") or s.get("card_fee_fixed_international")):
		return flt(s.card_fee_percent_international) / 100, flt(s.card_fee_fixed_international)
	return flt(s.get("card_fee_percent")) / 100, flt(s.get("card_fee_fixed"))


def card_fee_note(export: bool) -> str:
	percent, fixed = card_fee_rates(export)
	if not percent and not fixed:
		return ""
	parts = []
	if percent:
		parts.append(f"{flt(percent * 100, 2):g}%")
	if fixed:
		parts.append(fmt_money(fixed, currency=company_currency()))
	return _("Card fee {0}, added to the total").format(" + ".join(parts))


def branch_code_label(currency: str) -> str:
	"""What a bank branch code is called where the currency is used."""
	return {
		"USD": _("ACH routing number"),
		"AUD": _("BSB"),
		"GBP": _("Sort code"),
		"CAD": _("Transit number"),
	}.get(currency, _("Branch code"))


def bank_accounts() -> list[frappe._dict]:
	"""The store's bank transfer accounts: ERPNext company Bank Accounts with the currency of
	their account and the details customers pay to."""
	rows = []
	for row in settings().get("bank_accounts") or []:
		if not row.bank_account:
			continue
		account = frappe.get_cached_doc("Bank Account", row.bank_account)
		if account.disabled:
			continue
		currency = frappe.get_cached_value("Account", account.account, "account_currency") if account.account else None
		if not currency:
			continue
		rows.append(
			frappe._dict(
				bank_account=row.bank_account,
				currency=currency,
				label=row.label or currency,
				conversion_fee_percent=flt(row.conversion_fee_percent),
				details=bank_account_details(account, currency) + [
					line.strip() for line in (row.instructions or "").splitlines() if line.strip()
				],
			)
		)
	return rows


def bank_account_details(account, currency: str) -> list[str]:
	"""The lines a customer needs to pay into a Bank Account."""
	bank = frappe.get_cached_doc("Bank", account.bank) if account.bank else None
	# a company bank account is held by the company
	holder = frappe.get_cached_value("Company", account.company, "company_name") if account.company else account.account_name
	lines = [
		(_("Account name"), holder),
		(_("Bank"), bank.bank_name if bank else None),
		(branch_code_label(currency), account.branch_code),
		(_("Account number"), account.bank_account_no),
		(_("IBAN"), account.iban),
		(_("BIC/SWIFT"), bank.swift_number if bank else None),
	]
	return [f"{label}: {value}" for label, value in lines if value]


def bank_currencies() -> list[str]:
	return list(dict.fromkeys(row.currency for row in bank_accounts()))


def bank_account(currency: str) -> frappe._dict | None:
	for row in bank_accounts():
		if row.currency == currency:
			return row
	return None


def suggested_currency(address: frappe._dict | None) -> str:
	"""The customer's own currency when the store has an account in it, else the company's."""
	options = bank_currencies()
	if address and address.get("country"):
		from frappe.geo.country_info import get_country_info

		currency = (get_country_info(address.country) or {}).get("currency")
		if currency in options:
			return currency
	return company_currency()


def preferred_bank_currency(address: frappe._dict | None) -> str:
	"""The currency the shopper picked for prices, when the store takes bank transfers in it;
	otherwise the currency of where the order is going."""
	from shop.storefront import pricing

	chosen = pricing.chosen_currency()
	return chosen if chosen in bank_currencies() else suggested_currency(address)


def exchange_rate(from_currency: str, to_currency: str) -> float:
	if from_currency == to_currency:
		return 1.0
	from erpnext.setup.utils import get_exchange_rate

	rate = flt(get_exchange_rate(from_currency, to_currency))
	if not rate:
		frappe.throw(_("No exchange rate from {0} to {1} is available").format(from_currency, to_currency))
	return rate


def money(amount: float, currency: str) -> str:
	return fmt_money(amount, currency=currency)


def compute(
	cart,
	address: dict | None = None,
	shipping_option: str | None = None,
	payment_method: str | None = None,
	currency: str | None = None,
	refresh_shipping: bool = False,
) -> frappe._dict:
	"""The order as it stands, priced, taxed and totalled by ERPNext on an unsaved Sales Order, so
	the total shown is the total charged. Amounts are in the order currency."""
	from shop.storefront import cart as cart_module
	from shop.storefront import pricing

	address = shipping_module.normalize_address(address)
	export = is_export(address)
	template = tax_template_for(address)
	method = payment_method
	currency = order_currency(method, currency, address)

	cart_module.refresh_rates(cart)
	order = new_order(cart, pricing.session_customer(), currency, template)
	coupon = apply_coupon(order, cart)
	calculate(order)

	options = shipping_module.get_options(
		cart, address, flt(order.base_total) - flt(order.base_discount_amount), refresh_shipping
	)
	chosen = shipping_module.find_option(options["options"], shipping_option or cart.get("shipping_rate"))
	if not chosen and options["options"]:
		chosen = options["options"][0]
	if chosen:
		add_shipping(order, chosen)

	fees = []
	if method == GATEWAY:
		fees += add_card_fee(order, export)
	elif method == BANK and currency != order.company_currency:
		fees += add_conversion_fee(order, bank_account(currency))
	calculate(order)

	return frappe._dict(
		order=order,
		currency=currency,
		company_currency=company_currency(),
		exchange_rate=1 / flt(order.conversion_rate),
		export=export,
		address=address,
		payment_method=method,
		tax_template=template,
		coupon=coupon,
		shipping=frappe._dict(chosen) if chosen else None,
		shipping_options=[
			dict(option, customer_amount=shipping_line_amount(order, option)) for option in options["options"]
		],
		shipping_messages=options.get("messages") or [],
		shipping_pending=bool(options.get("pending")),
		fees=fees,
		total=order_total(order),
	)


def order_currency(method: str | None, currency: str | None, address: frappe._dict | None) -> str:
	"""Orders are in the company currency, except bank transfers to an account in another currency."""
	base = company_currency()
	options = bank_currencies()
	if method != BANK or not options:
		return base
	if currency in options:
		return currency
	preferred = preferred_bank_currency(address)
	if preferred in options:
		return preferred
	return base if base in options else options[0]


def new_order(cart, customer: str | None, currency: str, template: str | None):
	"""An unsaved Sales Order for the cart, priced by ERPNext as a desk order for this customer
	would be. Guests are priced as a new customer of the store's default Customer Group."""
	from frappe.utils import add_days, nowdate

	from shop.storefront import pricing

	s = settings()
	order = frappe.new_doc("Sales Order")
	order.update(
		{
			"company": s.company,
			"order_type": "Sales",
			"transaction_date": nowdate(),
			"delivery_date": add_days(nowdate(), 3),
			"customer": customer,
			"currency": currency,
			"conversion_rate": exchange_rate(currency, company_currency()),
			"selling_price_list": pricing.customer_price_list(customer) if customer else s.price_list,
		}
	)
	if not customer:
		order.customer_group = frappe.db.get_single_value("Selling Settings", "customer_group")
	for row in cart.items:
		order.append(
			"items",
			{"item_code": row.item_code, "qty": flt(row.qty), "warehouse": s.default_warehouse, "delivery_date": order.delivery_date},
		)
	if template:
		from shop.compat import get_sales_taxes

		order.taxes_and_charges = template
		for tax in get_sales_taxes(template):
			order.append("taxes", tax)
	order.set_missing_values(for_validate=True)
	if not order.ignore_pricing_rule:
		from erpnext.accounts.doctype.pricing_rule.utils import apply_pricing_rule_on_transaction

		apply_pricing_rule_on_transaction(order)
	remove_included_tax(order, template)
	return order


def remove_included_tax(order, template: str | None) -> None:
	"""Prices that include the store's own tax charge an order with other taxes (e.g. an export)
	without it: the store's tax is taken off each price and the order's own included tax put on."""
	s = settings()
	if not s.prices_include_tax or template == s.tax_template:
		return
	factor = (1 + included_rate(template) / 100) / (1 + included_rate(s.tax_template) / 100)
	if factor == 1:
		return
	for item in order.items:
		for field in ("price_list_rate", "rate", "discount_amount"):
			item.set(field, flt(flt(item.get(field)) * factor, item.precision(field)))


def included_rate(template: str | None) -> float:
	if not template:
		return 0.0
	from shop.compat import get_sales_taxes

	return sum(
		flt(row.get("rate"))
		for row in get_sales_taxes(template)
		if row.get("charge_type") == "On Net Total" and row.get("included_in_print_rate")
	)


def apply_coupon(order, cart) -> dict | None:
	"""The cart's coupon as a discount on the order: on the net total when tax is added to prices,
	on the grand total when prices include it, so the shopper saves the coupon's amount."""
	from shop.storefront import cart as cart_module

	calculate(order)
	coupon, discount = cart_module.applied_discount(cart, flt(order.base_total))
	if not discount:
		return None
	order.apply_discount_on = "Grand Total" if settings().prices_include_tax else "Net Total"
	order.discount_amount = flt(discount / flt(order.conversion_rate), order.precision("discount_amount"))
	return coupon


def calculate(order) -> None:
	order.calculate_taxes_and_totals()


def order_total(order) -> float:
	"""What the customer pays: the rounded total, unless rounding is turned off."""
	return flt(order.rounded_total) or flt(order.grand_total)


def shipping_line_amount(order, option: dict) -> float:
	"""A shipping option's price as an order line, in the order currency. A price that includes the
	store's own tax has it taken off when the order's lines don't include tax."""
	s = settings()
	amount = flt(option["amount"])
	home = 1 + included_rate(s.tax_template) / 100 if s.prices_include_tax else 1 + added_rate(s.tax_template) / 100
	if option.get("tax_included") and not s.prices_include_tax:
		amount /= home
	elif s.prices_include_tax:
		order_factor = 1 + included_rate(order.taxes_and_charges) / 100
		amount = amount / (home if option.get("tax_included") else 1) * order_factor
	return flt(amount / flt(order.conversion_rate), order.precision("grand_total"))


def added_rate(template: str | None) -> float:
	if not template:
		return 0.0
	from shop.compat import get_sales_taxes

	return sum(
		flt(row.get("rate"))
		for row in get_sales_taxes(template)
		if row.get("charge_type") == "On Net Total" and not row.get("included_in_print_rate")
	)


def add_shipping(order, option: dict) -> None:
	"""Shipping as the store's shipping item, or as a charge on its shipping account."""
	s = settings()
	amount = shipping_line_amount(order, option)
	if not amount:
		return
	label = option.get("label") or _("Shipping")
	if s.get("shipping_item"):
		add_line(order, s.shipping_item, amount, label)
	elif s.get("shipping_account"):
		order.append(
			"taxes",
			{"charge_type": "Actual", "account_head": s.shipping_account, "description": label, "tax_amount": amount},
		)


def add_line(order, item_code: str, rate: float, description: str):
	row = order.append(
		"items",
		{"item_code": item_code, "qty": 1, "rate": rate, "description": description, "delivery_date": order.delivery_date},
	)
	order.set_missing_item_details(for_validate=True)
	row.rate = rate
	return row


def add_card_fee(order, export: bool) -> list[frappe._dict]:
	"""The card fee as a line on the order. What the store keeps of it, after any tax on the fee,
	pays the provider's charge on the final total: percent of the total plus the fixed amount."""
	s = settings()
	percent, fixed = card_fee_rates(export)
	calculate(order)
	if not (percent or fixed) or order_total(order) <= 0:
		return []
	before = flt(order.grand_total)
	row, gross, net = probe_line(order, s.card_fee_item, _("Card fee"))
	kept = (percent * before + fixed / flt(order.conversion_rate)) / (1 - percent * gross / net)
	row.rate = round_up(kept / net, row.precision("rate"))
	return [frappe._dict(kind="card_fee", label=_("Card fee"), item=s.card_fee_item)]


def add_conversion_fee(order, account: frappe._dict | None) -> list[frappe._dict]:
	"""The bank account's currency conversion fee: the store keeps its percent of the order total."""
	s = settings()
	percent = flt(account.conversion_fee_percent) / 100 if account else 0
	calculate(order)
	if not percent or not s.get("conversion_fee_item") or order_total(order) <= 0:
		return []
	before = flt(order.grand_total)
	label = _("Currency conversion ({0}%)").format(f"{flt(percent * 100, 2):g}")
	row, _gross, net = probe_line(order, s.conversion_fee_item, label)
	row.rate = round_up(percent * before / net, row.precision("rate"))
	return [frappe._dict(kind="conversion_fee", label=label, item=s.conversion_fee_item)]


def probe_line(order, item_code: str, label: str):
	"""Add a fee line and measure, per unit of its rate, what ERPNext adds to the grand total and
	what the store keeps (the net amount, after any tax the item carries)."""
	gross_before, net_before = flt(order.grand_total), flt(order.net_total)
	row = add_line(order, item_code, 100, label)
	calculate(order)
	gross = (flt(order.grand_total) - gross_before) / 100 or 1
	net = (flt(order.net_total) - net_before) / 100 or 1
	return row, gross, net


def round_up(amount: float, precision: int = 2) -> float:
	factor = 10**precision
	return math.ceil(round(amount * factor, 6)) / factor


def tax_name(description: str | None) -> str:
	"""A tax's short name from its description: "GST @ 15.0" -> "GST", "VAT 20% (included)" -> "VAT"."""
	import re

	name = re.split(r"[@(]", description or "")[0]
	return re.sub(r"\s*\d+(\.\d+)?\s*%?\s*$", "", name).strip()


def summary_lines(order) -> tuple[list[dict], list[str]]:
	"""An order's lines as a receipt shows them: subtotal, discount, shipping, fees, taxes added on
	top, rounding, plus notes for taxes included in the prices. Amounts are in the order currency."""
	product_lines = [item for item in order.items if not is_charge_item(item.item_code)]
	lines = [{"kind": "subtotal", "label": _("Subtotal"), "amount": sum(flt(item.amount) for item in product_lines)}]
	if flt(order.discount_amount):
		lines.append({"kind": "discount", "label": _("Discount"), "amount": -flt(order.discount_amount)})
	s = settings()
	kinds = {s.get("shipping_item"): "shipping", s.get("card_fee_item"): "card_fee", s.get("conversion_fee_item"): "conversion_fee"}
	for item in order.items:
		if is_charge_item(item.item_code):
			lines.append({"kind": kinds.get(item.item_code, "charge"), "label": item.description or item.item_name, "amount": flt(item.amount)})
	notes = []
	for tax in order.taxes:
		amount = flt(tax.tax_amount_after_discount_amount)
		if not amount:
			continue
		if tax.charge_type == "Actual":
			lines.append({"kind": "shipping", "label": tax.description, "amount": amount})
		elif tax.included_in_print_rate:
			notes.append(_("Includes {0}: {1}").format(tax_name(tax.description) or tax.description, money(amount, order.currency)))
		else:
			lines.append({"kind": "tax", "label": tax.description, "amount": amount})
	if flt(order.rounding_adjustment) and flt(order.rounded_total):
		lines.append({"kind": "rounding", "label": _("Rounding"), "amount": flt(order.rounding_adjustment)})
	return lines, notes


def shown_with_tax(lines: list[dict], order) -> list[dict]:
	"""Lines with the tax added on top spread into them, for stores that show prices with tax; the
	subtotal takes any rounding difference so the lines still add up to the total."""
	added = sum(line["amount"] for line in lines if line["kind"] == "tax")
	net = sum(line["amount"] for line in lines if line["kind"] not in ("tax", "rounding"))
	if not added or not net:
		return lines
	factor = 1 + added / net
	shown = [dict(line) for line in lines if line["kind"] != "tax"]
	for line in shown[1:]:
		if line["kind"] != "rounding":
			line["amount"] = flt(line["amount"] * factor, order.precision("grand_total"))
	shown[0]["amount"] = flt(order_total(order) - sum(line["amount"] for line in shown[1:]), order.precision("grand_total"))
	return shown


def payload(quote: frappe._dict) -> dict:
	"""The quote for the browser: lines and totals already formatted."""
	from shop.storefront import pricing

	order = quote.order
	currency = quote.currency
	lines, notes = summary_lines(order)
	if quote.coupon:
		for line in lines:
			if line["kind"] == "discount":
				line["label"] = _("Discount ({0})").format(quote.coupon["code"])
	taxes_added = [line for line in lines if line["kind"] == "tax"]
	with_tax = bool(settings().get("show_prices_with_tax") and taxes_added and not quote.export)
	if with_tax:
		lines = shown_with_tax(lines, order)
		notes.insert(
			0,
			_("Includes {0}: {1}").format(
				", ".join(tax_name(line["label"]) or line["label"] for line in taxes_added),
				money(sum(line["amount"] for line in taxes_added), currency),
			),
		)
	elif quote.export and order.taxes_and_charges != settings().tax_template:
		notes.append(_("Tax-free export"))
	for line in lines:
		line["formatted"] = money(line["amount"], currency) if line["amount"] else _("Free")
	if quote.payment_method == GATEWAY:
		picked = pricing.display_currency()
		if picked != quote.company_currency:
			notes.append(
				_("Online payments are charged in {0}. Your card provider converts it to {1}, usually at a small cost. "
				  "To pay in {1}, choose bank transfer.").format(quote.company_currency, picked)
				if picked in bank_currencies()
				else _("Online payments are charged in {0}. Your card provider converts it.").format(quote.company_currency)
			)
	if quote.currency != quote.company_currency:
		notes.append(
			_("Converted from {0} at {1}").format(
				quote.company_currency, f"1 {quote.company_currency} = {flt(quote.exchange_rate, 4):g} {quote.currency}"
			)
		)

	def option_amount(option):
		amount = option["customer_amount"]
		if with_tax and amount:
			amount = flt(amount * (1 + added_rate(order.taxes_and_charges) / 100), order.precision("grand_total"))
		return money(amount, currency) if amount else _("Free")

	return {
		"currency": currency,
		"export": quote.export,
		"lines": lines,
		"notes": notes,
		"total": quote.total,
		"formatted_total": money(quote.total, currency),
		"shipping_options": [
			{
				"id": option["id"],
				"label": option["label"],
				"eta": option.get("eta"),
				"formatted": option_amount(option),
				"selected": bool(quote.shipping and option["id"] == quote.shipping["id"]),
			}
			for option in quote.shipping_options
		],
		"shipping_messages": quote.shipping_messages,
		"shipping_pending": quote.shipping_pending,
		"can_ship": bool(quote.shipping),
		"payment_methods": payment_methods(quote.export),
		"payment_method": quote.payment_method,
		"bank_currencies": [{"currency": row.currency, "label": row.label or row.currency} for row in bank_accounts()],
	}


def is_charge_item(item_code: str) -> bool:
	"""Shipping and fee lines, which are not products."""
	s = settings()
	return item_code in {s.get("shipping_item"), s.get("card_fee_item"), s.get("conversion_fee_item")} - {None, ""}
