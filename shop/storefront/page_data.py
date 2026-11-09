import math
from itertools import pairwise

import frappe
from frappe import _
from frappe.utils import cint, escape_html, flt
from frappe.utils.caching import redis_cache

from shop.routes import signin_url, store_url
from shop.storefront import cart, catalog, checkout, orders, pricing, product

PAGE_SIZE = 24


@frappe.whitelist(allow_guest=True)
def home() -> dict:
	return {
		"store": store_details(),
		"collections": catalog.get_collections(),
		"featured_products": catalog.get_products(limit=8)["products"],
	}


@frappe.whitelist(allow_guest=True)
def listing() -> dict:
	form = frappe.form_dict
	page = max(cint(form.get("page")) or 1, 1)
	price_min, price_max = parse_price_bucket(form.get("price"))
	collections = catalog.get_collections(nested=True)
	result = catalog.get_products(
		collection=form.get("collection"),
		search=form.get("search"),
		sort=form.get("sort") or "ranking",
		start=(page - 1) * PAGE_SIZE,
		limit=PAGE_SIZE,
		price_min=price_min,
		price_max=price_max,
		in_stock=form.get("stock") == "in",
	)
	applied = applied_filters(form)
	return {
		"store": store_details(),
		"collections": collections,
		"filters": listing_filters(form, collections),
		"filters_applied": "true" if applied else None,
		"filter_count": str(len(applied)) if applied else None,
		# page data is put into the page as HTML, so what the shopper typed is escaped
		"search": escape_html(form.get("search") or ""),
		"search_label": escape_html(_('Results for "{0}"').format(form.get("search"))) if form.get("search") else None,
		"no_results": None if result["products"] else "true",
		"page": page,
		"has_more": page * PAGE_SIZE < result["total"],
		"pager": pager(page, result["total"]),
		**result,
	}


