/**
 * Able Home Healthcare — public site behaviour.
 * Kept deliberately small: mobile navigation, accessible form validation
 * messaging, and dismissal of server-rendered status messages.
 */
(function () {
    "use strict";

    var navToggle = document.querySelector("[data-nav-toggle]");
    var nav = document.getElementById("site-nav");

    function setNav(open) {
        if (!nav || !navToggle) {
            return;
        }
        nav.classList.toggle("is-open", open);
        navToggle.setAttribute("aria-expanded", String(open));
    }

    if (navToggle && nav) {
        navToggle.addEventListener("click", function () {
            setNav(navToggle.getAttribute("aria-expanded") !== "true");
        });

        nav.addEventListener("click", function (event) {
            if (event.target.closest("a") && window.matchMedia("(max-width: 900px)").matches) {
                setNav(false);
            }
        });

        document.addEventListener("keydown", function (event) {
            if (event.key === "Escape" && navToggle.getAttribute("aria-expanded") === "true") {
                setNav(false);
                navToggle.focus();
            }
        });

        document.addEventListener("click", function (event) {
            if (!nav.classList.contains("is-open")) {
                return;
            }
            if (!event.target.closest("#site-nav") && !event.target.closest("[data-nav-toggle]")) {
                setNav(false);
            }
        });

        window.matchMedia("(min-width: 901px)").addEventListener("change", function (event) {
            if (event.matches) {
                setNav(false);
            }
        });
    }

    /* Native validation messages, surfaced next to the field and announced. */
    document.querySelectorAll("form[data-validate]").forEach(function (form) {
        var status = form.querySelector("[data-form-status]");

        function describe(field) {
            var label = form.querySelector('label[for="' + CSS.escape(field.id) + '"]');
            var name = label ? label.textContent.replace(/\*|\(required\)/gi, "").trim() : field.name;
            // Lead with the label so the sentence reads naturally. Wrap it in
            // quotes instead of prefixing "The", which turns labels that
            // already start with a possessive ("Your name") into awkward text.
            var reason = (field.validationMessage || "").trim().replace(/\.+$/, "").toLowerCase();
            return reason ? "\"" + name + "\": " + reason + "." : "\"" + name + "\" is not valid.";
        }

        form.addEventListener("submit", function (event) {
            if (form.checkValidity() && !form.hasAttribute("data-static")) {
                return;
            }

            event.preventDefault();

            var firstInvalid = null;
            form.querySelectorAll("input, select, textarea").forEach(function (field) {
                if (field.disabled || field.type === "hidden") {
                    return;
                }
                var invalid = !field.checkValidity();
                field.setAttribute("aria-invalid", invalid ? "true" : "false");
                if (invalid && !firstInvalid) {
                    firstInvalid = field;
                }
            });

            if (firstInvalid) {
                if (status) {
                    status.hidden = false;
                    status.className = "form-alert form-alert--danger";
                    status.textContent = "Please review the highlighted fields. " + describe(firstInvalid);
                }
                firstInvalid.focus();
                return;
            }

            if (form.hasAttribute("data-static") && status) {
                status.hidden = false;
                status.className = "form-alert form-alert--info";
                status.textContent =
                    "This preview is a static export, so the request was not sent. Please call or email the office instead.";
            }
        });

        form.addEventListener("input", function (event) {
            var field = event.target;
            if (field.getAttribute("aria-invalid") === "true" && field.checkValidity()) {
                field.setAttribute("aria-invalid", "false");
            }
        });
    });

    /* Auto-dismiss flash messages (they carry role="status" already). */
    document.querySelectorAll("[data-dismiss]").forEach(function (button) {
        button.addEventListener("click", function () {
            var alert = button.closest("[data-alert]");
            if (alert) {
                alert.remove();
            }
        });
    });
})();
