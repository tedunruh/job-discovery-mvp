/* Toast (Field Guide). Shows one short confirmation at the bottom of the page and takes it away again.
   The page has a live region (#toast-region) in place from the start, so a screen reader announces the toast when it
   is added; nothing here moves focus. It goes after about eight seconds, and later while the pointer is over it or
   focus is inside it. Markup comes from a <template> (fg.toast), never from this script.

       window.fgToast.show(document.getElementById("toast-template"), { text: "Job hidden.", onUndo: function () {} });
       window.fgToast.hide();

   text    replaces the toast's sentence (the template's own is used without it)
   onUndo  called when its [data-toast-undo] button is pressed; the toast then goes
*/
(function () {
  "use strict";

  var region = document.getElementById("toast-region");
  if (!region) return;

  var STAY_MS = 8000;      // how long a toast stays when nobody is pointing at it
  var timer = null;

  function stop() {
    if (timer) { clearTimeout(timer); timer = null; }
  }

  function start(ms) {
    stop();
    timer = setTimeout(hide, ms);
  }

  function hide() {
    stop();
    region.textContent = "";
  }

  function show(template, options) {
    options = options || {};
    hide();
    var node = template.content.firstElementChild.cloneNode(true);
    if (options.text) node.querySelector(".fg-toast-text").textContent = options.text;
    var undo = node.querySelector("[data-toast-undo]");
    if (undo && options.onUndo) {
      undo.addEventListener("click", function () {
        hide();
        options.onUndo();
      });
    }
    region.appendChild(node);
    start(STAY_MS);
    return node;
  }

  function hold() { stop(); }
  function release() {
    if (region.firstElementChild) start(STAY_MS / 2);
  }

  region.addEventListener("mouseenter", hold);
  region.addEventListener("mouseleave", release);
  region.addEventListener("focusin", hold);
  region.addEventListener("focusout", function (event) {
    if (!region.contains(event.relatedTarget)) release();
  });

  window.fgToast = { show: show, hide: hide };
})();
