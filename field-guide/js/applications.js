/* The Applications tab (SC-73): its row questions and its list. Markup comes from the server (the list is
   /applications/list; the questions and their <template>s are in _applications_list.html), never from here.

   A row that is still waiting may ask a question in its action spot (rules in checkins.py):
     Did you hear back?   Yes -> the row asks "What did they say?" (Interview, Offer, Not moving forward)
                          Not yet -> the row says "I'll check back in a week."
     Should I close it?   Yes, close it -> the application is closed by you; Not yet -> "No problem. I'll leave it here."
   Answers are saved first and the row changes only when they were. A stage change (an answer to What did they say?, or Yes,
   close it) moves the row to its group: the list is asked for again, a toast says what happened with an Undo that takes
   back exactly that change, and focus goes to the row's title in its new place.

   Shared with the panel (js/panel.js), which also changes stages:
     window.fgApplications.refresh()       ask for the grouped list again and swap it in (and Scout's line above the board)
     window.fgApplications.offerUndo(id, text, undoUrl, log)   the toast with Undo for a change just made
   After a change from here, or an Undo, "fg:application-changed" (detail.id) tells the panel to redraw if it shows that job. */
(function () {
  "use strict";

  var board = document.querySelector('[data-panels][data-panel-url="/application/"]');
  if (!board || !window.fetch) return;

  var live = document.getElementById("panel-live");
  var FAILED = "I couldn’t save that. Check your connection and try again.";

  function say(text) {
    if (!live || !text) return;
    live.textContent = "";
    window.setTimeout(function () { live.textContent = text; }, 50);
  }

  function post(url, fields) {
    var data = new FormData();
    Object.keys(fields || {}).forEach(function (key) { data.append(key, fields[key]); });
    return fetch(url, { method: "POST", body: data, credentials: "same-origin", headers: { "X-Requested-With": "fetch" } })
      .then(function (response) { if (!response.ok) throw new Error("no answer"); return response; });
  }

  function idOf(row) { return row.id.replace("posting-", ""); }

  function changed(id) {
    document.dispatchEvent(new CustomEvent("fg:application-changed", { detail: { id: String(id) } }));
  }

  /* Ask for the grouped list again and put it in place of the old one. */
  function refresh() {
    return fetch("/applications/list", { credentials: "same-origin", headers: { "X-Requested-With": "fetch" } })
      .then(function (response) { if (!response.ok) throw new Error("no list"); return response.text(); })
      .then(function (html) {
        var fresh = new DOMParser().parseFromString(html, "text/html").querySelector("[data-applications-list]");
        var old = board.querySelector("[data-applications-list]");
        if (!fresh || !old) return;
        old.replaceWith(document.importNode(fresh, true));
        var voice = document.querySelector("[data-voice]");
        var wrap = voice && voice.closest(".voice-wrap");
        var text = fresh.getAttribute("data-voice-text") || "";
        if (voice) voice.textContent = text;
        if (wrap) wrap.hidden = !text;
        document.dispatchEvent(new CustomEvent("fg:applications-refreshed"));
      });
  }

  function offerUndo(id, text, url, log) {
    var template = document.getElementById("toast-simple-template");
    if (!text || !template || !window.fgToast) return;
    window.fgToast.show(template, { text: text, onUndo: function () {
      post(url, { log: log })
        .then(function () { return refresh(); })
        .then(function () { changed(id); })
        .catch(function () { say("I couldn’t undo that. Check your connection and try again."); });
    } });
  }

  /* A stage change made on a row: save it, move the row, say so, and leave focus on the row. */
  function changeStage(id, stage) {
    return post("/application/" + id + "/stage", { stage: stage, source: "checkin" })
      .then(function (response) { return response.text(); })
      .then(function (html) {
        var history = new DOMParser().parseFromString(html, "text/html").querySelector("[data-history]");
        return refresh().then(function () {
          changed(id);
          var row = document.getElementById("posting-" + id);
          var link = row && row.querySelector(".fg-job-link");
          if (link) link.focus();
          if (history) offerUndo(id, history.getAttribute("data-toast"), history.getAttribute("data-undo-url"), history.getAttribute("data-undo-log"));
        });
      });
  }

  function showSay(row) {
    var template = document.getElementById("checkin-say-template");
    var action = row.querySelector("[data-action]");
    if (!template || !action) return;
    action.innerHTML = template.innerHTML.replace(/__id__/g, idOf(row));
    var box = action.querySelector("select");
    if (box) box.focus();
  }

  function showNote(row, text) {
    var template = document.getElementById("checkin-note-template");
    var action = row.querySelector("[data-action]");
    if (!template || !action) return;
    action.textContent = "";
    action.appendChild(template.content.cloneNode(true));
    var note = action.querySelector("[data-checkin-note]");
    note.textContent = text;
    note.focus();
  }

  board.addEventListener("click", function (event) {
    var button = event.target.closest && event.target.closest("[data-checkin]");
    if (!button) return;
    var row = button.closest(".fg-job");
    if (!row) return;
    var id = idOf(row), answer = button.getAttribute("data-checkin");
    button.disabled = true;
    var saved;
    if (answer === "close_yes") saved = changeStage(id, "mine");
    else saved = post("/application/" + id + "/checkin", { answer: answer }).then(function () {
      if (answer === "heard_yes") showSay(row);
      else showNote(row, button.getAttribute("data-note"));
    });
    saved.catch(function () { button.disabled = false; say(FAILED); });
  });

  board.addEventListener("change", function (event) {
    var box = event.target;
    if (!box.hasAttribute || !box.hasAttribute("data-checkin-say")) return;
    var row = box.closest(".fg-job");
    if (!row) return;
    box.disabled = true;
    changeStage(idOf(row), box.value).catch(function () { box.disabled = false; say(FAILED); });
  });

  window.fgApplications = { refresh: refresh, offerUndo: offerUndo };
})();
