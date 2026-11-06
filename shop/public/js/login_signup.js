// The login page: when accounts are made on the store's sign-up page (the site's own sign-up is
// off so customers accept the terms), point new customers there.
(function () {
	if (window.location.pathname !== "/login") return;
	fetch("/api/method/shop.storefront.accounts.signup_url")
		.then((response) => response.json())
		.then(({ message }) => {
			if (!message || document.querySelector("[data-shop-signup]")) return;
			const form = document.querySelector(".login-content.page-card, .for-login .page-card, .page-card");
			if (!form) return;
			const note = document.createElement("p");
			note.dataset.shopSignup = "1";
			note.className = "text-center sign-up-message";
			note.style.marginTop = "16px";
			note.innerHTML = 'New customer? <a href="' + message + '">Create an account</a>';
			form.appendChild(note);
		})
		.catch(() => {});
})();
