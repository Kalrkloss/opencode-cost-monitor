#!/usr/bin/env python3
"""OpenCode Cost Monitor -- Windows 11 System Tray App.

Resides in the notification area. Left-click shows the cost dashboard
window. Right-click for context menu (auto-start, time period, plan).

Usage:
    pythonw opencode-tray.py
    pythonw opencode-tray.py --db C:\\path\\to\\opencode.db
"""

import os
import sys
import json
import sqlite3
import winreg
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from functools import partial

# ── Bootstrap: use project venv if available ──
_script_dir = os.path.dirname(os.path.abspath(__file__))
_venv_dir = os.path.realpath(os.path.join(_script_dir, ".venv")).lower()
if not os.path.realpath(sys.executable).lower().startswith(_venv_dir):
    _venv_candidates = [
        os.path.join(_venv_dir, "Scripts", "pythonw.exe"),
        os.path.join(_venv_dir, "Scripts", "python.exe"),
        os.path.join(_venv_dir, "bin", "python3"),
    ]
    for _vp in _venv_candidates:
        if os.path.exists(_vp):
            os.execv(_vp, [_vp] + sys.argv)
            break

try:
    import pystray
    from PIL import Image, ImageDraw, ImageFont
except ImportError as e:
    print(f"Missing dependency: {e.name}. Install with: pip install pystray Pillow", file=sys.stderr)
    sys.exit(1)

# ── Paths ──
REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB = os.environ.get(
    "OPENCODE_DB_PATH",
    os.path.expanduser("~/.local/share/opencode/opencode.db")
)
PRICING_FILE = os.path.join(REPO_ROOT, "pricing.json")
AUTOSTART_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
AUTOSTART_NAME = "OpenCodeCostMonitor"


# ═══════════════════════════════════════════════════════════════════
#  Data layer
# ═══════════════════════════════════════════════════════════════════

def load_pricing(plan="go"):
    with open(PRICING_FILE) as f:
        data = json.load(f)
    plan_data = data.get("plans", {}).get(plan, {})
    return plan_data.get("models", {}), plan_data.get("name", plan.capitalize()), plan_data.get("limits", {})


def _parse_model(model_json):
    if not model_json:
        return None, None, None
    try:
        m = json.loads(model_json)
        return m.get("id"), m.get("providerID"), m.get("variant")
    except (json.JSONDecodeError, TypeError):
        return None, None, None


def _ts(ms):
    if not ms:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def _fmt_dollar(v):
    if v is None or v == 0:
        return "$0.00"
    if v < 0.01:
        return f"${v:.4f}"
    if v < 1:
        return f"${v:.3f}"
    if v < 100:
        return f"${v:.2f}"
    return f"${v:,.2f}"


def _fmt_tokens(v):
    if v is None or v == 0:
        return "0"
    if v < 1_000:
        return f"{v}"
    if v < 1_000_000:
        return f"{v/1e3:.1f}K"
    if v < 1_000_000_000:
        return f"{v/1e6:.2f}M"
    return f"{v/1e9:.2f}B"


def _fmt_pct(v):
    return f"{v:.1f}%"


def _bar(value, max_val, width=10):
    if max_val <= 0:
        return " " * width
    ratio = value / max_val
    filled = int(ratio * width)
    return "█" * filled + "░" * (width - filled)


BAR_CHARS = "▁▂▃▄▅▆▇█"


def _sparkline(values, width=30):
    if not values:
        return ""
    mn, mx = min(values), max(values)
    rng = mx - mn if mx != mn else 1
    out = []
    for v in values[-width:]:
        idx = int((v - mn) / rng * 7)
        out.append(BAR_CHARS[min(idx, 7)])
    return "".join(out)


