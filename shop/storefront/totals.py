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


def card_fee_for(total: float, export: bool) -> float:
	"""The card fee for an order whose total (with tax) is already fixed, e.g. an accepted quote."""
	s = settings()
	percent, fixed = card_fee_rates(export)
	if not (percent or fixed):
		return 0.0
	tax_factor = 1 + (included_tax_rate() / 100 if s.prices_include_tax and not export else 0)
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


def bank_accounts() -> list[frappe._dict]:
	return [frappe._dict(row.as_dict()) for row in settings().get("bank_accounts") or []]


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
		currency = frappe.get_cached_value("Country", address.country, "currency") if frappe.db.has_column(
			"Country", "currency"
		) else None
		currency = currency or COUNTRY_CURRENCY.get(address.get("country_code") or "")
		if currency in options:
			return currency
		if address.get("country_code") in EURO_COUNTRIES and "EUR" in options:
			return "EUR"
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
	export = is_export(address) and bool(s.get("export_tax_template"))
	method = normalize_method(payment_method)
	base_currency = company_currency()
	if method == BANK and not currency:
		currency = preferred_bank_currency(address)
	currency = currency if method == BANK and currency in bank_currencies() else base_currency
	if method == BANK and currency not in bank_currencies() and bank_currencies():
		currency = base_currency if base_currency in bank_currencies() else bank_currencies()[0]
	rate = exchange_rate(base_currency, currency)

	cart_module.refresh_rates(cart)
	tax_rate = included_tax_rate() if s.prices_include_tax else 0.0
	tax_factor = 1 + tax_rate / 100
	# remove included tax for exports
	price_factor = 1 / tax_factor if export and tax_rate else 1.0

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
		shipping_amount = customer_amount(chosen["amount"], chosen["tax_included"], export, tax_factor, s)
		shipping_amount = convert(shipping_amount)

	goods = flt(subtotal - discount + shipping_amount, 2)
	# tax added on top of the lines (stores whose prices exclude tax)
	template = s.get("export_tax_template") if export else s.tax_template
	added = 0.0 if s.prices_include_tax else added_tax_rate(template) / 100
	taxed_fee_factor = (1 + added) if not export or added else 1.0
	if s.prices_include_tax and not export:
		taxed_fee_factor = tax_factor

	fees = []
	if method == CARD:
		percent, fixed = card_fee_rates(export)
		if percent or fixed:
			gross_goods = goods * (1 + added)
			fee_ex_tax = (percent * gross_goods + fixed * rate) / (1 - percent * taxed_fee_factor)
			fee = round_up(fee_ex_tax * (taxed_fee_factor if s.prices_include_tax and not export else 1))
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
	if s.prices_include_tax and not export and tax_rate:
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
			dict(option, customer_amount=convert(customer_amount(option["amount"], option["tax_included"], export, tax_factor, s)))
			for option in quotes["options"]
		],
		shipping_messages=quotes.get("messages") or [],
		shipping_pending=bool(quotes.get("pending")),
		fees=fees,
		tax_included=tax_included,
		tax_added=tax_added,
		tax_label=tax_label(template, export),
		total=total,
	)


def customer_amount(amount: float, tax_included: bool, export: bool, tax_factor: float, s) -> float:
	"""A shipping price as the customer sees it: with tax when prices include tax, never for exports."""
	if s.prices_include_tax and not export:
		return flt(amount if tax_included else amount * tax_factor, 2)
	return flt(amount / tax_factor if tax_included and tax_factor else amount, 2)


def tax_label(template: str | None, export: bool) -> str:
	"""Short name of the tax, from the template's first row: "GST 15% (included)" -> "GST 15%"."""
	if not template:
		return ""
	from shop.compat import get_sales_taxes

	rows = get_sales_taxes(template)
	label = (rows[0].get("description") if rows else "") or template
	return label.split("(")[0].strip() or label


def payload(quote: frappe._dict) -> dict:
	"""The quote for the browser: lines and totals already formatted."""
	currency = quote.currency
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
	if quote.tax_added:
		lines.append({"kind": "tax", "label": quote.tax_label, "amount": quote.tax_added})
	for line in lines:
		line["formatted"] = money(line["amount"], currency) if line["amount"] else _("Free")
	notes = []
	if quote.tax_included:
		notes.append(_("Includes {0}: {1}").format(quote.tax_label, money(quote.tax_included, currency)))
	elif quote.export and quote.tax_label:
		notes.append(_("No New Zealand GST: the order is shipped overseas") if company_country() == "New Zealand"
					 else _("Tax-free export"))
	if quote.payment_method == CARD:
		from shop.storefront import pricing

		shown = pricing.display_currency()
		if shown != quote.company_currency:
			notes.append(
				_("Cards are charged in {0}. Your card provider converts it to {1}, usually at a small cost. "
				  "To pay in {1}, choose bank transfer.").format(quote.company_currency, shown)
				if shown in bank_currencies()
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
				"formatted": money(option["customer_amount"], currency) if option["customer_amount"] else _("Free"),
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


# Fallback when ERPNext's Country has no currency column (it does not in v16)
COUNTRY_CURRENCY = {
	"NZ": "NZD", "AU": "AUD", "US": "USD", "GB": "GBP", "CA": "CAD", "JP": "JPY", "SG": "SGD",
	"HK": "HKD", "CH": "CHF", "SE": "SEK", "NO": "NOK", "DK": "DKK", "IN": "INR", "CN": "CNY",
}
EURO_COUNTRIES = {
	"AT", "BE", "HR", "CY", "EE", "FI", "FR", "DE", "GR", "IE", "IT", "LV", "LT", "LU", "MT", "NL",
	"PT", "SK", "SI", "ES", "BG",
}
