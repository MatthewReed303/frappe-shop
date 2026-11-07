"""Order totals at checkout: what the customer sees, and what the Sales Order is built from.

Rules:
- Prices come from the store's price list in the company currency. With "Prices include tax",
  they include the tax of the store's tax template (e.g. NZ GST).
- Orders shipped outside the company's country use the Export Tax Template; tax included in
  the prices is taken off.
- Shipping, the card fee and the currency conversion fee are order lines (items, when the
  store names an item for them), so the same tax applies to them as to the goods.
- The card fee covers the payment provider's fee on the final total, including the fee
  itself and its tax, and no more: fee = (p * goods + fixed) / (1 - p * tax factor).
- Bank transfers can be paid in another currency the store has an account in. Amounts are
  converted at ERPNext's exchange rate and that account's conversion fee is added.
"""

import math

import frappe
from frappe import _
from frappe.utils import flt, fmt_money

from shop.storefront import shipping as shipping_module

CARD = "card"
BANK = "bank"
ACCOUNT = "account"
COD = "cod"
# Shop's original name for the online gateway method
GATEWAY = "gateway"


def settings():
	return frappe.get_cached_doc("Shop Settings")


def company_currency() -> str:
	return frappe.get_cached_value("Company", settings().company, "default_currency")


def company_country() -> str:
	return frappe.get_cached_value("Company", settings().company, "country")


def included_tax_rate(template: str | None = None) -> float:
	"""Percent of tax included in the store's prices (0 when prices exclude tax)."""
	s = settings()
	template = template or s.tax_template
	if not template:
		return 0.0
	from shop.compat import get_sales_taxes

	return sum(
		flt(row.get("rate"))
		for row in get_sales_taxes(template)
		if row.get("charge_type") == "On Net Total"
		and (row.get("included_in_print_rate") or s.prices_include_tax)
	)


def added_tax_rate(template: str | None) -> float:
	if not template:
		return 0.0
	from shop.compat import get_sales_taxes

	return sum(
		flt(row.get("rate"))
		for row in get_sales_taxes(template)
		if row.get("charge_type") == "On Net Total" and not row.get("included_in_print_rate")
	)


def tax_template_for(address: frappe._dict | None) -> str | None:
	"""The sales tax template for an order going to this address: ERPNext's Tax Rules marked "Use
	for Shopping Cart", matched on shipping and billing country and the customer's group and tax
	category, as ERPNext matches them; the store's own template when no rule matches."""
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
	return get_tax_template(nowdate(), args) or s.tax_template


def ensure_tax_rules(company: str, home_template: str, export_template: str | None) -> list[str]:
	"""ERPNext Tax Rules for the store: orders shipped in the company's country use home_template,
	orders shipped anywhere else use export_template (the more specific rule wins in ERPNext)."""
	country = frappe.get_cached_value("Company", company, "country")
	rules = []
	for template, shipping_country in ((home_template, country), (export_template, None)):
		if not template:
			continue
		filters = {"tax_type": "Sales", "company": company, "use_for_shopping_cart": 1,
				   "shipping_country": shipping_country or ["is", "not set"]}
		name = frappe.db.get_value("Tax Rule", filters)
		doc = frappe.get_doc("Tax Rule", name) if name else frappe.new_doc("Tax Rule")
		doc.update({"tax_type": "Sales", "company": company, "use_for_shopping_cart": 1, "sales_tax_template": template,
					"shipping_country": shipping_country, "priority": 1})
		doc.save(ignore_permissions=True)
		rules.append(doc.name)
	return rules


def is_export(address: frappe._dict | None) -> bool:
	return bool(address and address.get("country") and address.country != company_country())


def card_method_available() -> bool:
	return bool(settings().payment_gateway_account)


