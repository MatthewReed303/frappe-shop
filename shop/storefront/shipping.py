"""Shipping options at checkout: the store's flat rate and the rates of the shipping apps listed
in the `shop_shipping_rate_providers` hook (see docs/extending.md). Amounts are in the company
currency, and the option charged is the one the server quoted, never one sent by the browser."""

import hashlib
import json

import frappe
from frappe import _
from frappe.utils import cint, flt, fmt_money, now_datetime, time_diff_in_seconds

FLAT_RATE_ID = "flat"
QUOTE_TTL_SECONDS = 30 * 60
ADDRESS_KEYS = ("address_line1", "address_line2", "city", "state", "pincode", "country")


def normalize_address(address: dict | None) -> frappe._dict:
	address = frappe._dict(address or {})
	clean = frappe._dict({key: (address.get(key) or "").strip() for key in ADDRESS_KEYS})
	if clean.country:
		clean.country = valid_country(clean.country)
	clean.country_code = (
		(frappe.db.get_value("Country", clean.country, "code") or "").upper() if clean.country else ""
	)
	return clean


def valid_country(country: str) -> str:
	if frappe.db.exists("Country", country):
		return country
	by_code = frappe.db.get_value("Country", {"code": country.lower()})
	if by_code:
		return by_code
	frappe.throw(_("{0} is not a country we know").format(frappe.bold(country)))


def is_complete(address: frappe._dict) -> bool:
	return bool(address.address_line1 and address.city and address.country)


def quote_key(cart, address: frappe._dict) -> str:
	items = sorted((row.item_code, flt(row.qty)) for row in cart.items)
	raw = json.dumps([items, [address.get(key) for key in ADDRESS_KEYS]], sort_keys=True)
	return hashlib.sha256(raw.encode()).hexdigest()[:20]


def get_options(cart, address: frappe._dict, subtotal: float, refresh: bool = False) -> dict:
	"""Shipping options for this cart and address: {"options": [...], "messages": [...]}.

	Rates from shipping providers are cached on the cart for half an hour, keyed by the items and
	address; an answer without rates (a provider busy or down) is asked again next time. The store's
	flat rate depends on the order value, so it is worked out every time."""
	if not is_complete(address):
		return {"options": [], "messages": [], "pending": True}
	if not ships(cart):
		return {
			"options": [
				{"id": FLAT_RATE_ID, "label": _("No delivery needed"), "amount": 0.0, "tax_included": True}
			],
			"messages": [],
		}
	key = quote_key(cart, address)
	cached = cached_quotes(cart)
	if not refresh and cached.get("key") == key and fresh(cached):
		options, messages, active = cached["options"], cached.get("messages", []), cached.get("active", False)
	else:
		options, messages, active = provider_options(cart, address, subtotal)
		if options:
			store_quotes(
				cart,
				{
					"key": key,
					"created": str(now_datetime()),
					"options": options,
					"messages": messages,
					"active": active,
				},
			)
	options = list(options)
	flat = flat_rate_option(subtotal)
	if flat:
		options.append(flat)
	if not options and not active and not messages:
		# a store with no shipping set up ships for free
		options.append({"id": FLAT_RATE_ID, "label": _("Free shipping"), "amount": 0.0, "tax_included": True})
	options.sort(key=lambda option: option["amount"])
	return {"options": options, "messages": messages}


def ships(cart) -> bool:
	"""Whether anything in the cart is sent: services aren't (a Product Bundle is sent when any of
	its parts is)."""
	from shop.storefront.stock import is_stock_item

	return any(is_stock_item(row.item_code) for row in cart.items)


def provider_options(cart, address: frappe._dict, subtotal: float) -> tuple[list[dict], list[str], bool]:
	"""Rates from the shop_shipping_rate_providers hooks, the messages they gave, and whether any
	of them is switched on."""
	settings = frappe.get_cached_doc("Shop Settings")
	context = frappe._dict(
		cart_items=[
			{"item_code": row.item_code, "qty": flt(row.qty), "rate": flt(row.rate)} for row in cart.items
		],
		address=address,
		subtotal=subtotal,
		company=settings.company,
		currency=frappe.get_cached_value("Company", settings.company, "default_currency"),
	)
	options, messages = [], []
	active = False  # a provider that is switched off returns None
	for path in frappe.get_hooks("shop_shipping_rate_providers") or []:
		try:
			result = frappe.get_attr(path)(context)
			if result is None:
				continue
			active = True
		except frappe.ValidationError as error:
			messages.append(str(error))
			frappe.clear_messages()
			continue
		except Exception:
			frappe.log_error(title=f"Shipping rates failed: {path}")
			continue
		if isinstance(result, dict):
			messages.extend(result.get("messages") or [])
			result = result.get("options") or []
		for option in result or []:
			options.append(clean_option(option))
	return options, list(dict.fromkeys(messages)), active


def clean_option(option: dict) -> dict:
	return {
		"id": str(option["id"])[:140],
		"label": str(option.get("label") or option["id"])[:140],
		"amount": flt(option.get("amount"), 2),
		"tax_included": bool(option.get("tax_included")),
		"carrier": option.get("carrier"),
		"service": option.get("service"),
		"eta": option.get("eta"),
		"meta": option.get("meta") or {},
	}


def free_shipping_note() -> str | None:
	settings = frappe.get_cached_doc("Shop Settings")
	threshold = flt(settings.get("free_shipping_above"))
	if not threshold or not flat_rate_option(0):
		return None
	return _("Free shipping on orders over {0}").format(fmt_money(threshold, currency=settings.currency))


def flat_rate_option(subtotal: float) -> dict | None:
	settings = frappe.get_cached_doc("Shop Settings")
	rate = flt(settings.get("flat_shipping_rate"))
	if not rate or not (settings.get("shipping_account") or settings.get("shipping_item")):
		return None
	threshold = flt(settings.get("free_shipping_above"))
	free = bool(threshold and subtotal >= threshold)
	return {
		"id": FLAT_RATE_ID,
		"label": _("Free shipping") if free else _("Standard shipping"),
		"amount": 0.0 if free else rate,
		# the flat rate is entered the same way as the store's prices
		"tax_included": bool(settings.get("prices_include_tax")),
		"carrier": None,
		"service": None,
		"eta": None,
		"meta": {},
	}


def cached_quotes(cart) -> dict:
	try:
		return json.loads(cart.get("shipping_quotes") or "{}")
	except ValueError:
		return {}


def fresh(cached: dict) -> bool:
	created = cached.get("created")
	return bool(created) and time_diff_in_seconds(now_datetime(), created) < QUOTE_TTL_SECONDS


def store_quotes(cart, quotes: dict) -> None:
	cart.shipping_quotes = json.dumps(quotes, default=str)
	if not cart.is_new():
		cart.db_set("shipping_quotes", cart.shipping_quotes, update_modified=False)


def find_option(options: list[dict], option_id: str | None) -> dict | None:
	for option in options:
		if option["id"] == option_id:
			return option
	return None


def remember_choice(cart, option_id: str | None) -> None:
	if cint(bool(option_id)) and cart.get("shipping_rate") != option_id:
		cart.db_set("shipping_rate", option_id, update_modified=False)
