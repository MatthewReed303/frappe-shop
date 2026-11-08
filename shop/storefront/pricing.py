"""Storefront prices: the store's price list for guests; for a signed-in customer their own price
list with ERPNext's pricing rules applied. format_amount is the store-currency amount for staff
screens, display_amount what shoppers see (optionally with tax, or in another currency)."""

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import flt, fmt_money, getdate, nowdate

CURRENCY_COOKIE = "shop_currency"


def get_prices(item_codes: list[str], qtys: dict | None = None, customer: str | None = "session") -> dict:
	if not item_codes:
		return {}
	retail = store_prices(item_codes)
	if customer == "session":
		customer = session_customer()
	if not customer:
		return guest_prices(retail, qtys) if qtys else retail
	price_list = customer_price_list(customer)
	prices = {}
	for code in item_codes:
		qty = flt((qtys or {}).get(code)) or 1
		rate = customer_rate(code, customer, price_list, qty)
		if rate is None:
			# a customer's own price list is the one their orders use: what isn't on it isn't for sale to them
			if code in retail and price_list == frappe.get_cached_doc("Shop Settings").price_list:
				prices[code] = retail[code]
			continue
		entry = {"rate": rate, "formatted": format_amount(rate), **display_fields(rate, code)}
		base = retail.get(code, {}).get("rate")
		if base and rate < flt(base) - 0.004:
			entry.update(retail=base, formatted_retail=format_amount(base))
		prices[code] = entry
	return prices


def store_prices(item_codes: list[str]) -> dict:
	"""The store's price list rates that apply to anyone today (not a customer's own price)."""
	settings = frappe.get_cached_doc("Shop Settings")
	today = getdate()
	rates = {}
	for row in frappe.get_all(
		"Item Price",
		filters={
			"item_code": ["in", item_codes],
			"price_list": settings.price_list,
			"customer": ["is", "not set"],
		},
		fields=["item_code", "price_list_rate", "valid_from", "valid_upto"],
		order_by="valid_from desc",
	):
		if row.item_code in rates:
			continue
		if (row.valid_from and getdate(row.valid_from) > today) or (
			row.valid_upto and getdate(row.valid_upto) < today
		):
			continue
		rates[row.item_code] = row.price_list_rate
	return {
		code: {"rate": rate, "formatted": format_amount(rate), **display_fields(rate, code)}
		for code, rate in rates.items()
	}


def display_fields(rate: float, item_code: str | None = None) -> dict:
	tax_rate = item_tax_rate(item_code)
	return {
		"display": display_price(rate, tax_rate),
		"display_excl": display_price_without_tax(rate, tax_rate),
	}


def get_price(item_code: str) -> dict | None:
	return get_prices([item_code]).get(item_code)


def format_amount(amount: float) -> str:
	settings = frappe.get_cached_doc("Shop Settings")
	return fmt_money(amount, currency=settings.currency)


def document_amount(amount: float, currency: str) -> str:
	"""An amount in a document's own currency, with the code when that isn't the store's."""
	formatted = fmt_money(amount, currency=currency)
	return formatted if currency == store_currency() else f"{formatted} {currency}"


def store_amount(amount: float) -> str:
	"""An exact store-currency amount, with the currency code when the store shows several."""
	return with_code(format_amount(amount), store_currency())


def with_code(formatted: str, currency: str) -> str:
	return f"{formatted} {currency}" if len(display_currencies()) > 1 else formatted


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


def domestic_taxes() -> list:
	"""The rows of the store's own tax template, read once per request.

	Read directly: ERPNext's template lookup is for staff, and these pages are public."""
	cache = request_cache("shop_domestic_taxes")
	template = frappe.get_cached_doc("Shop Settings").tax_template or ""
	if template not in cache:
		cache[template] = (
			frappe.get_all(
				"Sales Taxes and Charges",
				filters={
					"parent": template,
					"parenttype": "Sales Taxes and Charges Template",
					"charge_type": "On Net Total",
				},
				fields=["account_head", "rate", "description"],
				order_by="idx",
			)
			if template
			else []
		)
	return cache[template]


def domestic_tax_rate() -> float:
	"""Percent of the store's own tax, whether its prices include it or not."""
	return sum(flt(row.rate) for row in domestic_taxes())


def item_tax_rate(item_code: str | None) -> float:
	"""Percent of the store's own tax on an item: the Item Tax Template ERPNext picks for it (from the
	item or its Item Group) changes the rate of the taxes it names."""
	from erpnext.stock.get_item_details import NOT_APPLICABLE_TAX, get_item_tax_map, get_item_tax_template

	if not item_code:
		return domestic_tax_rate()
	settings = frappe.get_cached_doc("Shop Settings")
	cache = request_cache("shop_item_tax_rates")
	key = (settings.tax_template, item_code)
	if key not in cache:
		taxes = domestic_taxes()
		ctx = {
			"company": settings.company,
			"transaction_date": nowdate(),
			"tax_category": settings.tax_template
			and frappe.get_cached_value(
				"Sales Taxes and Charges Template", settings.tax_template, "tax_category"
			),
		}
		template = get_item_tax_template(ctx, frappe.get_cached_doc("Item", item_code))
		rates = get_item_tax_map(
			doc={"company": settings.company, "taxes": [dict(row) for row in taxes]},
			tax_template=template,
			as_json=False,
		)
		cache[key] = sum(
			flt(rates.get(row.account_head))
			for row in taxes
			if rates.get(row.account_head) != NOT_APPLICABLE_TAX
		)
	return cache[key]


def tax_name() -> str:
	"""The short name of the store's tax, from its tax template."""
	from shop.storefront.totals import tax_name as short_name

	rows = domestic_taxes()
	return (short_name(rows[0].description) if rows else "") or _("tax")


