"""Storefront prices.

Guests see the store's price list. A signed-in customer sees their own prices: their price list
(Customer, else their Customer Group's default, else the store's) with ERPNext's pricing rules
applied, such as a trade discount for their group or customer and quantity breaks. When that
is lower than the store price, the store price is returned as "retail" so pages can show both.

Prices are kept and charged in the store currency. A shopper can pick another currency the
store can be paid in (the currencies of its bank accounts) to see approximate prices in it, at
ERPNext's exchange rate. Signed-in customers keep the choice as their Billing Currency.
format_amount is the store-currency amount for staff screens; display_amount is what shoppers see.
"""

import frappe
from frappe import _
from frappe.utils import flt, fmt_money, nowdate

CURRENCY_COOKIE = "shop_currency"


def get_prices(item_codes: list[str], qtys: dict | None = None, customer: str | None = "session") -> dict:
	if not item_codes:
		return {}
	retail = store_prices(item_codes)
	if customer == "session":
		customer = session_customer()
	if not customer:
		return retail
	price_list = customer_price_list(customer)
	prices = {}
	for code in item_codes:
		qty = flt((qtys or {}).get(code)) or 1
		rate = customer_rate(code, customer, price_list, qty)
		if rate is None:
			if code in retail:
				prices[code] = retail[code]
			continue
		entry = {"rate": rate, "formatted": format_amount(rate), "display": display_amount(rate)}
		base = retail.get(code, {}).get("rate")
		if base and rate < flt(base) - 0.004:
			entry.update(retail=base, formatted_retail=format_amount(base))
		prices[code] = entry
	return prices


def store_prices(item_codes: list[str]) -> dict:
	settings = frappe.get_cached_doc("Shop Settings")
	rates = frappe.get_all(
		"Item Price",
		filters={"item_code": ["in", item_codes], "price_list": settings.price_list},
		fields=["item_code", "price_list_rate"],
	)
	return {
		row.item_code: {
			"rate": row.price_list_rate,
			"formatted": format_amount(row.price_list_rate),
			"display": display_amount(row.price_list_rate),
		}
		for row in rates
	}


def get_price(item_code: str) -> dict | None:
	return get_prices([item_code]).get(item_code)


def format_amount(amount: float) -> str:
	settings = frappe.get_cached_doc("Shop Settings")
	return fmt_money(amount, currency=settings.currency)


def store_amount(amount: float) -> str:
	"""An exact store-currency amount with its code, e.g. "$ 189.00 NZD"."""
	return f"{format_amount(amount)} {store_currency()}"


def store_currency() -> str:
	return frappe.get_cached_doc("Shop Settings").currency


def display_currencies() -> list[str]:
	"""The store currency, then the other currencies the store can be paid in."""
	from shop.storefront import totals

	return list(dict.fromkeys([store_currency(), *totals.bank_currencies()]))


def chosen_currency() -> str | None:
	"""The currency the shopper picked, else their Billing Currency, when the store offers it."""
	options = display_currencies()
	request = getattr(frappe.local, "request", None)
	picked = request.cookies.get(CURRENCY_COOKIE) if request is not None else None
	if picked in options:
		return picked
	customer = session_customer()
	if customer:
		billing = frappe.get_cached_value("Customer", customer, "default_currency")
		if billing in options:
			return billing
	return None


def display_currency() -> str:
	return chosen_currency() or store_currency()


def display_rate(currency: str) -> float | None:
	"""Store currency to this currency at ERPNext's rate, once per request; None when unknown."""
	cache = request_cache("shop_display_rates")
	if currency not in cache:
		from shop.storefront import totals
		from shop.storefront.checkout import elevated

		try:
			# ERPNext lets only staff fetch a day's rate from its provider (it then caches it).
			# Checkout does the same; at most the store's few bank currencies are fetched, once a day.
			with elevated():
				cache[currency] = totals.exchange_rate(store_currency(), currency)
		except Exception:
			frappe.clear_messages()
			cache[currency] = None
	return cache[currency]


def display_amount(amount: float) -> str:
	"""A store price as the shopper sees it: in the store currency with its code, or converted
	into the currency they picked and marked as approximate."""
	base, currency = store_currency(), display_currency()
	rate = display_rate(currency) if currency != base else None
	if not rate:
		return f"{fmt_money(amount, currency=base)} {base}"
	return f"≈ {fmt_money(flt(amount) * rate, currency=currency)} {currency}"


