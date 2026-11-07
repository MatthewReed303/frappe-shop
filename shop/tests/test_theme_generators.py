from unittest.mock import patch

import frappe
from builder.builder.doctype.builder_client_script.builder_client_script import BuilderClientScript
from frappe.tests import IntegrationTestCase

from shop.theme_generators import dot
from shop.theme_generators import frappe as frappe_theme


class TestThemeGenerators(IntegrationTestCase):
	def test_site_theme_in_each_generator(self):
		for generator, prefix in ((frappe_theme, "shop"), (dot, "dot")):
			with self.subTest(generator=generator.GROUP):
				self.assert_site_theme(generator, prefix)

	def assert_site_theme(self, generator, prefix):
		group = f"acme-{generator.GROUP}"
		frappe.get_doc(
			{
				"doctype": "Builder Component",
				"component_id": f"{group}-footer",
				"component_name": "Acme Footer",
				"block": frappe.as_json({"element": "footer", "children": []}),
			}
		).insert(ignore_permissions=True)
		site_theme = generator.Theme(
			group=group,
			title="Acme",
			skip_routes={"about"},
			component_ids={f"{prefix}-navbar": f"{group}-bar", f"{prefix}-footer": f"{group}-footer"},
			site_components={f"{group}-footer"},
			faqs=[("Do you ship to the moon?", "Not yet.")],
			listing_subtitle="Everything Acme makes.",
			product_description="below",
			extra_css=".acme-only { color: red; }",
		)

		# developer mode would export the test theme into the app's templates, and writing a script's
		# file commits, so neither happens here
		with (
			patch.dict(frappe.conf, {"developer_mode": 0}),
			patch(f"{BuilderClientScript.__module__}.BuilderClientScript.update_script_file"),
		):
			generator.generate(site_theme)

		pages = frappe.get_all(
			"Builder Page",
			filters={"template_group": group, "is_template": 1},
			fields=["route", "blocks"],
		)
		routes = {page.route: page for page in pages}
		self.assertNotIn("about", routes)
		self.assertIn("checkout", routes)
		self.assertIn("Do you ship to the moon?", routes["faq"].blocks)
		self.assertIn("Everything Acme makes.", routes["products"].blocks)
		self.assertIn(f"{group}-bar", routes["home"].blocks)
		# the description has its own section under the images, and isn't in the buy box too
		names = block_names(frappe.parse_json(routes["product/:slug"].blocks))
		self.assertIn("Section · Product Description", names)
		self.assertEqual(names.count("Product Description"), 1)
		self.assertIn(f"{group}-footer", routes["home"].blocks)

		self.assertEqual(
			frappe.db.get_value("Builder Component", f"{group}-bar", "component_name"), "Acme Store Navbar"
		)
		self.assertEqual(
			frappe.db.get_value("Builder Component", f"{group}-footer", "component_name"), "Acme Footer"
		)
		self.assertIn(".acme-only", frappe.db.get_value("Builder Client Script", f"{group}-styles", "script"))
		# the generator's own components are left as they were
		self.assertEqual(
			frappe.db.get_value("Builder Component", f"{group}-hero", "component_name"), "Acme Store Hero"
		)
		self.assertIn(f"{group}-hero", routes["home"].blocks)
		self.assertNotIn(f'"{prefix}-hero"', routes["home"].blocks)
		self.assertEqual(generator.THEME.group, generator.GROUP)


def block_names(blocks) -> list[str]:
	names = []
	for node in blocks:
		if node.get("blockName"):
			names.append(node["blockName"])
		names += block_names(node.get("children") or [])
	return names
