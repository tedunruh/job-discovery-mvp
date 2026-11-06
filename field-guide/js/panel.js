/* The panel beside the job list (SC-72), and the page's scroll fold. Markup comes from the server, never from here:
   opening a card asks for /prep/<id> (the panel's inside, drawn by Jinja) and puts it in <aside id="apply-panel">.

   Rule: Scout never opens or closes anything on the person's behalf. A card opens the panel only when they click its title
   (a modified click, such as Ctrl or Cmd, still opens the posting in a new tab as before); only Close shuts it. Apply in
   the panel opens the posting and the panel stays. Focus:
     - opening a card moves focus to the panel's heading; Close returns it to that card's title link
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
    Array.prototype.forEach.call(workspace.querySelectorAll(".board, .fg-panel-body"), function (pane) {
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

  /* Show a job in the panel. Resolves once it is shown; rejects if the server could not give it. */
  function load(id, focusHeading) {
    var mine = ++latest;
    return fetch("/prep/" + id, { credentials: "same-origin", headers: { "X-Requested-With": "fetch" } })
      .then(function (response) {
        if (!response.ok) throw new Error("no panel");
        return response.text();
      })
      .then(function (html) {
        if (mine !== latest) return;
        panel.innerHTML = html;
        panel.hidden = false;
        current = id;
        workspace.classList.add("fg-has-panel");
        select(id);
        sync();
        var heading = panel.querySelector("[data-panel-title]");
        if (focusHeading && heading) heading.focus();
      });
  }

  function close() {
    var row = current ? rowFor(current) : null;
    latest++;
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
    load(idOf(row), true).catch(function () {
      window.open(link.href, "_blank", "noopener");   // the panel could not load: do what the link always did
    });
  });

  panel.addEventListener("click", function (event) {
    if (event.target.closest && event.target.closest("[data-panel-close]")) close();
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
