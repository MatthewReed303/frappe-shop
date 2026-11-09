"""Store pieces both themes share: the store's own CSS (checkout, orders, accounts, quotes, terms)
and the form blocks the store's endpoints read."""

from shop.theme_generators.blocks import block, dv, repeater


def store_css(refs, radius: str = "2px"):
	"""Styles for what the store's script and server render: checkout lines and options, the
	order's payment box and totals, account rows and forms, quote and terms pages."""
	ink, muted, line, card, paper, ok = (
		refs["ink"],
		refs["muted"],
		refs["line"],
		refs["card"],
		refs["paper"],
		refs["success"],
	)
	warn = refs.get("badge") or refs.get("accent") or refs["ink"]
	return f"""
[hidden] {{ display: none !important; }}
.product-description p, .product-description ul, .product-description ol, .product-description table {{ margin:0 0 12px; }}
.product-description ul, .product-description ol {{ padding-left:20px; }}
.product-description li {{ margin:4px 0; }}
.product-description a {{ color:{ink}; text-decoration:underline; }}
.product-description table {{ border-collapse:collapse; width:100%; }}
.product-description td, .product-description th {{ border-bottom:1px solid {line}; padding:6px 8px; text-align:left; }}
@media (max-width: 640px) {{ input, select, textarea {{ font-size:16px !important; }} }}
.visually-hidden {{ position:absolute; width:1px; height:1px; overflow:hidden; clip:rect(0 0 0 0); white-space:nowrap; }}
.store-currency:empty {{ display:none; }}
[data-shop="stock-status"] {{ color:{ok}; }}
[data-shop="stock-status"] .stock-dot {{ background:{ok}; }}
[data-shop="stock-status"][data-stock-state="out"], [data-shop="stock-status"][data-stock-state="unavailable"] {{ color:{muted}; }}
[data-shop="stock-status"][data-stock-state="out"] .stock-dot, [data-shop="stock-status"][data-stock-state="unavailable"] .stock-dot {{ background:{muted}; }}
.stock-locations {{ list-style:none; margin:0; padding:0; display:flex; flex-direction:column; gap:4px; font-size:13px; }}
.stock-locations li {{ display:flex; justify-content:space-between; gap:12px; color:{ink}; }}
.stock-locations li span:last-child {{ color:{ok}; }}
.stock-locations li[data-stock-state="out"] span:last-child {{ color:{muted}; }}
.shipping-note {{ margin:0; font-size:13px; color:{muted}; }}
.currency-picker select {{ font: inherit; font-size:13px; color:{ink}; background:{paper}; border:1px solid {line}; border-radius:{radius}; padding:5px 7px; cursor:pointer; }}
.checkout-option {{ display:flex; align-items:center; gap:10px; padding:12px 14px; border:1px solid {line}; border-radius:{radius}; cursor:pointer; width:100%; font-size:13px; }}
.checkout-option input {{ accent-color:{ink}; width:15px; height:15px; flex-shrink:0; }}
.checkout-option-label {{ flex-grow:1; }}
.checkout-option-label small {{ color:{muted}; margin-left:6px; }}
.checkout-option-price {{ font-weight:600; white-space:nowrap; }}
small.checkout-option-price {{ font-weight:400; color:{muted}; font-size:12px; }}
.checkout-option:has(input:checked) {{ border-color:{ink}; box-shadow:0 0 0 1px {ink}; }}
.checkout-hint {{ color:{muted}; font-size:12px; line-height:1.5; margin:0; }}
.checkout-hint a, .account-note a, .checkout-check a {{ color:{ink}; text-decoration:underline; }}
.checkout-warning {{ border:1px solid {warn}; border-radius:{radius}; padding:12px 14px; font-size:13px; line-height:1.5; }}
.checkout-warning p {{ margin:0; }}
.checkout-error {{ border:1px solid {warn}; color:{warn}; border-radius:{radius}; padding:12px 14px; font-size:13px; }}
.checkout-check {{ display:flex; gap:10px; align-items:flex-start; font-size:13px; line-height:1.5; cursor:pointer; }}
.checkout-check input {{ margin-top:3px; accent-color:{ink}; width:15px; height:15px; flex-shrink:0; }}
.terms-note::before {{ content:", "; }}
.checkout-totals[aria-busy="true"] {{ opacity:.55; }}
.checkout-line, .order-line {{ display:flex; justify-content:space-between; gap:12px; font-size:13px; padding:2px 0; }}
.checkout-total, .order-line.total {{ border-top:1px solid {line}; margin-top:6px; padding-top:10px; font-size:15px; font-weight:700; }}
.checkout-note, .order-note {{ color:{muted}; font-size:12px; margin:2px 0 0; }}
.order-item {{ display:grid; grid-template-columns:1fr auto auto; gap:14px; padding:10px 0; border-bottom:1px solid {line}; font-size:13px; }}
.order-qty {{ color:{muted}; }}
.order-qty::before {{ content:"× "; }}
.order-amount {{ font-weight:600; }}
.order-totals {{ display:flex; flex-direction:column; gap:6px; }}
.order-payment {{ border:1px solid {line}; border-radius:{radius}; padding:16px 18px; font-size:13px; line-height:1.6; }}
.order-payment h3 {{ margin:0 0 8px; font-size:15px; }}
.order-payment p {{ margin:0 0 8px; }}
.order-payment.bank, .order-payment.card {{ border-color:{warn}; }}
.order-payment.card {{ display:flex; align-items:center; justify-content:space-between; gap:16px; flex-wrap:wrap; }}
.order-payment.paid {{ border-color:{ok}; }}
.order-payment.cancelled {{ background:{card}; }}
.bank-details {{ background:{card}; border-radius:{radius}; padding:10px 12px; margin:8px 0; font-family: ui-monospace, Menlo, monospace; font-size:13px; }}
.order-pay-button {{ display:inline-block; background:{ink}; color:{paper}; border-radius:{radius}; padding:10px 18px; font-weight:600; text-decoration:none; }}
.account-form {{ display:grid; grid-template-columns:repeat(2, minmax(0, 1fr)); gap:10px; width:100%; }}
.account-form textarea, .account-form select {{ grid-column: span 2; }}
.account-form select[name="country"], .account-form select[name="industry"] {{ grid-column: span 1; }}
.form-submit, .quote-button {{ grid-column: span 2; cursor:pointer; }}
.quote-button {{ background:{paper}; color:{ink}; border:1px solid {ink}; border-radius:{radius}; margin-top:8px; padding:12px 20px; font-size:13px; font-weight:600; }}
.quote-request summary {{ cursor:pointer; font-size:13px; text-decoration:underline; }}
.quote-request textarea {{ margin-top:8px; }}
.form-done {{ border:1px solid {ok}; border-radius:{radius}; padding:14px 16px; font-size:13px; width:100%; }}
.account-note {{ background:{card}; border-radius:{radius}; padding:14px 16px; font-size:13px; margin-bottom:12px; }}
.account-details h2 {{ font-size:18px; font-weight:600; margin:24px 0 8px; }}
.account-actions {{ display:flex; gap:12px; align-items:baseline; flex-wrap:wrap; margin:6px 0; font-size:13px; color:{muted}; }}
.account-actions a {{ color:{ink}; font-weight:600; }}
.account-table {{ display:flex; flex-direction:column; width:100%; }}
.account-row {{ display:grid; grid-template-columns:1.4fr 1fr 1fr 1.6fr auto; gap:12px; padding:12px 0; border-bottom:1px solid {line}; font-size:13px; color:{ink}; text-decoration:none; align-items:center; }}
.account-row a {{ color:{ink}; text-decoration:underline; }}
.terms-text h2 {{ font-size:20px; margin:28px 0 10px; }}
.terms-text ol {{ padding-left:22px; }}
@media (max-width: 640px) {{
	.account-row {{ grid-template-columns:1fr 1fr; }}
	.account-form {{ grid-template-columns:minmax(0,1fr); }}
	.account-form > * {{ grid-column: span 1 !important; }}
}}
"""