def payment_methods(export: bool = False) -> list[dict]:
	s = settings()
	methods = []
	if card_method_available():
		methods.append({"method": CARD, "label": _("Card"), "fee_note": card_fee_note(export)})
	if s.get("enable_bank_transfer") and s.get("bank_accounts"):
		methods.append({"method": BANK, "label": _("Bank transfer"), "fee_note": ""})
	if s.enable_cod:
		methods.append({"method": COD, "label": _("Cash on Delivery"), "fee_note": ""})
	terms = on_account_terms()
	if terms:
		methods.insert(0, {"method": ACCOUNT, "label": _("Pay on account (invoice)"), "fee_note": terms})
	return methods


def on_account_terms() -> str | None:
	"""The signed-in customer's payment terms, when they may pay on account."""
	from shop.storefront.pricing import session_customer

	customer = session_customer()
	if not customer or not frappe.get_meta("Customer").has_field("shop_allow_on_account"):
		return None
	allowed, terms = frappe.db.get_value("Customer", customer, ["shop_allow_on_account", "payment_terms"])
	return terms if allowed and terms else None


def normalize_method(method: str | None) -> str | None:
	return CARD if method == GATEWAY else method


def card_fee_rates(export: bool) -> tuple[float, float]:
	s = settings()
	if not s.get("card_fee_item"):
		return 0.0, 0.0
	if export and (s.get("card_fee_percent_international") or s.get("card_fee_fixed_international")):
		return flt(s.card_fee_percent_international) / 100, flt(s.card_fee_fixed_international)
	return flt(s.get("card_fee_percent")) / 100, flt(s.get("card_fee_fixed"))


def card_fee_for(total: float, export: bool, template: str | None = None) -> float:
	"""The card fee for an order whose total (with tax) is already fixed, e.g. an accepted quote,
	whose taxes are `template`."""
	s = settings()
	percent, fixed = card_fee_rates(export)
	if not (percent or fixed):
		return 0.0
	tax_factor = 1 + (included_tax_rate(template) / 100 if s.prices_include_tax else 0)
	fee_ex_tax = (percent * flt(total) + fixed) / (1 - percent * tax_factor)
	return round_up(fee_ex_tax * tax_factor)


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


# how a branch code is known in the currency's home banking system
BRANCH_CODE_LABELS = {"USD": "ACH routing number", "AUD": "BSB", "GBP": "Sort code", "CAD": "Transit number"}


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
		(_(BRANCH_CODE_LABELS.get(currency, "Branch code")), account.branch_code),
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


def round_up(amount: float) -> float:
	return math.ceil(round(amount * 100, 6)) / 100


