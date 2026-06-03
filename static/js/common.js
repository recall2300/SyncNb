// ── Theme ─────────────────────────────────────────────────────────────────────

(function () {
  var saved = localStorage.getItem('syncnb-theme');
  var prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
  var theme = saved || (prefersDark ? 'dark' : 'light');
  document.documentElement.setAttribute('data-theme', theme);
})();

function toggleTheme() {
  var html = document.documentElement;
  var next = html.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
  html.setAttribute('data-theme', next);
  localStorage.setItem('syncnb-theme', next);
  var btn = document.getElementById('theme-btn');
  if (btn) btn.textContent = next === 'dark' ? '☀' : '☽';
}

document.addEventListener('DOMContentLoaded', function () {
  var btn = document.getElementById('theme-btn');
  if (btn) {
    btn.textContent = document.documentElement.getAttribute('data-theme') === 'dark' ? '☀' : '☽';
  }
});

// ── Toast ─────────────────────────────────────────────────────────────────────

let toastTimer = null;

function escHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#x27;");
}

function showToast(msg, type = "info") {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.className = `show ${type}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.className = "", 3000);
}
