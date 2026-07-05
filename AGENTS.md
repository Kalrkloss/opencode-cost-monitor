# OpenCode Cost Monitor — AGENTS.md

Live TUI that watches opencode.db and shows real-time cost stats, model breakdown, cache economics, and daily trends. Single-file Python app (639 lines).

## Remotes

Push to **both** on every change:
- `github.com/MikeCase/opencode-cost-monitor.git`
- `fj.splaq.us/splaq/opencode-cost-monitor.git`

## Stack & Key Files

| File | Role |
|------|------|
| `opencode-cost.py` | Single-file app — `SessionData` data layer + `CostMonitor` TUI |
| `opencode-tray.py` | Windows 11 tray app — same data layer + `pystray`/`tkinter` GUI |
| `opencode-tray.bat` | Launches tray app silently via `pythonw.exe` |
| `pricing.json` | Per-model token pricing for Go ($10/mo) and Zen (pay-per-token) plans |

Runs directly with Python 3.12+ and `textual` (TUI) or `pystray`+`Pillow` (tray). See `requirements.txt`.

## Commands

```sh
# Setup (one-time)
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Run (Unix)
./opencode-cost.py
./opencode-cost.py --db /custom/path/opencode.db

# Run (Windows, TUI)
occ.bat
occ.bat --db C:\path\to\opencode.db

# Run (Windows, tray)
opencode-tray.bat
opencode-tray.bat --db C:\path\to\opencode.db

# Syntax check
python3 -m py_compile opencode-cost.py
python3 -m py_compile opencode-tray.py

# Build EXE + MSI
# Requires: pip install pyinstaller, WiX Toolset for MSI (optional)
.\build.ps1
```

The script auto-detects `.venv` and re-execs itself with the venv Python. On Unix this uses `.venv/bin/python3`. On Windows use `occ.bat` instead — the bootstrap path check won't match `.venv\Scripts\python.exe`.

## Committing

```sh
git add opencode-cost.py pricing.json opencode-tray.py opencode-tray.bat build.ps1 requirements.txt
git commit -m "desc"
git push origin main && git push github main
```

After push, send email report to michael.e.case@gmail.com via Brevo (homelab account).

## Keybindings

| Key | Action |
|-----|--------|
| `q` | Quit |
| `r` | Refresh now |
| `m` | Cycle time period (all / month / week / day) |
| `p` | Cycle pricing plan (Go / Zen) |

## DB Schema

The app queries the `session` table for these columns:
`id, model, cost, tokens_input, tokens_output, tokens_reasoning, tokens_cache_read, tokens_cache_write, time_created, time_updated, title, slug`

The `model` column is a JSON string with fields `id`, `providerID`, `variant` — parsed by `_parse_model()` which extracts `(id, providerID, variant)`.

## Go Plan Limits

The Go plan ($10/mo) has usage limits defined in `pricing.json` under `plans.go.limits`:
- **5 hour**: $12
- **Weekly**: $30
- **Monthly**: $60

Limits are computed from `SessionData._all_sessions` (unfiltered by period) using absolute time windows. The `recompute()` method calculates `limit_5h`, `limit_weekly`, `limit_monthly` against the raw session list, independent of the `m`-key period filter.

The limits panel is only shown when the user has selected the Go plan (`plan == "go"`).

Limit labels change color based on usage percentage:
- **Yellow** at `limit_warn` (default 75%)
- **Orange** at `limit_alert` (default 90%)
- **Red** at `limit_critical` (default 100%)

Thresholds are configurable via the tray right-click menu (Limit Colors submenu).

## Known Patterns / Gotchas

- `SessionData.recompute(period)` filters `sessions_list` by the period cutoff. All downstream aggregates (totals, model breakdown, daily trend) **must** use `sessions_list` — do not add a second independent filter.
- `_poll_db()` calls `data.refresh()` (which calls `recompute("all")`) then `data.recompute(self._time_mode)` — two recomputes per poll every 3 seconds. This is intentional: refresh resets from DB, then the mode filter is applied on top.
- `pricing.json` is the authoritative pricing source. Add new models there, not in `opencode-cost.py`.
- The daily trend sparkline (`_sparkline`) renders the last `width` session costs from the filtered `sessions_list` — it respects the time period mode, not a hardcoded window.
- The `_poll_db` timer runs on a 3-second interval. Manual `r` refresh forces a table rebuild (sets `_table_timer` to 999). Table auto-rebuilds every 20 polls (~60s).
- `_autostart_cmd()` detects PyInstaller frozen EXE via `sys.frozen` and returns the EXE path directly. When run as a script, builds the path to `pythonw.exe` and the script.

## Decisions Log

- **2026-06-28:** Daily trend sparkline had a hardcoded 30-day cutoff independent of the `m`-key time period. Removed the redundant filter — trend now respects the selected mode. Trend-sum label made dynamic instead of hardcoded "last 30d".

## Style

- Black formatting, single-file app kept under 650 lines.
- No emoji in UI or code.
