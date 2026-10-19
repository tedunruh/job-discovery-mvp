/* Behavior for the profile screen (SC-46). Everything it shows comes from the page itself:
   chips are cloned from <template id="LIST-template">, the side panel is HTML the server renders,
   and the action bar's wording sits in data- attributes. No markup or copy lives here. */
(function () {
  "use strict";

  var form = document.getElementById("profile-form");
  if (!form) return;

  var mode = form.getAttribute("data-mode");
  var previewUrl = form.getAttribute("data-preview-url");
  var panel = document.getElementById("profile-preview");
  var save = document.getElementById("save-button");
  var discard = document.getElementById("discard-button");
  var note = document.querySelector(".fg-action-bar-note");
  var submitting = false;

  function keyOf(text) { return text.trim().replace(/\s+/g, " ").toLowerCase(); }

  /* ---------- chips ---------- */

  function chipValues(listId) {
    return Array.prototype.map.call(
      document.querySelectorAll("#" + listId + " input[type=hidden]"),
      function (input) { return keyOf(input.value); }
    );
  }

  function addChip(listId, text) {
    var value = text.trim().replace(/\s+/g, " ");
    var list = document.getElementById(listId);
    var template = document.getElementById(listId + "-template");
    if (value.length < 2 || !list || !template) return false;
    if (chipValues(listId).indexOf(keyOf(value)) !== -1) return true; // already there
    var chip = template.content.firstElementChild.cloneNode(true);
    var walker = document.createTreeWalker(chip, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) {
      walker.currentNode.nodeValue = walker.currentNode.nodeValue.replace("__label__", value);
    }
    var hidden = chip.querySelector("input[type=hidden]");
    if (hidden) hidden.value = value;
    var remove = chip.querySelector(".fg-chip-remove");
    if (remove) remove.setAttribute("aria-label", "Remove " + value);
    list.appendChild(chip);
    return true;
  }

  function addFromRow(row) {
    var input = row.querySelector("input[type=text]");
    if (!input) return;
    if (addChip(row.getAttribute("data-chip-list"), input.value)) input.value = "";
    input.focus();
    changed();
  }

  Array.prototype.forEach.call(form.querySelectorAll(".fg-add-row[data-chip-list]"), function (row) {
    var button = row.querySelector("button");
    var input = row.querySelector("input[type=text]");
    if (button) button.addEventListener("click", function () { addFromRow(row); });
    if (input) input.addEventListener("keydown", function (event) {
      if (event.key === "Enter") { event.preventDefault(); addFromRow(row); }
    });
  });

  form.addEventListener("click", function (event) {
    var remove = event.target.closest && event.target.closest(".fg-chip-remove");
    if (!remove) return;
    var chip = remove.closest("li");
    var list = chip && chip.parentNode;
    if (chip) chip.remove();
    var row = list && form.querySelector('.fg-add-row[data-chip-list="' + list.id + '"] input[type=text]');
    if (row) row.focus();
    changed();
  });

  /* Typed but not yet added text is not lost on save. */
  form.addEventListener("submit", function () {
    Array.prototype.forEach.call(form.querySelectorAll(".fg-add-row[data-chip-list]"), function (row) {
      var input = row.querySelector("input[type=text]");
      if (input && input.value.trim()) {
        if (addChip(row.getAttribute("data-chip-list"), input.value)) input.value = "";
      }
    });
    submitting = true;
  });

  /* ---------- level: "one level up" only makes sense once a level below the top is chosen ---------- */

  function syncLevel() {
    var picked = form.querySelector("input[name=level]:checked");
    var up = form.querySelector("[data-needs-level]");
    if (!up) return;
    var applies = picked && picked.value !== form.getAttribute("data-any-level") &&
      picked.value !== form.getAttribute("data-top-level");
    up.hidden = !applies;
    var box = up.querySelector("input[type=checkbox]");
    if (box && !applies) box.checked = false;
  }

  /* ---------- what changed ---------- */

  function snapshot() {
    var work = Array.prototype.map.call(form.querySelectorAll("input[name=work]:checked"), function (i) { return i.value; });
    var level = form.querySelector("input[name=level]:checked");
    var pendingTitle = (form.elements.titles_new && form.elements.titles_new.value.trim()) ? [form.elements.titles_new.value] : [];
    var pendingSkill = (form.elements.skills_new && form.elements.skills_new.value.trim()) ? [form.elements.skills_new.value] : [];
    var up = form.querySelector("input[name=one_level_up]");
    return {
      titles: chipValues("titles-list").concat(pendingTitle.map(keyOf)).sort().join("|"),
      skills: chipValues("skills-list").concat(pendingSkill.map(keyOf)).sort().join("|"),
      level: level ? level.value : "",
      up: up && up.checked ? "1" : "",
      city: form.elements.city ? keyOf(form.elements.city.value) : "",
      work: work.sort().join("|")
    };
  }

  var initial = snapshot();
  /* Opened already holding an unsaved draft (fields read from a resume, or a refused save)? The page says
     what the saved profile is, so the draft counts as changes. */
  if (mode === "settings") {
    try {
      var savedState = JSON.parse(form.getAttribute("data-saved") || "null");
      if (savedState) initial = savedState;
    } catch (e) { /* keep the snapshot taken from the page */ }
  }

  function changeCount() {
    var now = snapshot();
    return Object.keys(initial).filter(function (k) { return initial[k] !== now[k]; }).length;
  }

  function fill(template, n) { return template.replace("{n}", String(n)); }

  function syncBar() {
    if (mode === "setup") {
      var ready = chipValues("titles-list").length > 0 ||
        !!(form.elements.titles_new && form.elements.titles_new.value.trim().length >= 2);
      if (save) save.disabled = !ready;
      if (note) note.textContent = form.getAttribute(ready ? "data-ready" : "data-blocked");
      return;
    }
    var n = changeCount();
    if (save) save.disabled = n === 0;
    if (discard) discard.disabled = n === 0;
    if (note) {
      note.textContent = n === 0 ? form.getAttribute("data-clean")
        : fill(form.getAttribute(n === 1 ? "data-one" : "data-many"), n);
    }
  }

  /* ---------- live side panel ---------- */

  var timer = null, latest = 0;

  function refreshPanel() {
    if (!panel || !previewUrl) return;
    var ticket = ++latest;
    fetch(previewUrl, { method: "POST", body: new FormData(form), credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.text() : null; })
      .then(function (html) { if (html !== null && ticket === latest) panel.innerHTML = html; })
      .catch(function () { /* keep the panel as it was */ });
  }

  function changed() {
    syncLevel();
    syncBar();
    clearTimeout(timer);
    timer = setTimeout(refreshPanel, 400);
  }

  form.addEventListener("input", changed);
  form.addEventListener("change", changed);

  if (discard) discard.addEventListener("click", function () { submitting = true; window.location.href = window.location.pathname; });

  window.addEventListener("beforeunload", function (event) {
    if (mode === "settings" && !submitting && changeCount() > 0) {
      event.preventDefault();
      event.returnValue = "";
    }
  });

  syncLevel();
  syncBar();
})();
