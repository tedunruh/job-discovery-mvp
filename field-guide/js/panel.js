/* The panel beside the job list (SC-72), and the page's scroll fold. Markup comes from the server, never from here:
   opening a card asks for /prep/<id> (the panel's inside, drawn by Jinja) and puts it in <aside id="apply-panel">.
   Forms inside the panel (Tailor my resume, Cancel, Undo/Redo) post in the background and the answer is the panel's
   markup again; while a tailoring runs, the page asks /tailor/<id> every couple of seconds until it ends (a 204 means
   still working) and shows the progress line's wording, which the server wrote, one step at a time.
   A cover letter being written polls the same way, but only its own section is swapped when it is done, so the changes
   above it keep their place. The letter box saves as you leave it, and Copy puts it on the clipboard.

   Rule: Scout never opens or closes anything on the person's behalf. A card opens the panel only when they click its title
   (a modified click, such as Ctrl or Cmd, still opens the posting in a new tab as before); only Close shuts it. Apply in
   the panel opens the posting and the panel stays. Focus:
     - opening a card moves focus to the panel's heading; Close returns it to that card's title link
     - when the panel's stage changes because the person pressed something (Tailor, Cancel, Looks good, Back to the
       changes), focus goes to the new stage's first line; when only the letter's section changes (Write a cover letter,
       Try again), focus goes to that section's first line; when a tailoring finishes by itself, focus moves only if it was inside the panel (on Cancel,
       which is gone), and the live region says so either way. Undo/Redo keep focus on the same change's button.
     - when a row leaves Open roles (Yes, or hidden), focus goes to the next row's title link (the previous one if it was
       last; the count line if none are left). If the panel was showing that job it stays open and moves to the same next
       job, quietly, and the toast says so once ("Now showing Northwind Health."). A panel showing another job is left alone.
   Without this script a title still opens the posting. */
