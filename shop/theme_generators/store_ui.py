"""Styles shared by Shop's themes for what the store's script and server render."""


def store_css(refs, radius: str = "2px"):
	"""Styles for what the store's script and server render: checkout lines and options, the
	order's payment box and totals, account rows and forms, quote and terms pages."""
	ink, muted, line, card, paper, ok = refs["ink"], refs["muted"], refs["line"], refs["card"], refs["paper"], refs["success"]
	warn = refs.get("badge") or refs.get("accent") or refs["ink"]
	return f"""
[hidden] {{ display: none !important; }}
.visually-hidden {{ position:absolute; width:1px; height:1px; overflow:hidden; clip:rect(0 0 0 0); white-space:nowrap; }}
.store-currency:empty {{ display:none; }}
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
