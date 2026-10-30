/* Jobs on the dashboard (SC-63, SC-59). Each card has one action spot. This script does what a loaded page cannot:

   - "Hide" swaps the spot for "Why are you hiding?" (a reason menu and Skip). A reason or Skip hides the job: the card
     leaves Open roles at once, the counts move and a toast says "Job hidden." with Undo. "Already applied" is not
     hiding: the job goes to Applications instead.
   - "Yes" (the answer to "Did you apply?") does the same for Applications, with View and Undo in the toast.
   - "Not yet" puts Apply and Hide back and tells the server, so the job is not asked about again unless it is opened again.
     Until it is answered (Yes, Not yet, hidden), the question stays on every job that was opened.
   - In the hidden list, "Unhide" sends the card back to Open roles ("Back in Open roles." with Undo).
   - On the hidden list and the Viewed list the heading takes focus when the view opens.

   Without this script the forms still post and the page reloads: Hide hides with no reason, Yes marks it applied,
   Unhide unhides. Markup comes from the <template>s in the page, never from here. Nothing takes focus on its own except
   the reason menu a person has just asked for. */
(function () {
  "use strict";

  var board = document.querySelector("[data-hide-board]");
  var heading = document.querySelector("[data-focus-on-load]");
  if (heading) heading.focus();
  if (!board || !window.fetch) return;

  var live = document.getElementById("return-live");
  var applyTemplate = document.getElementById("return-apply-template");
  var askTemplate = document.getElementById("return-ask-template");
  var whyTemplate = document.getElementById("return-why-template");
  var viewedTemplate = document.getElementById("return-viewed-template");
  var appliedToast = document.getElementById("toast-template");
  var simpleToast = document.getElementById("toast-simple-template");
  var tabCounts = document.querySelectorAll("[data-tab-count]");   // [0] Open roles, [1] Applications
  var openCount = document.querySelector("[data-open-count]");
  var hiddenLink = document.querySelector("[data-hidden-link]");
  var foot = document.querySelector("[data-foot]");

  function fromTemplate(template, row) {
    var id = row.id.replace("posting-", "");
    var title = row.querySelector(".fg-job-title");
    var fragment = template.content.cloneNode(true);
    var walker = document.createTreeWalker(fragment, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
    function swap(value) {
      return value.replace(/__id__/g, id).replace(/__title__/g, title ? title.textContent.trim() : "this job");
    }
    while (walker.nextNode()) {
      var node = walker.currentNode;
      if (node.nodeType === Node.TEXT_NODE) {
        node.nodeValue = swap(node.nodeValue);
      } else {
        Array.prototype.forEach.call(node.attributes, function (attribute) {
          node.setAttribute(attribute.name, swap(attribute.value));
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
    spot.appendChild(fromTemplate(template, row));
    return spot;
  }
  function showApply(row) { setAction(row, applyTemplate); }
  function isAsking(row) { return !!row.querySelector("[data-ask-yes]"); }

  /* ---- counts ------------------------------------------------------------------------------------------ */

  function hiddenCount() {
    var match = hiddenLink && hiddenLink.textContent.match(/\d+/);
    return match ? parseInt(match[0], 10) : 0;
  }

  function adjust(delta) {
    if (tabCounts.length === 2) {
      tabCounts[0].textContent = String(Math.max(0, parseInt(tabCounts[0].textContent, 10) + (delta.open || 0)));
      tabCounts[1].textContent = String(Math.max(0, parseInt(tabCounts[1].textContent, 10) + (delta.apps || 0)));
    }
    if (openCount && tabCounts.length) {
      var n = parseInt(tabCounts[0].textContent, 10);
      openCount.textContent = n + " open role" + (n === 1 ? "" : "s");
    }
    if (hiddenLink && delta.hidden) {
      var h = Math.max(0, hiddenCount() + delta.hidden);
      hiddenLink.textContent = "Show " + h + " hidden job" + (h === 1 ? "" : "s");
      hiddenLink.hidden = h === 0;
      if (foot) foot.hidden = h === 0 && !foot.querySelector(".more");
    }
  }

  /* A card that leaves folds closed (CSS: .is-leaving; with reduced motion it only fades), then is taken out. The day
     heading's count follows, and the heading goes when its group is empty. */
  function leave(row, then) {
    var finished = false;
    function finish() {
      if (finished) return;
      finished = true;
      row.classList.remove("is-leaving");
      row.hidden = true;
      dayCount(row, -1);
      then();
    }
    row.classList.add("is-leaving");
    row.addEventListener("animationend", finish, { once: true });
    setTimeout(finish, 320);          // in case the animation never runs (hidden tab, no animation support)
  }

  function dayCount(row, delta) {
    var section = row.closest(".fg-day");
    var count = section && section.querySelector(".fg-day-count");
    if (!count) return;
    var n = Math.max(0, parseInt(count.textContent.replace(/\D+/g, ""), 10) + delta);
    count.textContent = "\u00b7 " + n;
    section.hidden = n === 0;
  }

  function comeBack(row) {
    row.hidden = false;
    dayCount(row, 1);
  }

  function post(action, fields) {
    return fetch(action, {
      method: "POST",
      credentials: "same-origin",
      headers: { "X-Requested-With": "fetch" },
      body: new URLSearchParams(fields || {})
    });
  }

  /* ---- the actions ------------------------------------------------------------------------------------- */

  function markApplied(row) {
    var id = idOf(row);
    return post("/set_applied/" + id, { applied: "true" }).then(function (response) {
      if (!response.ok) return false;
      adjust({ open: -1, apps: 1 });
      if (live) live.textContent = "";
      leave(row, function () {
        if (window.fgToast && appliedToast) {
          window.fgToast.show(appliedToast, { onUndo: function () {
            post("/set_applied/" + id, { applied: "false" }).then(function (r) {
              if (!r.ok) return;
              comeBack(row);
              showApply(row);
              adjust({ open: 1, apps: -1 });
            });
          } });
        }
      });
      return true;
    }).catch(function () { return false; });
  }

  function hideRow(row, reason) {
    var id = idOf(row);
    return post("/hide/" + id, reason ? { reason: reason } : {}).then(function (response) {
      if (!response.ok) return;
      adjust({ open: -1, hidden: 1 });
      leave(row, function () {
        if (window.fgToast && simpleToast) {
          window.fgToast.show(simpleToast, { text: "Job hidden.", onUndo: function () {
            post("/unhide/" + id).then(function (r) {
              if (!r.ok) return;
              comeBack(row);
              showApply(row);
              adjust({ open: 1, hidden: -1 });
            });
          } });
        }
      });
    }).catch(function () { /* the card stays as it was */ });
  }

  function unhideRow(row, reason) {
    var id = idOf(row);
    return post("/unhide/" + id).then(function (response) {
      if (!response.ok) return;
      row.hidden = true;
      adjust({ open: 1 });
      if (window.fgToast && simpleToast) {
        window.fgToast.show(simpleToast, { text: "Back in Open roles.", onUndo: function () {
          post("/hide/" + id, reason ? { reason: reason, again: "1" } : { again: "1" }).then(function (r) {
            if (!r.ok) return;
            row.hidden = false;
            adjust({ open: -1 });
          });
        } });
      }
    }).catch(function () { /* the card stays as it was */ });
  }

  board.addEventListener("submit", function (event) {
    var form = event.target;
    var row = form.closest(".fg-job");
    if (!row) return;

    if (form.hasAttribute("data-hide")) {          // Hide: ask why first; nothing is hidden until they answer
      event.preventDefault();
      var spot = setAction(row, whyTemplate);
      var menu = spot && spot.querySelector("select");
      if (menu) menu.focus();                      // they just asked for it
      return;
    }
    if (form.hasAttribute("data-unhide")) {
      event.preventDefault();
      unhideRow(row, form.getAttribute("data-reason") || "");
      return;
    }
    if (form.hasAttribute("data-ask-dismiss")) {  // Not yet: Apply and Hide come back now; tell the server in the background
      event.preventDefault();
      showApply(row);
      if (live) live.textContent = "";
      fetch(form.getAttribute("action"), { method: "POST", headers: { "X-Requested-With": "fetch" }, credentials: "same-origin" })
        .catch(function () { /* worst case it is asked once more on the next visit */ });
      return;
    }
    if (form.hasAttribute("data-ask-yes")) {       // Yes: if the server cannot be reached the card stays and nothing is claimed
      event.preventDefault();
      markApplied(row);
    }
  });

  board.addEventListener("change", function (event) {
    var menu = event.target;
    if (!menu.matches || !menu.matches("[data-why]")) return;
    var row = menu.closest(".fg-job");
    if (!row) return;
    if (menu.value === "applied") markApplied(row);   // not hiding: it goes to Applications
    else hideRow(row, menu.value);
  });

  board.addEventListener("click", function (event) {
    var skip = event.target.closest && event.target.closest("[data-skip]");
    if (!skip) return;
    var row = skip.closest(".fg-job");
    if (row) hideRow(row, "");
  });

  /* ---- what return.js needs ---------------------------------------------------------------------------- */

  window.fgJob = {
    rowFor: rowFor,
    showViewed: function (id) {
      var row = rowFor(id);
      if (!row || row.closest("[data-viewed-list]")) return;   // the Viewed list says when each was opened instead
      var when = row.querySelector("[data-state-when]");
      if (when && !when.querySelector("[data-viewed]") && viewedTemplate) when.appendChild(viewedTemplate.content.cloneNode(true));
    },
    /* The jobs to ask about, all of them: the question stays on a card until it is answered. A card that is no longer
       in the list (it was answered somewhere else) gets Apply back. */
    showAsks: function (asks) {
      var wanted = {};
      (asks || []).forEach(function (ask) { wanted[ask.id] = true; });
      Array.prototype.forEach.call(document.querySelectorAll(".fg-job"), function (row) {
        if (isAsking(row) && !wanted[idOf(row)]) showApply(row);
      });
      var added = [];
      (asks || []).forEach(function (ask) {
        var row = rowFor(ask.id);
        if (!row || row.hidden || isAsking(row)) return;
        if (setAction(row, askTemplate)) added.push(row);
      });
      if (live && added.length) {
        var title = added[0].querySelector(".fg-job-title");
        live.textContent = added.length === 1
          ? "Did you apply for " + (title ? title.textContent.trim() : "this one") + "?"
          : "Did you apply for the " + added.length + " jobs you opened?";
      }
    }
  };
})();
