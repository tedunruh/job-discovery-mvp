/* Coming back from a job (SC-63). The dashboard already draws the Viewed lines and the "Did you apply for this one?"
   question when it loads. This script does the one thing a loaded page cannot: when the tab comes back into view
   (you read the posting in another tab, then return) it asks the server what is true now and updates the rows in place,
   with no reload and no change of focus. "Not yet" closes the question and tells the server, so it is not asked again
   unless that job is opened again. Markup comes from the <template>s in the page, never from here. */
(function () {
  "use strict";

  var board = document.querySelector("[data-return-url]");
  if (!board || !window.fetch) return;

  var url = board.getAttribute("data-return-url");
  var viewedTemplate = document.getElementById("return-viewed-template");
  var askTemplate = document.getElementById("return-ask-template");
  var live = document.getElementById("return-live");
  var lastAsked = 0;

  function fromTemplate(template, replacements) {
    var node = template.content.firstElementChild.cloneNode(true);
    var walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) {
      Object.keys(replacements).forEach(function (key) {
        walker.currentNode.nodeValue = walker.currentNode.nodeValue.replace(key, replacements[key]);
      });
    }
    var form = node.querySelector("form");
    if (form) form.setAttribute("action", form.getAttribute("action").replace("__id__", replacements.__id__ || ""));
    return node;
  }

  function rowFor(id) { return document.getElementById("posting-" + id); }

  function showViewed(id, when) {
    var row = rowFor(id);
    if (!row || row.classList.contains("posting--done")) return;
    var line = row.querySelector("[data-viewed]");
    if (line) {
      line.querySelector("[data-viewed-when]").textContent = when;
      return;
    }
    var meta = row.querySelector(".posting-meta");
    if (meta) meta.appendChild(fromTemplate(viewedTemplate, { "__when__": when }));
  }

  function clearAsks(exceptId) {
    Array.prototype.forEach.call(document.querySelectorAll(".posting--asking"), function (row) {
      if (row.id === "posting-" + exceptId) return;
      row.classList.remove("posting--asking");
      var block = row.querySelector("[data-ask]");
      if (block) block.remove();
    });
  }

  function showAsk(ask) {
    clearAsks(ask ? ask.id : null);
    if (!ask) return;
    var row = rowFor(ask.id);
    if (!row || row.classList.contains("posting--done")) return;
    var existing = row.querySelector("[data-ask]");
    if (existing) {
      existing.querySelector("[data-ask-help]").textContent = ask.help;
      return;
    }
    row.classList.add("posting--asking");
    row.appendChild(fromTemplate(askTemplate, { "__id__": String(ask.id), "__help__": ask.help }));
    if (live) {
      var title = row.querySelector(".posting-title");
      live.textContent = "Welcome back. Did you apply for " + (title ? title.textContent.trim() : "this one") + "?";
    }
  }

  function refresh() {
    var now = Date.now();
    if (now - lastAsked < 2000) return;  // the tab can fire focus and visibility together
    lastAsked = now;
    fetch(url, { credentials: "same-origin", headers: { Accept: "application/json" } })
      .then(function (response) { return response.ok ? response.json() : null; })
      .then(function (state) {
        if (!state) return;
        Object.keys(state.viewed || {}).forEach(function (id) { showViewed(id, state.viewed[id]); });
        showAsk(state.ask);
      })
      .catch(function () { /* the page stays as it was */ });
  }

  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible") refresh();
  });
  window.addEventListener("focus", refresh);

  /* "Not yet": close it now, tell the server in the background. Nothing takes focus. */
  board.addEventListener("submit", function (event) {
    var form = event.target;
    if (!form.querySelector("[data-ask-dismiss]")) return;
    event.preventDefault();
    var row = form.closest(".posting");
    var block = form.closest("[data-ask]");
    if (block) block.remove();
    if (row) row.classList.remove("posting--asking");
    if (live) live.textContent = "";
    fetch(form.getAttribute("action"), { method: "POST", headers: { "X-Requested-With": "fetch" }, credentials: "same-origin" })
      .catch(function () { /* worst case it is asked once more on the next visit */ });
  });
})();
