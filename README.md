# OpenCode Cost Monitor

Live terminal UI + Windows 11 system tray app that watches your opencode session database and shows real-time cost stats, model breakdown, cache economics, Go plan limits, and daily trends.

This is a fork of [MikeCase/opencode-cost-monitor](https://github.com/MikeCase/opencode-cost-monitor) that adds a Windows native tray experience and Go plan limit tracking.

## What This Fork Adds

| Feature | Original | This Fork |
|---------|----------|-----------|
| Platform | Terminal only | Terminal + Windows 11 system tray |
| Go plan limits | — | 5h / week / month dollar limits with color bars |
| Limit colors | — | Yellow at 75%, orange at 90%, red at 100% (configurable) |
| Live tray icon | — | OpenCode O logo; turns yellow/orange/red, blinks on critical |
| Auto-start | — | Right-click → "Start at Windows boot" |
| EXE / MSI build | — | `.\build.ps1` → single-file EXE + WiX installer |

## TUI (Terminal)

```bash
pip install textual
python opencode-cost.py
python opencode-cost.py --db /path/to/opencode.db
```

### Keybindings

| Key | Action |
|-----|--------|
| `q` | Quit |
| `r` | Refresh now |
| `m` | Cycle time period (All Time / Last 30d / Last 7d / Today) |
| `p` | Cycle pricing plan (Go / Zen) |

## Tray App (Windows 11)

```bat
opencode-tray.bat
opencode-tray.bat --db C:\path\to\opencode.db
```

- **Left-click** tray icon — show/hide the cost dashboard window
- **Right-click** — context menu with auto-start toggle, limit color thresholds
- Opens in the lower-right corner of the screen on first launch

### Live Tray Icon

The opencode O logo in the system tray reflects your limit status in real time:

| State | Icon Color | Inner Accent |
|-------|-----------|--------------|
| Normal | Dark | Light gray |
| ≥ warn threshold (default 75%) | Dark | Yellow |
| ≥ alert threshold (default 90%) | Dark | Orange |
| ≥ critical (default 100%) | Dark | Red, blinking |

Thresholds are adjustable via the right-click menu → Limit Colors.

## Go Plan Limits

The Go plan ($10/month) has usage limits defined in [`pricing.json`](pricing.json):

| Window | Limit |
|--------|-------|
| 5 hour | $12 |
| Weekly | $30 |
| Monthly | $60 |

Consumption is calculated from all sessions in the database using absolute time windows (last 5h, 7d, 30d) — independent of the `m`-key time period filter.

## Build EXE + MSI

```powershell
.\build.ps1
```

Requires Python 3.12+ with `pyinstaller`. MSI requires [WiX Toolset v7+](https://wixtoolset.org).

Output: `dist\opencode-cost-monitor.exe` + `dist\msi\opencode-cost-monitor-<version>.msi`

## Requirements

- Python 3.12+
- `textual` (TUI), `pystray` + `Pillow` (tray app)
- An opencode SQLite database at `~/.local/share/opencode/opencode.db` or configured location

## License

MIT
