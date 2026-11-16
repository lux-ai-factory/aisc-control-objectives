// "Suggest with AI" makes one model call per risk while the page waits: it can take minutes, and each
// call is paid. Once its form is sent, its buttons are disabled and say that it is suggesting, so a
// second click cannot start a second run. Loaded after unsaved.js: a send that page cancelled (the
// person chose to stay) leaves the buttons as they were. A page the browser brings back from its
// history gets its buttons back.
(function () {
  var BUSY = "Suggesting: one model call per risk, this can take minutes";

  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (event.defaultPrevented || !form.matches || !form.matches("form[data-busy]")) return;
    form.querySelectorAll("button[type=submit]").forEach(function (button) {
      button.dataset.idle = button.textContent;
      button.textContent = BUSY;
      button.disabled = true;
    });
  });

  window.addEventListener("pageshow", function (event) {
    if (!event.persisted) return;
    document.querySelectorAll("form[data-busy] button[type=submit]").forEach(function (button) {
      if (button.dataset.idle) button.textContent = button.dataset.idle;
      button.disabled = false;
    });
  });
})();
