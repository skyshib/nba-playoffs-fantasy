/**
 * ESPN NBA API client (live overlay).
 * Filters out play-in games (notes contain "Play-In").
 */
const ESPN = (() => {
  const BASE = 'https://site.api.espn.com/apis/site/v2/sports/basketball/nba';

  async function fetchJSON(url) {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), 10000);
    try {
      const r = await fetch(url, { signal: ctrl.signal });
      if (!r.ok) throw new Error(`ESPN ${r.status}`);
      return r.json();
    } finally {
      clearTimeout(t);
    }
  }

  function isPlayIn(event) {
    const notes = event.competitions?.[0]?.notes || [];
    return notes.some(n => /play-?in/i.test(n.headline || ''));
  }

  // Map ESPN headline → our round code
  function classifyRound(event) {
    const notes = event.competitions?.[0]?.notes || [];
    for (const n of notes) {
      const h = (n.headline || '').toLowerCase();
      if (h.includes('first round')) return 'R1';
      if (h.includes('conference semi') || h.includes('semifinal')) return 'CSF';
      if (h.includes('conference final')) return 'CF';
      if (h.includes('nba final') || h.includes('finals')) return 'Finals';
    }
    return null;
  }

  async function getPlayoffScoreboard() {
    // seasontype=3 = postseason
    return fetchJSON(`${BASE}/scoreboard?seasontype=3&limit=100`);
  }

  async function getGameSummary(eventId) {
    return fetchJSON(`${BASE}/summary?event=${eventId}`);
  }

  function extractPlayerStats(summary) {
    const out = {};
    const box = summary.boxscore;
    if (!box || !box.players) return out;
    const teamNames = (box.players || []).map(tb => tb.team?.displayName || '');
    const oppOf = t => teamNames.find(x => x !== t) || '';
    for (const tb of box.players) {
      const team = tb.team?.displayName || '';
      for (const sg of tb.statistics || []) {
        const labels = (sg.labels || []).map(l => l.toLowerCase());
        const ptsIdx = labels.indexOf('pts');
        for (const a of sg.athletes || []) {
          const id = a.athlete?.id;
          if (!id) continue;
          const row = a.stats || [];
          out[id] = {
            name: a.athlete?.displayName || '',
            team, opponent: oppOf(team),
            pts: ptsIdx >= 0 ? parseInt(row[ptsIdx]) || 0 : 0,
          };
        }
      }
    }
    return out;
  }

  async function getLivePlayerStats(gameIds) {
    if (!gameIds?.length) return {};
    const results = await Promise.allSettled(gameIds.map(id => getGameSummary(id)));
    const merged = {};
    for (const r of results) {
      if (r.status !== 'fulfilled') continue;
      const s = extractPlayerStats(r.value);
      Object.assign(merged, s);
    }
    return merged;
  }

  return { getPlayoffScoreboard, getGameSummary, getLivePlayerStats, classifyRound, isPlayIn };
})();
