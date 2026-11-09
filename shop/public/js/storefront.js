(function () {
	// Set by Shop when the storefront is served under a route prefix, e.g. "/store".
	const base = window.shop_route_prefix || "";
	// Frappe's translation when the page has it
	const __ = window.__ || ((text) => text);
	const state = {
		product: window.page_data && window.page_data.product,
		selection: {},
		cart: (window.page_data && window.page_data.cart) || null,
	};

	function esc(value) {
		const div = document.createElement("div");
		div.textContent = value == null ? "" : String(value);
		return div.innerHTML;
	}

	const GENERIC_ERROR = __("Something went wrong. Please try again.");

	async function call(method, args) {
		let response;
		try {
			response = await fetch(`/api/method/${method}`, {
				method: "POST",
				headers: {
					"Content-Type": "application/json",
					"X-Frappe-CSRF-Token": (window.frappe && window.frappe.csrf_token) || "",
				},
				body: JSON.stringify(args || {}),
			});
		} catch (e) {
			throw new Error(__("We couldn't reach the store. Check your connection and try again."));
		}
		let body = {};
		try {
			body = await response.json();
		} catch (e) {
			if (!response.ok) throw new Error(GENERIC_ERROR);
		}
		if (!response.ok) throw new Error(serverMessage(body));
		return body.message;
	}

	function serverMessage(body) {
		try {
			const messages = JSON.parse(body._server_messages || "[]");
			if (messages.length) return JSON.parse(messages[0]).message.replace(/<[^>]+>/g, "");
		} catch (e) {}
		return GENERIC_ERROR;
	}

	// the error box of the form in use, else the page's
	function errorBox(scope) {
		return (scope && scope.querySelector('[data-shop="error"]')) || document.querySelector('[data-shop="error"]');
	}

	function showError(message, scope) {
		const target = errorBox(scope);
		if (target) {
			target.textContent = message;
			target.style.display = "block";
			target.scrollIntoView({ block: "nearest", behavior: "smooth" });
		} else {
			alert(message);
		}
	}

	function hideError(scope) {
		const target = errorBox(scope);
		if (target) target.style.display = "none";
	}

	function updateCartCount(count) {
		document.querySelectorAll('[data-shop="cart-count"]').forEach((el) => {
			el.textContent = count;
			el.dataset.empty = count ? "false" : "true";
		});
	}

	async function refreshCartCount() {
		if (!document.querySelector('[data-shop="cart-count"]')) return;
		if (window.page_data && window.page_data.cart) {
			updateCartCount(window.page_data.cart.item_count || 0);
			return;
		}
		try {
			const cart = await call("shop.storefront.cart.get_cart");
			updateCartCount(cart.item_count || 0);
		} catch (e) {}
	}

	// how many the shopper chose on the product page; null when it isn't a quantity
	function chosenQty() {
		const input = document.querySelector('[data-shop="pdp-qty"]');
		if (!input) return 1;
		const qty = parseFloat(input.value);
		if (qty > 0) return qty;
		showError(__("Enter how many you want."));
		input.focus();
		return null;
	}

	async function addToCart(button) {
		const itemCode = button.dataset.itemCode;
		const qty = chosenQty();
		if (!itemCode || qty === null) return;
		button.disabled = true;
		const label = button.textContent;
		try {
			const cart = await call("shop.storefront.cart.add_item", { item_code: itemCode, qty: qty });
			updateCartCount(cart.item_count || 0);
			if (drawerElement()) {
				renderDrawer(cart);
				openDrawer();
			} else {
				button.textContent = button.dataset.addedLabel || "Added";
				setTimeout(() => (button.textContent = label), 1500);
			}
		} catch (error) {
			showError(error.message);
		} finally {
			button.disabled = false;
		}
	}

	function drawerElement() {
		return document.querySelector('[data-shop="cart-drawer"]');
	}

	function openDrawer() {
		const drawer = drawerElement();
		if (!drawer) return false;
		drawer.dataset.open = "true";
		document.documentElement.style.overflow = "hidden";
		return true;
	}

	function closeDrawer() {
		const drawer = drawerElement();
		if (!drawer) return;
		drawer.dataset.open = "false";
		document.documentElement.style.overflow = "";
	}

	async function toggleDrawer() {
		try {
			renderDrawer(await call("shop.storefront.cart.get_cart"));
			openDrawer();
		} catch (error) {
			window.location.href = base + "/cart";
		}
	}

	function renderDrawer(cart) {
		state.cart = cart;
		updateCartCount(cart.item_count || 0);
		const drawer = drawerElement();
		if (!drawer) return;
		const total = drawer.querySelector('[data-shop="drawer-total"]');
		if (total) total.textContent = cart.formatted_total || "";
		const list = drawer.querySelector('[data-shop="drawer-items"]');
		if (!list) return;
		if (!cart.items.length) {
			list.innerHTML = '<p class="drawer-empty">Your cart is empty.</p>';
			return;
		}
		list.innerHTML = cart.items
			.map(
				(item) => `
			<div class="drawer-item">
				<img class="drawer-thumb" src="${esc(item.image || "")}" alt="" loading="lazy">
				<div class="drawer-info">
					<a class="drawer-name" href="${base}/product/${esc(item.slug || "")}">${esc(item.product_name || item.item_code)}</a>
					<span class="drawer-rate">${esc(item.formatted_rate || "")}</span>
					<div class="drawer-qty">
						<button type="button" data-drawer-step="-1" data-item-code="${esc(item.item_code)}" aria-label="Decrease quantity">&minus;</button>
						<input type="number" data-drawer-qty data-item-code="${esc(item.item_code)}" value="${esc(item.qty)}" min="0" step="any" inputmode="decimal" aria-label="${esc(__("Quantity"))}">
						<button type="button" data-drawer-step="1" data-item-code="${esc(item.item_code)}" aria-label="Increase quantity">+</button>
						<button type="button" class="drawer-remove" data-drawer-step="0" data-item-code="${esc(item.item_code)}">Remove</button>
					</div>
				</div>
				<span class="drawer-amount">${esc(item.formatted_amount || "")}</span>
			</div>`
			)
			.join("");
	}

	async function drawerStep(itemCode, step) {
		const row = state.cart && state.cart.items.find((item) => item.item_code === itemCode);
		await drawerQty(itemCode, step === 0 ? 0 : Math.max((row ? row.qty : 1) + step, 0));
	}

	async function drawerQty(itemCode, qty) {
		try {
			const cart = await call("shop.storefront.cart.set_qty", { item_code: itemCode, qty: qty });
			renderDrawer(cart);
			cartChanged();
		} catch (error) {
			if (state.cart) renderDrawer(state.cart);
			showError(error.message);
		}
	}

	// on the checkout page a changed cart has a new total
	function cartChanged() {
		if (!checkoutForm()) return;
		checkout.quote = null;
		scheduleQuote(0);
	}

	async function setQty(itemCode, qty) {
		try {
			await call("shop.storefront.cart.set_qty", { item_code: itemCode, qty: qty });
			window.location.reload();
			return true;
		} catch (error) {
			showError(error.message);
			return false;
		}
	}

	// a quantity typed in the cart or the drawer: none removes the line; what isn't a quantity is put back
	function typedQty(input, current) {
		const qty = input.value.trim() === "" ? NaN : parseFloat(input.value);
		if (qty >= 0) return qty;
		input.value = current;
		showError(__("Enter how many you want."));
		return null;
	}

	function rowQty(itemCode) {
		const row = window.page_data && window.page_data.cart
			? window.page_data.cart.items.find((item) => item.item_code === itemCode)
			: null;
		return row ? row.qty : 1;
	}

	function mainImage() {
		return document.querySelector('[data-shop="main-image"]');
	}

	function showImage(src) {
		const target = mainImage();
		if (!target || !src || target.getAttribute("src") === src) return;
		target.setAttribute("src", src);
		document.querySelectorAll('[data-shop="thumb"]').forEach((thumb) => {
			thumb.dataset.selected = thumb.dataset.image === src ? "true" : "false";
		});
	}

	function initGallery() {
		const first = document.querySelector('[data-shop="thumb"]');
		if (first) first.dataset.selected = "true";
	}

	function syncAvailability(item) {
		const buttons = document.querySelectorAll('[data-shop="add-to-cart"], [data-shop="buy-now"]');
		buttons.forEach((button) => {
			if (item.item_code) button.dataset.itemCode = item.item_code;
			button.disabled = !item.purchasable;
			if (!item.purchasable) button.textContent = item.stock_status || button.dataset.outOfStockLabel;
			else if (button.dataset.label) button.textContent = button.dataset.label;
		});
		document.querySelectorAll('[data-shop="pdp-qty"]').forEach((input) => {
			input.disabled = !item.purchasable;
		});
		document.querySelectorAll('[data-shop="stock-status"]').forEach((line) => {
			line.dataset.stockState = item.stock_state || "";
			const text = line.querySelector('[data-shop="stock-status-text"]');
			if (text) text.textContent = item.stock_status || "";
		});
		// stock at each location the store shows (ready-made, escaped, by the server)
		document.querySelectorAll('[data-shop="stock-locations"]').forEach((list) => {
			list.innerHTML = item.stock_locations_html || "";
			list.hidden = !item.stock_locations_html;
		});
		// where the variant can be sent (escaped by the server)
		document.querySelectorAll('[data-shop="shipping-note"]').forEach((note) => {
			note.innerHTML = item.shipping_note || "";
			note.hidden = !item.shipping_note;
		});
	}

	function initVariantPicker() {
		const product = state.product;
		if (product && !product.has_variants && product.stock_status) syncAvailability(product);
		if (!product || !product.has_variants) return;
		const defaultVariant = product.variants.find(
			(variant) => variant.item_code === product.default_item_code
		);
		if (defaultVariant) state.selection = Object.assign({}, defaultVariant.attributes);
		syncVariantUI();
	}

	function selectOption(button) {
		state.selection[button.dataset.attribute] = button.dataset.value;
		syncVariantUI();
	}

	function selectedVariant() {
		const product = state.product;
		if (!product) return null;
		return product.variants.find((variant) =>
			Object.entries(variant.attributes).every(
				([attribute, value]) => state.selection[attribute] === value
			)
		);
	}

	// whether picking this value, with the other options as they are, gives a variant that can be bought
	function optionAvailable(attribute, value) {
		const wanted = Object.assign({}, state.selection, { [attribute]: value });
		return state.product.variants.some(
			(variant) =>
				variant.purchasable &&
				Object.entries(variant.attributes).every(([name, picked]) => wanted[name] === picked)
		);
	}

	function syncVariantUI() {
		document.querySelectorAll('[data-shop="variant-option"]').forEach((button) => {
			const selected = state.selection[button.dataset.attribute] === button.dataset.value;
			button.dataset.selected = selected ? "true" : "false";
			button.setAttribute("aria-pressed", selected ? "true" : "false");
			button.dataset.available = optionAvailable(button.dataset.attribute, button.dataset.value) ? "true" : "false";
		});
		const variant = selectedVariant();
		const prices = document.querySelectorAll(
			'[data-shop="pdp-price"], [data-shop="pdp-price-note"], [data-shop="pdp-compare-at"]'
		);
		if (!variant) {
			// a combination the store doesn't sell: nothing of another variant stays on the page
			syncAvailability({
				purchasable: false,
				stock_status: state.product.unavailable_label,
				stock_state: "unavailable",
			});
			prices.forEach((price) => (price.hidden = true));
			syncSavings({});
			return;
		}
		prices.forEach((price) => (price.hidden = false));
		syncAvailability(variant);
		if (variant.formatted_price) {
			document.querySelectorAll('[data-shop="pdp-price"]').forEach((price) => {
				price.textContent = variant.formatted_price;
			});
			document.querySelectorAll('[data-shop="pdp-price-note"]').forEach((note) => {
				note.textContent = variant.price_note || "";
			});
		}
		syncSavings(variant);
		if (variant.image) showImage(variant.image);
	}

	function syncSavings(variant) {
		const savings = document.querySelector('[data-shop="pdp-savings"]');
		if (savings) {
			savings.hidden = !variant.formatted_savings;
			const amount = savings.querySelectorAll("span")[1];
			if (amount && variant.formatted_savings) amount.textContent = variant.formatted_savings;
		}
		const badge = document.querySelector('[data-shop="pdp-discount"]');
		if (badge) {
			badge.hidden = !variant.discount_pct;
			const pct = badge.querySelector("span");
			if (pct && variant.discount_pct) pct.textContent = variant.discount_pct;
		}
	}

	async function buyNow(button) {
		const itemCode = button.dataset.itemCode;
		const qty = chosenQty();
		if (!itemCode || qty === null) return;
		button.disabled = true;
		try {
			await call("shop.storefront.cart.add_item", { item_code: itemCode, qty: qty });
			window.location.href = base + "/checkout";
		} catch (error) {
			showError(error.message);
			button.disabled = false;
		}
	}

	// ---- checkout -------------------------------------------------------------
	// A theme opts in by placing these hooks in the checkout form:
	// [data-shop="shipping-options"], [data-shop="checkout-totals"], [data-shop="bank-currency"],
	// [data-shop="payment-fee"][data-method], and inputs named accept_terms / business_buyer.
	const checkout = { quote: null, timer: null, seq: 0, submitting: false };
	const ADDRESS_FIELDS = ["address_line1", "address_line2", "city", "state", "country", "pincode"];

	function checkoutForm() {
		return document.querySelector('[data-shop="checkout-form"]');
	}

	function formAddress(form) {
		const data = new FormData(form);
		const address = {};
		ADDRESS_FIELDS.forEach((field) => (address[field] = (data.get(field) || "").trim()));
		return address;
	}

	// one service, or one per parcel when the order is sent from more than one warehouse
	function shippingChoice(form) {
		const picks = [...form.querySelectorAll('input[name^="shipping_option:"]:checked')];
		if (picks.length) {
			return JSON.stringify(Object.fromEntries(picks.map((input) => [input.name.slice("shipping_option:".length), input.value])));
		}
		const picked = new FormData(form).get("shipping_option");
		if (picked) return picked;
		// a choice restored before the quote has drawn its services
		const held = Object.entries(checkout.heldChoices || {});
		const parcels = held.filter(([name]) => name.startsWith("shipping_option:"));
		if (parcels.length) {
			return JSON.stringify(Object.fromEntries(parcels.map(([name, value]) => [name.slice("shipping_option:".length), value])));
		}
		return (checkout.heldChoices || {}).shipping_option || undefined;
	}

	// check the held choices among the services the quote drew, once
	function applyHeldChoices(form) {
		Object.entries(checkout.heldChoices || {}).forEach(([name, value]) => {
			const radio = [...form.querySelectorAll(`input[type="radio"][name="${CSS.escape(name)}"]`)].find(
				(field) => field.value === value
			);
			if (radio) {
				radio.checked = true;
				delete checkout.heldChoices[name];
			}
		});
	}

	function shippingOptionsHtml(options, name) {
		return options
			.map(
				(option) => `<label class="checkout-option"><input type="radio" name="${esc(name)}" value="${esc(option.id)}"${option.selected ? " checked" : ""}>
						<span class="checkout-option-label">${esc(option.label)}${option.eta ? ` <small>${esc(option.eta)}</small>` : ""}</span>
						<span class="checkout-option-price">${esc(option.formatted)}</span></label>`
			)
			.join("");
	}

	function checkoutChoice(form) {
		const data = new FormData(form);
		const currency = form.querySelector('select[name="currency"]');
		const method = data.get("payment_method") || undefined;
		return {
			shipping_option: shippingChoice(form),
			// an international transfer is chosen under bank transfer, as where the money comes from
			payment_method: method === "bank" && data.get("bank_route") ? data.get("bank_route") : method,
			// until the shopper picks one, the store suggests their own currency
			currency: currency && currency.dataset.chosen ? data.get("currency") : undefined,
		};
	}

	function scheduleQuote(delay) {
		const form = checkoutForm();
		if (!form || !document.querySelector('[data-shop="checkout-totals"]')) return;
		// what changed may change the total: an order isn't placed at the old one
		checkout.quote = null;
		clearTimeout(checkout.timer);
		checkout.timer = setTimeout(() => requestQuote(form), delay == null ? 450 : delay);
	}

	async function requestQuote(form) {
		const seq = ++checkout.seq;
		const totalsBox = document.querySelector('[data-shop="checkout-totals"]');
		totalsBox && totalsBox.setAttribute("aria-busy", "true");
		try {
			const quote = await call("shop.storefront.checkout.quote", { address: formAddress(form), ...checkoutChoice(form) });
			if (seq !== checkout.seq) return null;
			checkout.quote = quote;
			hideError(form);
			renderQuote(form, quote);
			return quote;
		} catch (error) {
			if (seq === checkout.seq) {
				checkout.quote = null;
				showError(error.message, form);
			}
			return null;
		} finally {
			totalsBox && totalsBox.removeAttribute("aria-busy");
		}
	}

	function renderQuote(form, quote) {
		const options = form.querySelector('[data-shop="shipping-options"]');
		if (options) {
			const hints = (quote.shipping_messages || []).map((m) => `<p class="checkout-hint">${esc(m)}</p>`).join("");
			let html;
			if (quote.shipping_pending) {
				html = `<p class="checkout-hint">${esc(__("Enter your address to see shipping options."))}</p>`;
			} else if ((quote.shipping_groups || []).length) {
				html =
					quote.shipping_groups
						.map(
							(group) => `<div class="checkout-parcel"><p class="checkout-hint"><strong>${esc(group.label)}</strong> · ${esc(group.items)}</p>
							${group.options.length ? shippingOptionsHtml(group.options, `shipping_option:${group.key}`) : `<div class="checkout-warning" role="alert"><p>${esc(__("We can't send this parcel to this address."))}</p></div>`}</div>`
						)
						.join("") + hints;
			} else if (!quote.shipping_options.length) {
				const reasons = (quote.shipping_messages || []).map((m) => `<p>${esc(m)}</p>`).join("");
				html = `<div class="checkout-warning" role="alert">${reasons || `<p>${esc(__("We can't ship to this address. Check it, or contact us."))}</p>`}</div>`;
			} else {
				html = shippingOptionsHtml(quote.shipping_options, "shipping_option") + hints;
			}
			// drawn again only when it changed, and the shopper keeps their place in it
			if (options.dataset.rendered !== html) {
				const focused = options.contains(document.activeElement) ? document.activeElement : null;
				options.innerHTML = html;
				options.dataset.rendered = html;
				if (focused && focused.name) {
					const again = [...options.querySelectorAll(`[name="${CSS.escape(focused.name)}"]`)].find(
						(field) => field.value === focused.value
					);
					if (again) again.focus();
				}
			}
			applyHeldChoices(form);
		}
		form.querySelectorAll('[data-shop="payment-fee"]').forEach((note) => {
			const method = (quote.payment_methods || []).find((m) => m.method === note.dataset.method);
			note.textContent = (method && method.fee_note) || "";
		});
		const bank = form.querySelector('[data-shop="bank-currency"]');
		if (bank) {
			const byBank = ["bank", "swift"].includes(quote.payment_method);
			const currencies = quote.bank_currencies || [];
			const routes = quote.bank_routes || [];
			const select = bank.querySelector('select[name="currency"]');
			if (select) {
				const options = currencies.map((c) => `<option value="${esc(c.currency)}">${esc(c.label)}</option>`).join("");
				if (select.dataset.options !== options) {
					select.innerHTML = options;
					select.dataset.options = options;
				}
				if (quote.currency && byBank) select.value = quote.currency;
			}
			const currencyChoice = bank.querySelector('[data-shop="bank-currency-choice"]');
			if (currencyChoice) currencyChoice.hidden = currencies.length < 2;
			const route = bank.querySelector('select[name="bank_route"]');
			if (route) {
				route.innerHTML = routes.map((r) => `<option value="${esc(r.method)}">${esc(r.label)}</option>`).join("");
				route.value = quote.payment_method;
				// until the shopper says where they pay from, go by where the order is going
				const suggested = quote.suggested_bank_method;
				if (byBank && !route.dataset.chosen && suggested && suggested !== quote.payment_method && routes.some((r) => r.method === suggested)) {
					route.value = suggested;
					scheduleQuote(0);
				}
			}
			const routeChoice = bank.querySelector('[data-shop="bank-route"]');
			if (routeChoice) routeChoice.hidden = routes.length < 2;
			bank.hidden = !byBank || (currencies.length < 2 && routes.length < 2);
		}
		(quote.items || []).forEach((item) => {
			document
				.querySelectorAll(`[data-shop="line-amount"][data-item-code="${CSS.escape(item.item_code)}"]`)
				.forEach((amount) => (amount.textContent = item.formatted_amount));
		});
		const totalsBox = document.querySelector('[data-shop="checkout-totals"]');
		if (totalsBox) {
			const lines = quote.lines
				.map((line) => `<div class="checkout-line" data-kind="${esc(line.kind)}"><span>${esc(line.label)}</span><span>${esc(line.formatted)}</span></div>`)
				.join("");
			const notes = (quote.notes || []).map((note) => `<p class="checkout-note">${esc(note)}</p>`).join("");
			totalsBox.innerHTML = `${lines}<div class="checkout-line checkout-total"><span>Total</span><span data-shop="checkout-total">${esc(quote.formatted_total)} ${esc(quote.currency)}</span></div>${notes}`;
		}
		const submit = form.querySelector('[type="submit"]');
		if (submit) submit.disabled = !quote.can_ship || checkout.submitting;
		syncPaymentUI();
	}

	async function submitCheckout(form) {
		if (checkout.submitting) return;
		const data = new FormData(form);
		const submit = form.querySelector('[type="submit"]');
		const terms = form.querySelector('[name="accept_terms"]');
		if (terms && !terms.checked) {
			showError(__("Please accept the terms and conditions."), form);
			terms.focus();
			return;
		}
		clearTimeout(checkout.timer);
		checkout.submitting = true;
		if (submit) submit.disabled = true;
		// the order is placed at the total the shopper is shown, worked out now if it isn't yet
		const quote = checkout.quote || (await requestQuote(form));
		checkout.seq++;
		if (!quote) {
			checkout.submitting = false;
			if (submit) submit.disabled = false;
			return;
		}
		try {
			const result = await call("shop.storefront.checkout.place_order", {
				customer: {
					email: data.get("email"),
					full_name: data.get("full_name"),
					phone: data.get("phone"),
				},
				address: formAddress(form),
				...checkoutChoice(form),
				accept_terms: terms && terms.checked ? 1 : 0,
				business_buyer: data.get("business_buyer") ? 1 : 0,
				expected_total: quote.total,
			});
			window.location.href = result.payment_url || result.confirmation_url;
		} catch (error) {
			checkout.submitting = false;
			showError(error.message, form);
			if (submit) submit.disabled = false;
			scheduleQuote(0);
		}
	}

	async function submitAccountForm(form, method, collect) {
		const submit = form.querySelector('[type="submit"]');
		const done = form.parentElement.querySelector('[data-shop="form-done"]');
		if (submit) submit.disabled = true;
		try {
			const result = await call(method, collect(new FormData(form)));
			form.hidden = true;
			if (done) {
				done.textContent = result.message;
				done.hidden = false;
			}
		} catch (error) {
			showError(error.message, form);
			if (submit) submit.disabled = false;
		}
	}

	async function acceptQuote(form) {
		const data = new FormData(form);
		const submit = form.querySelector('[type="submit"]');
		if (submit) submit.disabled = true;
		try {
			const result = await call("shop.storefront.quotes.accept_quote", {
				name: form.dataset.quote,
				token: form.dataset.token,
				payment_method: data.get("payment_method"),
				accept_terms: data.get("accept_terms") ? 1 : 0,
				business_buyer: data.get("business_buyer") ? 1 : 0,
			});
			window.location.href = result.redirect;
		} catch (error) {
			showError(error.message, form);
			if (submit) submit.disabled = false;
		}
	}

	async function submitQuoteRequest(button) {
		const form = checkoutForm();
		if (!form) return;
		// a quote needs contact and address details, not the terms (those come with the order)
		const missing = [...form.querySelectorAll("[required]")].find(
			(input) => input.name !== "accept_terms" && !input.checkValidity()
		);
		if (missing) {
			missing.reportValidity();
			return;
		}
		const data = new FormData(form);
		button.disabled = true;
		try {
			const result = await call("shop.storefront.quotes.request_quote", {
				customer: { email: data.get("email"), full_name: data.get("full_name"), phone: data.get("phone") },
				address: formAddress(form),
				shipping_option: shippingChoice(form),
				message: data.get("quote_message") || "",
			});
			const done = document.querySelector('[data-shop="quote-done"]');
			if (done) {
				done.textContent = result.message + " (" + result.quote + ")";
				done.hidden = false;
			}
			button.hidden = true;
		} catch (error) {
			showError(error.message);
			button.disabled = false;
		}
	}

	function initCheckout() {
		const form = checkoutForm();
		if (!form) return;
		const country = form.querySelector('select[name="country"]');
		if (country) {
			const pageData = window.page_data || {};
			const wanted = (pageData.prefill && pageData.prefill.country) || pageData.default_country;
			if (wanted) country.value = wanted;
		}
		restoreCheckoutDraft(form);
		form.addEventListener("input", (event) => {
			if (ADDRESS_FIELDS.includes(event.target.name)) scheduleQuote();
		});
		form.addEventListener("change", (event) => {
			const name = event.target.name;
			if (name === "currency" || name === "bank_route") event.target.dataset.chosen = "1";
			if (["shipping_option", "payment_method", "currency", "bank_route", "country"].includes(name) || name.startsWith("shipping_option:")) scheduleQuote(0);
		});
		scheduleQuote(0);
	}

	document.addEventListener("click", (event) => {
		const stepper = event.target.closest("[data-drawer-step]");
		if (stepper) {
			drawerStep(stepper.dataset.itemCode, parseInt(stepper.dataset.drawerStep, 10));
			return;
		}
		const target = event.target.closest("[data-shop]");
		if (!target) return;
		const action = target.dataset.shop;
		if (action === "cart-toggle" && drawerElement()) {
			event.preventDefault();
			toggleDrawer();
		} else if (action === "drawer-close" || action === "drawer-backdrop") closeDrawer();
		else if (action === "coupon-remove") removeCoupon();
		else if (action === "sign-out") {
			event.preventDefault();
			signOut();
		}
		else if (action === "add-to-cart") addToCart(target);
		else if (action === "buy-now") buyNow(target);
		else if (action === "thumb") showImage(target.dataset.image);
		else if (action === "rating-star") selectRating(parseInt(target.dataset.value, 10));
		else if (action === "variant-option") selectOption(target);
		else if (action === "qty-inc") setQty(target.dataset.itemCode, rowQty(target.dataset.itemCode) + 1);
		else if (action === "qty-dec") setQty(target.dataset.itemCode, rowQty(target.dataset.itemCode) - 1);
		else if (action === "remove") setQty(target.dataset.itemCode, 0);
		else if (action === "request-quote") submitQuoteRequest(target);
		else if (action === "forgot-password") forgotPassword(target.closest("form"));
	});

	document.addEventListener("keydown", (event) => {
		if (event.key === "Escape") closeDrawer();
	});

	document.addEventListener("change", async (event) => {
		const input = event.target;
		if (input.matches('[data-shop="qty-input"]')) {
			const current = rowQty(input.dataset.itemCode);
			const qty = typedQty(input, current);
			if (qty !== null && !(await setQty(input.dataset.itemCode, qty))) input.value = current;
		} else if (input.matches("[data-drawer-qty]")) {
			const row = state.cart && state.cart.items.find((item) => item.item_code === input.dataset.itemCode);
			const qty = typedQty(input, row ? row.qty : 1);
			if (qty !== null) drawerQty(input.dataset.itemCode, qty);
		}
	});

	document.addEventListener("submit", (event) => {
		const form = event.target.closest("[data-shop]");
		if (!form) return;
		if (form.dataset.shop === "checkout-form") {
			event.preventDefault();
			submitCheckout(form);
		} else if (form.dataset.shop === "signin-form") {
			event.preventDefault();
			signIn(form);
		} else if (form.dataset.shop === "register-form") {
			event.preventDefault();
			submitAccountForm(form, "shop.storefront.accounts.register", (data) => ({
				full_name: data.get("full_name"),
				email: data.get("email"),
				accept_terms: data.get("accept_terms") ? 1 : 0,
			}));
		} else if (form.dataset.shop === "trade-form") {
			event.preventDefault();
			submitAccountForm(form, "shop.storefront.accounts.apply_trade", (data) => ({
				application: Object.fromEntries(
					["company_name", "company_number", "tax_id", "industry", "website", "country", "message",
					 "contact_name", "email", "phone", "address_line1", "address_line2", "city", "state", "pincode"]
						.map((key) => [key, data.get(key) || ""])
				),
				accept_terms: data.get("accept_terms") ? 1 : 0,
			}));
		} else if (form.dataset.shop === "quote-form") {
			event.preventDefault();
			acceptQuote(form);
		} else if (form.dataset.shop === "review-form") {
			event.preventDefault();
			submitReview(form);
		} else if (form.dataset.shop === "coupon-form") {
			event.preventDefault();
			applyCoupon(form);
		} else if (form.dataset.shop === "return-form") {
			event.preventDefault();
			submitReturn(form);
		} else if (form.dataset.shop === "search-form") {
			event.preventDefault();
			const term = form.querySelector('[name="search"]');
			window.location.href = base + "/products?search=" + encodeURIComponent(term ? term.value : "");
		}
	});

	function loggedIn() {
		const match = document.cookie.match(/(?:^|; )user_id=([^;]*)/);
		return !!match && decodeURIComponent(match[1]) !== "Guest";
	}

	function initReviewForm() {
		const form = document.querySelector('[data-shop="review-form"]');
		const prompt = document.querySelector('[data-shop="review-signin"]');
		if (!form) return;
		if (loggedIn()) {
			form.style.display = "flex";
			if (prompt) prompt.style.display = "none";
		} else if (prompt) {
			prompt.setAttribute(
				"href",
				base + "/account/signin?redirect-to=" + encodeURIComponent(window.location.pathname)
			);
		}
	}

	function selectRating(value) {
		state.reviewRating = value;
		document.querySelectorAll('[data-shop="rating-star"]').forEach((star) => {
			star.dataset.selected = parseInt(star.dataset.value, 10) <= value ? "true" : "false";
		});
	}

	async function submitReview(form) {
		if (!state.reviewRating) {
			showError(__("Pick a star rating first."), form);
			return;
		}
		const data = new FormData(form);
		const submit = form.querySelector('[type="submit"]');
		if (submit) submit.disabled = true;
		try {
			await call("shop.storefront.reviews.add_review", {
				product: window.page_data.product.name,
				rating: state.reviewRating,
				title: data.get("title"),
				review: data.get("review"),
			});
			const thanks = document.createElement("p");
			thanks.className = "form-done";
			thanks.textContent = __("Thank you for your review.");
			form.replaceWith(thanks);
		} catch (error) {
			showError(error.message, form);
			if (submit) submit.disabled = false;
		}
	}

	// what the shopper typed at checkout is kept over the reload a coupon change makes
	const DRAFT_KEY = "shop_checkout_draft";

	function saveCheckoutDraft() {
		const form = checkoutForm();
		if (!form) return;
		const draft = {};
		form.querySelectorAll("input[name], select[name], textarea[name]").forEach((field) => {
			if (field.type === "radio" || field.type === "checkbox") {
				if (field.checked) draft[field.name] = field.type === "checkbox" ? true : field.value;
			} else if (field.value) draft[field.name] = field.value;
		});
		try {
			sessionStorage.setItem(DRAFT_KEY, JSON.stringify(draft));
		} catch (error) {
			// private windows may refuse storage; the shopper retypes
		}
	}

	function restoreCheckoutDraft(form) {
		let draft = null;
		try {
			draft = JSON.parse(sessionStorage.getItem(DRAFT_KEY) || "null");
			sessionStorage.removeItem(DRAFT_KEY);
		} catch (error) {
			return;
		}
		if (!draft) return;
		checkout.heldChoices = {};
		Object.entries(draft).forEach(([name, value]) => {
			const fields = form.querySelectorAll(`[name="${CSS.escape(name)}"]`);
			// services are drawn by the first quote: their choice waits for it
			if (!fields.length && name.startsWith("shipping_option")) checkout.heldChoices[name] = value;
			fields.forEach((field) => {
				if (field.type === "radio") field.checked = field.value === value;
				else if (field.type === "checkbox") field.checked = !!value;
				else if (field.tagName === "SELECT" && !field.options.length) {
					// filled from the quote: hold the choice until the quote fills it
					field.innerHTML = `<option value="${esc(value)}"></option>`;
					field.value = value;
				} else field.value = value;
				if (name === "currency" || name === "bank_route") field.dataset.chosen = "1";
			});
		});
	}

	async function applyCoupon(form) {
		const input = form.querySelector('[name="code"]');
		if (!input || !input.value.trim()) return;
		const submit = form.querySelector('[type="submit"]');
		if (submit) submit.disabled = true;
		try {
			await call("shop.storefront.cart.apply_coupon", { code: input.value.trim() });
			saveCheckoutDraft();
			window.location.reload();
		} catch (error) {
			showError(error.message);
			if (submit) submit.disabled = false;
		}
	}

	// Frappe's own login and password reset, from the store's sign-in page
	async function signIn(form) {
		const data = new FormData(form);
		const submit = form.querySelector('[type="submit"]');
		const next = form.dataset.redirect || base + "/account/orders";
		hideError(form);
		if (submit) submit.disabled = true;
		let result;
		try {
			result = await call("login", { usr: data.get("email"), pwd: data.get("password") });
		} catch (error) {
			showError(__("That email and password don't match an account. Check them, or reset your password."), form);
			if (submit) submit.disabled = false;
			return;
		}
		// two-factor sign-in needs Frappe's own page
		if (result === undefined || (result && result.verification)) {
			window.location.href = "/login?redirect-to=" + encodeURIComponent(next);
			return;
		}
		window.location.href = next;
	}

	async function forgotPassword(form) {
		const email = form && form.querySelector('[name="email"]');
		if (!email || !email.value || !email.checkValidity()) {
			showError(__("Enter your email address first, then choose Forgot your password."), form);
			if (email) email.focus();
			return;
		}
		hideError(form);
		const done = form.parentElement.querySelector('[data-shop="form-done"]');
		try {
			await call("frappe.core.doctype.user.user.reset_password", { user: email.value });
			if (done) {
				done.textContent = __("If {0} has an account, we've emailed it a link to set a new password.").replace("{0}", email.value);
				done.hidden = false;
			}
		} catch (error) {
			showError(error.message, form);
		}
	}

	async function signOut() {
		try {
			await call("logout", {});
		} finally {
			window.location.href = base || "/";
		}
	}

	async function removeCoupon() {
		try {
			await call("shop.storefront.cart.remove_coupon", {});
			saveCheckoutDraft();
			window.location.reload();
		} catch (error) {
			showError(error.message);
		}
	}

	function applySavedAddress(picker) {
		const addresses = (window.page_data && window.page_data.addresses) || [];
		const chosen = addresses.find((address) => address.name === picker.value);
		ADDRESS_FIELDS.forEach((field) => {
			const input = document.querySelector(`[data-shop="checkout-form"] [name="${field}"]`);
			// a new address keeps the country the shopper is in
			if (!input || (!chosen && field === "country")) return;
			input.value = (chosen && chosen[field]) || "";
		});
	}

	function syncPaymentUI() {
		const chosen = document.querySelector('input[name="payment_method"]:checked');
		const submit = document.querySelector('[data-shop="checkout-form"] [type="submit"]');
		const online = chosen && (chosen.value === "gateway" || chosen.value.startsWith("gateway:"));
		if (submit && chosen) {
			// the theme's own button wording, when it sets one
			submit.dataset.label = submit.dataset.label || submit.textContent;
			const label = online ? submit.dataset.payLabel || __("Continue to payment") : submit.dataset.label;
			submit.textContent = label;
		}
		const note = document.querySelector('[data-shop="gateway-note"]');
		if (note) note.hidden = !online;
	}

	function preselectPayment() {
		const radios = document.querySelectorAll(
			'[data-shop="checkout-form"] input[name="payment_method"]'
		);
		if (radios.length && ![...radios].some((radio) => radio.checked)) radios[0].checked = true;
	}

	async function submitReturn(form) {
		const data = new FormData(form);
		const submit = form.querySelector('[type="submit"]');
		if (submit) submit.disabled = true;
		try {
			await call("shop.storefront.returns.create_request", {
				order: window.location.pathname.split("/").filter(Boolean).pop(),
				token: new URLSearchParams(window.location.search).get("token") || undefined,
				item_code: data.get("item_code"),
				request_type: data.get("request_type"),
				qty: data.get("qty") || 1,
				reason: data.get("reason"),
			});
			window.location.reload();
		} catch (error) {
			showError(error.message, form);
			if (submit) submit.disabled = false;
		}
	}

	function initFilters() {
		const toggle = document.querySelector('[data-shop="filter-toggle"]');
		const panel = document.querySelector('[data-shop="filter-panel"]');
		if (!toggle || !panel) return;
		// applying a filter reloads the page, so the panel folds away on its own
		toggle.addEventListener("click", () => {
			const open = panel.dataset.open !== "true";
			panel.dataset.open = open ? "true" : "false";
			toggle.setAttribute("aria-expanded", open ? "true" : "false");
		});
	}

	function initBuyBar() {
		const bar = document.querySelector(".pdp-buybar");
		const anchor = document.querySelector('[data-shop="add-to-cart"]');
		if (!bar || !anchor || !("IntersectionObserver" in window)) return;
		new IntersectionObserver(
			(entries) => {
				bar.dataset.visible = entries[0].isIntersecting ? "false" : "true";
			},
			{ rootMargin: "-60px 0px 0px 0px" }
		).observe(anchor);
	}

	function initCurrencyPicker() {
		// the shopper picks the currency prices are shown in; the page is redrawn with it
		document.querySelectorAll('[data-shop="currency-picker"]').forEach((select) =>
			select.addEventListener("change", async () => {
				select.disabled = true;
				try {
					await call("shop.storefront.pricing.set_currency", { currency: select.value });
					window.location.reload();
				} catch (error) {
					select.disabled = false;
					showError(error.message);
				}
			})
		);
	}

	function initBuyingFor() {
		// a login linked to several customers picks which one it buys for; prices and terms follow
		document.addEventListener("change", async (event) => {
			const select = event.target.closest('select[data-shop="buying-for"]');
			if (!select) return;
			select.disabled = true;
			try {
				await call("shop.storefront.pricing.set_buying_for", { customer: select.value });
				window.location.reload();
			} catch (error) {
				select.disabled = false;
				showError(error.message);
			}
		});
	}

	function init() {
		initCurrencyPicker();
		initBuyingFor();
		initGallery();
		initVariantPicker();
		refreshCartCount();
		initReviewForm();
		preselectPayment();
		syncPaymentUI();
		initBuyBar();
		initFilters();
		document
			.querySelectorAll('[data-shop="checkout-form"] input[name="payment_method"]')
			.forEach((radio) => radio.addEventListener("change", syncPaymentUI));
		const picker = document.querySelector('[data-shop="address-picker"]');
		if (picker)
			picker.addEventListener("change", () => {
				applySavedAddress(picker);
				scheduleQuote(0);
			});
		initCheckout();
		window.shop_storefront_ready = true;
	}

	// the script may run after the page has loaded (deferred, or added after load)
	if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
	else init();
})();
