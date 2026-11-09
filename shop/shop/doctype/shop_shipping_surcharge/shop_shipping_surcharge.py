import re

from frappe.model.document import Document


class ShopShippingSurcharge(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		address_contains: DF.Data
		amount: DF.Float
		amount_includes_tax: DF.Check
		applies_to: DF.Literal["Within the store's country", "Abroad", "Everywhere"]
		carrier: DF.Data | None
		label: DF.Data
		parent: DF.Data
		parentfield: DF.Data
		parenttype: DF.Data
	# end: auto-generated types

	def pattern(self) -> re.Pattern:
		return address_pattern(self.address_contains)

	def matches(self, address_text: str) -> bool:
		return bool(self.pattern().search(address_text or ""))


def address_pattern(text: str) -> re.Pattern:
	"""What "Address Contains" means: its words in any case, a letter optionally followed by a full
	stop (R.D.), # for a number, with no letter or digit joined on at either end ("RD #" finds
	"RD 4" and "rd4", not "3rd 4" or "Ford 4")."""
	parts = []
	for char in (text or "").strip():
		if char == "#":
			parts.append(r"\s*\d+")
		elif char.isspace():
			parts.append(r"[\s,]*")
		elif char.isalpha():
			parts.append(re.escape(char) + r"\.?")
		else:
			parts.append(re.escape(char))
	return re.compile(r"(?<![^\W_])" + "".join(parts) + r"(?![^\W_])", re.IGNORECASE)