class SessionData:
    """Aggregated snapshot of opencode.db."""

    def __init__(self, db_path):
        self._db_path = db_path
        self._all_sessions = []
        self._pricing, _, self._limits = load_pricing("go")
        self.reset()

    def reset(self):
        self.total_cost = 0.0
        self.total_sessions = 0
        self.paid_sessions = 0
        self.total_in = 0
        self.total_out = 0
        self.total_cache_r = 0
        self.total_cache_w = 0
        self.cache_hit_rate = 0.0
        self.cache_cost = 0.0
        self.full_price_cost = 0.0
        self.cache_savings = 0.0
        self.models = []
        self.daily_trend = []
        self.last_updated = ""
        self.last_session_cost = 0.0
        self.last_session_title = ""
        self.active_sessions = 0
        self.plan = "go"
        self.plan_label = "Go"
        self.time_period = "all"
        self.limit_5h = 0.0
        self.limit_weekly = 0.0
        self.limit_monthly = 0.0
        self.limit_5h_max = 12.0
        self.limit_weekly_max = 30.0
        self.limit_monthly_max = 60.0
        self.limit_warn = 75
        self.limit_alert = 90
        self.limit_critical = 100

    def refresh(self):
        """Read all rows from the DB."""
        try:
            conn = sqlite3.connect(self._db_path)
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT id, model, cost, tokens_input, tokens_output,
                       tokens_reasoning, tokens_cache_read, tokens_cache_write,
                       time_created, time_updated, title, slug
                FROM session ORDER BY time_created
            """).fetchall()
            conn.close()
        except (sqlite3.Error, FileNotFoundError):
            return

        all_sessions = []
        max_updated = 0

        for r in rows:
            cost = float(r["cost"] or 0)
            tin = int(r["tokens_input"] or 0)
            tout = int(r["tokens_output"] or 0)
            cr = int(r["tokens_cache_read"] or 0)
            cw = int(r["tokens_cache_write"] or 0)
            created = _ts(r["time_created"])
            mid, prov, _ = _parse_model(r["model"])

            updated = int(r["time_updated"] or 0)
            if updated > max_updated:
                max_updated = updated
                self.last_session_cost = cost
                self.last_session_title = r["title"] or r["slug"] or ""

            all_sessions.append({
                "cost": cost, "tin": tin, "tout": tout,
                "cr": cr, "cw": cw, "mid": mid, "prov": prov,
                "created": created,
            })

        self._all_sessions = all_sessions
        self.last_updated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.recompute("all", self.plan)

    def recompute(self, period="all", plan=None):
        """Recompute aggregates filtered by time period."""
        if plan:
            self.plan = plan
            self._pricing, self.plan_label, self._limits = load_pricing(plan)

        now = datetime.now(timezone.utc)
        cutoff = {
            "all": None,
            "month": now - timedelta(days=30),
            "week": now - timedelta(days=7),
            "day": now.replace(hour=0, minute=0, second=0, microsecond=0),
        }.get(period)

        sessions_list = self._all_sessions
        if cutoff:
            sessions_list = [s for s in sessions_list if s["created"] and s["created"] >= cutoff]

        self.time_period = period

        class _MA:
            def __init__(self):
                self.cost = 0.0
                self.count = 0
                self.tin = 0
                self.tout = 0
                self.cr = 0
                self.cw = 0
                self.model_id = "unknown"
                self.provider = ""

        model_agg = defaultdict(_MA)
        for s in sessions_list:
            key = s["mid"] or "unknown"
            a = model_agg[key]
            a.cost += s["cost"]
            a.count += 1
            a.tin += s["tin"]
            a.tout += s["tout"]
            a.cr += s["cr"]
            a.cw += s["cw"]
            a.model_id = key
            if s["prov"]:
                a.provider = s["prov"]

        pricing = self._pricing
        self.total_sessions = len(sessions_list)
        self.total_cost = sum(s["cost"] for s in sessions_list)
        self.paid_sessions = sum(1 for s in sessions_list if s["cost"] > 0)
        self.total_in = sum(s["tin"] for s in sessions_list)
        self.total_out = sum(s["tout"] for s in sessions_list)
        self.total_cache_r = sum(s["cr"] for s in sessions_list)
        self.total_cache_w = sum(s["cw"] for s in sessions_list)

        total_fresh = self.total_in + self.total_cache_r
        self.cache_hit_rate = self.total_cache_r / total_fresh * 100 if total_fresh > 0 else 0.0

        self.cache_cost = 0.0
        self.full_price_cost = 0.0
        for s in sessions_list:
            mid = s["mid"]
            p = pricing.get(mid)
            if p:
                cr_rate = p.get("cache_read")
                cw_rate = p.get("cache_write")
                inp_rate = p.get("input")

                if cr_rate and s["cr"]:
                    self.cache_cost += s["cr"] / 1_000_000 * cr_rate
                if inp_rate and s["cr"]:
                    self.full_price_cost += s["cr"] / 1_000_000 * inp_rate
                if cw_rate and s["cw"]:
                    self.cache_cost += s["cw"] / 1_000_000 * cw_rate

        self.cache_savings = self.full_price_cost - self.cache_cost

        self.models = sorted(model_agg.values(), key=lambda m: m.cost, reverse=True)

        self.daily_trend = [s["cost"] for s in sessions_list if s["created"]]

        recent_cutoff = datetime.now(timezone.utc) - timedelta(minutes=5)
        self.active_sessions = sum(
            1 for s in sessions_list
            if s["created"] and s["created"] >= recent_cutoff
        )

        # Go plan limits
        self.limit_5h_max = self._limits.get("5hour", 0)
        self.limit_weekly_max = self._limits.get("weekly", 0)
        self.limit_monthly_max = self._limits.get("monthly", 0)

        now = datetime.now(timezone.utc)
        h5_cutoff = now - timedelta(hours=5)
        week_cutoff = now - timedelta(days=7)
        month_cutoff = now - timedelta(days=30)
        all_sessions = self._all_sessions
        self.limit_5h = sum(
            s["cost"] for s in all_sessions
            if s["created"] and s["created"] >= h5_cutoff
        )
        self.limit_weekly = sum(
            s["cost"] for s in all_sessions
            if s["created"] and s["created"] >= week_cutoff
        )
        self.limit_monthly = sum(
            s["cost"] for s in all_sessions
            if s["created"] and s["created"] >= month_cutoff
        )


# ═══════════════════════════════════════════════════════════════════
#  Tray icon image
# ═══════════════════════════════════════════════════════════════════

def _create_icon_image(accent_color=None):
    """Return a list of (size, PIL Image) pairs for multi-resolution ICO.

    Uses the opencode 'O' logo with body #211E1E and given accent (default #CFCECD).
    """
    if accent_color is None:
        accent_color = (207, 206, 205, 255)

    def _render(px):
        img = Image.new("RGBA", (px, px), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        k = px / 300.0
        body = (33, 30, 30, 255)
        draw.rectangle([0, 0, 240*k, 60*k], fill=body)
        draw.rectangle([0, 240*k, 240*k, 300*k], fill=body)
        draw.rectangle([0, 60*k, 60*k, 240*k], fill=body)
        draw.rectangle([180*k, 60*k, 240*k, 240*k], fill=body)
        draw.rectangle([60*k, 120*k, 180*k, 240*k], fill=accent_color)
        return img

    sizes = [256, 128, 64, 48, 32, 16]
    return [(s, _render(s)) for s in sizes]


# ═══════════════════════════════════════════════════════════════════
#  Tray application
# ═══════════════════════════════════════════════════════════════════

class TrayApp:
    TIME_LABELS = {"all": "All Time", "month": "Last 30d", "week": "Last 7d", "day": "Today"}
    TIME_MODES = ["all", "month", "week", "day"]

    def __init__(self, db_path=DEFAULT_DB):
        self.data = SessionData(db_path)
        self._time_mode = "all"
        self._running = True
        self._first_show = True
        self._last_accent = (207, 206, 205, 255)
        self._blink_active = False
        self._blink_visible = True

        self.data.refresh()
        self.data.recompute(self._time_mode)

        self._build_window()
        self._schedule_poll()

        self.icon = self._build_icon()

    def _limit_color(self, pct):
        """Return color for limit percentage based on thresholds."""
        d = self.data
        if pct >= d.limit_critical:
            return "#ff4444"
        if pct >= d.limit_alert:
            return "#ff8c00"
        if pct >= d.limit_warn:
            return "#ffd700"
        return "#e0e0e0"

    def _limit_accent_rgba(self):
        """Return RGBA accent color for the tray icon based on worst limit."""
        d = self.data
        if d.plan != "go" or d.limit_5h_max <= 0:
            return (207, 206, 205, 255)  # default gray
        pcts = [
            d.limit_5h / d.limit_5h_max * 100 if d.limit_5h_max > 0 else 0,
            d.limit_weekly / d.limit_weekly_max * 100 if d.limit_weekly_max > 0 else 0,
            d.limit_monthly / d.limit_monthly_max * 100 if d.limit_monthly_max > 0 else 0,
        ]
        worst = max(pcts)
        if worst >= d.limit_critical:
            return (255, 68, 68, 255)       # red
        if worst >= d.limit_alert:
            return (255, 140, 0, 255)       # orange
        if worst >= d.limit_warn:
            return (255, 215, 0, 255)       # yellow
        return (207, 206, 205, 255)         # default gray

    def _update_tray_icon(self):
        """Rebuild the tray icon if the limit accent color changed."""
        accent = self._limit_accent_rgba()
        is_critical = accent == (255, 68, 68, 255)

        if is_critical and not self._blink_active:
            self._blink_active = True
            self._blink_visible = True
            self._set_icon_color(accent)
            self.window.after(800, self._blink_tick)
        elif not is_critical and self._blink_active:
            self._blink_active = False
            self._last_accent = accent
            self._set_icon_color(accent)
        elif not is_critical and accent != self._last_accent:
            self._last_accent = accent
            self._set_icon_color(accent)

    def _set_icon_color(self, accent_color):
        frames = _create_icon_image(accent_color=accent_color)
        img = next(f[1] for f in frames if f[0] == 64)
        self.icon.icon = img

    def _blink_tick(self):
        if not self._blink_active:
            return
        self._blink_visible = not self._blink_visible
        if self._blink_visible:
            accent = (255, 68, 68, 255)
        else:
            accent = (207, 206, 205, 255)
        self._set_icon_color(accent)
        self.window.after(800, self._blink_tick)

    def _build_window(self):
        self.window = tk.Tk()
        self.window.title("OpenCode Cost Monitor")
        self.window.configure(bg="#0f0f14")
        self.window.minsize(720, 480)
        self.window.geometry("800x560+0+0")
        self.window.protocol("WM_DELETE_WINDOW", self._withdraw)

        # Set window icon from icon.ico
        icon_path = os.path.join(
            getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__))),
            "icon.ico"
        )
        if os.path.exists(icon_path):
            try:
                self.window.iconbitmap(icon_path)
            except Exception:
                pass

        # treeview style
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(
            "Treeview",
            background="#16162a",
            foreground="#e0e0e0",
            fieldbackground="#16162a",
            rowheight=24,
            font=("Segoe UI", 9),
        )
        style.map("Treeview", background=[("selected", "#2a2a3e")])
        style.configure(
            "Treeview.Heading",
            background="#1e1e34",
            foreground="#aaaacc",
            font=("Segoe UI", 9, "bold"),
        )

        self._build_ui()
        self.window.withdraw()

    def _build_ui(self):
        bg = "#0f0f14"
        box_bg = "#16162a"
        border = "#2a2a3e"
        green = "#00d4aa"
        accent = "#ff7a5c"

        # ── Top stat boxes ──
        top = tk.Frame(self.window, bg=bg)
        top.pack(fill=tk.X, padx=8, pady=(8, 4))

        stat_defs = [
            ("total cost", "value", "Total cost"),
            ("cache hit rate", "value", "Cache hit rate"),
            ("sessions", "value", "Sessions"),
            ("cache savings", "accent", "Cache savings"),
        ]
        self.stat_vals = {}
        self.stat_subs = {}

        for name, kind, _ in stat_defs:
            f = tk.Frame(top, bg=box_bg, highlightbackground=border, highlightthickness=1)
            f.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)

            val_color = accent if kind == "accent" else green
            val = tk.Label(f, text="$0.00", bg=box_bg, fg=val_color,
                           font=("Segoe UI", 16, "bold"))
            val.pack(pady=(6, 0))

            sub = tk.Label(f, text="", bg=box_bg, fg="#666688",
                           font=("Segoe UI", 8, "italic"))
            sub.pack(pady=(0, 4))

            self.stat_vals[name] = val
            self.stat_subs[name] = sub

        # ── Model table ──
        table_frame = tk.Frame(self.window, bg=bg)
        table_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)

        inner = tk.Frame(table_frame, bg=box_bg,
                         highlightbackground=border, highlightthickness=1)
        inner.pack(fill=tk.BOTH, expand=True)

        cols = ("Model", "Sess", "Cost", "In", "Out", "CacheR", "Hit%", "Bar")
        self.tree = ttk.Treeview(inner, columns=cols, show="headings",
                                 height=6, selectmode="browse")
        widths = [200, 50, 90, 80, 70, 80, 60, 120]
        for col, w in zip(cols, widths):
            self.tree.heading(col, text=col)
            self.tree.column(col, width=w, anchor="center" if col != "Model" else "w")

        vsb = ttk.Scrollbar(inner, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)

        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

        # ── Bottom panels ──
        bottom = tk.Frame(self.window, bg=bg)
        bottom.pack(fill=tk.X, padx=8, pady=(4, 8))

        # Go Limits
        self.limits_f = tk.Frame(bottom, bg=box_bg,
                                 highlightbackground=border, highlightthickness=1)
        self.limits_f.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 2))

        _lbl(self.limits_f, "Go Limits")
        self.limits_lbls = []
        for _ in range(3):
            lbl = tk.Label(self.limits_f, text="", bg=box_bg, fg="#e0e0e0",
                           font=("Segoe UI", 9), anchor=tk.W)
            lbl.pack(fill=tk.X, padx=6)
            self.limits_lbls.append(lbl)

        if not self.data.plan == "go":
            self.limits_f.pack_forget()

        # Trend (wider)
        self.trend_f = tk.Frame(bottom, bg=box_bg,
                                highlightbackground=border, highlightthickness=1)
        self.trend_f.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 2))

        _lbl(self.trend_f, "Daily Cost Trend")
        self.trend_lbl = tk.Label(self.trend_f, text="", bg=box_bg, fg=green,
                                  font=("Segoe UI", 10), anchor=tk.W)
        self.trend_lbl.pack(fill=tk.X, padx=6, pady=(0, 0))
        self.trend_sub = tk.Label(self.trend_f, text="", bg=box_bg, fg="#666688",
                                  font=("Segoe UI", 8), anchor=tk.W)
        self.trend_sub.pack(fill=tk.X, padx=6, pady=(0, 4))

        # Cache
        cache_f = tk.Frame(bottom, bg=box_bg,
                           highlightbackground=border, highlightthickness=1)
        cache_f.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=2)

        _lbl(cache_f, "Cache Economics")
        self.cache_lbls = []
        for _ in range(3):
            lbl = tk.Label(cache_f, text="", bg=box_bg, fg="#e0e0e0",
                           font=("Segoe UI", 9), anchor=tk.W)
            lbl.pack(fill=tk.X, padx=6)
            self.cache_lbls.append(lbl)

        # Last session
        last_f = tk.Frame(bottom, bg=box_bg,
                          highlightbackground=border, highlightthickness=1)
        last_f.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=2)

        _lbl(last_f, "Last Session")
        self.last_lbls = []
        for _ in range(3):
            lbl = tk.Label(last_f, text="", bg=box_bg, fg="#e0e0e0",
                           font=("Segoe UI", 9), anchor=tk.W)
            lbl.pack(fill=tk.X, padx=6)
            self.last_lbls.append(lbl)

        # ── Controls bar ──
        ctrl = tk.Frame(self.window, bg="#1a1a2e")
        ctrl.pack(fill=tk.X)

        tk.Label(ctrl, text="Period:", bg="#1a1a2e", fg="#8888aa",
                 font=("Segoe UI", 8)).pack(side=tk.LEFT, padx=(10, 2))

        self.period_btns = {}
        for mode, label in [("all", "All"), ("month", "30d"), ("week", "7d"), ("day", "Today")]:
            btn = tk.Label(ctrl, text=label, bg="#1a1a2e", fg="#666688",
                           font=("Segoe UI", 8, "bold"), cursor="hand2", padx=6)
            btn.pack(side=tk.LEFT, padx=1)
            btn.bind("<Button-1>", lambda e, m=mode: self._on_period_click(m))
            btn.bind("<Enter>", lambda e, b=btn: b.configure(fg="#aaaacc"))
            btn.bind("<Leave>", lambda e, b=btn: self._refresh_controls())
            self.period_btns[mode] = btn

        tk.Label(ctrl, text="   Plan:", bg="#1a1a2e", fg="#8888aa",
                 font=("Segoe UI", 8)).pack(side=tk.LEFT, padx=(10, 2))

        self.plan_btns = {}
        for plan, label in [("go", "Go"), ("zen", "Zen")]:
            btn = tk.Label(ctrl, text=label, bg="#1a1a2e", fg="#666688",
                           font=("Segoe UI", 8, "bold"), cursor="hand2", padx=6)
            btn.pack(side=tk.LEFT, padx=1)
            btn.bind("<Button-1>", lambda e, p=plan: self._on_plan_click(p))
            btn.bind("<Enter>", lambda e, b=btn: b.configure(fg="#aaaacc"))
            btn.bind("<Leave>", lambda e, b=btn: self._refresh_controls())
            self.plan_btns[plan] = btn

        # ── Footer ──
        self.footer = tk.Label(self.window, text="", bg="#1a1a2e", fg="#666688",
                               font=("Segoe UI", 8), anchor=tk.W, padx=10)
        self.footer.pack(fill=tk.X)

    def _update_window(self):
        d = self.data
        label = self.TIME_LABELS.get(self._time_mode, "All Time")

        # Top stats
        self.stat_vals["total cost"].configure(text=_fmt_dollar(d.total_cost))
        self.stat_subs["total cost"].configure(
            text=f"{label} \u00b7 {d.models[0].model_id if d.models else '?'}"
        )

        self.stat_vals["cache hit rate"].configure(text=_fmt_pct(d.cache_hit_rate))
        self.stat_subs["cache hit rate"].configure(
            text=f"{_fmt_tokens(d.total_cache_r)} cached / {_fmt_tokens(d.total_in)} fresh"
        )

        self.stat_vals["sessions"].configure(text=str(d.total_sessions))
        self.stat_subs["sessions"].configure(
            text=f"{d.paid_sessions} paid \u00b7 {d.total_sessions - d.paid_sessions} free"
        )

        self.stat_vals["cache savings"].configure(text=_fmt_dollar(d.cache_savings))
        self.stat_subs["cache savings"].configure(
            text=f"{_fmt_pct(d.cache_savings / d.full_price_cost * 100) if d.full_price_cost > 0 else '0%'} of full price"
        )

        # Model table
        for row in self.tree.get_children():
            self.tree.delete(row)

        max_cost = d.models[0].cost if d.models else 1
        for m in d.models:
            if m.cost < 0.01 and m.count <= 1:
                continue
            total_r = m.tin + m.cr
            hit = m.cr / total_r * 100 if total_r > 0 else 0.0
            self.tree.insert("", tk.END, values=(
                m.model_id[:22],
                str(m.count),
                _fmt_dollar(m.cost),
                _fmt_tokens(m.tin),
                _fmt_tokens(m.tout),
                _fmt_tokens(m.cr) if m.cr else "\u2014",
                _fmt_pct(hit),
                _bar(m.cost, max_cost, 12),
            ))

        # Trend
        trend = d.daily_trend
        period_label = self.TIME_LABELS.get(self._time_mode, "all").lower()
        self.trend_lbl.configure(
            text=f"{_sparkline(trend, 40)}   {period_label}: {_fmt_dollar(sum(trend))}"
        )
        if trend:
            self.trend_sub.configure(
                text=f"min: {_fmt_dollar(min(trend))}  max: {_fmt_dollar(max(trend))}  avg: {_fmt_dollar(sum(trend)/len(trend))}"
            )
        else:
            self.trend_sub.configure(text="")

        # Cache
        self.cache_lbls[0].configure(text=f"Cache cost (at rate):  {_fmt_dollar(d.cache_cost)}")
        self.cache_lbls[1].configure(text=f"Full-price equivalent: {_fmt_dollar(d.full_price_cost)}")
        self.cache_lbls[2].configure(
            text=f"Savings: {_fmt_dollar(d.cache_savings)} ({_fmt_pct(d.cache_savings / d.full_price_cost * 100) if d.full_price_cost > 0 else '0%'})"
        )

        # Last session
        self.last_lbls[0].configure(text=f"Cost: {_fmt_dollar(d.last_session_cost)}")
        title = (d.last_session_title[:42] + "..") if len(d.last_session_title) > 42 else d.last_session_title
        self.last_lbls[1].configure(text=f"Session: {title}" if title else "Session: \u2014")
        self.last_lbls[2].configure(text=f"Updated: {d.last_updated}")

        # Go limits
        if d.plan == "go":
            self.limits_f.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 2), before=self.trend_f)
            pct5 = d.limit_5h / d.limit_5h_max * 100 if d.limit_5h_max > 0 else 0
            pctw = d.limit_weekly / d.limit_weekly_max * 100 if d.limit_weekly_max > 0 else 0
            pctm = d.limit_monthly / d.limit_monthly_max * 100 if d.limit_monthly_max > 0 else 0
            self.limits_lbls[0].configure(
                text=f"5h: {_fmt_dollar(d.limit_5h)} / {_fmt_dollar(d.limit_5h_max)} ({_fmt_pct(pct5)})",
                fg=self._limit_color(pct5)
            )
            self.limits_lbls[1].configure(
                text=f"Week: {_fmt_dollar(d.limit_weekly)} / {_fmt_dollar(d.limit_weekly_max)} ({_fmt_pct(pctw)})",
                fg=self._limit_color(pctw)
            )
            self.limits_lbls[2].configure(
                text=f"Month: {_fmt_dollar(d.limit_monthly)} / {_fmt_dollar(d.limit_monthly_max)} ({_fmt_pct(pctm)})",
                fg=self._limit_color(pctm)
            )
        else:
            self.limits_f.pack_forget()

        # Footer
        status = f"Polling every 3s \u00b7 {d.active_sessions} active (last 5min)"
        self.footer.configure(text=status)

        self._refresh_controls()
        self._update_tray_icon()

    # ── Tray icon ──────────────────────────────────────────────────

    def _build_icon(self):
        frames = _create_icon_image()
        # Use the 64x64 frame for the tray icon
        img = next(f[1] for f in frames if f[0] == 64)
        menu = self._build_menu()
        icon = pystray.Icon("opencode-cost", img, "OpenCode Cost Monitor", menu)
        return icon

    def _build_menu(self):
        warn_items = []
        for val in [50, 60, 70, 75, 80, 90]:
            warn_items.append(
                pystray.MenuItem(
                    f"{val}%",
                    partial(self._set_threshold, "warn", val),
                    checked=lambda _, v=val: self.data.limit_warn == v,
                )
            )

        alert_items = []
        for val in [80, 85, 90, 95]:
            alert_items.append(
                pystray.MenuItem(
                    f"{val}%",
                    partial(self._set_threshold, "alert", val),
                    checked=lambda _, v=val: self.data.limit_alert == v,
                )
            )

        critical_items = []
        for val in [85, 90, 95, 100]:
            critical_items.append(
                pystray.MenuItem(
                    f"{val}%",
                    partial(self._set_threshold, "critical", val),
                    checked=lambda _, v=val: self.data.limit_critical == v,
                )
            )

        return pystray.Menu(
            pystray.MenuItem("Show/Hide Window", self._toggle_window, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Start at Windows boot",
                self._toggle_autostart,
                checked=lambda _: self._is_autostart(),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Limit Colors", pystray.Menu(
                pystray.MenuItem("Yellow (warn)", pystray.Menu(*warn_items)),
                pystray.MenuItem("Orange (alert)", pystray.Menu(*alert_items)),
                pystray.MenuItem("Red (critical)", pystray.Menu(*critical_items)),
            )),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("About", self._about),
            pystray.MenuItem("Exit", self._exit),
        )

    # ── Menu actions (called from pystray thread) ──────────────────

    def _toggle_window(self):
        self.window.after(0, self._do_toggle_window)

    def _do_toggle_window(self):
        if self.window.state() == "withdrawn":
            if self._first_show:
                self._first_show = False
                # Position in lower-right corner
                sw = self.window.winfo_screenwidth()
                sh = self.window.winfo_screenheight()
                ww, wh = 800, 560
                self.window.geometry(f"{ww}x{wh}+{sw - ww}+{sh - wh - 80}")
            self.window.deiconify()
            self.window.lift()
            self.window.focus_force()
            self._update_window()
        else:
            self.window.withdraw()

    def _withdraw(self):
        self.window.withdraw()

    def _set_period(self, mode):
        self._time_mode = mode
        self.data.recompute(mode)
        self.window.after(0, self._update_window)

    def _on_period_click(self, mode):
        self._set_period(mode)
        self._refresh_controls()

    def _on_plan_click(self, plan):
        self.data.recompute(self._time_mode, plan=plan)
        self.window.after(0, self._update_window)
        self._refresh_controls()

    def _refresh_controls(self):
        for mode, btn in self.period_btns.items():
            btn.configure(fg="#00d4aa" if self._time_mode == mode else "#666688")
        for plan, btn in self.plan_btns.items():
            btn.configure(fg="#00d4aa" if self.data.plan == plan else "#666688")

    def _toggle_autostart(self):
        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, AUTOSTART_KEY, 0,
                winreg.KEY_SET_VALUE
            )
            if self._is_autostart():
                winreg.DeleteValue(key, AUTOSTART_NAME)
            else:
                winreg.SetValueEx(
                    key, AUTOSTART_NAME, 0, winreg.REG_SZ,
                    self._autostart_cmd()
                )
            winreg.CloseKey(key)
        except Exception as e:
            self.window.after(0, lambda: messagebox.showerror("Auto-start Error", str(e)))

    def _is_autostart(self):
        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, AUTOSTART_KEY, 0, winreg.KEY_READ
            )
            val, _ = winreg.QueryValueEx(key, AUTOSTART_NAME)
            winreg.CloseKey(key)
            target = self._autostart_cmd()
            return val.strip('"') == target.strip('"')
        except FileNotFoundError:
            return False

    def _autostart_cmd(self):
        if getattr(sys, 'frozen', False):
            return f'"{sys.executable}"'
        script = os.path.abspath(__file__)
        venv_pythonw = os.path.join(_script_dir, ".venv", "Scripts", "pythonw.exe")
        if os.path.exists(venv_pythonw):
            pythonw = venv_pythonw
        else:
            base = os.path.dirname(sys.executable)
            pythonw = os.path.join(base, "pythonw.exe")
            if not os.path.exists(pythonw):
                pythonw = sys.executable
        return f'"{pythonw}" "{script}"'

    def _about(self):
        self.window.after(
            0,
            lambda: messagebox.showinfo(
                "About OpenCode Cost Monitor",
                "Real-time cost tracking for OpenCode.\n\n"
                f"Database: {self.data._db_path}\n"
                f"Pricing: {PRICING_FILE}\n\n"
                "Left-click tray icon to show dashboard.\n"
                "Right-click for menu.",
            )
        )

    def _set_threshold(self, level, value):
        if level == "warn":
            self.data.limit_warn = value
        elif level == "alert":
            self.data.limit_alert = value
        elif level == "critical":
            self.data.limit_critical = value
        self.window.after(0, self._update_window)

    def _exit(self):
        self._running = False
        try:
            self.icon.visible = False
        except Exception:
            pass
        self.window.after(0, self._do_exit)

    def _do_exit(self):
        try:
            self.icon.stop()
        except Exception:
            pass
        self.window.quit()
        self.window.destroy()

    # ── Polling ────────────────────────────────────────────────────

    def _schedule_poll(self):
        if not self._running:
            return

        self.data.refresh()
        self.data.recompute(self._time_mode)

        if self.window.winfo_exists() and self.window.state() != "withdrawn":
            self._update_window()

        self.window.after(3000, self._schedule_poll)

    # ── Start ──────────────────────────────────────────────────────

    def run(self):
        """Start the tray app. Blocks until Exit is chosen."""
        self.icon.run_detached()
        self.window.mainloop()
        try:
            self.icon.visible = False
            self.icon.stop()
        except Exception:
            pass


def _lbl(parent, text):
    tk.Label(parent, text=text, bg=parent.cget("bg"), fg="#8888aa",
             font=("Segoe UI", 8), anchor=tk.W).pack(fill=tk.X, padx=6, pady=(6, 0))


# ── Entry point ───────────────────────────────────────────────────

def main():
    import argparse

    parser = argparse.ArgumentParser(description="OpenCode Cost Monitor Tray App")
    parser.add_argument(
        "--db", default=DEFAULT_DB,
        help=f"SQLite DB path (default: {DEFAULT_DB})"
    )
    args = parser.parse_args()

    if not os.path.exists(args.db):
        print(f"Error: DB not found at {args.db}", file=sys.stderr)
        sys.exit(1)

    app = TrayApp(db_path=args.db)
    app.run()


if __name__ == "__main__":
    main()
