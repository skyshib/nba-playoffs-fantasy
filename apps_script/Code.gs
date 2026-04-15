/**
 * NBA Playoffs Fantasy — pick intake.
 *
 * Deploy as a Web App (Extensions → Apps Script → Deploy → New deployment
 *   → type: Web app, execute as: Me, who has access: Anyone). Copy the
 * resulting /exec URL into data/config.json as `picks_endpoint`.
 *
 * The frontend POSTs JSON like:
 *   { name, submitted_at, picks: { "1": {player_id, name, team, seed, cost, effective_cost}, ... } }
 *
 * We append one row to the active sheet (header auto-created on first write).
 */

const HEADER = [
  'Submitted',
  'Entrant',
  'Email',
  'Total Cost',
  'Tiebreaker',
  'Seed 1 Player', 'Seed 1 Cost',
  'Seed 2 Player', 'Seed 2 Cost',
  'Seed 3 Player', 'Seed 3 Cost',
  'Seed 4 Player', 'Seed 4 Cost',
  'Seed 5 Player', 'Seed 5 Cost',
  'Seed 6 Player', 'Seed 6 Cost',
  'Seed 7 Player', 'Seed 7 Cost',
  'Seed 8 Player', 'Seed 8 Cost',
  'Raw JSON',
];

const BUG_HEADER = ['Submitted', 'Name', 'Description'];

function doPost(e) {
  try {
    const body = JSON.parse(e.postData.contents);

    // Route bug reports to a separate tab
    if (body.action === 'bug') {
      const ss = SpreadsheetApp.getActiveSpreadsheet();
      let bugs = ss.getSheetByName('Bugs');
      if (!bugs) {
        bugs = ss.insertSheet('Bugs');
        bugs.appendRow(BUG_HEADER);
        bugs.setFrozenRows(1);
      }
      bugs.appendRow([
        body.submitted_at || new Date().toISOString(),
        body.name || '(anonymous)',
        body.description || '',
      ]);
      return ContentService
        .createTextOutput(JSON.stringify({ ok: true, kind: 'bug' }))
        .setMimeType(ContentService.MimeType.JSON);
    }

    const sheet = SpreadsheetApp.getActiveSpreadsheet().getActiveSheet();

    // Ensure header
    if (sheet.getLastRow() === 0) {
      sheet.appendRow(HEADER);
      sheet.setFrozenRows(1);
    }

    let totalCost = 0;
    const seedCols = [];
    for (let s = 1; s <= 8; s++) {
      const p = body.picks[String(s)] || {};
      const eff = p.effective_cost != null ? Number(p.effective_cost) : (Number(p.cost) || 0);
      totalCost += eff;
      const playerLabel = p.team ? `${p.name} (${p.team})` : (p.name || '');
      seedCols.push(playerLabel, eff);
    }

    const row = [
      body.submitted_at || new Date().toISOString(),
      body.name || '(unnamed)',
      body.email || '',
      Math.round(totalCost * 100) / 100,
      body.tiebreaker != null ? Number(body.tiebreaker) : '',
      ...seedCols,
      JSON.stringify(body),
    ];
    sheet.appendRow(row);

    return ContentService
      .createTextOutput(JSON.stringify({ ok: true }))
      .setMimeType(ContentService.MimeType.JSON);
  } catch (err) {
    return ContentService
      .createTextOutput(JSON.stringify({ ok: false, error: String(err) }))
      .setMimeType(ContentService.MimeType.JSON);
  }
}

function doGet(e) {
  const action = (e && e.parameter && e.parameter.action) || '';
  if (action === 'picks') {
    // Dump every row's Raw JSON so the admin sync script can rebuild picks.json
    const sheet = SpreadsheetApp.getActiveSpreadsheet().getActiveSheet();
    const lastRow = sheet.getLastRow();
    if (lastRow < 2) {
      return ContentService.createTextOutput(JSON.stringify({ rows: [] }))
        .setMimeType(ContentService.MimeType.JSON);
    }
    const headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
    const rawIdx = headers.indexOf('Raw JSON');
    const subIdx = headers.indexOf('Submitted');
    const nameIdx = headers.indexOf('Entrant');
    const data = sheet.getRange(2, 1, lastRow - 1, sheet.getLastColumn()).getValues();
    const rows = data
      .map((r, i) => ({
        row: i + 2,
        submitted: r[subIdx],
        entrant: r[nameIdx],
        raw: r[rawIdx],
      }))
      .filter(r => r.entrant && r.raw);
    return ContentService.createTextOutput(JSON.stringify({ rows }))
      .setMimeType(ContentService.MimeType.JSON);
  }
  // Friendly default
  return ContentService
    .createTextOutput('NBA Playoffs Fantasy intake — POST picks JSON to this URL. GET ?action=picks for admin sync.')
    .setMimeType(ContentService.MimeType.TEXT);
}
