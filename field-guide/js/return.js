/* Open roles (SC-63). The dashboard draws each row's state line ("Discovered 45 minutes ago", or "Viewed 12 minutes
   ago" once you have opened the job) and its one action: Apply, or "Did you apply?" with Yes and Not yet when you are
   back from that job. This script does what a loaded page cannot:

   - when the tab comes back into view (you read the posting in another tab, then return) it asks the server what is
     true now and updates the rows in place, with no reload and no change of focus;
   - "Not yet" puts Apply back and tells the server, so the job is not asked about again unless it is opened again;
   - "Yes" marks the job applied: the row leaves Open roles at once, the counts follow, and a toast says where it
     went, with View (the Applications tab) and Undo (puts the row back).

   Markup comes from the <template>s in the page, never from here. Nothing here moves focus. */
(function () {
  "use strict";

  var board = document.querySelector("[data-return-url]");
  if (!board || !window.fetch) return;

  var url = board.getAttribute("data-return-url");
  var applyTemplate = document.getElementById("return-apply-template");
  var askTemplate = document.getElementById("return-ask-template");
  var toastTemplate = document.getElementById("toast-template");
  var live = document.getElementById("return-live");
  var tabCounts = document.querySelectorAll("[data-tab-count]");   // [0] Open roles, [1] Applications
  var openCount = document.querySelector("[data-open-count]");
  var lastAsked = 0;
  var lastApplied = null;   // the row the toast's Undo would bring back

  function fromTemplate(template, id) {
    var fragment = template.content.cloneNode(true);
    var walker = document.createTreeWalker(fragment, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) {
      var node = walker.currentNode;
      if (node.nodeType === Node.TEXT_NODE) {
        node.nodeValue = node.nodeValue.replace("__id__", id);
      } else {
        Array.prototype.forEach.call(node.attributes, function (attribute) {
          node.setAttribute(attribute.name, attribute.value.replace("__id__", id));
        });
      }
    }
    return fragment;
  }

  function rowFor(id) { return document.getElementById("posting-" + id); }
  function idOf(row) { return row.id.replace("posting-", ""); }

  function setAction(row, template) {
    var spot = row.querySelector("[data-action]");
    if (!spot) return null;
    spot.textContent = "";
    spot.appendChild(fromTemplate(template, idOf(row)));
    return spot;
  }
  function showApply(row) { setAction(row, applyTemplate); }
  function isAsking(row) { return !!row.querySelector("[data-ask-yes]"); }

  function showViewed(id, when) {
    var row = rowFor(id);
    var state = row && row.querySelector("[data-state]");
    if (state) state.textContent = "Viewed " + when;
  }

  function showAsk(ask) {
    Array.prototype.forEach.call(document.querySelectorAll(".posting"), function (row) {
      if (isAsking(row) && (!ask || row.id !== "posting-" + ask.id)) showApply(row);
    });
    if (!ask) return;
    var row = rowFor(ask.id);
    if (!row || row.hidden || isAsking(row)) return;
    setAction(row, askTemplate);
    if (live) {
      var title = row.querySelector(".posting-title");
      live.textContent = "Did you apply for " + (title ? title.textContent.trim() : "this one") + "?";
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

  function adjustCounts(openBy) {
    if (tabCounts.length === 2) {
      tabCounts[0].textContent = String(Math.max(0, parseInt(tabCounts[0].textContent, 10) + openBy));
      tabCounts[1].textContent = String(Math.max(0, parseInt(tabCounts[1].textContent, 10) - openBy));
    }
    if (openCount && tabCounts.length) {
      var n = parseInt(tabCounts[0].textContent, 10);
      openCount.textContent = n + " open role" + (n === 1 ? "" : "s");
    }
  }

  function post(action, applied) {
    return fetch(action, {
      method: "POST",
      credentials: "same-origin",
      headers: { "X-Requested-With": "fetch" },
      body: new URLSearchParams({ applied: applied ? "true" : "false" })
    });
  }

  board.addEventListener("submit", function (event) {
    var form = event.target;
    var row = form.closest(".posting");
    if (!row) return;

    /* "Not yet": Apply comes back now; tell the server in the background. */
    if (form.hasAttribute("data-ask-dismiss")) {
      event.preventDefault();
      showApply(row);
      if (live) live.textContent = "";
      fetch(form.getAttribute("action"), { method: "POST", headers: { "X-Requested-With": "fetch" }, credentials: "same-origin" })
        .catch(function () { /* worst case it is asked once more on the next visit */ });
      return;
    }

    /* "Yes": the job is applied to. If the server cannot be reached the row stays and nothing is claimed. */
    if (form.hasAttribute("data-ask-yes")) {
      event.preventDefault();
      post(form.getAttribute("action"), true).then(function (response) {
        if (!response.ok) return;
        row.hidden = true;
        lastApplied = row;
        adjustCounts(-1);
        if (live) live.textContent = "";
        if (window.fgToast && toastTemplate) window.fgToast.show(toastTemplate);
      }).catch(function () { /* the row stays; they can try again */ });
    }
  });

  /* Undo, in the toast: the server forgets the answer and the row comes back where it was, with Apply. */
  document.addEventListener("click", function (event) {
    var undo = event.target.closest && event.target.closest("[data-toast-undo]");
    if (!undo || !lastApplied) return;
    var row = lastApplied;
    post("/set_applied/" + idOf(row), false).then(function (response) {
      if (!response.ok) return;
      row.hidden = false;
      showApply(row);
      adjustCounts(1);
      lastApplied = null;
      if (window.fgToast) window.fgToast.hide();
    }).catch(function () { /* the toast stays, so they can try again */ });
  });
})();