def pager(page: int, total: int) -> dict | None:
	"""Links to the pages before and after this one, keeping the shopper's filters."""
	from urllib.parse import urlencode

	request_path = frappe.local.request.path if getattr(frappe.local, "request", None) else ""

	def url(number: int) -> str:
		query = {k: v for k, v in frappe.form_dict.items() if k in (*FILTER_PARAMS, "search") and v}
		if number > 1:
			query["page"] = number
		return f"{request_path}?{urlencode(query)}" if query else request_path

	last = max((total + PAGE_SIZE - 1) // PAGE_SIZE, 1)
	links = {
		# past the end, back to the last page there is
		"prev_url": url(min(page - 1, last)) if page > 1 else None,
		"next_url": url(page + 1) if page * PAGE_SIZE < total else None,
	}
	return links if any(links.values()) else None


FILTER_PARAMS = ("collection", "price", "stock", "sort")


def applied_filters(form) -> list[str]:
	"""Which filters the shopper actually set, so the listing can say so when they are hidden."""
	return [key for key in FILTER_PARAMS if form.get(key)]


SORT_OPTIONS = [
	("ranking", "Featured"),
	("newest", "Newest"),
	("name", "Name"),
	("price_asc", "Price, low to high"),
	("price_desc", "Price, high to low"),
]


def parse_price_bucket(bucket: str | None):
	"""A price range from the listing's URL, "low-high" with either end left open."""
	low, _sep, high = (bucket or "").partition("-")
	try:
		return (float(low) if low else None), (float(high) if high else None)
	except ValueError:
		return None, None


def tidy_amount(amount: float) -> float:
	"""An amount rounded up to 1, 2, 2.5 or 5 of its order of magnitude, for a price range's edge."""
	if amount <= 0:
		return 0
	scale = 10 ** math.floor(math.log10(amount))
	return next(step * scale for step in (1, 2, 2.5, 5, 10) if amount <= step * scale)


def price_buckets(rates: list[float]) -> list[tuple]:
	"""Price ranges for the store's own prices: about a quarter of the products in each, with edges
	at tidy amounts in the store's currency."""
	rates = sorted(rate for rate in rates if rate > 0)
	if len(rates) < 2:
		return []
	edges = sorted({tidy_amount(rates[int(len(rates) * share)]) for share in (0.25, 0.5, 0.75)})
	edges = [edge for edge in edges if rates[0] < edge <= rates[-1]]
	ends = [None, *edges, None]
	return [
		(
			f"{low or 0:g}-{'' if high is None else f'{high:g}'}",
			"Under {0}" if low is None else ("Over {0}" if high is None else "{0} to {1}"),
			(low, high),
		)
		for low, high in pairwise(ends)
	]


def useful_price_buckets(form) -> list:
	"""The price ranges that have products in them. With fewer than two, a price filter can't
	narrow anything down and is left out (unless the shopper already chose a range)."""
	rates = listed_prices(frappe.get_cached_doc("Shop Settings").price_list)
	buckets = [
		bucket
		for bucket in price_buckets(rates)
		if any((bucket[2][0] or 0) <= rate and (bucket[2][1] is None or rate < bucket[2][1]) for rate in rates)
	]
	if len(buckets) < 2 and not form.get("price"):
		return []
	return buckets


@redis_cache(ttl=300)
def listed_prices(price_list: str) -> list[float]:
	"""The store price each published product is listed at (its lowest variant's, for variants)."""
	products = frappe.get_all("Shop Product", filters={"published": 1}, fields=["item", "has_variants"])
	prices = catalog.display_prices(products, customer=None)
	return [flt(prices[p.item]["rate"]) for p in products if p.item in prices]


def listing_filters(form, collections) -> list[dict]:
	def option(label, param, value):
		current = form.get(param)
		active = (current or "") == (value or "")
		return {"label": label, "url": filter_url(form, {param: value}), "active": "true" if active else "false"}

	from frappe.utils import fmt_money

	currency = frappe.get_cached_doc("Shop Settings").currency
	money = lambda amount: f"{fmt_money(amount, currency=currency, precision=0)} {currency}"
	buckets = useful_price_buckets(form)
	return [
		{
			"label": _("Collection"),
			"options": [
				option(_("All"), "collection", None),
				# a collection inside another is listed under it
				*[option("– " * row.level + row.title, "collection", row.slug) for row in collections],
			],
		},
		*(
			[
				{
					"label": _("Price"),
					"options": [
						option(_("Any price"), "price", None),
						*[
							option(_(label).format(money(low or high), money(high or low)), "price", key)
							for key, label, (low, high) in buckets
						],
					],
				}
			]
			if buckets
			else []
		),
		{
			"label": _("Availability"),
			"options": [option(_("All"), "stock", None), option(_("In stock"), "stock", "in")],
		},
		{
			"label": _("Sort"),
			"options": [option(_(label), "sort", None if key == "ranking" else key) for key, label in SORT_OPTIONS],
		},
	]


def filter_url(form, changes: dict) -> str:
	from urllib.parse import urlencode

	params = {
		key: form.get(key) for key in ("collection", "search", "sort", "price", "stock") if form.get(key)
	}
	for key, value in changes.items():
		if value:
			params[key] = value
		else:
			params.pop(key, None)
	return store_url("products") + (f"?{urlencode(params)}" if params else "")


@frappe.whitelist(allow_guest=True)
def product_page() -> dict:
	detail = product.get_product(frappe.form_dict.slug)
	detail["image"] = detail["images"][0]["image"] if detail["images"] else None
	detail["buy_item_code"] = detail.get("default_item_code") or detail["item"]
	detail["description_text"] = frappe.utils.strip_html(detail.get("description") or "")
	# the description as safe HTML, so lists, headings and spec tables keep their layout
	from frappe.utils.html_utils import sanitize_html

	detail["description_html"] = sanitize_html(detail.get("description") or "")
	detail["attribute_options"] = [
		{
			"attribute": option["attribute"],
			"values": [{"attribute": option["attribute"], "value": value} for value in option["values"]],
		}
		for option in detail.get("attributes", [])
	]
	detail["show_fabric_band"] = (
		"true" if any(c.get("slug") == "apparel" for c in detail.get("collections") or []) else None
	)
	from shop.storefront import reviews

	return {
		"store": store_details(),
		"product": detail,
		"related_products": related_products(detail),
		"reviews": reviews.get_reviews(detail["name"], limit=6),
	}


def related_products(detail: dict) -> list:
	collections = detail.get("collections") or []
	if collections:
		result = catalog.get_products(collection=collections[0]["slug"], limit=5)["products"]
	else:
		result = catalog.get_products(limit=5)["products"]
	return [product for product in result if product.slug != detail["slug"]][:4]


@frappe.whitelist(allow_guest=True)
def collection_page() -> dict:
	slug = frappe.form_dict.slug
	tree = catalog.published_collections()
	row = next((row for row in tree.values() if row.slug == slug), None)
	if not row:
		frappe.throw(frappe._("Collection not found"), frappe.DoesNotExistError)
	name = row.name
	collection = {key: row.get(key) for key in ("title", "slug", "description", "image")}
	page = max(cint(frappe.form_dict.get("page")) or 1, 1)
	result = catalog.get_products(collection=slug, start=(page - 1) * PAGE_SIZE, limit=PAGE_SIZE)
	return {
		"store": store_details(),
		"collection": collection,
		# where the collection sits (parent > child), and the collections inside it
		"breadcrumbs": [
			{"title": row.title, "route": store_url(f"collection/{row.slug}")} for row in catalog.ancestors(tree, name)
		]
		or None,
		"sub_collections": catalog.get_collections(parent=name) or None,
		"pager": pager(page, result["total"]),
		**result,
	}


@frappe.whitelist(allow_guest=True)
def basic() -> dict:
	return {"store": store_details()}


@frappe.whitelist(allow_guest=True)
def cart_page() -> dict:
	return {"store": store_details(), "cart": cart.get_cart()}


@frappe.whitelist(allow_guest=True)
def checkout_page() -> dict:
	return {"store": store_details(), **checkout.get_checkout_summary()}


@frappe.whitelist(allow_guest=True)
def order_confirmation() -> dict:
	form = frappe.form_dict
	return {
		"store": store_details(),
		"order": orders.get_order_summary(form.order_id, form.get("token")),
	}


@frappe.whitelist()
def account_orders() -> dict:
	from shop.storefront import accounts

	customers = orders.session_customers()
	return {
		"store": store_details(),
		"orders": orders.get_orders(),
		"account_html": account_html(customers),
		"trade": accounts.trade_status(),
	}


def account_html(customers: list[str]) -> str:
	"""Invoices and quotes as ready-made HTML for the account page."""
	from shop.storefront import accounts

	esc = frappe.utils.escape_html
	html = ""
	trade = accounts.trade_status()
	if trade and trade.get("trade"):
		note = _("Trade account: you see your trade prices across the store.")
		if trade.get("on_account"):
			note += " " + _("You can pay on account ({0}).").format(trade.get("payment_terms"))
		html += f'<div class="account-note">{esc(note)}</div>'
	elif trade and trade.get("status") == "Pending":
		html += f'<div class="account-note">{esc(_("Your trade account application is being reviewed."))}</div>'
	else:
		html += (
			f'<div class="account-note">{esc(_("Buying for a business?"))} '
			f'<a href="{store_url("account/trade")}">{esc(_("Apply for a trade account"))}</a></div>'
		)
	rows = accounts.invoices(customers)
	if customers and frappe.db.exists("GL Entry", {"party_type": "Customer", "party": ["in", customers], "is_cancelled": 0}):
		html += (
			f'<div class="account-actions"><a class="account-statement" '
			f'href="/api/method/shop.storefront.statements.statement_pdf">{esc(_("Download statement"))}</a> '
			f'<span>{esc(_("Your invoices and payments for the last 12 months."))}</span></div>'
		)
	if rows:
		html += f"<h2>{esc(_('Invoices'))}</h2><div class='account-table'>"
		for row in rows:
			owing = (
				f"{esc(_('Owing'))} {esc(row.formatted_outstanding)}, {esc(_('due'))} {esc(frappe.utils.formatdate(row.due_date))}"
				if frappe.utils.flt(row.outstanding_amount) > 0
				else esc(_("Paid"))
			)
			html += (
				f'<div class="account-row"><span>{esc(row.name)}</span><span>{esc(frappe.utils.formatdate(row.posting_date))}</span>'
				f"<span>{esc(row.formatted_total)}</span><span>{owing}</span>"
				f'<a href="{esc(row.pdf_url)}">{esc(_("PDF"))}</a></div>'
			)
		html += "</div>"
	quotes = frappe.get_all(
		"Quotation",
		filters={"party_name": ["in", customers or [""]], "quotation_to": "Customer", "docstatus": ["<", 2]},
		fields=[
			"name",
			"transaction_date",
			"valid_till",
			"grand_total",
			"rounded_total",
			"currency",
			"docstatus",
			"status",
			"shop_access_token",
		],
		order_by="creation desc",
		limit=20,
	) if customers and frappe.get_meta("Quotation").has_field("shop_access_token") else []
	if quotes:
		html += f"<h2>{esc(_('Quotes'))}</h2><div class='account-table'>"
		from frappe.utils import getdate, nowdate

		from shop.storefront.quotes import quote_status

		for row in quotes:
			expired = bool(row.valid_till and getdate(row.valid_till) < getdate(nowdate()))
			closed = row.status in ("Lost", "Cancelled")
			ordered = "Ordered" if row.status == "Ordered" else None
			state = quote_status(row, ordered, expired, closed)
			# a lost or expired quote has nothing to open
			can_open = row.docstatus == 1 and (ordered or not (expired or closed))
			link = store_url(f"quote/{row.name}?token={row.shop_access_token}") if can_open else ""
			total = flt(row.rounded_total) or flt(row.grand_total)
			html += (
				f'<div class="account-row"><span>{esc(row.name)}</span><span>{esc(frappe.utils.formatdate(row.transaction_date))}</span>'
				f"<span>{esc(frappe.utils.fmt_money(total, currency=row.currency))}</span><span>{esc(state)}</span>"
				+ (f'<a href="{esc(link)}">{esc(_("View"))}</a>' if link else "<span></span>")
				+ "</div>"
			)
		html += "</div>"
	return html


@frappe.whitelist(allow_guest=True)
def register_page() -> dict:
	from shop.storefront import checkout

	signed_in = frappe.session.user != "Guest"
	return {
		"store": store_details(),
		"terms": checkout.terms_details(),
		"show_form": None if signed_in else "true",
		"notice": _("You're signed in. Your orders and invoices are on your account page.") if signed_in else None,
		"trade_applications": trade_applications(),
	}


def trade_applications() -> str | None:
	"""Whether the store takes trade account applications, for pages that offer them."""
	return "true" if frappe.get_cached_doc("Shop Settings").allow_trade_applications else None


@frappe.whitelist(allow_guest=True)
def signin_page() -> dict:
	signed_in = frappe.session.user != "Guest"
	return {
		"store": store_details(),
		"show_form": None if signed_in else "true",
		"notice": _("You're signed in. Your orders and invoices are on your account page.") if signed_in else None,
		"redirect_to": return_path(frappe.form_dict.get("redirect-to")),
		"register_url": store_url("account/register"),
		"trade_applications": trade_applications(),
	}


def return_path(path: str | None) -> str:
	"""Where to go after signing in: a page on this site, else the account page."""
	from urllib.parse import urlsplit

	if (
		path
		and path.startswith("/")
		and path.isprintable()
		and "\\" not in path
		and not path.startswith("//")
		and not urlsplit(path).netloc
	):
		return path
	return store_url("account/orders")


@frappe.whitelist(allow_guest=True)
def trade_page() -> dict:
	from shop.storefront import accounts, checkout

	return {
		"store": store_details(),
		"terms": checkout.terms_details(),
		"countries": checkout.countries(),
		"default_country": checkout.totals.company_country(),
		"prefill": checkout.checkout_prefill(),
		**trade_form_state(accounts.trade_status()),
		# what the business does: ERPNext's Industry Types, as on a Lead
		"industries": [{"value": "", "label": frappe._("What does your business do?")}]
		+ [{"value": name, "label": name} for name in frappe.get_all("Industry Type", pluck="name", order_by="name")],
	}


def trade_form_state(trade: dict | None) -> dict:
	"""Whether the trade application form shows, or what to tell a business that has applied."""
	if not trade_applications():
		return {"trade": trade, "show_form": None, "notice": _("We aren't taking trade account applications.")}
	status = (trade or {}).get("status")
	notice = {
		"Approved": _("Your trade account is open. Sign in to see your trade prices."),
		"Pending": _("We have your application and will email you once we've reviewed it."),
	}.get(status)
	if trade and trade.get("trade"):
		notice = _("Your trade account is open. Your trade prices show while you're signed in.")
	return {"trade": trade, "show_form": None if notice else "true", "notice": notice}


@frappe.whitelist(allow_guest=True)
def quote_page() -> dict:
	from shop.storefront import checkout, quotes

	form = frappe.form_dict
	try:
		quote = quotes.quote_summary(form.get("quote_id"), form.get("token"))
	except (frappe.PermissionError, frappe.DoesNotExistError):
		frappe.clear_messages()
		quote = None
	return {"store": store_details(), "quote": quote, "quote_invalid": None if quote else "true", "terms": checkout.terms_details()}


@frappe.whitelist(allow_guest=True)
def terms_page() -> dict:
	from frappe.utils.html_utils import sanitize_html

	from shop.storefront import terms

	return {
		"store": store_details(),
		"terms_html": sanitize_html(terms.text()),
		"terms_version": terms.version() or "",
	}


def store_details() -> dict:
	from shop.storefront import returns, shipping

	settings = frappe.get_cached_doc("Shop Settings")
	signed_in = frappe.session.user not in ("Guest", None)
	delivery_note, returns_note = shipping.free_shipping_note(), returns.returns_note()
	return {
		"name": settings.store_name,
		"logo": settings.store_logo,
		"currency": settings.currency,
		"enable_cod": settings.enable_cod,
		"delivery_note": delivery_note,
		"returns_note": returns_note,
		"delivery_card": "true" if delivery_note or returns_note or settings.enable_cod else None,
		"account_label": frappe._("Account") if signed_in else frappe._("Sign in"),
		"account_url": store_url("account/orders") if signed_in else signin_url(store_url("account/orders")),
		"register_url": None if signed_in else store_url("account/register"),
		"home_url": store_url(""),
		"display_currency": pricing.display_currency(),
		"currency_picker_html": pricing.currency_picker_html(),
	}