def price_note() -> str:
	"""The line under a product's price."""
	base, currency = store_currency(), display_currency()
	if currency == base or not display_rate(currency):
		return _("Includes GST. Overseas orders are GST-free.")
	return _(
		"Approximate, including NZ GST, which comes off for overseas delivery. Cards are charged in {0}; "
		"bank transfers can be paid in {1}. The exact total is shown at checkout."
	).format(base, currency)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def set_currency(currency: str) -> str:
	"""The shopper picks the currency prices are shown in. Signed-in customers keep it as
	their Billing Currency."""
	if currency not in display_currencies():
		frappe.throw(_("Prices can't be shown in {0}").format(currency))
	frappe.local.cookie_manager.set_cookie(CURRENCY_COOKIE, currency, max_age=60 * 60 * 24 * 365, samesite="Lax")
	customer = session_customer()
	if customer and frappe.db.get_value("Customer", customer, "default_currency") != currency:
		frappe.db.set_value("Customer", customer, "default_currency", currency)
	return currency


def currency_picker_html() -> str:
	"""A <select> of the currencies prices can be shown in; storefront.js saves the choice."""
	options = display_currencies()
	if len(options) < 2:
		return ""
	current = display_currency()
	esc = frappe.utils.escape_html
	items = "".join(
		f'<option value="{esc(code)}"{" selected" if code == current else ""}>{esc(code)}</option>' for code in options
	)
	return (
		f'<label class="currency-picker"><span class="visually-hidden">{esc(_("Currency"))}</span>'
		f'<select data-shop="currency-picker" aria-label="{esc(_("Show prices in"))}">{items}</select></label>'
	)


def session_customer() -> str | None:
	"""The customer of the signed-in shopper (the first, when a login is linked to several)."""
	user = shopper()
	if user in ("Guest", None, "Administrator"):
		return None
	cache = request_cache("shop_session_customer")
	if user not in cache:
		from shop.storefront.orders import session_customers

		customers = session_customers(user)
		cache[user] = customers[0] if customers else None
	return cache[user]


def shopper() -> str | None:
	"""The signed-in shopper, also while checkout runs with raised permissions."""
	return getattr(frappe.local, "shop_shopper", None) or frappe.session.user


def customer_price_list(customer: str) -> str:
	settings = frappe.get_cached_doc("Shop Settings")
	own, group = frappe.get_cached_value("Customer", customer, ["default_price_list", "customer_group"])
	price_list = own or (group and frappe.get_cached_value("Customer Group", group, "default_price_list"))
	if price_list and frappe.get_cached_value("Price List", price_list, "currency") == settings.currency:
		return price_list
	return settings.price_list


def customer_rate(item_code: str, customer: str, price_list: str, qty: float) -> float | None:
	"""The customer's net price for one unit at this quantity, after pricing rules."""
	cache = request_cache("shop_customer_rates")
	key = (item_code, customer, price_list, qty)
	if key in cache:
		return cache[key]
	from erpnext.stock.get_item_details import get_item_details

	from shop.storefront.checkout import elevated

	settings = frappe.get_cached_doc("Shop Settings")
	args = frappe._dict(
		item_code=item_code,
		customer=customer,
		company=settings.company,
		currency=settings.currency,
		price_list=price_list,
		price_list_currency=settings.currency,
		plc_conversion_rate=1,
		conversion_rate=1,
		doctype="Sales Order",
		transaction_date=nowdate(),
		qty=qty,
		stock_qty=qty,
		conversion_factor=1,
		transaction_type="selling",
	)
	try:
		with elevated():
			details = get_item_details(args)
	except Exception:
		frappe.clear_messages()
		frappe.log_error(title=f"Customer price failed for {item_code}")
		cache[key] = None
		return None
	list_rate = flt(details.get("price_list_rate"))
	if not list_rate:
		cache[key] = None
		return None
	if flt(details.get("discount_percentage")):
		rate = list_rate * (1 - flt(details.discount_percentage) / 100)
	else:
		rate = list_rate - flt(details.get("discount_amount"))
	cache[key] = flt(rate, 2)
	return cache[key]


def request_cache(name: str) -> dict:
	"""A dict that lives for the current request only."""
	cache = getattr(frappe.local, name, None)
	if cache is None:
		cache = {}
		setattr(frappe.local, name, cache)
	return cache