def shows_tax() -> bool:
	"""Whether prices are shown with the store's tax, the price without tax under them."""
	return bool(frappe.get_cached_doc("Shop Settings").get("show_prices_with_tax") and domestic_tax_rate())


def with_and_without_tax(amount: float, tax_rate: float | None = None) -> tuple[float, float]:
	"""A price-list amount as (including tax, excluding tax) at the store's own tax rate, or at the
	item's (item_tax_rate)."""
	factor = 1 + (domestic_tax_rate() if tax_rate is None else tax_rate) / 100
	if frappe.get_cached_doc("Shop Settings").prices_include_tax:
		return flt(amount), flt(amount) / factor
	return flt(amount) * factor, flt(amount)


def shown_amount(amount: float, tax_rate: float | None = None) -> float:
	"""A price-list amount as the store shows it in its own currency: with tax when it shows prices
	with tax."""
	return with_and_without_tax(amount, tax_rate)[0] if shows_tax() else flt(amount)


def shows_store_currency() -> bool:
	currency = display_currency()
	return currency == store_currency() or not display_rate(currency)


def display_amount(amount: float, tax_rate: float | None = None) -> str:
	"""A price-list amount as the shopper sees it: in the store currency as the price list has it,
	or with tax when the store shows prices with tax. In another currency the buyer pays no local
	tax: the price without tax, converted at ERPNext's rate and marked approximate."""
	including, excluding = with_and_without_tax(amount, tax_rate)
	if shows_store_currency():
		shown = including if shows_tax() else flt(amount)
		return with_code(fmt_money(flt(shown, 2), currency=store_currency()), store_currency())
	currency = display_currency()
	return f"≈ {fmt_money(excluding * display_rate(currency), currency=currency)} {currency}"


def display_price(amount: float, tax_rate: float | None = None) -> str:
	"""A product's headline price: display_amount, saying it includes tax when it does."""
	shown = display_amount(amount, tax_rate)
	if shows_store_currency() and shows_tax() and tax_rate != 0:
		return _("{0} incl. {1}").format(shown, tax_name())
	return shown


def display_price_without_tax(amount: float, tax_rate: float | None = None) -> str | None:
	"""The line under a headline price shown with tax: the price without it."""
	if not shows_store_currency() or not shows_tax() or tax_rate == 0:
		return None
	excluding = with_and_without_tax(amount, tax_rate)[1]
	return _("{0} excl. {1}").format(
		with_code(fmt_money(flt(excluding, 2), currency=store_currency()), store_currency()), tax_name()
	)


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=30, seconds=60)
def set_currency(currency: str) -> str:
	"""The shopper picks the currency prices are shown in. A signed-in customer keeps it as their
	Billing Currency where ERPNext allows it for their account."""
	if currency not in display_currencies():
		frappe.throw(_("Prices can't be shown in {0}").format(currency))
	frappe.local.cookie_manager.set_cookie(
		CURRENCY_COOKIE, currency, max_age=60 * 60 * 24 * 365, samesite="Lax"
	)
	customer = session_customer()
	if customer and frappe.db.get_value("Customer", customer, "default_currency") != currency:
		save_billing_currency(customer, currency)
	return currency


def save_billing_currency(customer: str, currency: str) -> None:
	from shop.storefront.checkout import elevated

	frappe.db.savepoint("shop_billing_currency")
	try:
		with elevated():
			doc = frappe.get_doc("Customer", customer)
			doc.default_currency = currency
			doc.save(ignore_permissions=True)
	except frappe.ValidationError:
		# ERPNext only allows a Billing Currency the customer's accounts are kept in
		frappe.db.rollback(save_point="shop_billing_currency")
		frappe.clear_messages()


def currency_picker_html() -> str:
	"""A <select> of the currencies prices can be shown in; storefront.js saves the choice."""
	options = display_currencies()
	if len(options) < 2:
		return ""
	current = display_currency()
	esc = frappe.utils.escape_html
	items = "".join(
		f'<option value="{esc(code)}"{" selected" if code == current else ""}>{esc(code)}</option>'
		for code in options
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


def guest_prices(retail: dict, qtys: dict) -> dict:
	"""A guest's cart prices: the store's prices with the Pricing Rules for the default Customer Group
	(quantity breaks), as checkout prices the order."""
	group = frappe.db.get_single_value("Selling Settings", "customer_group")
	price_list = frappe.get_cached_doc("Shop Settings").price_list
	prices = {}
	for code, entry in retail.items():
		rate = customer_rate(code, None, price_list, flt(qtys.get(code)) or 1, customer_group=group)
		if rate is None or rate >= flt(entry["rate"]) - 0.004:
			prices[code] = entry
			continue
		prices[code] = {
			"rate": rate,
			"formatted": format_amount(rate),
			**display_fields(rate, code),
			"retail": entry["rate"],
			"formatted_retail": format_amount(entry["rate"]),
		}
	return prices


def customer_rate(
	item_code: str, customer: str | None, price_list: str, qty: float, customer_group: str | None = None
) -> float | None:
	"""The customer's (or, without one, the Customer Group's) net price for one unit at this
	quantity, after pricing rules."""
	cache = request_cache("shop_customer_rates")
	key = (item_code, customer, customer_group, price_list, qty)
	if key in cache:
		return cache[key]
	from erpnext.stock.get_item_details import get_item_details

	from shop.storefront.checkout import elevated

	settings = frappe.get_cached_doc("Shop Settings")
	args = frappe._dict(
		item_code=item_code,
		customer=customer,
		customer_group=customer_group,
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
