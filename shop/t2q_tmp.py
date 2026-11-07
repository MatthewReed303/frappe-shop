import frappe, json

PAGER = [f"PAGER-TEST-{i:02d}" for i in range(1, 27)]

def setup():
    frappe.set_user("Administrator")
    for code in ("TTEST-TRANSFER-FEE", "TTEST-CONVERSION-FEE"):
        frappe.db.set_value("Item", code, "disabled", 0)
    if not frappe.db.exists("Item", "T2TEST-CARD-FEE"):
        frappe.get_doc({"doctype": "Item", "item_code": "T2TEST-CARD-FEE", "item_name": "Card fee (t2 test)", "item_group": "Services" if frappe.db.exists("Item Group", "Services") else "Products", "stock_uom": "Nos", "is_stock_item": 0}).insert()
    s = frappe.get_doc("Shop Settings")
    s.payment_gateway_account = "Stripe-_Test Stripe - NZD - TSL"
    s.card_fee_item = "T2TEST-CARD-FEE"
    s.card_fee_percent = 2.5; s.card_fee_fixed = 0.30
    s.card_fee_percent_international = 3.5; s.card_fee_fixed_international = 0.30
    s.transfer_fee_item = "TTEST-TRANSFER-FEE"; s.conversion_fee_item = "TTEST-CONVERSION-FEE"
    s.show_prices_with_tax = 1; s.tax_template = "New Zealand - TSL"
    s.low_stock_threshold = 15
    for r in s.bank_accounts:
        r.swift_transfer_fee = 15 if "USD" in r.bank_account else 25
    s.save()
    if not frappe.db.exists("Shop Collection", {"slug": "pager-test"}):
        frappe.get_doc({"doctype": "Shop Collection", "title": "Pager Test", "slug": "pager-test", "published": 1}).insert()
    coll = frappe.db.get_value("Shop Collection", {"slug": "pager-test"})
    for i, code in enumerate(PAGER, 1):
        if not frappe.db.exists("Item", code):
            frappe.get_doc({"doctype": "Item", "item_code": code, "item_name": f"Pager Test {i}", "item_group": "Products", "stock_uom": "Nos", "is_stock_item": 0}).insert()
        if i != 26 and not frappe.db.exists("Item Price", {"item_code": code, "price_list": "Standard Selling"}):
            frappe.get_doc({"doctype": "Item Price", "item_code": code, "price_list": "Standard Selling", "price_list_rate": 10 + i}).insert()
        if not frappe.db.exists("Shop Product", {"item": code}):
            frappe.get_doc({"doctype": "Shop Product", "item": code, "product_name": f"Pager Test {i}", "published": 1, "collections": [{"collection": coll}]}).insert()
    frappe.db.commit()
    print("setup ok", coll)

def teardown():
    frappe.set_user("Administrator")
    snap = json.load(open("/tmp/t2_settings_snapshot.json"))
    s = frappe.get_doc("Shop Settings")
    for k in ("payment_gateway_account","card_fee_item","card_fee_percent","card_fee_fixed","card_fee_percent_international","card_fee_fixed_international","transfer_fee_item","conversion_fee_item","show_prices_with_tax","tax_template","low_stock_threshold"):
        s.set(k, snap[k])
    old = {r["name"]: r for r in snap["bank_accounts"]}
    for r in s.bank_accounts:
        for k in ("conversion_fee_percent","local_transfer_fee","swift_transfer_fee","label","instructions"):
            r.set(k, old[r.name][k])
    s.save()
    for code in PAGER:
        for p in frappe.get_all("Shop Product", {"item": code}, pluck="name"):
            frappe.delete_doc("Shop Product", p, force=True)
        for p in frappe.get_all("Item Price", {"item_code": code}, pluck="name"):
            frappe.delete_doc("Item Price", p, force=True)
        if frappe.db.exists("Item", code):
            frappe.delete_doc("Item", code, force=True)
    for c in frappe.get_all("Shop Collection", {"slug": "pager-test"}, pluck="name"):
        frappe.delete_doc("Shop Collection", c, force=True)
    used = frappe.db.exists("Sales Order Item", {"item_code": "T2TEST-CARD-FEE"}) or frappe.db.exists("Quotation Item", {"item_code": "T2TEST-CARD-FEE"})
    if frappe.db.exists("Item", "T2TEST-CARD-FEE"):
        if used: frappe.db.set_value("Item", "T2TEST-CARD-FEE", "disabled", 1)
        else: frappe.delete_doc("Item", "T2TEST-CARD-FEE", force=True)
    for code in ("TTEST-TRANSFER-FEE", "TTEST-CONVERSION-FEE"):
        frappe.db.set_value("Item", code, "disabled", 1)
    frappe.db.commit()
    print("teardown ok; card fee item used:", bool(used))

def diff():
    snap = json.load(open("/tmp/t2_settings_snapshot.json"))
    now = json.loads(json.dumps(frappe.get_doc("Shop Settings").as_dict(), default=str))
    skip = {"modified","creation","modified_by"}
    def clean(v):
        if isinstance(v, list): return [{k: x for k, x in r.items() if k not in skip} for r in v]
        return v
    for k in snap:
        if k in skip: continue
        if clean(snap[k]) != clean(now.get(k)): print("DIFF", k, str(snap[k])[:200], "->", str(now.get(k))[:200])
    print("diff done")

def make_dn(so):
    frappe.set_user("Administrator")
    from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note
    dn = make_delivery_note(so)
    dn.insert(); dn.submit(); frappe.db.commit()
    print("DN", dn.name, [(i.item_code, i.qty) for i in dn.items])

def quote_link(q):
    frappe.set_user("Administrator")
    doc = frappe.get_doc("Quotation", q)
    if doc.docstatus == 0:
        doc.submit()
    if not doc.get("shop_access_token"):
        doc.db_set("shop_access_token", frappe.generate_hash(length=32))
    frappe.db.commit()
    from shop.storefront.quotes import quote_url
    print("LINK", quote_url(doc.name, doc.shop_access_token), doc.grand_total, doc.currency, doc.status)

def latest_quotes():
    print(frappe.get_all("Quotation", fields=["name","party_name","docstatus","status","grand_total","creation"], order_by="creation desc", limit=4))

def so_info(so):
    d = frappe.get_doc("Sales Order", so)
    print(d.name, d.customer, d.customer_group, d.currency, d.grand_total, d.rounded_total, d.discount_amount, d.apply_discount_on, d.coupon_code, d.get("shop_payment_method"), [(i.item_code, i.qty, i.rate, i.amount) for i in d.items], [(t.description, t.tax_amount) for t in d.taxes])
    print("cust type", frappe.db.get_value("Customer", d.customer, ["customer_type","customer_group"]))

def ret(so):
    from shop.storefront import returns, orders
    print(returns.summary(so), returns.shipped_date(so), frappe.utils.now_datetime(), orders.shipment_summary(so))

def set_window(days):
    if days in (None, "none", "None"):
        frappe.db.delete("Singles", {"doctype": "Shop Settings", "field": "return_window_days"})
    else:
        frappe.db.set_single_value("Shop Settings", "return_window_days", int(days))
    frappe.db.commit(); frappe.clear_document_cache("Shop Settings", "Shop Settings")
    print("window now", frappe.db.sql("select value from tabSingles where doctype='Shop Settings' and field='return_window_days'"))