(function () {
  "use strict";

  var workspace = document.querySelector("[data-workspace]");
  if (!workspace) return;

  /* ---- the scroll fold: the first scroll in the list or the panel folds Scout's line up and slims the header ------ */

  var FOLD_AT = 8;        // px scrolled before it folds
  var MIN_RANGE = 250;    // a pane must have this much to scroll, or the fold would take its own scrolling away
  var body = document.body;

  function sync() {
    var scrolled = 0, range = 0;
    Array.prototype.forEach.call(workspace.querySelectorAll(".board > [role=tabpanel], .fg-panel-body"), function (pane) {
      scrolled = Math.max(scrolled, pane.scrollTop);
      range = Math.max(range, pane.scrollHeight - pane.clientHeight);
    });
    var folded = body.classList.contains("is-collapsed");
    if (!folded && scrolled > FOLD_AT && range >= MIN_RANGE) body.classList.add("is-collapsed");
    else if (folded && scrolled <= 0) body.classList.remove("is-collapsed");   // back at the top of every pane
  }
  workspace.addEventListener("scroll", sync, true);   // scroll events don't bubble: listen on the way down
  window.addEventListener("resize", sync);

  /* ---- the panel ----------------------------------------------------------------------------------------------- */

  var board = workspace.querySelector("[data-panels]");
  var panel = document.getElementById("apply-panel");
  if (!board || !panel || !window.fetch) return;

  var current = null;   // the id of the job the panel shows
  var latest = 0;       // the newest request, so a slow answer never replaces a newer one
  var live = document.getElementById("panel-live");
  var POLL_MS = 2000;       // how often a running tailoring is asked about
  var STEP_SECONDS = 6;     // how long each line of the progress text stays up
  var SETTLE_SECONDS = 40;  // the bar eases toward full over about this long, and never finishes early
  var CEILING = 92;         // percent: it only completes when the result arrives
  var timers = [];          // the poll and the progress ticker of the stage on show

  function rowFor(id) { return document.getElementById("posting-" + id); }
  function idOf(row) { return row.id.replace("posting-", ""); }
  function titleLink(row) { return row && row.querySelector(".fg-job-link"); }

  function select(id) {
    Array.prototype.forEach.call(board.querySelectorAll(".fg-job.is-selected"), function (row) {
      row.classList.remove("is-selected");
      row.removeAttribute("aria-current");
    });
    var row = id ? rowFor(id) : null;
    if (row) {
      row.classList.add("is-selected");
      row.setAttribute("aria-current", "true");
    }
  }

  function stopWork() {
    timers.forEach(function (t) { window.clearTimeout(t); window.clearInterval(t); });
    timers = [];
  }

  function say(text) {
    if (!live || !text) return;
    live.textContent = "";
    window.setTimeout(function () { live.textContent = text; }, 50);   // an unchanged string is not announced twice
  }

  /* While a tailoring runs: step the progress text and ask the server until it ends. */
  function startWork(id) {
    var work = panel.querySelector("[data-poll]");
    if (!work) return;
    var url = work.getAttribute("data-poll");
    var swap = work.getAttribute("data-poll-swap");   // when set, only this part of the panel is replaced
    var bar = panel.querySelector(".fg-progress");
    var steps = [];
    try { steps = JSON.parse((bar && bar.getAttribute("data-steps")) || "[]"); } catch (e) { /* bar only */ }
    var text = bar && bar.querySelector(".fg-progress-text");
    var fill = bar && bar.querySelector(".fg-progress-bar span");
    var started = Date.now(), shown = 0, failures = 0;

    timers.push(window.setInterval(function () {
      var seconds = (Date.now() - started) / 1000;
      if (fill) fill.style.width = (CEILING * (1 - Math.exp(-seconds / SETTLE_SECONDS))).toFixed(1) + "%";
      if (text && steps.length) {
        var next = Math.min(steps.length - 1, Math.floor(seconds / STEP_SECONDS));
        if (next !== shown) { shown = next; text.textContent = steps[next]; }
      }
    }, 250));

    function poll() {
      fetch(url, { credentials: "same-origin", headers: { "X-Requested-With": "fetch" } })
        .then(function (response) {
          if (response.status === 204) { failures = 0; return again(); }
          if (!response.ok) throw new Error("no answer");
          return response.text().then(function (html) {
            if (current !== id) return;   // closed or moved on: the result waits in the panel for next time
            var inside = panel.contains(document.activeElement);
            if (swap) {
              if (!swapSection(html, swap, false)) render(html, id, false);
              else { stopWork(); startWork(id); }
            } else {
              render(html, id, inside);
              var voice = panel.querySelector("[data-announce]");
              say(voice ? voice.getAttribute("data-announce") : "");
            }
          });
        })
        .catch(function () { failures++; if (failures < 5 && current === id) again(); });
    }
    function again() { timers.push(window.setTimeout(poll, POLL_MS)); }
    again();
  }

  /* The letter box grows with its text, so the panel body is the only thing that scrolls (no scroll inside a scroll). */
  function fitLetter() {
    Array.prototype.forEach.call(panel.querySelectorAll("[data-letter-form] textarea"), function (box) {
      if (!box.offsetParent) return;
      var look = window.getComputedStyle(box);
      box.classList.add("is-fitted");
      box.style.height = "auto";
      box.style.height = (box.scrollHeight + parseFloat(look.borderTopWidth) + parseFloat(look.borderBottomWidth)) + "px";
    });
  }
  panel.addEventListener("input", function (event) { if (event.target.tagName === "TEXTAREA") fitLetter(); });
  window.addEventListener("resize", fitLetter);

  /* Put the server's markup in the panel and start whatever its stage needs. */
  function render(html, id, focusFirst) {
    stopWork();
    panel.innerHTML = html;
    panel.hidden = false;
    current = id;
    workspace.classList.add("fg-has-panel");
    select(id);
    fitLetter();
    sync();
    if (window.fgJob && window.fgJob.showTailored) window.fgJob.showTailored(id, !!panel.querySelector("[data-tailored]"));
    if (focusFirst) {
      var first = panel.querySelector("[data-panel-focus]");
      if (first) first.focus();
    }
    startWork(id);
  }

  /* The moment a card is clicked the panel opens with that job's own monogram, company and title and quiet placeholder
     lines (markup from the page's <template>, words copied from the card), so nothing waits on the server to answer. */
  function showOpening(id, row) {
    var template = document.getElementById("panel-loading-template");
    if (!template) return;
    stopWork();
    panel.textContent = "";
    panel.appendChild(template.content.cloneNode(true));
    var mono = row.querySelector(".fg-mono"), mark = panel.querySelector("[data-loading-mono]");
    if (mono && mark) { mark.className = mono.className; mark.textContent = mono.textContent; }
    var company = row.querySelector(".fg-job-company"), title = row.querySelector(".fg-job-link");
    panel.querySelector("[data-loading-company]").textContent = company ? company.textContent.trim() : "";
    panel.querySelector("[data-loading-title]").textContent = title ? title.textContent.trim() : "";
    panel.hidden = false;
    current = id;
    workspace.classList.add("fg-has-panel");
    select(id);
    sync();
  }

  /* Show a job in the panel. Resolves once it is shown; rejects if the server could not give it. `row` (the card that
     was clicked) shows the opening state straight away. */
  function load(id, focusHeading, row) {
    var mine = ++latest;
    if (row) showOpening(id, row);
    return fetch("/prep/" + id, { credentials: "same-origin", headers: { "X-Requested-With": "fetch" } })
      .then(function (response) {
        if (!response.ok) throw new Error("no panel");
        return response.text();
      })
      .then(function (html) {
        if (mine !== latest) return;
        render(html, id, false);
        var heading = panel.querySelector("[data-panel-title]");
        if (focusHeading && heading) heading.focus();
      });
  }

  function close() {
    var row = current ? rowFor(current) : null;
    latest++;
    stopWork();
    current = null;
    panel.hidden = true;
    panel.innerHTML = "";
    workspace.classList.remove("fg-has-panel");
    select(null);
    sync();
    var link = row && !row.hidden ? titleLink(row) : null;
    if (link) link.focus();
  }

  board.addEventListener("click", function (event) {
    var link = event.target.closest && event.target.closest(".fg-job-link");
    if (!link || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    var row = link.closest(".fg-job");
    if (!row) return;
    event.preventDefault();
    load(idOf(row), true, row).catch(function () {
      if (current === idOf(row) && panel.querySelector("[aria-busy]")) close();   // the opening state: take it away again
      window.open(link.href, "_blank", "noopener");   // the panel could not load: do what the link always did
    });
  });

  panel.addEventListener("click", function (event) {
    if (event.target.closest && event.target.closest("[data-panel-close]")) close();
  });

  /* Forms in the panel post in the background. A plain one (Tailor, Cancel) answers with the panel's new stage. An
     Undo/Redo form ("change") answers with the whole panel too, but only that card and the count are swapped in, so the
     scroll position stays and focus lands on the same change's button. Nothing is sent twice: the button waits. */
  function failedForm(form, button) {
    if (button) button.disabled = false;
    var message = panel.querySelector("[data-panel-error]");
    if (message) { message.hidden = false; say(message.textContent); }
  }

  function swapChange(html, changeId) {
    var next = new DOMParser().parseFromString(html, "text/html");
    var fresh = next.querySelector('[data-change="' + changeId + '"]');
    var old = panel.querySelector('[data-change="' + changeId + '"]');
    if (!fresh || !old) return false;
    old.replaceWith(document.importNode(fresh, true));
    var lines = next.querySelectorAll("[data-kept-line]");
    Array.prototype.forEach.call(panel.querySelectorAll("[data-kept-line]"), function (el, i) {
      if (lines[i]) el.textContent = lines[i].textContent;
    });
    var button = panel.querySelector('[data-change="' + changeId + '"] button');
    if (button) button.focus();
    return true;
  }

  /* Replace one part of the panel (the letter's section) with the same part of the server's markup. Focus moves to the
     new part's first line only when it was in the old part (or `focus` says so). Returns false if either is missing. */
  function swapSection(html, selector, focus) {
    var next = new DOMParser().parseFromString(html, "text/html");
    var fresh = next.querySelector(selector);
    var old = panel.querySelector(selector);
    if (!fresh || !old) return false;
    var had = old.contains(document.activeElement);
    old.replaceWith(document.importNode(fresh, true));
    fitLetter();
    var first = panel.querySelector(selector + " [data-letter-focus]");
    if (first && (focus || had)) first.focus();
    var voice = panel.querySelector(selector + " [data-announce]");
    if (voice) say(voice.getAttribute("data-announce"));
    return true;
  }

  var saving = Promise.resolve();   // the letter's latest save: other buttons wait for it, so nothing is lost

  panel.addEventListener("submit", function (event) {
    var form = event.target.closest && event.target.closest("form[data-panel-form]");
    if (!form || !window.fetch) return;
    event.preventDefault();
    var button = form.querySelector("button");
    var kind = form.getAttribute("data-panel-form");
    var id = current, change = kind === "change", section = kind === "letter";
    var changeId = change ? form.querySelector('input[name="change"]').value : "";
    if (button) button.disabled = true;
    saving.then(function () {
      return fetch(form.getAttribute("action"), { method: "POST", body: new FormData(form), credentials: "same-origin",
                                                  headers: { "X-Requested-With": "fetch" } });
    })
      .then(function (response) {
        if (!response.ok) throw new Error("no answer");
        return response.text();
      })
      .then(function (html) {
        if (current !== id) return;   // they opened another job meanwhile: that panel is not ours to change
        if (change && swapChange(html, changeId)) return;
        if (section && swapSection(html, "[data-letter]", true)) { stopWork(); startWork(id); return; }
        latest++;
        render(html, id, true);
      })
      .catch(function () { failedForm(form, button); });
  });

  /* The letter box saves as the person leaves it (the change event), quietly; Copy puts what is in the box on the
     clipboard. Both say what happened in words, in the live region and beside the box. */
  function letterNote(text) {
    var note = panel.querySelector("[data-letter-saved]");
    if (note) note.textContent = text;
    say(text);
  }

  panel.addEventListener("change", function (event) {
    var box = event.target;
    var form = box.closest && box.closest("[data-letter-form]");
    if (!form || box.tagName !== "TEXTAREA") return;
    saving = fetch(form.getAttribute("action"), { method: "POST", body: new FormData(form), credentials: "same-origin",
                                                   headers: { "X-Requested-With": "fetch" } })
      .then(function (response) { letterNote(response.ok ? "Cover letter saved." : "I couldn’t save your edit. Check your connection and try again."); })
      .catch(function () { letterNote("I couldn’t save your edit. Check your connection and try again."); });
  });

  panel.addEventListener("click", function (event) {
    var copy = event.target.closest && event.target.closest("[data-copy-letter]");
    if (!copy) return;
    var box = panel.querySelector("[data-letter-form] textarea");
    if (!box) return;
    function done() { letterNote("Cover letter copied."); }
    function fallback() {
      box.select();
      try { document.execCommand("copy") ? done() : letterNote("I couldn’t copy it. Select the text and copy it yourself."); }
      catch (e) { letterNote("I couldn’t copy it. Select the text and copy it yourself."); }
    }
    if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(box.value).then(done, fallback);
    else fallback();
  });

  /* The nearest row still in the list after this one, else the nearest before it. */
  function neighbour(row) {
    var rows = Array.prototype.slice.call(board.querySelectorAll(".fg-job"));
    var shown = function (r) { return !r.closest("[hidden]"); };
    var at = rows.indexOf(row), i;
    for (i = at + 1; i < rows.length; i++) if (shown(rows[i])) return rows[i];
    for (i = at - 1; i >= 0; i--) if (shown(rows[i])) return rows[i];
    return null;
  }

  /* job.js calls this when a row has left Open roles. Returns a sentence for the toast to add, or "". */
  function rowLeft(id) {
    var row = rowFor(id);
    var next = row ? neighbour(row) : null;
    var link = titleLink(next);
    if (link) {
      link.focus();
    } else {
      var count = document.querySelector("[data-open-count]");   // nothing left: the "0 open roles" line
      if (count) { count.tabIndex = -1; count.focus(); }
    }
    if (current !== id || !next) return "";
    var company = next.querySelector(".fg-job-company");
    var previous = current, nextId = idOf(next);
    current = nextId;               // the Forest edge moves now; the panel's text follows when it arrives
    select(nextId);
    load(nextId, false).catch(function () {
      if (current !== nextId) return;   // closed or changed meanwhile
      current = previous;               // it could not load: the panel stays on the job it had
      select(previous);
    });
    return company ? " Now showing " + company.textContent.trim() + "." : "";
  }

  window.fgPanel = { rowLeft: rowLeft };
})();
