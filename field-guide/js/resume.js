/* Behavior for adding and deleting a resume (SC-46). The file inputs sit outside any other form and point at
   #resume-form with their form attribute; choosing or dropping a PDF sends it straight away, and a progress bar
   with changing text shows while it is read. All wording comes from the page: the steps are in the progress
   element's data-steps, the delete confirmation in the delete form's data-confirm. */
(function () {
  "use strict";

  var form = document.getElementById("resume-form");
  var progress = document.getElementById("resume-progress");
  var STEP_SECONDS = 2.6;   // how long each line stays up
  var SETTLE_SECONDS = 7;   // the bar eases toward full over about this long, and never finishes early
  var CEILING = 92;         // percent: it only reaches the end when the next page loads

  function startProgress() {
    if (!progress) return;
    var steps = [];
    try { steps = JSON.parse(progress.getAttribute("data-steps") || "[]"); } catch (e) { /* no wording: bar only */ }
    var text = progress.querySelector(".fg-progress-text");
    var fill = progress.querySelector(".fg-progress-bar span");
    var started = Date.now();
    var shown = 0;

    function tick() {
      var seconds = (Date.now() - started) / 1000;
      if (fill) fill.style.width = (CEILING * (1 - Math.exp(-seconds / SETTLE_SECONDS))).toFixed(1) + "%";
      if (text && steps.length) {
        var next = Math.min(steps.length - 1, Math.floor(seconds / STEP_SECONDS));
        if (next !== shown) { shown = next; text.textContent = steps[next]; }
      }
    }
    progress.hidden = false;
    tick();
    window.setInterval(tick, 250);
  }

  function send() {
    startProgress();
    /* requestSubmit fires the submit event, which is how the profile page knows this navigation is on purpose. */
    if (form.requestSubmit) form.requestSubmit();
    else if (form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }))) form.submit();
  }

  if (form) {
    Array.prototype.forEach.call(document.querySelectorAll('input[type=file][form="resume-form"]'), function (input) {
      input.addEventListener("change", function () {
        if (input.files && input.files.length) send();
      });
    });

    Array.prototype.forEach.call(document.querySelectorAll(".fg-dropzone"), function (zone) {
      var input = zone.querySelector('input[type=file][form="resume-form"]');
      if (!input) return;
      ["dragenter", "dragover"].forEach(function (name) {
        zone.addEventListener(name, function (event) { event.preventDefault(); zone.classList.add("is-dragover"); });
      });
      ["dragleave", "dragend"].forEach(function (name) {
        zone.addEventListener(name, function () { zone.classList.remove("is-dragover"); });
      });
      zone.addEventListener("drop", function (event) {
        event.preventDefault();
        zone.classList.remove("is-dragover");
        if (event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files.length) {
          input.files = event.dataTransfer.files;
          send();
        }
      });
    });
  }

  var remove = document.getElementById("resume-delete-form");
  if (remove) {
    remove.addEventListener("submit", function (event) {
      var message = remove.getAttribute("data-confirm");
      if (message && !window.confirm(message)) event.preventDefault();
    });
  }
})();
