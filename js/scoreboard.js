/**
 * NBA playoffs fantasy scoring + render.
 *
 * Scoring (per pick):
 *   round_score = (avg of player's top 4 games that round) * (1 + seed/10 if year >= MULT_START)
 *   pick_total  = sum over rounds
 *   entrant_total = sum over 8 seed slots
 */
const Scoreboard = (() => {
  const ROUNDS = ['R1', 'CSF', 'CF', 'Finals'];
  const ROUND_LABELS = { R1: 'Round 1', CSF: 'Conf Semis', CF: 'Conf Finals', Finals: 'Finals' };

  let picksData = null;
  let statsData = null;
  let headshotsData = {};
  let teamLogosData = {};
  let year = null;
  let multStartYear = 2025;
  let liveOverrides = {};
  let liveGamesInfo = [];
  let compactMode = false;

  function setData(picks, stats, headshots, teamLogos, _year, _multStart) {
    picksData = picks;
    statsData = stats;
    headshotsData = headshots || {};
    teamLogosData = teamLogos || {};
    year = _year;
    multStartYear = _multStart || 2025;
  }

  function setLiveOverrides(o) { liveOverrides = o || {}; }
  function setLiveGames(games) { liveGamesInfo = games || []; }

  function multiplierFor(seed) {
    return year >= multStartYear ? (1 + seed / 10) : 1;
  }

  function getRoundPPG(playerId) {
    const p = statsData?.players?.[playerId];
    if (!p) return {};
    if (p.round_ppg) return p.round_ppg;
    const byRound = {};
    for (const g of p.games || []) {
      (byRound[g.round] ||= []).push(g.pts || 0);
    }
    const live = liveOverrides[playerId];
    if (live && live.round && typeof live.pts === 'number') {
      const arr = (byRound[live.round] ||= []);
      if (arr.length > 0) arr[arr.length - 1] = live.pts;
      else arr.push(live.pts);
    }
    const out = {};
    for (const [rd, pts] of Object.entries(byRound)) {
      const top = pts.slice().sort((a, b) => b - a).slice(0, 4);
      out[rd] = top.reduce((a, b) => a + b, 0) / top.length;
    }
    return out;
  }

  function getGames(playerId) {
    const p = statsData?.players?.[playerId];
    return p?.games || [];
  }

  function getTeamName(pick) {
    if (pick.team) return pick.team;
    const p = statsData?.players?.[pick.player_id];
    return p?.team || '';
  }

  function isEliminated(playerId) {
    const p = statsData?.players?.[playerId];
    if (!p) return false;
    return !!p.eliminated;
  }

  function isLive(playerId) {
    return !!liveOverrides[playerId];
  }

  function effectiveCost(cost) {
    const budget = window.__NBA_CFG__?.budget || 0;
    if (cost == null) return null;
    return budget > 0 ? Math.max(cost, budget / 16) : cost;
  }

  function scorePick(pick) {
    const seed = pick.seed || parseInt(pick.seed, 10);
    const ppg = getRoundPPG(pick.player_id);
    const mult = multiplierFor(seed);
    const perRound = {};
    let subtotal = 0;
    let roundsPlayed = 0;
    let rawTotal = 0;
    for (const rd of ROUNDS) {
      const v = ppg[rd];
      if (typeof v === 'number') {
        roundsPlayed++;
        rawTotal += v;
      }
      perRound[rd] = (typeof v === 'number') ? v * mult : 0;
      subtotal += perRound[rd];
    }
    // Points +/- per pick = avg PPG actually achieved per round - cost
    // (matches Excel Pts+/- column)
    const cost = pick.cost;
    const pointsDiff = (cost != null && roundsPlayed > 0)
      ? (rawTotal / roundsPlayed) - cost
      : null;
    return { perRound, subtotal, multiplier: mult, ppg, roundsPlayed, rawTotal, pointsDiff };
  }

  function scoreEntrant(entrant) {
    let total = 0;
    let pointsDiff = 0;
    let ppgRem = 0;
    const breakdown = {};
    let alive = 0;
    for (let seed = 1; seed <= 8; seed++) {
      const pick = entrant.picks[String(seed)];
      if (!pick) {
        breakdown[seed] = { pick: null, subtotal: 0, perRound: {}, multiplier: 1 };
        continue;
      }
      const sc = scorePick(pick);
      const eliminated = isEliminated(pick.player_id);
      if (!eliminated) {
        alive++;
        const eff = effectiveCost(pick.cost);
        if (eff != null) ppgRem += eff * sc.multiplier;
      }
      if (sc.pointsDiff != null) pointsDiff += sc.pointsDiff;
      breakdown[seed] = { pick, ...sc, eliminated, live: isLive(pick.player_id) };
      total += sc.subtotal;
    }
    return {
      total: Math.round(total * 1000) / 1000,
      pointsDiff: Math.round(pointsDiff * 1000) / 1000,
      ppgRem: Math.round(ppgRem * 100) / 100,
      breakdown,
      alive,
    };
  }

  function rankAll() {
    if (!picksData?.entrants) return [];
    // Only rank real entrants. Synthetic ones (e.g. Best Possible Roster)
    // render above the table, not in it.
    return picksData.entrants
      .filter(e => !e._synthetic)
      .map(e => ({ name: e.name, entrant: e, ...scoreEntrant(e) }))
      .sort((a, b) => b.total - a.total);
  }

  function syntheticEntrants() {
    if (!picksData?.entrants) return [];
    return picksData.entrants
      .filter(e => e._synthetic)
      .map(e => ({ name: e.name, entrant: e, synthetic: true, ...scoreEntrant(e) }))
      .sort((a, b) => b.total - a.total);
  }

  function fmtPts(v) {
    if (!isFinite(v)) return '0';
    return (Math.round(v * 100) / 100).toString();
  }

  function lastName(full) {
    const parts = full.replace(/\s+(Jr\.?|Sr\.?|III|II|IV|V)$/i, '').trim().split(' ');
    return parts[parts.length - 1];
  }

  function renderSynthetic() {
    const host = document.getElementById('synthetic-container');
    if (!host) return;
    const synth = syntheticEntrants();
    if (!synth.length) { host.innerHTML = ''; return; }
    host.innerHTML = '';

    for (const r of synth) {
      const key = 'synth_open_' + r.name.replace(/\W+/g, '_');
      const isOpen = localStorage.getItem(key) === '1';
      const details = document.createElement('details');
      details.className = 'synthetic-card';
      details.dataset.key = key;
      if (isOpen) details.open = true;

      const summary = document.createElement('summary');
      summary.innerHTML = `<span class="synth-title">${r.name}</span><span class="synth-total">${fmtPts(r.total)} pts</span>`;
      details.appendChild(summary);

      // Build a one-row mini-scoreboard matching the main table columns
      const body = document.createElement('div');
      body.className = 'synth-body';
      const table = document.createElement('table');
      table.className = 'synth-scoreboard';
      if (compactMode) table.classList.add('compact-mode');

      const tbody = document.createElement('tbody');
      const tr = document.createElement('tr');

      const tdSpacer = document.createElement('td');
      tdSpacer.className = 'col-rank';
      tdSpacer.textContent = '⭐';
      tr.appendChild(tdSpacer);

      const tdName = document.createElement('td');
      tdName.className = 'col-name';
      tdName.textContent = r.name;
      tr.appendChild(tdName);

      const tdTotal = document.createElement('td');
      tdTotal.className = 'col-total';
      tdTotal.textContent = fmtPts(r.total);
      tr.appendChild(tdTotal);

      // Skip +/-, Alive, PPG Rem columns for synthetic (mostly meaningless)
      const tdSkip1 = document.createElement('td');
      tdSkip1.className = 'col-diff'; tdSkip1.textContent = '—';
      tr.appendChild(tdSkip1);
      const tdSkip2 = document.createElement('td');
      tdSkip2.className = 'col-remaining'; tdSkip2.textContent = `${r.alive}/8`;
      tr.appendChild(tdSkip2);
      const tdSkip3 = document.createElement('td');
      tdSkip3.className = 'col-ppgrem'; tdSkip3.textContent = '—';
      tr.appendChild(tdSkip3);

      // Seed cells — reuse the same builder (no color, no click for tooltip though)
      for (let seed = 1; seed <= 8; seed++) {
        tr.appendChild(buildSeedCell(r, seed, { noColor: true }));
      }

      tbody.appendChild(tr);
      table.appendChild(tbody);
      body.appendChild(table);
      details.appendChild(body);
      host.appendChild(details);
    }

    host.querySelectorAll('details').forEach(d => {
      d.addEventListener('toggle', () => {
        localStorage.setItem(d.dataset.key, d.open ? '1' : '0');
      });
    });
  }

  function render() {
    const tbody = document.getElementById('scoreboard-body');
    if (!tbody) return;
    renderSynthetic();
    const ranked = rankAll();
    tbody.innerHTML = '';

    // Per-player pick counts
    const pickCounts = {};
    const totalEntrants = picksData?.entrants?.length || 0;
    for (const e of picksData?.entrants || []) {
      for (const p of Object.values(e.picks || {})) {
        pickCounts[p.player_id] = (pickCounts[p.player_id] || 0) + 1;
      }
    }

    // Min/max for color gradient
    let minPts = Infinity, maxPts = -Infinity;
    for (const r of ranked) {
      for (let s = 1; s <= 8; s++) {
        const info = r.breakdown[s];
        if (info.pick) {
          if (info.subtotal < minPts) minPts = info.subtotal;
          if (info.subtotal > maxPts) maxPts = info.subtotal;
        }
      }
    }
    if (!isFinite(minPts)) minPts = 0;
    if (!isFinite(maxPts)) maxPts = 0;

    // Uniqueness ranking
    const uniqArr = ranked.map(r => {
      let sum = 0, n = 0;
      for (let s = 1; s <= 8; s++) {
        const p = r.breakdown[s].pick;
        if (p) { sum += pickCounts[p.player_id] || 0; n++; }
      }
      return { name: r.name, avg: n ? sum / n : 0 };
    }).sort((a, b) => a.avg - b.avg);
    const uniqRanks = {};
    uniqArr.forEach((e, i) => { uniqRanks[e.name] = i + 1; });

    // Ranks with ties
    const ranks = [];
    for (let i = 0; i < ranked.length; i++) {
      if (i === 0 || ranked[i].total < ranked[i - 1].total) ranks.push(i + 1);
      else ranks.push(ranks[i - 1]);
    }

    for (let i = 0; i < ranked.length; i++) {
      const r = ranked[i];
      const tr = document.createElement('tr');
      tr.dataset.entrant = r.name;

      const rank = ranks[i];
      const isTied = ranks.filter(x => x === rank).length > 1;
      const badges = ['🥇', '🥈', '🥉', '💲', '💲'];
      const tdRank = document.createElement('td');
      tdRank.className = 'col-rank';
      tdRank.textContent = rank <= 5
        ? `${isTied ? 'T-' : ''}${rank} ${badges[rank - 1]}`
        : `${isTied ? 'T-' : ''}${rank}`;
      tr.appendChild(tdRank);

      const tdName = document.createElement('td');
      tdName.className = 'col-name';
      tdName.textContent = r.name;
      tr.appendChild(tdName);

      const tdTotal = document.createElement('td');
      tdTotal.className = 'col-total';
      tdTotal.textContent = fmtPts(r.total);
      tr.appendChild(tdTotal);

      const tdDiff = document.createElement('td');
      tdDiff.className = 'col-diff';
      const dv = r.pointsDiff;
      if (dv > 0) tdDiff.classList.add('pos');
      else if (dv < 0) tdDiff.classList.add('neg');
      tdDiff.textContent = (dv >= 0 ? '+' : '') + fmtPts(dv);
      tr.appendChild(tdDiff);

      const tdRem = document.createElement('td');
      tdRem.className = 'col-remaining';
      tdRem.textContent = `${r.alive}/8`;
      tr.appendChild(tdRem);

      const tdPpg = document.createElement('td');
      tdPpg.className = 'col-ppgrem';
      tdPpg.textContent = r.alive > 0 ? fmtPts(r.ppgRem) : '—';
      tr.appendChild(tdPpg);

      for (let seed = 1; seed <= 8; seed++) {
        tr.appendChild(buildSeedCell(r, seed, { minPts, maxPts, pickCounts, totalEntrants }));
      }

      tr.addEventListener('click', () => showDetail(r, pickCounts, totalEntrants, uniqRanks));
      tbody.appendChild(tr);
    }
  }

  // Build a single seed-cell <td>. Shared between main scoreboard + synthetic card.
  function buildSeedCell(r, seed, opts) {
    const { minPts, maxPts, pickCounts, totalEntrants, noColor, noTooltip } = opts || {};
    const td = document.createElement('td');
    td.className = 'seed-cell';
    if (compactMode) td.classList.add('compact');
    const info = r.breakdown[seed];
    if (!info.pick) {
      td.textContent = '-';
      td.classList.add('eliminated');
      return td;
    }
    if (info.eliminated) td.classList.add('eliminated');
    if (info.live) td.classList.add('live');

    let gradientBg = null;
    if (!noColor && maxPts > minPts) {
      const t = (info.subtotal - minPts) / (maxPts - minPts);
      const r0 = Math.round(180 - 150 * t);
      const g0 = Math.round(40 + 100 * t);
      const b0 = Math.round(40 + 30 * t);
      gradientBg = `rgb(${r0},${g0},${b0})`;
    }

    if (compactMode) {
      // Compact mode: no split, apply gradient to the whole cell
      if (gradientBg) td.style.backgroundColor = gradientBg;
      const nameEl = document.createElement('span');
      nameEl.className = 'compact-name';
      nameEl.textContent = lastName(info.pick.name);
      td.appendChild(nameEl);
      const ptsEl = document.createElement('span');
      ptsEl.className = 'compact-pts';
      ptsEl.textContent = fmtPts(info.subtotal);
      td.appendChild(ptsEl);
    } else {
      const row = document.createElement('div');
      row.className = 'seed-cell-row';
      const left = document.createElement('div');
      left.className = 'seed-cell-left';
      const right = document.createElement('div');
      right.className = 'seed-cell-right';

      const seedLabel = document.createElement('span');
      seedLabel.className = 'seed-number';
      seedLabel.textContent = year >= multStartYear
        ? `${info.multiplier.toFixed(1)}×`
        : `Seed ${seed}`;
      left.appendChild(seedLabel);

      const hsUrl = headshotsData[info.pick.player_id];
      const teamName = getTeamName(info.pick);
      const logoUrl = teamName ? teamLogosData[teamName] : null;
      if (hsUrl || logoUrl) {
        const wrap = document.createElement('div');
        wrap.className = 'seed-headshot-wrap';
        if (logoUrl) wrap.style.backgroundImage = `url(${logoUrl})`;
        if (hsUrl) {
          const img = document.createElement('img');
          img.className = 'seed-headshot';
          img.src = hsUrl;
          img.alt = '';
          img.onerror = function () { this.style.display = 'none'; };
          wrap.appendChild(img);
        }
        left.appendChild(wrap);
      }

      const nameSpan = document.createElement('span');
      nameSpan.className = 'seed-player-name';
      nameSpan.textContent = lastName(info.pick.name);
      left.appendChild(nameSpan);

      const ptsSpan = document.createElement('span');
      ptsSpan.className = 'seed-pts';
      ptsSpan.textContent = fmtPts(info.subtotal);
      // Gradient paints only the total score (not the whole cell half)
      if (gradientBg) ptsSpan.style.backgroundColor = gradientBg;
      left.appendChild(ptsSpan);

      const costEl = document.createElement('span');
      costEl.className = 'seed-cost';
      costEl.textContent = info.pick.cost != null
        ? `$${info.pick.cost.toFixed(info.pick.cost % 1 === 0 ? 0 : 1)}`
        : '';
      right.appendChild(costEl);

      if (info.roundsPlayed > 0 && info.pointsDiff != null) {
        const diffEl = document.createElement('span');
        diffEl.className = 'seed-diff ' + (info.pointsDiff >= 0 ? 'pos' : 'neg');
        const sign = info.pointsDiff >= 0 ? '+' : '';
        diffEl.textContent = `${sign}${info.pointsDiff.toFixed(1)}`;
        right.appendChild(diffEl);
      }

      row.appendChild(left);
      row.appendChild(right);
      td.appendChild(row);
    }

    if (!noTooltip) {
      td.addEventListener('mouseenter', e => showTooltip(e, info, pickCounts || {}, totalEntrants || 0));
      td.addEventListener('mouseleave', hideTooltip);
    }
    return td;
  }

  function showTooltip(e, info, pickCounts, totalEntrants) {
    hideTooltip();
    const games = getGames(info.pick.player_id);
    const count = pickCounts[info.pick.player_id] || 0;
    const team = getTeamName(info.pick);

    const tip = document.createElement('div');
    tip.id = 'player-tooltip';
    tip.className = 'player-tooltip';

    const multBadge = year >= multStartYear
      ? ` <span class="mult-badge">${info.multiplier.toFixed(1)}×</span>` : '';
    let html = `<div class="tt-header">${info.pick.name}${multBadge}</div>`;
    html += `<div class="tt-team">${team || ''} · ${info.pick.seed} seed · Picked by ${count}/${totalEntrants}</div>`;
    if (info.pick.cost != null) {
      html += `<div class="tt-cost">Cost: ${info.pick.cost.toFixed(1)} PPG</div>`;
    }
    // Live game indicator with clock
    if (info.live) {
      const liveData = liveOverrides[info.pick.player_id];
      const clock = liveData?.gameStatus || '';
      html += `<div class="tt-live-game">LIVE${clock ? ' \u2022 ' + clock : ''}</div>`;
    }

    if (games.length) {
      // One row per round; columns = G1..G7 + Round total (multiplier applied)
      html += `<table class="tt-games"><thead><tr><th>Round</th>`;
      for (let i = 1; i <= 7; i++) html += `<th>G${i}</th>`;
      html += `<th>Total</th></tr></thead><tbody>`;
      const byRound = {};
      for (const g of games) (byRound[g.round] ||= []).push(g);
      for (const rd of ROUNDS) {
        const arr = (byRound[rd] || []).slice().sort((a, b) => (a.game_num || 0) - (b.game_num || 0));
        const top4 = new Set(arr.slice().sort((a, b) => b.pts - a.pts).slice(0, 4));
        html += `<tr><td class="tt-round-label">${ROUND_LABELS[rd]}</td>`;
        for (let i = 0; i < 7; i++) {
          const g = arr[i];
          if (g) {
            const cls = top4.has(g) ? 'top4' : 'other';
            const opp = g.opponent ? ` vs ${g.opponent}` : '';
            html += `<td class="${cls}" title="G${g.game_num || i + 1}${opp}">${fmtPts(g.pts)}</td>`;
          } else {
            html += `<td class="empty">—</td>`;
          }
        }
        const rt = info.perRound[rd];
        html += `<td class="tt-round-total">${typeof rt === 'number' && rt > 0 ? fmtPts(rt) : '—'}</td></tr>`;
      }
      html += `</tbody></table>`;
    } else {
      html += '<div class="tt-no-games">No games played yet</div>';
    }

    html += `<div class="tt-total">Total: ${fmtPts(info.subtotal)}</div>`;

    tip.innerHTML = html;
    document.body.appendChild(tip);

    const rect = e.currentTarget.getBoundingClientRect();
    const tipRect = tip.getBoundingClientRect();
    let left = rect.left + rect.width / 2 - tipRect.width / 2;
    let top = rect.top - tipRect.height - 8;
    if (left < 8) left = 8;
    if (left + tipRect.width > window.innerWidth - 8) left = window.innerWidth - tipRect.width - 8;
    if (top < 8) top = rect.bottom + 8;
    tip.style.left = left + 'px';
    tip.style.top = top + 'px';
    requestAnimationFrame(() => tip.classList.add('visible'));
  }

  function hideTooltip() {
    document.getElementById('player-tooltip')?.remove();
  }

  function showDetail(r, pickCounts, totalEntrants, uniqRanks) {
    const panel = document.getElementById('player-detail');
    const nameEl = document.getElementById('detail-name');
    const contentEl = document.getElementById('detail-content');
    nameEl.textContent = `${r.name} — ${fmtPts(r.total)}`;

    const uRank = uniqRanks?.[r.name] || '?';

    // Most similar entrant
    const my = new Set();
    for (let s = 1; s <= 8; s++) {
      const p = r.breakdown[s].pick;
      if (p) my.add(p.player_id);
    }
    let bestMatch = null, bestOverlap = -1;
    for (const ent of picksData?.entrants || []) {
      if (ent.name === r.name) continue;
      let o = 0;
      for (const p of Object.values(ent.picks || {})) {
        if (my.has(p.player_id)) o++;
      }
      if (o > bestOverlap) { bestOverlap = o; bestMatch = ent.name; }
    }

    let html = `<div class="detail-meta">Uniqueness: #${uRank} of ${totalEntrants}`;
    if (bestMatch) html += ` · Most similar: <strong>${bestMatch}</strong> (${bestOverlap}/8)`;
    html += '</div>';

    // "Rooting for" section — shows which live games matter to this entrant
    if (liveGamesInfo.length > 0) {
      const myTeams = new Set();
      for (let s = 1; s <= 8; s++) {
        const info = r.breakdown[s];
        if (info.pick && !info.eliminated) myTeams.add(info.pick.team?.toLowerCase());
      }
      const rooting = [];
      for (const game of liveGamesInfo) {
        if (!game.teams || game.teams.length < 2) continue;
        const t0 = game.teams[0], t1 = game.teams[1];
        const match0 = [...myTeams].some(pt => (t0.fullName || t0.name || '').toLowerCase().includes(pt));
        const match1 = [...myTeams].some(pt => (t1.fullName || t1.name || '').toLowerCase().includes(pt));
        const n0 = t0.abbrev || t0.name, n1 = t1.abbrev || t1.name;
        if (match0 && !match1) rooting.push(`${n0} over ${n1}`);
        else if (match1 && !match0) rooting.push(`${n1} over ${n0}`);
        else if (match0 && match1) rooting.push(`${n0} vs ${n1} — conflicted!`);
      }
      if (rooting.length > 0) {
        html += '<div class="detail-rooting"><div class="detail-rooting-title">\uD83C\uDFC0 Rooting for...</div>';
        for (const line of rooting) html += `<div class="detail-rooting-line">${line}</div>`;
        html += '</div>';
      }
    }

    html += '<table class="detail-table"><thead><tr>';
    html += '<th>Seed</th><th>Player</th><th>Cost</th>';
    for (const rd of ROUNDS) html += `<th>${ROUND_LABELS[rd]}</th>`;
    html += '<th>Total</th></tr></thead><tbody>';

    for (let seed = 1; seed <= 8; seed++) {
      const info = r.breakdown[seed];
      if (!info.pick) {
        html += `<tr><td>${seed}</td><td colspan="${4 + ROUNDS.length}" style="color:var(--text-muted)">—</td></tr>`;
        continue;
      }
      const elim = info.eliminated ? ' style="opacity:.6;text-decoration:line-through"' : '';
      const liveRow = info.live ? ' class="detail-live-row"' : '';
      const cost = info.pick.cost != null ? info.pick.cost.toFixed(1) : '—';
      const multStr = year >= multStartYear ? ` <span style="color:var(--text-muted);font-size:.7em">${info.multiplier.toFixed(1)}×</span>` : '';
      const hsUrl = headshotsData[info.pick.player_id];
      const hsImg = hsUrl ? `<img class="detail-headshot" src="${hsUrl}" alt="" onerror="this.style.display='none'">` : '';
      const liveBadge = info.live ? ' <span style="color:var(--live-green);font-size:.7em">● LIVE</span>' : '';
      html += `<tr${elim}${liveRow}><td>${seed}${multStr}</td><td><span class="detail-player-cell">${hsImg}${info.pick.name}${liveBadge}</span></td><td>${cost}</td>`;
      for (const rd of ROUNDS) {
        const ppg = info.ppg?.[rd];
        const scored = info.perRound[rd];
        const cell = (typeof ppg === 'number')
          ? `<span title="${fmtPts(ppg)} PPG">${fmtPts(scored)}</span>`
          : '<span class="nope">—</span>';
        html += `<td class="detail-round-cell">${cell}</td>`;
      }
      html += `<td><strong>${fmtPts(info.subtotal)}</strong></td></tr>`;
    }
    html += '</tbody></table>';

    // Per-game breakdown
    html += '<h3 style="margin-top:1.5rem;font-size:1rem">Per-game scoring</h3>';
    for (let seed = 1; seed <= 8; seed++) {
      const info = r.breakdown[seed];
      if (!info.pick) continue;
      const games = getGames(info.pick.player_id);
      if (!games.length) continue;
      html += `<div style="margin-top:.6rem"><strong>Seed ${seed} · ${info.pick.name}</strong>`;
      const byRound = {};
      for (const g of games) (byRound[g.round] ||= []).push(g);
      html += '<div style="font-size:.8rem;color:var(--text-secondary);margin-top:.2rem">';
      for (const rd of ROUNDS) {
        const arr = byRound[rd];
        if (!arr) continue;
        const top4 = new Set(arr.slice().sort((a, b) => b.pts - a.pts).slice(0, 4));
        const cells = arr.map(g => {
          const isTop = top4.has(g);
          return `<span class="game-pill ${isTop ? 'top4' : ''}">${fmtPts(g.pts)}</span>`;
        }).join('');
        html += `<div>${ROUND_LABELS[rd]}: ${cells}</div>`;
      }
      html += '</div></div>';
    }

    contentEl.innerHTML = html;
    panel.classList.remove('hidden');
    panel.classList.add('visible');
  }

  function hideDetail() {
    const panel = document.getElementById('player-detail');
    panel.classList.remove('visible');
    panel.classList.add('hidden');
  }

  function toggleCompact() {
    compactMode = !compactMode;
    document.getElementById('scoreboard')?.classList.toggle('compact-mode', compactMode);
    render();
    return compactMode;
  }

  function markEliminated(slugs) {
    if (!statsData) return;
    if (!statsData.players) statsData.players = {};
    for (const slug of slugs) {
      if (statsData.players[slug]) {
        statsData.players[slug].eliminated = true;
      } else {
        statsData.players[slug] = { name: '', team: '', seed: 0, eliminated: true, games: [] };
      }
    }
    render();
  }

  return { setData, setLiveOverrides, setLiveGames, markEliminated, render, rankAll, scoreEntrant, hideDetail, toggleCompact, ROUNDS };
})();
