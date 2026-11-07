import frappe
from frappe import _
from frappe.utils import flt

from shop.storefront import pricing, stock


@frappe.whitelist(allow_guest=True)
def get_product(slug: str) -> dict:
	name = frappe.db.get_value("Shop Product", {"slug": slug, "published": 1})
	if not name:
		frappe.throw(frappe._("Product not found"), frappe.DoesNotExistError)
	doc = frappe.get_doc("Shop Product", name)
	from shop.storefront import reviews

	payload = {
		"name": doc.name,
		"product_name": doc.product_name,
		"slug": doc.slug,
		"item": doc.item,
		"short_description": doc.short_description,
		"description": doc.description,
		"has_variants": doc.has_variants,
		"compare_at_price": doc.compare_at_price,
		"highlights": [
			{"label": line.strip()} for line in (doc.highlights or "").splitlines() if line.strip()
		],
		"images": [{"image": row.image, "alt_text": row.alt_text} for row in doc.images],
		"collections": product_collections(doc),
		"rating": reviews.summary(doc.name),
	}
	if doc.has_variants:
		payload.update(variant_details(doc.item, doc.compare_at_price))
	else:
		price = pricing.get_price(doc.item) or {}
		if price.get("retail"):  # a trade or customer price: show the store price struck through
			payload["compare_at_price"] = max(flt(doc.compare_at_price), flt(price["retail"]))
		available = stock.availability(doc.item, priced=price.get("rate") is not None)
		payload.update(
			{
				"price": price.get("rate"),
				"formatted_price": price.get("display"),
				"price_note": price.get("display_excl"),
				"in_stock": available["purchasable"],
				**available,
			}
		)
	from shop.storefront.catalog import apply_compare_at, star_string

	apply_compare_at(payload, payload.get("price"))
	if payload["rating"]["count"]:
		payload["rating"]["stars"] = star_string(payload["rating"]["average"])
	return payload


def product_collections(doc) -> list[dict]:
	collections = [row.collection for row in doc.collections]
	if not collections:
		return []
	return frappe.get_all(
		"Shop Collection",
		filters={"name": ["in", collections], "published": 1},
		fields=["title", "slug"],
	)


def variant_details(template: str, compare_at: float | None = None) -> dict:
	variants = load_variants(template, compare_at)
	rates = [v["price"] for v in variants if v["price"] is not None]
	default = next((v for v in variants if v["purchasable"]), variants[0] if variants else None)
	return {
		"attributes": attribute_options(template, variants),
		"variants": variants,
		"price": min(rates) if rates else None,
		"formatted_price": pricing.display_price(min(rates)) if rates else None,
		"price_note": pricing.display_price_without_tax(min(rates)) if rates else None,
		"in_stock": any(v["purchasable"] for v in variants),
		"purchasable": bool(default and default["purchasable"]),
		"stock_status": default["stock_status"] if default else _("Not available online"),
		"stock_state": default["stock_state"] if default else "unavailable",
		"default_item_code": default["item_code"] if default else None,
	}


def load_variants(template: str, compare_at: float | None = None) -> list[dict]:
	rows = frappe.get_all(
		"Item", filters={"variant_of": template, "disabled": 0}, fields=["name", "image"]
	)
	items = [row.name for row in rows]
	images = {row.name: row.image for row in rows if row.image}
	prices = pricing.get_prices(items)
	attributes = frappe.get_all(
		"Item Variant Attribute",
		filters={"parent": ["in", items]},
		fields=["parent", "attribute", "attribute_value"],
	)
	variants = []
	for item_code in items:
		price = prices.get(item_code, {})
		variants.append(
			{
				"item_code": item_code,
				"attributes": {
					row.attribute: row.attribute_value for row in attributes if row.parent == item_code
				},
				"price": price.get("rate"),
				"formatted_price": price.get("display"),
				"price_note": price.get("display_excl"),
				**stock.availability(item_code, priced=price.get("rate") is not None),
				"image": images.get(item_code),
				**variant_savings(price.get("rate"), compare_at),
			}
		)
	return variants


def variant_savings(rate: float | None, compare_at: float | None) -> dict:
	from frappe.utils import flt

	if not rate or not compare_at or flt(compare_at) <= flt(rate):
		return {"formatted_savings": None, "discount_pct": None}
	saved = flt(compare_at) - flt(rate)
	return {
		"formatted_savings": pricing.display_amount(saved),
		"discount_pct": round(saved * 100 / flt(compare_at)),
	}


def attribute_options(template: str, variants: list[dict]) -> list[dict]:
	order = frappe.get_all(
		"Item Variant Attribute",
		filters={"parent": template},
		fields=["attribute"],
		order_by="idx",
		pluck="attribute",
	)
	options = []
	for attribute in order:
		values = ordered_values(attribute)
		used = {v["attributes"].get(attribute) for v in variants}
		options.append(
			{"attribute": attribute, "values": [v for v in values if v in used]}
		)
	return options


def ordered_values(attribute: str) -> list[str]:
	return frappe.get_all(
		"Item Attribute Value",
		filters={"parent": attribute},
		order_by="idx",
		pluck="attribute_value",
	)
