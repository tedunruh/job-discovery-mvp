// Field Guide filter menus (.fg-filter, built on <details>).
// Adds what <details> lacks: only one menu open at a time, close on an outside
// click, close on Escape (returning focus to the pill).
(function () {
  function filters() { return document.querySelectorAll("details.fg-filter"); }

  document.addEventListener("toggle", function (e) {
    var d = e.target;
    if (!d.matches || !d.matches("details.fg-filter") || !d.open) return;
    filters().forEach(function (other) { if (other !== d) other.open = false; });
  }, true);

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
