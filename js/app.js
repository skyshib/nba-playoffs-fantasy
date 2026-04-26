/**
 * App init: config + year routing + data load + live refresh + elimination
 * banners + live game tracker with player breakdown.
 *
 * Ported from march-madness-fantasy with NBA-specific adaptations:
 *   - 8 seeds (not 16), no captains (seed multiplier instead)
 *   - NBA team logos via abbreviation-based CDN URL
 *   - Round classifier handles "1st Round", "East Finals", etc.
 */
(async function () {
  let currentYear = null;
  let config = null;
  let refreshTimer = null;
  let liveTimer = null;
  let currentPicks = null;
  let currentStats = null;
  let currentHeadshots = {};
  let currentTeamLogos = {};
  let espnIdToSlug = {};
  let nameToSlug = {};
  let knownEliminated = new Set();
  let knownCompletedGames = new Set();
  let bannerShownForSlugs = new Set();
  let isFirstLoad = true;
  let lastLive = null;

  // --- Helpers ---

  function normalizeName(name) {
    let n = name.toLowerCase().replace(/['\.\-\u2019]/g, '').replace(/\s+(jr|sr|iii|ii|iv|v)$/i, '').replace(/\s+/g, ' ').trim();
    return n;
  }

  function teamsMatch(pickTeam, espnTeam) {
    if (!pickTeam || !espnTeam) return false;
    const pt = pickTeam.toLowerCase().trim();
    const et = espnTeam.toLowerCase().trim();
    if (pt === et) return true;
    // Strip mascot (last 1-2 words)
    const words = et.split(' ');
    if (pt === words.slice(0, -1).join(' ')) return true;
    if (words.length > 3 && pt === words.slice(0, -2).join(' ')) return true;
    return false;
  }

  function buildPlayerMappings(picks, headshots) {
    espnIdToSlug = {};
    nameToSlug = {};
    for (const [slug, url] of Object.entries(headshots || {})) {
      const match = url.match(/\/full\/(\d+)\.png/);
      if (match) espnIdToSlug[match[1]] = slug;
    }
    for (const entrant of picks.entrants || []) {
      for (const [seed, pick] of Object.entries(entrant.picks || {})) {
        nameToSlug[normalizeName(pick.name)] = pick.player_id;
      }
    }
  }

  function translateLiveStats(espnStats) {
    const translated = {};
    for (const [espnId, stats] of Object.entries(espnStats)) {
      let slug = espnIdToSlug[espnId];
      if (!slug && stats.name) slug = nameToSlug[normalizeName(stats.name)];
      if (slug) translated[slug] = stats;
    }
    return translated;
  }

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

  // --- Data loading ---
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

    currentPicks = picks;
    currentStats = stats;
    currentHeadshots = headshots;
    currentTeamLogos = teamLogos;

    buildPlayerMappings(picks, headshots);

    Scoreboard.setData(picks, stats, headshots, teamLogos, year, config.multiplier_start_year);
    Scoreboard.setLiveOverrides({});
    Scoreboard.render();

    if (isCurrent) {
      checkForEliminations(picks, stats);
      // Seed knownCompletedGames from ESPN so we don't re-trigger old eliminations
      try {
        const scoreboard = await ESPN.getPlayoffScoreboard();
        for (const ev of scoreboard.events || []) {
          if (ESPN.isPlayIn(ev)) continue;
          const comp = ev.competitions?.[0];
          if (comp?.status?.type?.state === 'post') {
            knownCompletedGames.add(ev.id);
            for (const team of comp.competitors || []) {
              if (team.winner === false) {
                const losingTeam = team.team?.displayName || '';
                for (const ent of currentPicks.entrants || []) {
                  for (const [seed, pick] of Object.entries(ent.picks || {})) {
                    if (teamsMatch(pick.team, losingTeam)) {
                      bannerShownForSlugs.add(pick.player_id);
                    }
                  }
                }
              }
            }
          }
        }
      } catch (e) {}
      refreshFromESPN();
    }

    renderLiveGames(stats);
    updateIndicator(stats);
  }

  // --- Year selector ---
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

  // --- Live games tracker with player breakdown ---
  function renderLiveGames(stats, livePlayerStats) {
    const c = document.getElementById('live-games-container');
    if (!c) return;
    const liveGames = stats.live_games || [];
    if (!liveGames.length) { c.innerHTML = ''; return; }

    let html = '<div class="live-games">';
    for (const game of liveGames) {
      html += '<div class="live-game-card">';
      // Series record from team_series data
      const teams = game.teams || [];
      let seriesLine = '';
      if (teams.length >= 2) {
        const ts = stats?.team_series || {};
        const t0name = teams[0].name || teams[0].abbrev || '';
        const t1name = teams[1].name || teams[1].abbrev || '';
        const s0 = ts[t0name] || ts[t1name];
        if (s0 && s0.summary) {
          seriesLine = s0.summary;
        } else if (s0) {
          seriesLine = `Series ${s0.wins}-${s0.losses}`;
        }
      }
      const seriesHtml = seriesLine ? `<span class="live-game-series">${seriesLine}</span>` : '';
      html += `<div class="live-game-status">${game.status || 'Live'}${seriesHtml}</div>`;
      html += '<div class="live-game-matchup">';
      for (let i = 0; i < teams.length; i++) {
        const t = teams[i];
        const otherScore = parseInt(teams[1 - i]?.score || 0);
        const myScore = parseInt(t.score || 0);
        const isWinning = myScore >= otherScore && (i === 0 || myScore > otherScore);
        html += `<div class="live-game-team ${isWinning ? 'winning' : ''}">`;
        html += `<img class="live-game-logo" src="${t.logo || ''}" alt="" onerror="this.style.display='none'">`;
        html += '<div class="live-game-team-info">';
        html += `<span class="live-game-team-name">${t.abbrev || t.name || ''}</span>`;
        html += `<span class="live-game-score">${t.score || '0'}</span>`;
        html += '</div></div>';
        if (i === 0) html += '<div class="live-game-vs">vs</div>';
      }
      html += '</div>';

      // Players grouped by side — show which entrants' picks are in this game
      const hasPicks = teams.some(t => {
        for (const ent of currentPicks?.entrants || []) {
          for (const [s, pick] of Object.entries(ent.picks || {})) {
            if (teamsMatch(pick.team, t.name || t.fullName)) return true;
          }
        }
        return false;
      });

      if (hasPicks) {
        html += '<div class="live-game-players-row">';
        for (let i = 0; i < teams.length; i++) {
          const t = teams[i];
          const byPlayer = {};
          for (const ent of currentPicks?.entrants || []) {
            for (const [s, pick] of Object.entries(ent.picks || {})) {
              if (teamsMatch(pick.team, t.name || t.fullName)) {
                if (!byPlayer[pick.name]) byPlayer[pick.name] = { owners: [], slug: pick.player_id };
                byPlayer[pick.name].owners.push(ent.name);
              }
            }
          }
          const align = i === 0 ? 'left' : 'right';
          html += `<div class="live-game-side-picks ${align}">`;
          for (const [player, data] of Object.entries(byPlayer)) {
            const count = data.owners.length;
            const safeOwners = data.owners.join(', ').replace(/&/g, '&amp;').replace(/"/g, '&quot;');
            const gamePts = livePlayerStats?.[data.slug]?.pts;
            const ptsHtml = gamePts > 0 ? ` <span class="live-game-pts">${gamePts}</span>` : '';
            html += `<div class="live-game-pick" data-owners="${safeOwners}">${count}x ${player}${ptsHtml}</div>`;
          }
          html += '</div>';
          if (i === 0) html += '<div class="live-game-picks-divider"></div>';
        }
        html += '</div>';
      }
      html += '</div>';
    }
    html += '</div>';
    c.innerHTML = html;

    // Attach hover tooltips to player picks in live game cards
    c.querySelectorAll('.live-game-pick[data-owners]').forEach(el => {
      el.addEventListener('mouseenter', (e) => {
        document.getElementById('player-tooltip')?.remove();
        const owners = el.dataset.owners;
        if (!owners) return;
        const playerName = el.textContent.replace(/^\d+x\s*/, '').replace(/\s*\d+$/, '').trim();
        const ownerLines = owners.split(', ').map(o => `<div style="padding:0.1rem 0">${o}</div>`).join('');
        const tip = document.createElement('div');
        tip.id = 'player-tooltip';
        tip.className = 'player-tooltip';
        tip.innerHTML = `<div class="tt-header">${playerName}</div><div style="font-size:0.7rem;color:var(--text-muted);margin-bottom:0.3rem">Picked by</div><div style="font-size:0.8rem;color:var(--text-secondary)">${ownerLines}</div>`;
        document.body.appendChild(tip);
        const rect = el.getBoundingClientRect();
        const tipRect = tip.getBoundingClientRect();
        let left = rect.left + rect.width / 2 - tipRect.width / 2;
        let top = rect.top - tipRect.height - 6;
        if (left < 4) left = 4;
        if (left + tipRect.width > window.innerWidth - 4) left = window.innerWidth - tipRect.width - 4;
        if (top < 4) top = rect.bottom + 6;
        tip.style.left = left + 'px';
        tip.style.top = top + 'px';
        tip.classList.add('visible');
      });
      el.addEventListener('mouseleave', () => {
        document.getElementById('player-tooltip')?.remove();
      });
    });
  }

  // --- Elimination detection + dramatic banner ---
  function checkForEliminations(picks, stats) {
    for (const [slug, player] of Object.entries(stats.players || {})) {
      if (player.eliminated) {
        knownEliminated.add(slug);
        bannerShownForSlugs.add(slug);
      }
    }
    localStorage.removeItem('lastElimination');
    isFirstLoad = false;
  }

  let audioCtx = null;
  document.addEventListener('click', () => {
    if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  }, { once: true });

  function playEliminationSound() {
    try {
      if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      if (audioCtx.state === 'suspended') audioCtx.resume();
      const t = audioCtx.currentTime;
      const osc1 = audioCtx.createOscillator(), gain1 = audioCtx.createGain();
      osc1.type = 'sine'; osc1.frequency.setValueAtTime(80, t); osc1.frequency.exponentialRampToValueAtTime(30, t + 1.5);
      gain1.gain.setValueAtTime(0.6, t); gain1.gain.exponentialRampToValueAtTime(0.001, t + 1.5);
      osc1.connect(gain1).connect(audioCtx.destination); osc1.start(t); osc1.stop(t + 1.5);
      const osc2 = audioCtx.createOscillator(), gain2 = audioCtx.createGain();
      osc2.type = 'sawtooth'; osc2.frequency.setValueAtTime(150, t); osc2.frequency.exponentialRampToValueAtTime(50, t + 0.8);
      gain2.gain.setValueAtTime(0.3, t); gain2.gain.exponentialRampToValueAtTime(0.001, t + 0.8);
      osc2.connect(gain2).connect(audioCtx.destination); osc2.start(t); osc2.stop(t + 0.8);
      const osc3 = audioCtx.createOscillator(), gain3 = audioCtx.createGain();
      osc3.type = 'sine'; osc3.frequency.setValueAtTime(60, t + 0.3); osc3.frequency.exponentialRampToValueAtTime(20, t + 2);
      gain3.gain.setValueAtTime(0, t); gain3.gain.setValueAtTime(0.4, t + 0.3); gain3.gain.exponentialRampToValueAtTime(0.001, t + 2);
      osc3.connect(gain3).connect(audioCtx.destination); osc3.start(t); osc3.stop(t + 2);
    } catch (e) {}
  }

  function showEliminationBanner(eliminated) {
    const newSlugs = eliminated.filter(e => !bannerShownForSlugs.has(e.slug));
    if (newSlugs.length === 0) return;
    for (const e of eliminated) bannerShownForSlugs.add(e.slug);

    document.getElementById('elimination-banner')?.remove();
    playEliminationSound();

    const banner = document.createElement('div');
    banner.id = 'elimination-banner';
    banner.className = 'elimination-banner';

    for (let i = 0; i < 20; i++) {
      const skull = document.createElement('span');
      skull.className = 'falling-skull';
      skull.textContent = '\uD83D\uDC80';
      skull.style.left = Math.random() * 100 + '%';
      skull.style.animationDelay = Math.random() * 2 + 's';
      skull.style.animationDuration = (2 + Math.random() * 3) + 's';
      banner.appendChild(skull);
    }

    const content = document.createElement('div');
    content.className = 'elimination-content';

    const byTeam = {};
    for (const p of eliminated) {
      const teamKey = p.team || 'Unknown';
      if (!byTeam[teamKey]) byTeam[teamKey] = { team: teamKey, opponent: p.opponent, seed: p.seed, players: [] };
      byTeam[teamKey].players.push(p);
    }

    function findLogo(teamFullName) {
      if (currentTeamLogos[teamFullName]) return currentTeamLogos[teamFullName];
      for (const [key, url] of Object.entries(currentTeamLogos)) {
        if (teamFullName.toLowerCase().startsWith(key.toLowerCase())) return url;
      }
      return null;
    }

    let html = '<div class="elimination-title">\uD83D\uDC80 DOWN GO THE \uD83D\uDC80</div>';
    html += '<div class="elim-teams-grid">';
    for (const [teamName, group] of Object.entries(byTeam)) {
      const logoUrl = findLogo(teamName);
      const logoHtml = logoUrl ? `<img class="elim-team-logo" src="${logoUrl}" alt="">` : '';
      html += '<div class="elim-team-block">';
      html += `<div class="elim-team-header">${logoHtml}<span class="elim-team-name">${teamName}</span></div>`;
      if (group.opponent) html += `<div class="elim-lost-to">Eliminated by ${group.opponent}</div>`;
      html += '<div class="elim-players">';
      for (const p of group.players) {
        const hsUrl = currentHeadshots[p.slug] || '';
        const hsHtml = hsUrl ? `<img class="elim-headshot" src="${hsUrl}" alt="">` : '';
        const ownerLines = p.owners.map(o => `<span class="owner-line">\u2620\uFE0F ${o}</span>`).join('');
        const totalPts = currentStats?.players?.[p.slug]?.stats?.pts || 0;
        html += '<div class="elim-player-card">';
        html += hsHtml;
        html += '<div class="elim-player-info">';
        html += `<div class="elim-player-name">${p.name}</div>`;
        html += `<div class="elim-player-dates">He scored ${totalPts} points.<br>May he rest in peace.</div>`;
        html += '<div class="elim-player-divider"></div>';
        html += `<div class="elim-player-owners">${ownerLines}</div>`;
        html += '</div></div>';
      }
      html += '</div></div>';
    }
    html += '</div>';
    html += '<div class="elim-dismiss">tap to dismiss</div>';

    content.innerHTML = html;
    banner.appendChild(content);
    banner.addEventListener('click', () => {
      banner.classList.add('banner-exit');
      setTimeout(() => banner.remove(), 500);
    });
    document.body.appendChild(banner);
  }

  // --- Poll ESPN for live data ---
  async function refreshFromESPN() {
    if (!currentYear || currentYear !== config.year || !currentPicks) return;
    try {
      const data = await ESPN.getPlayoffScoreboard();
      const activeIds = [];
      const liveGames = [];
      const newEliminations = [];

      for (const ev of data.events || []) {
        if (ESPN.isPlayIn(ev)) continue;
        const comp = ev.competitions?.[0];
        const state = comp?.status?.type?.state;

        if (state === 'in') {
          activeIds.push(ev.id);
          const statusDetail = comp.status?.type?.shortDetail || '';
          const teams = (comp.competitors || []).map(t => ({
            name: t.team?.displayName || '',
            abbrev: t.team?.abbreviation || '',
            seed: t.curatedRank?.current || t.seed || '',
            score: t.score || '0',
            logo: `https://a.espncdn.com/i/teamlogos/nba/500/${(t.team?.abbreviation || '').toLowerCase()}.png`,
          }));
          liveGames.push({ id: ev.id, status: statusDetail, teams });
        }

        // Detect newly completed games
        if (state === 'post' && !knownCompletedGames.has(ev.id)) {
          knownCompletedGames.add(ev.id);
          for (const team of comp.competitors || []) {
            if (team.winner === false) {
              const losingTeam = team.team?.displayName || '';
              const winner = comp.competitors.find(t => t.winner === true);
              const winnerName = winner?.team?.displayName || '';

              for (const ent of currentPicks.entrants || []) {
                for (const [seed, pick] of Object.entries(ent.picks || {})) {
                  if (teamsMatch(pick.team, losingTeam)) {
                    const existing = newEliminations.find(e => e.slug === pick.player_id);
                    if (existing) {
                      if (!existing.owners.includes(ent.name)) existing.owners.push(ent.name);
                    } else {
                      newEliminations.push({
                        slug: pick.player_id, name: pick.name,
                        team: losingTeam, seed: team.curatedRank?.current || '',
                        owners: [ent.name], opponent: winnerName,
                      });
                    }
                  }
                }
              }
            }
          }
        }
      }

      // NBA series elimination: a team is out only when their opponent wins
      // 4 games in the same series (best-of-7). Track wins per (team, round)
      // across all completed games.
      const seriesWins = {};  // "teamName:round" → win count
      for (const ev of data.events || []) {
        if (ESPN.isPlayIn(ev)) continue;
        const comp2 = ev.competitions?.[0];
        if (comp2?.status?.type?.state !== 'post') continue;
        const rd = ESPN.classifyRound(ev);
        if (!rd) continue;
        for (const team of comp2.competitors || []) {
          if (team.winner === true) {
            const key = (team.team?.displayName || '') + ':' + rd;
            seriesWins[key] = (seriesWins[key] || 0) + 1;
          }
        }
      }

      // A team is eliminated if their opponent in the same round has 4 wins
      const eliminatedTeams = new Set();
      for (const ev of data.events || []) {
        if (ESPN.isPlayIn(ev)) continue;
        const comp2 = ev.competitions?.[0];
        if (comp2?.status?.type?.state !== 'post') continue;
        const rd = ESPN.classifyRound(ev);
        if (!rd) continue;
        for (const team of comp2.competitors || []) {
          const oppTeam = comp2.competitors.find(t => t !== team);
          const oppKey = (oppTeam?.team?.displayName || '') + ':' + rd;
          if ((seriesWins[oppKey] || 0) >= 4) {
            eliminatedTeams.add(team.team?.displayName || '');
          }
        }
      }

      if (eliminatedTeams.size > 0) {
        const allEliminatedSlugs = [];
        for (const ent of currentPicks.entrants || []) {
          for (const [seed, pick] of Object.entries(ent.picks || {})) {
            if (eliminatedTeams.has(pick.team)) {
              allEliminatedSlugs.push(pick.player_id);
            }
          }
        }
        if (allEliminatedSlugs.length > 0) {
          Scoreboard.markEliminated([...new Set(allEliminatedSlugs)]);
        }
      }

      // Show banner only for NEW eliminations (series losses, not single games)
      // newEliminations is already populated from single-game detection above;
      // filter to only include teams that are actually series-eliminated.
      const seriesEliminations = newEliminations.filter(e => eliminatedTeams.has(e.team));
      if (seriesEliminations.length > 0) {
        try { showEliminationBanner(seriesEliminations); } catch (e) {}
      }

      // Fetch recently completed games not yet in stats.json
      const recentlyCompleted = [];
      for (const ev of data.events || []) {
        if (ESPN.isPlayIn(ev)) continue;
        const comp3 = ev.competitions?.[0];
        if (comp3?.status?.type?.state === 'post') {
          const hasInStats = Object.values(currentStats?.players || {}).some(p =>
            (p.games || []).some(g => g.game_id === ev.id)
          );
          if (!hasInStats) recentlyCompleted.push(ev.id);
        }
      }

      if (currentStats) {
        currentStats.active_games = [...activeIds, ...recentlyCompleted];
      }

      let translatedLive = {};
      const fetchIds = [...activeIds, ...recentlyCompleted];
      // Pass live game info to Scoreboard for "Rooting for" feature
      const liveForScoreboard = liveGames.map(g => ({
        teams: g.teams.map(t => ({
          name: t.abbrev || t.name,
          fullName: (t.name || '').toLowerCase(),
          seed: t.seed,
        })),
      }));
      Scoreboard.setLiveGames(liveForScoreboard);

      if (fetchIds.length > 0) {
        const espnLive = await ESPN.getLivePlayerStats(fetchIds);
        if (Object.keys(espnLive).length > 0) {
          translatedLive = translateLiveStats(espnLive);
          Scoreboard.setLiveOverrides(translatedLive);
          Scoreboard.render();
        }
      } else {
        Scoreboard.setLiveOverrides({});
        Scoreboard.render();
      }

      // Update live game tracker with fresh player stats
      renderLiveGames({ live_games: liveGames, players: currentStats?.players, team_series: currentStats?.team_series }, translatedLive);

      lastLive = fetchIds.length > 0 ? new Date() : null;
      updateIndicator(currentStats);
    } catch (e) {
      console.warn('ESPN refresh failed:', e);
    }
  }

  // --- Timers ---
  function startTimers() {
    if (refreshTimer) clearInterval(refreshTimer);
    refreshTimer = setInterval(() => { if (currentYear === config.year) loadYear(currentYear); }, config.refresh_static_ms);
    if (liveTimer) clearInterval(liveTimer);
    liveTimer = setInterval(refreshFromESPN, config.refresh_live_ms);
    setInterval(updateIndicator, 10000);
  }

  // --- View toggle ---
  document.getElementById('view-toggle')?.addEventListener('click', () => {
    const btn = document.getElementById('view-toggle');
    const isCompact = Scoreboard.toggleCompact();
    btn.textContent = isCompact ? 'Full' : 'Compact';
  });

  // --- Detail close ---
  document.getElementById('close-detail')?.addEventListener('click', () => Scoreboard.hideDetail());
  document.addEventListener('keydown', e => { if (e.key === 'Escape') Scoreboard.hideDetail(); });

  // --- Picks modal ---
  const picksModal = document.getElementById('picks-modal');
  document.getElementById('enter-picks-btn')?.addEventListener('click', async () => {
    picksModal.classList.remove('hidden');
    PicksUI.open(config);
  });
  document.getElementById('picks-close')?.addEventListener('click', () => picksModal.classList.add('hidden'));
  picksModal?.addEventListener('click', e => { if (e.target === picksModal) picksModal.classList.add('hidden'); });

  // --- Bug report modal ---
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
      status.textContent = 'Sending\u2026';
      try {
        await fetch(endpoint, {
          method: 'POST', mode: 'no-cors',
          headers: { 'Content-Type': 'text/plain;charset=utf-8' },
          body: JSON.stringify({ action: 'bug', name, description: desc, submitted_at: new Date().toISOString() }),
        });
        status.textContent = '\u2713 Submitted!';
      } catch (err) {
        status.textContent = 'Failed to send: ' + err.message;
      }
    } else {
      status.textContent = '\u2713 Saved locally (endpoint not configured).';
    }
    bugForm.reset();
    loadBugReports();
    setTimeout(() => { status.textContent = ''; }, 3000);
  });

  // --- Init ---
  try {
    config = await loadJSON('data/config.json');
    window.__NBA_CFG__ = config;
    const year = await initYearSelector();
    await loadYear(year);
    startTimers();

    function handleHash() {
      if (location.hash === '#picks') {
        picksModal?.classList.remove('hidden');
        PicksUI.open(config);
        history.replaceState(null, '', location.pathname);
      } else if (location.hash === '#bug') {
        bugModal?.classList.remove('hidden');
        loadBugReports();
        history.replaceState(null, '', location.pathname);
      }
    }
    handleHash();
    window.addEventListener('hashchange', handleHash);
  } catch (e) {
    console.error('Init failed', e);
    document.getElementById('scoreboard-body').innerHTML =
      `<tr><td colspan="14" style="text-align:center;padding:2rem;color:var(--text-muted)">Init failed: ${e.message}</td></tr>`;
  }
})();
