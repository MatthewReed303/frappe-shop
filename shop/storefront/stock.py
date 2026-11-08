import math

import frappe
from frappe import _
from frappe.utils import cint, flt


def get_stock(item_codes: list[str], sellable: bool = False) -> dict[str, float]:
	"""Stock in the store's warehouse; sellable leaves out what orders not yet delivered wait for. A
	Product Bundle has as many as its stocked parts make up."""
	if not item_codes:
		return {}
	settings = frappe.get_cached_doc("Shop Settings")
	parts = {code: bundle_parts(code) for code in item_codes}
	codes = list(dict.fromkeys([*item_codes, *(part for rows in parts.values() for part, _qty in rows)]))
	rows = frappe.get_all(
		"Bin",
		filters={"item_code": ["in", codes], "warehouse": settings.default_warehouse},
		fields=["item_code", "actual_qty", "reserved_qty"],
	)
	qty_map = dict.fromkeys(codes, 0.0)
	qty_map.update(
		{row.item_code: flt(row.actual_qty) - (flt(row.reserved_qty) if sellable else 0) for row in rows}
	)
	for code, bundle in parts.items():
		stocked = [(part, qty) for part, qty in bundle if frappe.get_cached_value("Item", part, "is_stock_item")]
		if stocked:
			qty_map[code] = min(math.floor(qty_map.get(part, 0) / qty) for part, qty in stocked)
	return {code: qty_map[code] for code in item_codes}


def bundle_parts(item_code: str) -> list[tuple[str, float]]:
	"""The items of the item's Product Bundle and how many of each, or none."""
	bundle = frappe.db.get_value("Product Bundle", {"new_item_code": item_code, "disabled": 0})
	if not bundle:
		return []
	return [
		(row.item_code, flt(row.qty) or 1)
		for row in frappe.get_all(
			"Product Bundle Item", filters={"parent": bundle}, fields=["item_code", "qty"]
		)
	]


def is_stock_item(item_code: str) -> bool:
	"""Kept in stock: the item, or for a Product Bundle any of its parts."""
	if frappe.get_cached_value("Item", item_code, "is_stock_item"):
		return True
	return any(frappe.get_cached_value("Item", part, "is_stock_item") for part, _qty in bundle_parts(item_code))


def available_qty(item_code: str) -> float | None:
	"""How many the store can sell: None when it isn't limited (a service, or the store sells
	out-of-stock items)."""
	if frappe.get_cached_doc("Shop Settings").allow_out_of_stock or not is_stock_item(item_code):
		return None
	return get_stock([item_code], sellable=True).get(item_code, 0)


def availability(item_code: str, priced: bool = True) -> dict:
	"""Whether the item can be bought online, and what the product page says about it."""
	if not priced:
		return state(False, "unavailable", _("Not available online"))
	qty = available_qty(item_code)
	if qty is None:
		return state(True, "in", _("In stock") if is_stock_item(item_code) else _("Available"))
	if qty <= 0:
		return state(False, "out", _("Out of stock"))
	threshold = cint(frappe.get_cached_doc("Shop Settings").get("low_stock_threshold"))
	if threshold and qty <= threshold:
		return state(True, "low", _("Only {0} left").format(cint(qty)))
	return state(True, "in", _("In stock"))


def state(purchasable: bool, stock_state: str, stock_status: str) -> dict:
	return {"purchasable": purchasable, "stock_state": stock_state, "stock_status": stock_status}


def is_in_stock(item_code: str) -> bool:
	settings = frappe.get_cached_doc("Shop Settings")
	if settings.allow_out_of_stock:
		return True
	if not is_stock_item(item_code):
		return True
	return get_stock([item_code], sellable=True).get(item_code, 0) > 0
