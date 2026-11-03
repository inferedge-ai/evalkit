const marker = document.querySelector("[data-evalkit-outdated]");
const banner = marker?.closest("[data-md-component=outdated]");

function fillBanner() {
  if (banner.hidden || marker.childElementCount) return;
  const root = new URL(marker.dataset.root, window.location.href);
  const page = window.location.pathname.slice(root.pathname.length);
  const latest = new URL(`../latest/${page}`, root);
  const link = document.createElement("a");
  link.href = latest.href;
  link.innerHTML = "<strong>Go to latest.</strong>";
  marker.append("You're viewing docs for an older or unreleased version. ", link);
}

if (banner) {
  new MutationObserver(fillBanner).observe(banner, { attributes: true, attributeFilter: ["hidden"] });
  fillBanner();
}
