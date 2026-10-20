// The matrix (step 2): an objective under several risks has a key tick in each of its rows. They are
// one choice, so ticking or unticking any of them sets them all.
document.addEventListener("change", function (event) {
  var box = event.target;
  if (!box.matches || !box.matches('input[name="key"]')) return;
  document.querySelectorAll('input[name="key"]').forEach(function (other) {
    if (other !== box && other.value === box.value) other.checked = box.checked;
  });
});
