/* The sign-in form (SC-62): says what is happening after "Email me a sign-in link" is pressed.
   Without this script the form still works as a plain post; with it the page stays where it is:
   ready -> sending -> link sent -> (after a wait) send another link. Everything it shows is on the page:
   the wording sits in data- attributes on the form and the messages are already in the markup, hidden. */
(function () {
  "use strict";

  var form = document.getElementById("signin-form");
  if (!form || !window.fetch) return;

  var input = form.elements.email;
  var button = document.getElementById("signin-button");
  var label = button.querySelector(".signin-label");
  var spinner = button.querySelector(".fg-spinner");
  var check = button.querySelector(".signin-check");
  var timer = null;

  function data(name) { return form.getAttribute("data-" + name); }

  /* The hidden attribute, set the same way on every element: an SVG has no hidden property, so assigning .hidden
     on the check mark would silently do nothing. */
  function setHidden(element, hidden) {
    if (hidden) element.setAttribute("hidden", ""); else element.removeAttribute("hidden");
  }

  function show(state) {
    Array.prototype.forEach.call(form.querySelectorAll("[data-state]"), function (box) {
      box.hidden = box.getAttribute("data-state") !== state;
    });
  }

  /* kind: ready, sending, sent or resend. Busy is aria-busy and aria-disabled, never disabled, so focus stays. */
  function setButton(kind) {
    button.removeAttribute("aria-busy");
    button.removeAttribute("aria-disabled");
    button.disabled = false;
    setHidden(spinner, true);
    setHidden(check, true);
    var secondary = kind === "resend";
    button.classList.toggle("fg-button--primary", !secondary);
    button.classList.toggle("fg-button--secondary", secondary);
    label.textContent = data("label-" + kind);
    if (kind === "sending") {
      button.setAttribute("aria-busy", "true");
      button.setAttribute("aria-disabled", "true");
      setHidden(spinner, false);
    } else if (kind === "sent") {
      button.disabled = true;
      setHidden(check, false);
    }
  }

  function busy() { return button.getAttribute("aria-busy") === "true"; }

  /* A quiet limit on this device: no more than max-sends links to one address in window-minutes. The server keeps
     its own limit and answers every address the same way, so it never says whether an address is registered. */
  function recentSends(email) {
    var windowMs = parseInt(data("window-minutes"), 10) * 60000, now = Date.now(), all = {};
    try { all = JSON.parse(window.localStorage.getItem("scout.signin.sends") || "{}"); } catch (e) { all = {}; }
    var mine = (all[email] || []).filter(function (t) { return now - t < windowMs; });
    return { all: all, mine: mine, now: now };
  }

  function throttled(email) {
    return recentSends(email).mine.length >= parseInt(data("max-sends"), 10);
  }

  function recordSend(email) {
    var r = recentSends(email);
    r.mine.push(r.now);
    r.all[email] = r.mine;
    try { window.localStorage.setItem("scout.signin.sends", JSON.stringify(r.all)); } catch (e) { /* private mode: no memory */ }
  }

  function sent(email) {
    form.querySelector("[data-email]").textContent = email;
    show("sent");
    setButton("sent");
    timer = setTimeout(function () { setButton("resend"); }, parseInt(data("resend-seconds"), 10) * 1000);
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    if (busy()) return;
    var email = input.value.trim();
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) {
      show("invalid");
      setButton("ready");
      input.focus();
      return;
    }
    if (throttled(email.toLowerCase())) {
      show("throttled");
      setButton("ready");
      return;
    }
    clearTimeout(timer);
    show(null);
    setButton("sending");
    recordSend(email.toLowerCase());
    fetch(form.getAttribute("action"), {
      method: "POST",
      body: new URLSearchParams({ email: email }),
      headers: { "X-Requested-With": "fetch" },
      credentials: "same-origin"
    }).then(function (response) {
      if (!response.ok) throw new Error("status " + response.status);
      sent(email);
    }).catch(function () {
      setButton("ready");
      show("failed");
    });
  });

  /* Typing a different address starts again. */
  input.addEventListener("input", function () {
    if (busy()) return;
    clearTimeout(timer);
    show(null);
    setButton("ready");
  });
})();