def hint(refs, text="", **extra):
	return block(
		"p",
		text=text,
		classes=["checkout-hint"],
		styles={
			"color": refs["muted"],
			"fontSize": "12px",
			"gridColumn": "span 2",
			"lineHeight": "1.5",
			"width": "100%",
		},
		**extra,
	)


def form_error():
	"""Where storefront.js shows a form's errors."""
	return block(
		"div",
		attrs={"data-shop": "error", "role": "alert"},
		classes=["checkout-error"],
		styles={"display": "none", "gridColumn": "span 2"},
	)


def terms_box(lead="I accept the", span=2, margin="6px", business=True):
	"""The terms checkbox, with the store's Terms and Conditions link and note (terms.*), and, for
	purchases, the store's business buyer statement."""
	children = [
		block(
			"label",
			classes=["checkout-check"],
			children=[
				block("input", attrs={"type": "checkbox", "name": "accept_terms", "required": "required"}),
				block(
					"span",
					children=[
						block("span", text=f"{lead} "),
						block(
							"a",
							text="Terms and Conditions",
							attrs={"href": "/terms", "target": "_blank", "data-shop": "terms-link"},
							dynamicValues=[dv("terms.url", "href", "attribute")],
						),
						block(
							"span",
							text="",
							classes=["terms-note"],
							visibilityCondition={"key": "terms.note", "comesFrom": "dataScript"},
							dynamicValues=[dv("terms.note", "innerHTML")],
						),
					],
				),
			],
		),
	]
	if business:
		children.append(
			block(
				"label",
				classes=["checkout-check"],
				visibilityCondition={"key": "terms.business_label", "comesFrom": "dataScript"},
				children=[
					block("input", attrs={"type": "checkbox", "name": "business_buyer"}),
					block("span", text="", dynamicValues=[dv("terms.business_label", "innerHTML")]),
				],
			)
		)
	return block(
		"div",
		name="Terms",
		visibilityCondition={"key": "terms", "comesFrom": "dataScript"},
		styles={
			"display": "flex",
			"flexDirection": "column",
			"gap": "10px",
			"gridColumn": f"span {span}",
			"marginTop": margin,
			"width": "100%",
		},
		children=children,
	)


