# Picks intake — Google Apps Script setup

One-time setup so the in-browser picks UI can POST entries directly to a Google Sheet you own.

## Steps

1. Create a fresh Google Sheet (e.g. "NBA Playoffs Fantasy 2026 — Picks").
2. In that Sheet: **Extensions → Apps Script**. A new editor tab opens.
3. Replace the contents of `Code.gs` in the editor with the contents of `apps_script/Code.gs` from this repo.
4. Click **Deploy → New deployment**.
   - Type: **Web app**
   - Description: anything (e.g. "picks intake v1")
   - Execute as: **Me** (your Google account)
   - Who has access: **Anyone**
5. Click **Deploy**. Authorize the script when prompted (it needs permission to write to the Sheet).
6. Copy the **Web app URL** that ends in `/exec`.
7. Paste it into `data/config.json` as the value of `picks_endpoint`:
   ```json
   {
     ...
     "picks_endpoint": "https://script.google.com/macros/s/XXX/exec"
   }
   ```
8. Reload the site. Submitted picks now land as rows in the Sheet.

## What gets recorded

Each submission writes one row with: timestamp, entrant name, total effective cost, then 8 columns of `Player (Team)` + cost, plus a final `Raw JSON` column with the full payload.

## Curating + publishing entries

Submissions land in the Sheet but don't automatically appear on the scoreboard. To filter out tests/invalid rosters and publish the cleaned list:

1. Edit the Sheet directly — delete bogus rows, fix typos in entrant names. (You can resubmit valid rosters too; the script keeps the latest submission per name.)
2. From the project root, run:
   ```
   python3 scripts/sync_picks.py
   ```
   This pulls the current Sheet state via the same Web App URL (using `?action=picks`), dedupes by entrant name (latest submission wins), and writes `data/picks.json`.
3. Reload the scoreboard. Only the curated rosters appear.

For a preview without writing the file:
```
python3 scripts/sync_picks.py --dry-run
```

After deploying a new version of `Code.gs`, you'll need to re-deploy the Web App so the new `?action=picks` GET handler is live.

## Updating

If you change `Code.gs`, redeploy: **Deploy → Manage deployments → Edit (pencil) → Version: New version → Deploy**. The same `/exec` URL keeps working.

## Troubleshooting

- "Failed to submit": check the Apps Script execution log (Apps Script editor → Executions). Most common cause is the deployment access setting — must be **Anyone**, not "Anyone with Google account".
- The frontend posts with `Content-Type: text/plain` to avoid a CORS preflight; Apps Script still parses the body via `e.postData.contents`.
