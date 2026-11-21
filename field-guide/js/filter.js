// Field Guide filter menus (.fg-filter, built on <details>).
// Adds what <details> lacks: only one menu open at a time, close on an outside
// click, close on Escape (returning focus to the pill). The account menu (.fg-account,
// role="menu") also works like a menu button: opening it puts focus on its first row, the
// arrow keys, Home and End move between rows, and Tab closes it.
(function () {
  // Specimens marked data-fg-static (the /field-guide gallery) are left alone.
  function filters() { return document.querySelectorAll("details.fg-filter:not([data-fg-static])"); }

  document.addEventListener("toggle", function (e) {
    var d = e.target;
    if (!d.matches || !d.matches("details.fg-filter:not([data-fg-static])") || !d.open) return;
    filters().forEach(function (other) { if (other !== d) other.open = false; });
    var first = d.matches(".fg-account") && d.querySelector("[role=menuitem]");
    if (first) first.focus();
  }, true);

  document.addEventListener("keydown", function (e) {
    var menu = e.target.closest && e.target.closest(".fg-account [role=menu]");
    var summary = e.target.matches && e.target.matches(".fg-account > summary") ? e.target : null;
    if (summary && e.key === "ArrowDown") {          // the usual way into a menu button
      e.preventDefault();
      summary.parentNode.open = true;
      return;
    }
    if (!menu) return;
    var rows = Array.prototype.slice.call(menu.querySelectorAll("[role=menuitem]"));
    var at = rows.indexOf(document.activeElement);
    var to = null;
    if (e.key === "ArrowDown") to = rows[(at + 1) % rows.length];
    else if (e.key === "ArrowUp") to = rows[(at - 1 + rows.length) % rows.length];
    else if (e.key === "Home") to = rows[0];
    else if (e.key === "End") to = rows[rows.length - 1];
    else if (e.key === "Tab") { menu.closest("details").open = false; return; }
    if (to) { e.preventDefault(); to.focus(); }
  });

  document.addEventListener("click", function (e) {
    filters().forEach(function (d) { if (d.open && !d.contains(e.target)) d.open = false; });
  });

  document.addEventListener("keydown", function (e) {
    if (e.key !== "Escape") return;
    filters().forEach(function (d) {
      if (!d.open) return;
      d.open = false;
      var pill = d.querySelector("summary");
      if (pill) pill.focus();
    });
  });
})();