def pager(refs):
	"""Previous and next page links under a product grid."""
	link = {"color": refs["ink"], "fontSize": "13px", "textDecoration": "underline", "width": "fit-content"}
	return block(
		"nav",
		name="Pager",
		attrs={"aria-label": "Pages"},
		classes=["store-pager"],
		visibilityCondition={"key": "pager", "comesFrom": "dataScript"},
		styles={
			"display": "flex",
			"flexDirection": "row",
			"gap": "24px",
			"justifyContent": "center",
			"marginTop": "32px",
			"width": "100%",
		},
		children=[
			block(
				"a",
				text="← Previous",
				visibilityCondition={"key": "pager.prev_url", "comesFrom": "dataScript"},
				styles=link,
				dynamicValues=[dv("pager.prev_url", "href", "attribute")],
			),
			block(
				"a",
				text="Next →",
				visibilityCondition={"key": "pager.next_url", "comesFrom": "dataScript"},
				styles=link,
				dynamicValues=[dv("pager.next_url", "href", "attribute")],
			),
		],
	)


def breadcrumbs(refs, size="13px"):
	"""The collections a collection is inside (page data key `breadcrumbs`), each a link and a separator."""
	crumb = block(
		"div",
		styles={"alignItems": "center", "display": "flex", "gap": "6px"},
		children=[
			block(
				"a",
				text="Collection",
				attrs={"href": "#"},
				styles={"color": refs["muted"], "fontSize": size, "textDecoration": "none"},
				dynamicValues=[dv("title", "innerHTML"), dv("route", "href", "attribute")],
			),
			block("span", text="\u203a", styles={"color": refs["muted"], "fontSize": size}),
		],
	)
	return repeater(
		"breadcrumbs",
		crumb,
		{"display": "flex", "flexWrap": "wrap", "gap": "6px", "width": "100%"},
		name="Breadcrumbs",
		visibilityCondition={"key": "breadcrumbs", "comesFrom": "dataScript"},
	)


def sub_collections(tile: dict) -> dict:
	"""The collections inside this one (page data key `sub_collections`), as tiles."""
	return repeater(
		"sub_collections",
		tile,
		{
			"display": "grid",
			"gap": "16px",
			"gridTemplateColumns": "repeat(4, minmax(0, 1fr))",
			"width": "100%",
		},
		mobile={"gridTemplateColumns": "repeat(2, minmax(0, 1fr))"},
		tablet={"gridTemplateColumns": "repeat(3, minmax(0, 1fr))"},
		name="Grid · sub-collections",
		visibilityCondition={"key": "sub_collections", "comesFrom": "dataScript"},
	)


def forgot_password(refs):
	"""Sends Frappe's password reset email to the address in the sign-in form."""
	return block(
		"button",
		text="Forgot your password?",
		attrs={"type": "button", "data-shop": "forgot-password"},
		styles={
			"backgroundColor": "transparent",
			"borderWidth": "0px",
			"color": refs["muted"],
			"cursor": "pointer",
			"fontSize": "12px",
			"gridColumn": "span 2",
			"justifySelf": "start",
			"padding": "0px",
			"textDecoration": "underline",
			"width": "fit-content",
		},
	)


def notice(refs):
	"""A message a page shows instead of its form (page data key `notice`)."""
	return block(
		"p",
		text="",
		classes=["form-done"],
		visibilityCondition={"key": "notice", "comesFrom": "dataScript"},
		dynamicValues=[dv("notice", "innerHTML")],
	)


def shown_with_form(form: dict) -> dict:
	"""The form, shown only when the page data says so (key `show_form`)."""
	form["visibilityCondition"] = {"key": "show_form", "comesFrom": "dataScript"}
	return form
