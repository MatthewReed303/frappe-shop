"""Storefront prices.

Guests see the store's price list. A signed-in customer sees their own prices: their price list
(Customer, else their Customer Group's default, else the store's) with ERPNext's pricing rules
applied, such as a trade discount for their group or customer and quantity breaks. When that
is lower than the store price, the store price is returned as "retail" so pages can show both.
"""

import frappe
from frappe.utils import flt, fmt_money, nowdate


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
		entry = {"rate": rate, "formatted": format_amount(rate)}
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
		}
		for row in rates
	}


def get_price(item_code: str) -> dict | None:
	return get_prices([item_code]).get(item_code)


def format_amount(amount: float) -> str:
	settings = frappe.get_cached_doc("Shop Settings")
	return fmt_money(amount, currency=settings.currency)


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
