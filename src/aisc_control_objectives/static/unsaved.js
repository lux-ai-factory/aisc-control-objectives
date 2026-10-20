// The assessment page holds several forms (the ratings and their rationales, the key ticks, each
// risk's objectives) and buttons that reload it. Anything typed into one form and not saved is lost
// when the page is left some other way, so the page asks first. Saving the changed form is not
// leaving, nor is picking a part of the left menu (it only shows another part of this page), nor is
// a download.
(function () {
  var MESSAGE = "Leave without saving? What you changed here will be lost.";
  var forms = Array.prototype.slice.call(document.forms);
  if (forms.length === 0) return;

  // FormData also takes the inputs that sit outside a form and name it with form="..." (the key ticks)
  function content(form) {
    return new URLSearchParams(new FormData(form)).toString();
  }
  var saved = forms.map(content);

  function changedBesides(except) {
    return forms.some(function (form, i) {
      return form !== except && content(form) !== saved[i];
    });
  }

  // true once the page is on its way out, so the browser does not ask a second time
  var leaving = false;

  function mayLeave(except) {
    if (!changedBesides(except)) return true;
    leaving = window.confirm(MESSAGE);
    return leaving;
  }

  document.addEventListener("submit", function (event) {
    if (mayLeave(event.target)) leaving = true;
    else event.preventDefault();
  });

  document.addEventListener("click", function (event) {
    var link = event.target.closest ? event.target.closest("a[href]") : null;
    if (!link || link.hasAttribute("download") || link.target === "_blank") return;
    // a link opened in a new tab leaves nothing behind
    if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey) return;
    var to = new URL(link.href, location.href);
    if (to.origin === location.origin && to.pathname === location.pathname && to.search === location.search) return;
    if (!mayLeave(null)) event.preventDefault();
  });

  window.addEventListener("beforeunload", function (event) {
    if (leaving || !changedBesides(null)) return;
    event.preventDefault();
    event.returnValue = MESSAGE;
  });
})();

// A risk's "Edit" popover closed without saving goes back to the objectives saved for that risk:
// the ticks left in it would otherwise be there on the next opening, as if they were saved.
document.querySelectorAll(".co-edit-pop").forEach(function (pop) {
  pop.addEventListener("toggle", function (event) {
    if (event.newState !== "closed") return;
    var form = pop.querySelector("form");
    if (form) form.reset();
  });
});
