"""Store discounts: a simple table on a Customer or Customer Group, e.g. "HATs 15%".

Each row is kept as an ERPNext Pricing Rule (discount on that product group, for that customer
or group), so the same discount applies in the store, on quotes and on orders made in the desk.
Rules made here are titled "Store discount: ..."; others are left alone. A customer's own
discounts take priority over their group's.
"""

import frappe
from frappe import _
from frappe.utils import flt

TITLE = "Store discount: {party} / {group}"
PRIORITY = {"Customer": "10", "Customer Group": "5"}


def sync(doc, method=None) -> None:
	"""Customer / Customer Group on_update: make the pricing rules match the discounts table."""
	if not doc.meta.has_field("shop_discounts"):
		return
	applicable_for = doc.doctype
	party_field = "customer" if applicable_for == "Customer" else "customer_group"
	company = frappe.db.get_single_value("Shop Settings", "company")
	wanted = {}
	for row in doc.get("shop_discounts") or []:
		if not row.item_group or flt(row.discount_percentage) <= 0:
			continue
		if row.item_group in wanted:
			frappe.throw(_("{0} is listed twice in Store Discounts").format(row.item_group))
		wanted[row.item_group] = flt(row.discount_percentage)
	existing = {
		rule.title: rule.name
		for rule in frappe.get_all(
			"Pricing Rule",
			filters={"applicable_for": applicable_for, party_field: doc.name, "title": ["like", "Store discount:%"]},
			fields=["name", "title"],
		)
	}
	keep = set()
	for group, percent in wanted.items():
		title = TITLE.format(party=doc.name, group=group)
		keep.add(title)
		rule = frappe.get_doc("Pricing Rule", existing[title]) if title in existing else frappe.new_doc("Pricing Rule")
		rule.update(
			{
				"title": title,
				"apply_on": "Item Group",
				"applicable_for": applicable_for,
				party_field: doc.name,
				"selling": 1,
				"buying": 0,
				"rate_or_discount": "Discount Percentage",
				"discount_percentage": percent,
				"company": company,
				"has_priority": 1,
				"priority": PRIORITY[applicable_for],
				"disable": 0,
			}
		)
		rule.set("item_groups", [{"item_group": group}])
		rule.flags.ignore_permissions = True
		rule.save(ignore_permissions=True) if not rule.is_new() else rule.insert(ignore_permissions=True)
	for title, name in existing.items():
		if title not in keep:
			frappe.delete_doc("Pricing Rule", name, ignore_permissions=True)
