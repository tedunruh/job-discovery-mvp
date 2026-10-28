/* Coming back from a job (SC-63). When the tab comes back into view (you read the posting in another tab, then return)
   this asks the server what is true now and updates the cards in place, with no reload and no change of focus: the cards
   you have opened get the quiet Viewed marker, and the one you opened last swaps Apply and Hide for "Did you apply?".
   The cards and their actions are drawn by job.js. */
(function () {
  "use strict";

  var board = document.querySelector("[data-return-url]");
  if (!board || !window.fetch) return;

  var url = board.getAttribute("data-return-url");
  var lastAsked = 0;

  function refresh() {
    var now = Date.now();
    if (now - lastAsked < 2000) return;  // the tab can fire focus and visibility together
    lastAsked = now;
    fetch(url, { credentials: "same-origin", headers: { Accept: "application/json" } })
      .then(function (response) { return response.ok ? response.json() : null; })
      .then(function (state) {
        if (!state || !window.fgJob) return;
        Object.keys(state.viewed || {}).forEach(function (id) { window.fgJob.showViewed(id); });
        window.fgJob.showAsk(state.ask);
      })
      .catch(function () { /* the page stays as it was */ });
  }

  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible") refresh();
  });
  window.addEventListener("focus", refresh);
})();
