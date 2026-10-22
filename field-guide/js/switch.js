// Field Guide switch: flips a .fg-switch[data-fg-async] in place instead of reloading the page.
// The switch is a submit button in a real form, so without this script (or if the request
// fails) the form posts normally. The endpoint answers Accept: application/json with
// {on, value, detail, message}. See the switch macro in dashboard/templates/_fg.html.
document.addEventListener("click", async (event) => {
  const sw = event.target.closest(".fg-switch[data-fg-async]");
  if (!sw || !sw.form || sw.disabled) return;
  event.preventDefault();
  if (sw.getAttribute("aria-busy") === "true") return;
  sw.setAttribute("aria-busy", "true");
  const body = new FormData(sw.form);
  if (sw.name) body.set(sw.name, sw.value);
  try {
    // getAttribute, not form.action: a control named "action" shadows that property.
    const res = await fetch(sw.getAttribute("formaction") || sw.form.getAttribute("action"), {
      method: "POST", body, credentials: "same-origin", headers: { Accept: "application/json" },
    });
    if (!res.ok) throw new Error(res.status);
    const data = await res.json();
    sw.setAttribute("aria-checked", data.on ? "true" : "false");
    if (data.value) sw.value = data.value;
    const detail = document.getElementById(sw.id + "-detail");
    if (detail && data.detail) detail.textContent = data.detail;
    const live = sw.dataset.fgLive && document.getElementById(sw.dataset.fgLive);
    if (live && data.message) live.textContent = data.message;
  } catch (err) {
    sw.removeAttribute("aria-busy");
    sw.form.requestSubmit(sw);  // fall back to a normal post
    return;
  }
  sw.removeAttribute("aria-busy");
});
