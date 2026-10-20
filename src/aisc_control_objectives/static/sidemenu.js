// The assessment page's left menu (project.html.j2): one part in view at a time. Every part stays
// in the page, only hidden, so nothing typed in one is lost by looking at another. The part shown
// is the one the address names (#register, #matrix), or the one holding the element it names (the
// server comes back to #map-<risk> after a risk's objectives are saved); otherwise the first.
(function () {
  var menu = document.querySelector(".co-sidemenu");
  if (!menu) return;
  var parts = Array.prototype.slice.call(document.querySelectorAll(".co-part"));
  var links = menu.querySelectorAll("a[data-part]");
  if (parts.length === 0) return;

  function partFor(hash) {
    var el = hash ? document.getElementById(hash.slice(1)) : null;
    for (var i = 0; i < parts.length; i++) {
      if (el && (parts[i] === el || parts[i].contains(el))) return parts[i];
    }
    return parts[0];
  }

  function show() {
    var part = partFor(location.hash);
    parts.forEach(function (p) {
      p.hidden = p !== part;
    });
    part.open = true;
    links.forEach(function (a) {
      var on = a.dataset.part === part.id;
      a.classList.toggle("active", on);
      if (on) a.setAttribute("aria-current", "true");
      else a.removeAttribute("aria-current");
    });
    // A part picked from the menu starts at the top of the page, title included.
    if (location.hash === "#" + part.id) window.scrollTo(0, 0);
  }

  window.addEventListener("hashchange", show);
  show();
})();
