import frappe
from frappe.utils import cint, flt

from shop.routes import store_url
from shop.storefront import pricing, stock

MAX_PAGE_SIZE = 60
MAX_CATALOG_SIZE = 500

SORT_ORDERS = {
	"ranking": "ranking desc, modified desc",
	"newest": "creation desc",
	"name": "product_name asc",
	"price_asc": None,
	"price_desc": None,
}


@frappe.whitelist(allow_guest=True)
def get_products(
	collection: str | None = None,
	search: str | None = None,
	sort: str = "ranking",
	start: int = 0,
	limit: int = 24,
	price_min: float | None = None,
	price_max: float | None = None,
	in_stock: bool = False,
) -> dict:
	filters = {"published": 1}
	if collection:
		filters["name"] = ["in", collection_members(collection)]
	products = frappe.get_all(
		"Shop Product",
		filters=filters,
		or_filters=search_filters(search),
		fields=[
			"name",
			"product_name",
			"slug",
			"short_description",
			"has_variants",
			"item",
			"compare_at_price",
		],
		order_by=SORT_ORDERS.get(sort) or SORT_ORDERS["ranking"],
		limit=MAX_CATALOG_SIZE,
	)
	decorate(products)
	products = apply_post_filters(products, price_min, price_max, in_stock)
	if sort in ("price_asc", "price_desc"):
		products.sort(key=lambda p: p.price if p.price is not None else float("inf"))
		if sort == "price_desc":
			products.reverse()
	start = cint(start)
	limit = min(cint(limit) or 24, MAX_PAGE_SIZE)
	return {"products": products[start : start + limit], "total": len(products)}


def apply_post_filters(products, price_min, price_max, in_stock):
	def keep(product):
		# the price ranges are the prices as the store shows them
		price = (
			pricing.shown_amount(product.price, pricing.item_tax_rate(product.item))
			if product.price is not None
			else None
		)
		if price_min is not None and (price is None or price < flt(price_min)):
			return False
		if price_max is not None and (price is None or price >= flt(price_max)):
			return False
		if in_stock and not product.in_stock:
			return False
		return True

	return [product for product in products if keep(product)]


@frappe.whitelist(allow_guest=True)
def get_collections(parent: str | None = None, nested: bool = False) -> list[dict]:
	"""Published collections: those inside parent (the top ones when not given), or with nested
	every one, each followed by the collections inside it."""
	tree = published_collections()
	if nested:
		collections = [row for top in children(tree, None) for row in with_descendants(tree, top)]
	else:
		collections = children(tree, parent)
	counts = collection_counts([c.name for c in collections])
	for row in collections:
		row.route = store_url(f"collection/{row.slug}")
		row.product_count = counts.get(row.name, 0)
		row.image_css = tile_background(row.image)
	return collections


def published_collections() -> dict:
	"""Published collections by name. One inside an unpublished collection is left out with it."""
	rows = frappe.get_all(
		"Shop Collection",
		filters={"published": 1},
		fields=["name", "title", "slug", "description", "image", "parent_collection"],
		order_by="ranking desc, title asc",
	)
	tree = {row.name: row for row in rows}
	for row in rows:
		row.level = 0
		parent = row.parent_collection
		while parent:
			if parent not in tree:
				row.hidden = True
				break
			row.level += 1
			parent = tree[parent].parent_collection
	return {name: row for name, row in tree.items() if not row.get("hidden")}


def children(tree: dict, parent: str | None) -> list:
	return [row for row in tree.values() if (row.parent_collection or None) == parent]


def with_descendants(tree: dict, row) -> list:
	return [row, *[d for child in children(tree, row.name) for d in with_descendants(tree, child)]]


def ancestors(tree: dict, name: str) -> list:
	"""The collections a collection is inside, outermost first."""
	trail, parent = [], tree[name].parent_collection
	while parent in tree:
		trail.insert(0, tree[parent])
		parent = tree[parent].parent_collection
	return trail


def tile_background(image: str | None) -> str | None:
	if not image:
		return None
	return f"url('{image}')"


def collection_members(slug: str) -> list[str]:
	"""The products in a collection and in the collections inside it."""
	tree = published_collections()
	collection = next((row for row in tree.values() if row.slug == slug), None)
	if not collection:
		return []
	names = [row.name for row in with_descendants(tree, collection)]
	return list(
		set(frappe.get_all("Shop Product Collection", filters={"collection": ["in", names]}, pluck="parent"))
	)


