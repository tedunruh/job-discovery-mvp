/* Toast (Field Guide). Shows one short confirmation at the bottom of the page and takes it away again.
   The page has a live region (#toast-region) in place from the start, so a screen reader announces the toast when it
   is added; nothing here moves focus. It goes after about eight seconds, and later while the pointer is over it or
   focus is inside it. Markup comes from a <template> (fg.toast), never from this script.

       var toast = window.fgToast.show(document.getElementById("toast-template"));
       window.fgToast.hide();
*/
(function () {
  "use strict";

  var region = document.getElementById("toast-region");
  if (!region) return;

  var STAY_MS = 8000;      // how long a toast stays when nobody is pointing at it
  var timer = null;
  var held = false;        // pointer over it or focus inside it

  function stop() {
    if (timer) { clearTimeout(timer); timer = null; }
  }

  function start(ms) {
    stop();
    timer = setTimeout(hide, ms);
  }

  function hide() {
    stop();
    held = false;
    region.textContent = "";
  }

  function show(template) {
    hide();
    var node = template.content.firstElementChild.cloneNode(true);
    region.appendChild(node);
    start(STAY_MS);
    return node;
  }

  function hold() { held = true; stop(); }
  function release() {
    held = false;
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