def compute(
	cart,
	address: dict | None = None,
	shipping_option: str | None = None,
	payment_method: str | None = None,
	currency: str | None = None,
	refresh_shipping: bool = False,
) -> frappe._dict:
	"""Every line and total of the order as it stands. Amounts are in the order currency."""
	from shop.storefront import cart as cart_module

	s = settings()
	address = shipping_module.normalize_address(address)
	export = is_export(address)
	# the order's taxes: ERPNext's Tax Rules for the store, as ERPNext picks them for an order
	template = tax_template_for(address)
	method = normalize_method(payment_method)
	base_currency = company_currency()
	if method == BANK and not currency:
		currency = preferred_bank_currency(address)
	currency = currency if method == BANK and currency in bank_currencies() else base_currency
	if method == BANK and currency not in bank_currencies() and bank_currencies():
		currency = base_currency if base_currency in bank_currencies() else bank_currencies()[0]
	rate = exchange_rate(base_currency, currency)

	cart_module.refresh_rates(cart)
	# prices that include tax include the store's own tax; the order's lines include its template's
	home_rate = included_tax_rate() if s.prices_include_tax else 0.0
	tax_rate = included_tax_rate(template) if s.prices_include_tax else 0.0
	tax_factor = 1 + tax_rate / 100
	price_factor = tax_factor / (1 + home_rate / 100)

	def convert(amount: float) -> float:
		return flt(amount * rate, 2)

	items = []
	for row in cart.items:
		unit = convert(flt(row.rate) * price_factor)
		items.append(
			frappe._dict(item_code=row.item_code, qty=flt(row.qty), rate=unit, amount=flt(unit * flt(row.qty), 2))
		)
	subtotal = flt(sum(item.amount for item in items), 2)

	coupon, discount_base = cart_module.applied_discount(cart, sum(flt(r.rate) * flt(r.qty) for r in cart.items))
	discount = convert(discount_base * price_factor)

	quotes = shipping_module.get_options(
		cart, address, sum(flt(r.rate) * flt(r.qty) for r in cart.items) - discount_base, refresh_shipping
	)
	chosen = shipping_module.find_option(quotes["options"], shipping_option or cart.get("shipping_rate"))
	if not chosen and quotes["options"]:
		chosen = quotes["options"][0]
	shipping_amount = 0.0
	if chosen:
		shipping_amount = customer_amount(chosen["amount"], chosen["tax_included"], tax_factor, s)
		shipping_amount = convert(shipping_amount)

	goods = flt(subtotal - discount + shipping_amount, 2)
	# tax added on top of the lines (stores whose prices exclude tax)
	added = 0.0 if s.prices_include_tax else added_tax_rate(template) / 100
	# what a fee line costs the customer with its tax
	taxed_fee_factor = tax_factor if s.prices_include_tax else 1 + added

	fees = []
	if method == CARD:
		percent, fixed = card_fee_rates(export)
		if percent or fixed:
			gross_goods = goods * (1 + added)
			fee_ex_tax = (percent * gross_goods + fixed * rate) / (1 - percent * taxed_fee_factor)
			fee = round_up(fee_ex_tax * (taxed_fee_factor if s.prices_include_tax else 1))
			fees.append(frappe._dict(kind="card_fee", label=_("Card fee"), item=s.card_fee_item, amount=fee))
	if method == BANK and currency != base_currency:
		account = bank_account(currency)
		percent = flt(account.conversion_fee_percent) / 100 if account else 0
		if percent and s.get("conversion_fee_item"):
			base = goods * (1 + added)
			fees.append(
				frappe._dict(
					kind="conversion_fee",
					label=_("Currency conversion ({0}%)").format(f"{flt(percent * 100, 2):g}"),
					item=s.conversion_fee_item,
					amount=round_up(base * percent),
				)
			)

	lines_total = flt(goods + sum(fee.amount for fee in fees), 2)
	tax_added = flt(lines_total * added, 2)
	total = flt(lines_total + tax_added, 2)
	tax_included = 0.0
	if s.prices_include_tax and tax_rate:
		tax_included = flt(total - total / tax_factor, 2)

	return frappe._dict(
		currency=currency,
		company_currency=base_currency,
		exchange_rate=rate,
		export=export,
		address=address,
		payment_method=method,
		tax_template=template,
		order_items=items,
		subtotal=subtotal,
		coupon=coupon,
		discount=discount,
		shipping=frappe._dict(chosen or {}, customer_amount=shipping_amount) if chosen else None,
		shipping_options=[
			dict(option, customer_amount=convert(customer_amount(option["amount"], option["tax_included"], tax_factor, s)))
			for option in quotes["options"]
		],
		shipping_messages=quotes.get("messages") or [],
		shipping_pending=bool(quotes.get("pending")),
		fees=fees,
		tax_included=tax_included,
		tax_added=tax_added,
		added_rate=added,
		tax_label=tax_label(template, export),
		total=total,
	)


def domestic_tax_factor(s) -> float:
	"""1 + the store's own tax rate (e.g. 1.15 for NZ GST), whether prices include it or not."""
	rate = included_tax_rate() if s.prices_include_tax else added_tax_rate(s.tax_template)
	return 1 + rate / 100


def customer_amount(amount: float, tax_included: bool, line_tax_factor: float, s) -> float:
	"""A shipping line as it goes on the order. A carrier price that includes the store's own tax
	has it taken off. When the store's prices include tax, the line then includes the order's tax
	(line_tax_factor, 1 for a tax-free order); otherwise tax is added on the order like any other line."""
	without_tax = amount / domestic_tax_factor(s) if tax_included else amount
	return flt(without_tax * line_tax_factor if s.prices_include_tax else without_tax, 2)