def collection_counts(collections: list[str]) -> dict:
	"""How many products each collection has, with those in the collections inside it."""
	if not collections:
		return {}
	tree = published_collections()
	if not any(name in tree for name in collections):
		return {}
	inside = {
		name: [row.name for row in with_descendants(tree, tree[name])] for name in collections if name in tree
	}
	members = {}
	for row in frappe.get_all(
		"Shop Product Collection",
		filters={"collection": ["in", list({n for names in inside.values() for n in names})]},
		fields=["collection", "parent"],
	):
		members.setdefault(row.collection, set()).add(row.parent)
	return {name: len(set().union(*(members.get(n, set()) for n in names))) for name, names in inside.items()}


def search_filters(search: str | None) -> list | None:
	if not search:
		return None
	term = f"%{search.strip()}%"
	return [
		["product_name", "like", term],
		["short_description", "like", term],
		["item", "like", term],
	]


def decorate(products: list) -> None:
	from shop.storefront import reviews

	images = first_images([p.name for p in products])
	prices = display_prices(products)
	availability = display_stock(products)
	ratings = reviews.summaries([p.name for p in products])
	for product in products:
		product.route = store_url(f"product/{product.slug}")
		product.image = images.get(product.name)
		price = prices.get(product.item, {})
		product.price = price.get("rate")
		product.formatted_price = price.get("display")
		product.formatted_price_excl = price.get("display_excl")
		if price.get("retail"):
			product.compare_at_price = max(flt(product.get("compare_at_price")), flt(price["retail"]))
		product.in_stock = availability.get(product.item, False)
		apply_compare_at(product, product.price)
		rating = ratings.get(product.name)
		product.rating_average = rating["average"] if rating else None
		product.rating_count = rating["count"] if rating else 0
		product.rating_stars = star_string(rating["average"]) if rating else None


def apply_compare_at(target, price) -> None:
	compare_at = flt(target.get("compare_at_price"))
	if not price or compare_at <= flt(price):
		target["compare_at_price"] = None
		return
	tax_rate = pricing.item_tax_rate(target.get("item"))
	target["formatted_compare_at"] = pricing.display_amount(compare_at, tax_rate)
	target["discount_pct"] = round((compare_at - flt(price)) * 100 / compare_at)
	target["formatted_savings"] = pricing.display_amount(compare_at - flt(price), tax_rate)


def star_string(average: float) -> str:
	full = int(flt(average) + 0.5)
	return "★" * full + "☆" * (5 - full)


def first_images(product_names: list[str]) -> dict:
	if not product_names:
		return {}
	rows = frappe.get_all(
		"Shop Product Image",
		filters={"parent": ["in", product_names]},
		fields=["parent", "image"],
		order_by="parent, idx",
	)
	images = {}
	for row in rows:
		images.setdefault(row.parent, row.image)
	return images


def display_prices(products: list, customer: str | None = "session") -> dict:
	"""Each product's price for the customer (the shopper's by default); a product with variants
	shows its lowest. One lookup for the page."""
	simple = [p.item for p in products if not p.has_variants]
	variants = variants_by_template([p.item for p in products if p.has_variants])
	prices = pricing.get_prices(
		simple + [code for codes in variants.values() for code in codes], customer=customer
	)
	for template, codes in variants.items():
		rates = [prices[code]["rate"] for code in codes if code in prices]
		if rates:
			prices[template] = {
				"rate": min(rates),
				"formatted": pricing.format_amount(min(rates)),
				**pricing.display_fields(min(rates), template),
			}
	return prices


def display_stock(products: list) -> dict:
	settings = frappe.get_cached_doc("Shop Settings")
	if settings.allow_out_of_stock:
		return dict.fromkeys([p.item for p in products], True)
	simple = [p.item for p in products if not p.has_variants]
	templates = [p.item for p in products if p.has_variants]
	variants = variants_by_template(templates)
	codes = simple + [code for group in variants.values() for code in group]
	stocked = (
		set(frappe.get_all("Item", filters={"name": ["in", codes], "is_stock_item": 1}, pluck="name"))
		if codes
		else set()
	)
	quantities = stock.get_stock([code for code in codes if code in stocked], sellable=True)
	# services aren't counted in stock
	available = {code: code not in stocked or quantities.get(code, 0) > 0 for code in codes}
	availability = {code: available[code] for code in simple}
	for template, group in variants.items():
		availability[template] = any(available[code] for code in group)
	return availability


def variants_by_template(templates: list[str]) -> dict[str, list[str]]:
	if not templates:
		return {}
	rows = frappe.get_all("Item", filters={"variant_of": ["in", templates]}, fields=["name", "variant_of"])
	grouped = {}
	for row in rows:
		grouped.setdefault(row.variant_of, []).append(row.name)
	return grouped
