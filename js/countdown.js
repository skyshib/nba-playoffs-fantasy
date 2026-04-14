/**
 * Header countdown timer to pick-submission deadline.
 * Reads `lock_at` (ISO 8601) from data/config.json. Updates every second.
 */
(async function () {
  const el = document.getElementById('countdown');
  if (!el) return;
  let cfg;
  try {
    cfg = await fetch('data/config.json?t=' + Date.now()).then(r => r.json());
  } catch (e) {
    el.textContent = '';
    return;
  }
  const lockAt = cfg.lock_at ? new Date(cfg.lock_at).getTime() : null;
  if (!lockAt) { el.textContent = ''; return; }

  function pad(n) { return n < 10 ? '0' + n : '' + n; }
  function render() {
    const ms = lockAt - Date.now();
    if (ms <= 0) {
      el.textContent = '🔒 Picks locked';
      el.className = 'countdown locked';
      return;
    }
    const totalSec = Math.floor(ms / 1000);
    const d = Math.floor(totalSec / 86400);
    const h = Math.floor((totalSec % 86400) / 3600);
    const m = Math.floor((totalSec % 3600) / 60);
    const s = totalSec % 60;
    const parts = d > 0
      ? `${d}d ${pad(h)}h ${pad(m)}m ${pad(s)}s`
      : `${pad(h)}:${pad(m)}:${pad(s)}`;
    el.textContent = '⏱ Picks lock in ' + parts;
    el.className = 'countdown' + (ms < 3600 * 1000 ? ' urgent' : '');
  }
  render();
  setInterval(render, 1000);
})();
