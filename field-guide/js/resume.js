/* Behavior for adding and deleting a resume (SC-46). The file inputs sit outside any other form and point at
   #resume-form with their form attribute; choosing or dropping a PDF sends it straight away. Wording comes from
   the page (the "reading" message and the delete confirmation are in the HTML). */
(function () {
  "use strict";

  var form = document.getElementById("resume-form");
  var busy = document.getElementById("resume-busy");

  function send() {
    if (busy) busy.hidden = false;
    form.submit();
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