def tax_label(template: str | None, export: bool) -> str:
	"""Short name of the tax, from the template's first row: "GST 15% (included)" -> "GST 15%"."""
	if not template:
		return ""
	from shop.compat import get_sales_taxes

	rows = get_sales_taxes(template)
	label = (rows[0].get("description") if rows else "") or template
	return label.split("(")[0].strip() or label


def payload(quote: frappe._dict) -> dict:
	"""The quote for the browser: lines and totals already formatted.

	A home order whose tax is added on top (prices exclude tax) is shown to the buyer with tax
	included in every line, as consumer prices are; the order and invoice keep the lines without
	tax and the tax added, as a tax invoice does. Any rounding cent goes on the subtotal line, so
	the lines always add up to the total."""
	currency = quote.currency
	with_tax = bool(quote.tax_added) and not quote.export
	factor = 1 + flt(quote.get("added_rate")) if with_tax else 1.0

	def shown(amount: float) -> float:
		return flt(flt(amount) * factor, 2)

	lines = [{"kind": "subtotal", "label": _("Subtotal"), "amount": quote.subtotal}]
	if quote.discount:
		lines.append(
			{
				"kind": "discount",
				"label": _("Discount ({0})").format(quote.coupon["code"]) if quote.coupon else _("Discount"),
				"amount": -quote.discount,
			}
		)
	if quote.shipping:
		lines.append({"kind": "shipping", "label": quote.shipping["label"], "amount": quote.shipping.customer_amount})
	for fee in quote.fees:
		lines.append({"kind": fee.kind, "label": fee.label, "amount": fee.amount})
	if with_tax:
		for line in lines[1:]:
			line["amount"] = shown(line["amount"])
		lines[0]["amount"] = flt(quote.total - sum(line["amount"] for line in lines[1:]), 2)
	elif quote.tax_added:
		lines[0]["label"] = _("Subtotal excl. {0}").format(quote.tax_label.split()[0]) if quote.tax_label else _("Subtotal")
		lines.append({"kind": "tax", "label": quote.tax_label, "amount": quote.tax_added})
	for line in lines:
		line["formatted"] = money(line["amount"], currency) if line["amount"] else _("Free")
	notes = []
	if quote.tax_included or with_tax:
		notes.append(
			_("Includes {0}: {1}").format(quote.tax_label, money(quote.tax_included or quote.tax_added, currency))
		)
	elif quote.export and quote.tax_label:
		notes.append(_("No New Zealand GST: the order is shipped overseas") if company_country() == "New Zealand"
					 else _("Tax-free export"))
	if quote.payment_method == CARD:
		from shop.storefront import pricing

		picked = pricing.display_currency()
		if picked != quote.company_currency:
			notes.append(
				_("Cards are charged in {0}. Your card provider converts it to {1}, usually at a small cost. "
				  "To pay in {1}, choose bank transfer.").format(quote.company_currency, picked)
				if picked in bank_currencies()
				else _("Cards are charged in {0}. Your card provider converts it.").format(quote.company_currency)
			)
	if quote.currency != quote.company_currency:
		notes.append(
			_("Converted from {0} at {1}").format(
				quote.company_currency, f"1 {quote.company_currency} = {flt(quote.exchange_rate, 4):g} {quote.currency}"
			)
		)
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
				"formatted": money(shown(option["customer_amount"]), currency) if option["customer_amount"] else _("Free"),
				"selected": bool(quote.shipping and option["id"] == quote.shipping["id"]),
			}
			for option in quote.shipping_options
		],
		"shipping_messages": quote.shipping_messages,
		"shipping_pending": quote.shipping_pending,
		"can_ship": bool(quote.shipping),
		"payment_methods": payment_methods(quote.export),
		"payment_method": quote.payment_method,
		"bank_currencies": [
			{"currency": row.currency, "label": row.label or row.currency} for row in bank_accounts()
		],
	}


def is_charge_item(item_code: str) -> bool:
	"""Shipping and fee lines, which are not products."""
	s = settings()
	return item_code in {s.get("shipping_item"), s.get("card_fee_item"), s.get("conversion_fee_item")} - {None, ""}
