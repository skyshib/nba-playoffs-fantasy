/**
 * App init: config + year routing + data load + (optional) live refresh.
 */
(async function () {
  let currentYear = null;
  let config = null;
  let refreshTimer = null;
  let liveTimer = null;
  let lastLive = null;

  async function loadJSON(path) {
    const r = await fetch(path + '?t=' + Date.now());
    if (!r.ok) throw new Error(`Failed to load ${path}: ${r.status}`);
    return r.json();
  }

  function timeAgo(iso) {
    if (!iso) return 'never';
    const s = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
    if (s < 10) return 'just now';
    if (s < 60) return `${s}s ago`;
    const m = Math.floor(s / 60);
    if (m < 60) return `${m}m ago`;
    return `${Math.floor(m / 60)}h ago`;
  }

  function updateIndicator(stats) {
    const el = document.getElementById('update-indicator');
    if (!el) return;
    if (lastLive) {
      el.textContent = `Live · ${timeAgo(lastLive.toISOString())}`;
      el.className = 'update-indicator live';
    } else if (stats?.last_updated) {
      el.textContent = `Updated ${timeAgo(stats.last_updated)}`;
      el.className = 'update-indicator';
    } else {
      el.textContent = `${currentYear} historical`;
      el.className = 'update-indicator';
    }
  }

  async function loadYear(year) {
    currentYear = year;
    const isCurrent = (year === config.year);
    const prefix = isCurrent ? 'data' : `data/${year}`;
    let picks, stats;
    try {
      [picks, stats] = await Promise.all([
        loadJSON(`${prefix}/picks.json`),
        loadJSON(`${prefix}/stats.json`),
      ]);
    } catch (e) {
      console.error('Load failed', e);
      document.getElementById('scoreboard-body').innerHTML =
        `<tr><td colspan="14" style="text-align:center;padding:2rem;color:var(--text-muted)">No data for ${year} yet.</td></tr>`;
      return;
    }
    const [headshots, teamLogos] = await Promise.all([
      loadJSON('data/headshots.json').catch(() => ({})),
      loadJSON('data/team_logos.json').catch(() => ({})),
    ]);
    Scoreboard.setData(picks, stats, headshots, teamLogos, year, config.multiplier_start_year);
    Scoreboard.setLiveOverrides({});
    Scoreboard.render();
    updateIndicator(stats);

    if (isCurrent) {
      refreshFromESPN();
    }
  }

  async function refreshFromESPN() {
    if (!currentYear || currentYear !== config.year) return;
    try {
      const data = await ESPN.getPlayoffScoreboard();
      const activeIds = [];
      const liveGames = [];
      for (const ev of data.events || []) {
        if (ESPN.isPlayIn(ev)) continue;
        const comp = ev.competitions?.[0];
        const state = comp?.status?.type?.state;
        if (state === 'in') {
          activeIds.push(ev.id);
          liveGames.push({
            id: ev.id,
            status: comp.status?.type?.shortDetail || '',
            teams: (comp.competitors || []).map(t => ({
              name: t.team?.shortDisplayName || t.team?.displayName || '',
              fullName: t.team?.displayName || '',
              score: t.score || '0',
              winner: t.winner === true,
              logo: `https://a.espncdn.com/i/teamlogos/nba/500/${t.team?.abbreviation?.toLowerCase() || ''}.png`,
            })),
          });
        }
      }
      renderLiveGames(liveGames);

      if (activeIds.length) {
        const live = await ESPN.getLivePlayerStats(activeIds);
        // Re-key from ESPN id → player slug isn't trivial here; for now we ignore live overlay
        // until we have a proper id mapping (TODO: build from rosters).
        Scoreboard.setLiveOverrides({});
        Scoreboard.render();
        lastLive = new Date();
      } else {
        lastLive = null;
      }
    } catch (e) {
      console.warn('ESPN refresh failed:', e);
    }
  }

  function renderLiveGames(games) {
    const c = document.getElementById('live-games-container');
    if (!c) return;
    if (!games.length) { c.innerHTML = ''; return; }
    let html = '<div class="live-games">';
    for (const g of games) {
      html += '<div class="live-game-card">';
      html += `<div class="live-game-status">${g.status}</div>`;
      html += '<div class="live-game-matchup">';
      for (const t of g.teams) {
        html += `<div class="live-game-team ${t.winner ? 'winning' : ''}">`;
        html += `<img class="live-game-logo" src="${t.logo}" alt="" onerror="this.style.display='none'">`;
        html += `<div><div class="live-game-team-name">${t.name}</div><div class="live-game-score">${t.score}</div></div>`;
        html += '</div>';
      }
      html += '</div></div>';
    }
    html += '</div>';
    c.innerHTML = html;
  }

  async function initYearSelector() {
    const sel = document.getElementById('year-select');
    sel.innerHTML = '';
    for (const y of config.years_available.sort((a, b) => b - a)) {
      const opt = document.createElement('option');
      opt.value = y;
      opt.textContent = y;
      if (y === config.year) opt.selected = true;
      sel.appendChild(opt);
    }
    sel.addEventListener('change', () => loadYear(parseInt(sel.value)));
    return config.year;
  }

  function startTimers() {
    if (refreshTimer) clearInterval(refreshTimer);
    refreshTimer = setInterval(() => { if (currentYear === config.year) loadYear(currentYear); }, config.refresh_static_ms);
    if (liveTimer) clearInterval(liveTimer);
    liveTimer = setInterval(refreshFromESPN, config.refresh_live_ms);
  }

  // --- View toggle
  document.getElementById('view-toggle')?.addEventListener('click', () => {
    const btn = document.getElementById('view-toggle');
    const isCompact = Scoreboard.toggleCompact();
    btn.textContent = isCompact ? 'Full' : 'Compact';
  });

  // --- Detail close
  document.getElementById('close-detail')?.addEventListener('click', () => Scoreboard.hideDetail());
  document.addEventListener('keydown', e => { if (e.key === 'Escape') Scoreboard.hideDetail(); });

  // --- Picks modal
  const picksModal = document.getElementById('picks-modal');
  document.getElementById('enter-picks-btn')?.addEventListener('click', async () => {
    picksModal.classList.remove('hidden');
    PicksUI.open(config);
  });
  document.getElementById('picks-close')?.addEventListener('click', () => picksModal.classList.add('hidden'));
  picksModal?.addEventListener('click', e => { if (e.target === picksModal) picksModal.classList.add('hidden'); });

  // --- Bug report modal
  const bugModal = document.getElementById('bug-modal');
  const bugForm = document.getElementById('bug-form');
  const bugList = document.getElementById('bug-list');

  function loadBugReports() {
    const bugs = JSON.parse(localStorage.getItem('nbaBugReports') || '[]');
    if (bugList) {
      bugList.innerHTML = bugs.length === 0
        ? '<div style="color:var(--text-muted);font-size:0.75rem">No reports yet</div>'
        : bugs.slice(-20).reverse().map(b =>
          `<div class="bug-entry"><span class="bug-entry-name">${b.name}</span> <span class="bug-entry-time">${new Date(b.time).toLocaleString()}</span><br>${b.desc}</div>`
        ).join('');
    }
  }

  document.getElementById('bug-report-btn')?.addEventListener('click', () => {
    bugModal?.classList.remove('hidden');
    loadBugReports();
  });
  document.getElementById('bug-close')?.addEventListener('click', () => bugModal?.classList.add('hidden'));
  bugModal?.addEventListener('click', e => { if (e.target === bugModal) bugModal.classList.add('hidden'); });

  bugForm?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const name = document.getElementById('bug-name').value.trim();
    const desc = document.getElementById('bug-desc').value.trim();
    if (!name || !desc) return;

    const report = { name, desc, time: Date.now() };
    const bugs = JSON.parse(localStorage.getItem('nbaBugReports') || '[]');
    bugs.push(report);
    localStorage.setItem('nbaBugReports', JSON.stringify(bugs));

    const status = document.getElementById('bug-status');
    const endpoint = config.picks_endpoint || '';
    if (endpoint) {
      status.textContent = 'Sending…';
      try {
        await fetch(endpoint, {
          method: 'POST',
          mode: 'no-cors',
          headers: { 'Content-Type': 'text/plain;charset=utf-8' },
          body: JSON.stringify({ action: 'bug', name, description: desc, submitted_at: new Date().toISOString() }),
        });
        status.textContent = '✓ Submitted!';
      } catch (err) {
        status.textContent = 'Failed to send: ' + err.message;
      }
    } else {
      status.textContent = '✓ Saved locally (endpoint not configured).';
    }

    bugForm.reset();
    loadBugReports();
    setTimeout(() => { status.textContent = ''; }, 3000);
  });

  // --- Init
  try {
    config = await loadJSON('data/config.json');
    window.__NBA_CFG__ = config;
    const year = await initYearSelector();
    await loadYear(year);
    startTimers();
  } catch (e) {
    console.error('Init failed', e);
    document.getElementById('scoreboard-body').innerHTML =
      `<tr><td colspan="12" style="text-align:center;padding:2rem;color:var(--text-muted)">Init failed: ${e.message}</td></tr>`;
  }
})();
