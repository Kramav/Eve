import io
import math
import queue
import threading
import tkinter as tk
import urllib.request
from datetime import datetime

# ── Iron Man HUD palette ──────────────────────────────────────────────────────
_CYAN        = "#00d4ff"
_ORANGE      = "#ff8c00"
_GREEN       = "#00ff88"
_RED         = "#ff3030"
_CYAN_DIM    = "#0d2535"
_ORANGE_DIM  = "#2d1800"
_TEXT        = "#c8e8ff"    # primary text, slightly blue-tinted white
_TEXT_DIM    = "#2a5070"    # secondary / timestamps

# Main status bar
_BG          = "#04080f"
_H_NORMAL    = 80
_LIST_ROW_H  = 28

# Per-state dot color
_COLORS = {
    "idle":       "#1e3a52",
    "listening":  _ORANGE,
    "processing": _CYAN,
    "error":      _RED,
}

# Visual Overlay
_OV_W, _OV_H = 520, 500
_OV_BG       = "#030810"
_OV_HDR_BG   = "#020508"

_KIND_ICON  = {"heard": "▶", "action": "✓", "error": "✗", "system": "·"}
_KIND_COLOR = {
    "heard":  _CYAN,
    "action": _GREEN,
    "error":  _ORANGE,
    "system": "#1e3a52",
}
_MODE_BADGE = {
    "idle":       ("IDLE",      "#08121e"),
    "listening":  ("LISTENING", _ORANGE_DIM),
    "processing": ("THINKING",  "#001825"),
    "playing":    ("PLAYING",   "#002218"),
}
_MODE_FG = {
    "idle":       _TEXT_DIM,
    "listening":  _ORANGE,
    "processing": _CYAN,
    "playing":    _GREEN,
}
_LOG_ROW_BG = ("#060f1e", "#04080f")

# Ring geometry
_CX, _CY     = 65, 65
_R_OUT       = 55
_R_HALO      = 61                          # outer glow ring
_R_INNER     = 42                          # inner depth ring
_R_IN_TICK   = {0: 46, 45: 50, 15: 52}   # major / mid / minor inner radii
_ARC_BBOX    = (_CX - _R_OUT,   _CY - _R_OUT,   _CX + _R_OUT,   _CY + _R_OUT)
_ARC_HALO    = (_CX - _R_HALO,  _CY - _R_HALO,  _CX + _R_HALO,  _CY + _R_HALO)
_ARC_INNER   = (_CX - _R_INNER, _CY - _R_INNER, _CX + _R_INNER, _CY + _R_INNER)

# Waveform bars (inside ring)
_N_BARS, _BAR_W, _BAR_GAP = 7, 4, 3
_BAR_TOTAL_W = _N_BARS * _BAR_W + (_N_BARS - 1) * _BAR_GAP
_BAR_X0      = _CX - _BAR_TOTAL_W // 2
_BAR_XS      = [(_BAR_X0 + i * (_BAR_W + _BAR_GAP),
                 _BAR_X0 + i * (_BAR_W + _BAR_GAP) + _BAR_W)
                for i in range(_N_BARS)]


def _lerp_color(c1: str, c2: str, t: float) -> str:
    r1, g1, b1 = int(c1[1:3], 16), int(c1[3:5], 16), int(c1[5:7], 16)
    r2, g2, b2 = int(c2[1:3], 16), int(c2[3:5], 16), int(c2[5:7], 16)
    r = max(0, min(255, int(r1 + (r2 - r1) * t)))
    g = max(0, min(255, int(g1 + (g2 - g1) * t)))
    b = max(0, min(255, int(b1 + (b2 - b1) * t)))
    return f"#{r:02x}{g:02x}{b:02x}"


class Display:
    def __init__(self):
        self._q: queue.Queue = queue.Queue()
        self._ready = threading.Event()
        self._drag_x = self._drag_y = 0
        self._ov_mode       = "idle"
        self._ov_anim_tick  = 0
        self._ov_thumb_photo  = None
        self._pending_thumb   = None
        self._ov_log_row_count = 0
        threading.Thread(target=self._run, daemon=True).start()
        self._ready.wait(timeout=5)

    # ── Tk thread ─────────────────────────────────────────────────────────────

    def _run(self):
        root = self.root = tk.Tk()
        root.title("")
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.attributes("-alpha", 0.95)
        root.configure(bg=_BG)

        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()
        self._sw, self._sh = sw, sh
        root.geometry(f"{700}x{_H_NORMAL}+{(sw - 700) // 2}+{sh - _H_NORMAL - 60}")

        # ── Cyan top-edge accent line ─────────────────────────────────────────
        tk.Frame(root, bg=_CYAN, height=2).pack(fill="x")

        outer = tk.Frame(root, bg=_BG, padx=14, pady=8)
        outer.pack(fill="both", expand=True)

        # ── List panel (hidden by default) ────────────────────────────────────
        self._list_frame  = tk.Frame(outer, bg=_BG)
        self._list_labels = []

        # ── Status row ────────────────────────────────────────────────────────
        self._top = tk.Frame(outer, bg=_BG)
        self._top.pack(fill="x")

        self._dot = tk.Label(self._top, text="◆", font=("Consolas", 9),
                             fg=_COLORS["idle"], bg=_BG)
        self._dot.pack(side="left")

        self._status_var = tk.StringVar(value="")
        tk.Label(self._top, textvariable=self._status_var,
                 font=("Consolas", 8), fg=_TEXT_DIM, bg=_BG
                 ).pack(side="left", padx=(6, 0))

        # ── Main text ─────────────────────────────────────────────────────────
        self._text_var = tk.StringVar(value="")
        tk.Label(outer, textvariable=self._text_var,
                 font=("Consolas", 12, "bold"),
                 fg=_TEXT, bg=_BG, anchor="w",
                 wraplength=660).pack(fill="x", pady=(4, 0))

        root.withdraw()

        # ── Visual Overlay ────────────────────────────────────────────────────
        ov = self._ov_win = tk.Toplevel(root)
        ov.title("Eve — Visual Overlay")
        ov.overrideredirect(True)
        ov.attributes("-topmost", True)
        ov.attributes("-alpha", 0.96)
        ov.configure(bg=_OV_BG)
        ov.geometry(f"{_OV_W}x{_OV_H}+{sw - _OV_W - 20}+20")

        # Top orange accent line on overlay
        tk.Frame(ov, bg=_ORANGE, height=2).pack(fill="x")

        # Header
        hdr = tk.Frame(ov, bg=_OV_HDR_BG, height=40)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)

        tk.Label(hdr, text="◈  E · V · E", font=("Consolas", 11, "bold"),
                 fg=_ORANGE, bg=_OV_HDR_BG).pack(side="left", padx=(12, 0), pady=10)

        self._ov_mode_lbl = tk.Label(
            hdr, text="IDLE", font=("Consolas", 7, "bold"),
            fg=_TEXT_DIM, bg="#08121e", padx=8, pady=3)
        self._ov_mode_lbl.pack(side="right", padx=(0, 10), pady=10)

        tk.Button(hdr, text="✕", font=("Consolas", 9),
                  fg="#2a4060", bg=_OV_HDR_BG,
                  activebackground=_OV_BG, activeforeground=_TEXT,
                  relief="flat", bd=0, padx=6,
                  command=ov.withdraw).pack(side="right", pady=6)

        tk.Button(hdr, text="CLR", font=("Consolas", 7),
                  fg="#1a3050", bg=_OV_HDR_BG,
                  activebackground=_OV_BG, activeforeground=_TEXT,
                  relief="flat", bd=0, padx=6,
                  command=lambda: self._q.put({"action": "log_clear"})
                  ).pack(side="right", pady=6)

        for w in [hdr] + list(hdr.winfo_children()):
            w.bind("<ButtonPress-1>", self._drag_start)
            w.bind("<B1-Motion>",     self._drag_move)

        # Thin sub-header separator
        tk.Frame(ov, bg=_CYAN_DIM, height=1).pack(fill="x")

        # Ring canvas
        ring_row = tk.Frame(ov, bg=_OV_BG)
        ring_row.pack(fill="x", pady=(8, 4))

        ring_c = self._ov_canvas = tk.Canvas(
            ring_row, width=130, height=130,
            bg=_OV_BG, highlightthickness=0)
        ring_c.pack()

        # Corner HUD brackets
        _m, _a = 3, 14
        for (x1, y1, x2, y2, x3, y3) in [
            (_m, _m + _a, _m, _m, _m + _a, _m),
            (130 - _m - _a, _m, 130 - _m, _m, 130 - _m, _m + _a),
            (_m, 130 - _m - _a, _m, 130 - _m, _m + _a, 130 - _m),
            (130 - _m - _a, 130 - _m, 130 - _m, 130 - _m,
             130 - _m, 130 - _m - _a),
        ]:
            ring_c.create_line(x1, y1, x2, y2, x3, y3,
                               fill=_CYAN_DIM, width=2)

        # Tick marks around the ring (every 15°, with major ticks at 0/90/180/270)
        for deg in range(0, 360, 15):
            rad = math.radians(deg - 90)
            key = 0 if deg % 90 == 0 else (45 if deg % 45 == 0 else 15)
            r_in = _R_IN_TICK[key]
            lw   = 2 if key == 0 else 1
            x1t  = _CX + r_in         * math.cos(rad)
            y1t  = _CY + r_in         * math.sin(rad)
            x2t  = _CX + (_R_OUT - 1) * math.cos(rad)
            y2t  = _CY + (_R_OUT - 1) * math.sin(rad)
            ring_c.create_line(x1t, y1t, x2t, y2t, fill=_CYAN_DIM, width=lw)

        # Ring arcs — drawn back-to-front so halo sits behind everything
        x1a, y1a, x2a, y2a = _ARC_BBOX

        # Outer halo: thick + dim, creates glow depth behind the main ring
        self._ov_ring_halo_id = ring_c.create_arc(
            *_ARC_HALO, start=0, extent=359.9,
            style="arc", width=10, outline="#020a14")

        # Background ring: dim full circle
        self._ov_ring_bg_id = ring_c.create_arc(
            x1a, y1a, x2a, y2a, start=0, extent=359.9,
            style="arc", width=3, outline=_CYAN_DIM)

        # Main animated ring
        self._ov_ring_id = ring_c.create_arc(
            x1a, y1a, x2a, y2a, start=90, extent=359.9,
            style="arc", width=5, outline="#0d1a2a")

        # Inner depth ring: small concentric ring inside the main one
        self._ov_ring_inner_id = ring_c.create_arc(
            *_ARC_INNER, start=0, extent=359.9,
            style="arc", width=1, outline=_CYAN_DIM)

        # Center: three nested ovals for a layered, lit-from-inside look
        self._ov_center_outer_id = ring_c.create_oval(
            _CX - 11, _CY - 11, _CX + 11, _CY + 11,
            fill="#050e1a", outline="")
        self._ov_center_mid_id = ring_c.create_oval(
            _CX - 7, _CY - 7, _CX + 7, _CY + 7,
            fill="#0a1828", outline="")
        self._ov_center_id = ring_c.create_oval(
            _CX - 3, _CY - 3, _CX + 3, _CY + 3,
            fill=_CYAN_DIM, outline="")

        # Scan line: two layers (wide dim outer + narrow bright inner) for glow
        self._ov_scan_glow_id = ring_c.create_line(
            x1a + 4, _CY, x2a - 4, _CY,
            fill=_CYAN_DIM, width=3, state="hidden")
        self._ov_scan_id = ring_c.create_line(
            x1a + 4, _CY, x2a - 4, _CY,
            fill=_CYAN_DIM, width=1, state="hidden")

        # Waveform bars
        self._ov_bar_ids = []
        for bx1, bx2 in _BAR_XS:
            bid = ring_c.create_rectangle(
                bx1, _CY, bx2, _CY,
                fill=_CYAN_DIM, outline="", state="hidden")
            self._ov_bar_ids.append(bid)

        # Thumbnail panel
        self._ov_thumb_frame = tk.Frame(ov, bg=_OV_BG)
        thumb_inner = tk.Frame(self._ov_thumb_frame, bg=_OV_BG)
        thumb_inner.pack(fill="x", padx=12, pady=4)
        self._ov_thumb_lbl = tk.Label(
            thumb_inner, bg="#060f1e", width=160, height=90)
        self._ov_thumb_lbl.pack(side="left")
        self._ov_thumb_title_lbl = tk.Label(
            thumb_inner, text="", font=("Consolas", 8),
            fg=_TEXT_DIM, bg=_OV_BG, anchor="nw",
            justify="left", wraplength=300)
        self._ov_thumb_title_lbl.pack(
            side="left", padx=(10, 0), fill="both")

        # Divider with label
        self._ov_divider = tk.Frame(ov, bg=_OV_BG)
        self._ov_divider.pack(fill="x")
        tk.Frame(self._ov_divider, bg=_CYAN_DIM, height=1).pack(fill="x")
        tk.Label(self._ov_divider,
                 text="─ ─ ─  ACTIVITY FEED  ─ ─ ─",
                 font=("Consolas", 7), fg=_CYAN_DIM, bg=_OV_BG
                 ).pack(pady=(2, 0))

        # Scrollable log
        log_outer = tk.Frame(ov, bg=_OV_BG)
        log_outer.pack(fill="both", expand=True)

        log_vsb = tk.Scrollbar(log_outer, orient="vertical",
                               bg=_CYAN_DIM, troughcolor=_OV_BG,
                               relief="flat", width=6)
        log_vsb.pack(side="right", fill="y")

        self._ov_log_canvas = tk.Canvas(
            log_outer, bg=_OV_BG, highlightthickness=0,
            yscrollcommand=log_vsb.set)
        self._ov_log_canvas.pack(side="left", fill="both", expand=True)
        log_vsb.configure(command=self._ov_log_canvas.yview)

        self._ov_log_inner  = tk.Frame(self._ov_log_canvas, bg=_OV_BG)
        self._ov_log_win_id = self._ov_log_canvas.create_window(
            (0, 0), window=self._ov_log_inner, anchor="nw")

        self._ov_log_inner.bind("<Configure>", lambda e:
            self._ov_log_canvas.configure(
                scrollregion=self._ov_log_canvas.bbox("all")))
        self._ov_log_canvas.bind("<Configure>", lambda e:
            self._ov_log_canvas.itemconfig(
                self._ov_log_win_id, width=e.width))

        ov.withdraw()
        # ── End Visual Overlay ────────────────────────────────────────────────

        self._ready.set()

        def poll():
            while not self._q.empty():
                msg = self._q.get_nowait()
                a   = msg["action"]

                if a == "show":
                    root.deiconify()
                elif a == "hide":
                    root.withdraw()
                elif a == "status":
                    self._status_var.set(msg["text"].upper())
                    self._dot.config(
                        fg=_COLORS.get(msg.get("color", "idle"), _COLORS["idle"]))
                elif a == "text":
                    self._text_var.set(msg["text"])

                elif a == "list":
                    items = msg["items"]
                    for lbl in self._list_labels:
                        lbl.destroy()
                    self._list_labels = []
                    for item in items:
                        row = tk.Frame(self._list_frame, bg=_BG)
                        row.pack(fill="x", pady=1)
                        tk.Frame(row, bg=_CYAN_DIM, width=2).pack(
                            side="left", fill="y")
                        tk.Label(row, text=item, font=("Consolas", 9),
                                 fg=_TEXT, bg=_BG, anchor="w",
                                 padx=8).pack(side="left")
                        self._list_labels.append(row)
                    tk.Frame(self._list_frame, bg=_CYAN_DIM, height=1).pack(
                        fill="x", pady=(4, 0))
                    self._list_frame.pack(fill="x", before=self._top)
                    n     = len(items)
                    new_h = _H_NORMAL + n * _LIST_ROW_H + 14
                    root.geometry(
                        f"700x{new_h}+{(sw - 700) // 2}+{sh - new_h - 60}")
                    root.deiconify()

                elif a == "hide_list":
                    for lbl in self._list_labels:
                        lbl.destroy()
                    self._list_labels = []
                    for child in self._list_frame.winfo_children():
                        child.destroy()
                    self._list_frame.pack_forget()
                    root.geometry(
                        f"700x{_H_NORMAL}+{(sw - 700) // 2}+{sh - _H_NORMAL - 60}")

                elif a in ("toggle_overlay", "toggle_log"):
                    if ov.winfo_viewable():
                        ov.withdraw()
                    else:
                        ov.deiconify()
                        ov.lift()

                elif a == "set_mode":
                    self._ov_mode = msg["mode"]
                    label, bg = _MODE_BADGE.get(msg["mode"], ("IDLE", "#08121e"))
                    fg = _MODE_FG.get(msg["mode"], _TEXT_DIM)
                    self._ov_mode_lbl.config(text=label, bg=bg, fg=fg)

                elif a == "log_entry":
                    self._append_log_row(msg["kind"], msg["text"])

                elif a == "log_clear":
                    for child in self._ov_log_inner.winfo_children():
                        child.destroy()
                    self._ov_log_row_count = 0

                elif a == "thumbnail_show":
                    self._ov_thumb_photo = msg["photo"]
                    self._ov_thumb_lbl.config(image=self._ov_thumb_photo)
                    self._ov_thumb_title_lbl.config(text=msg["title"])
                    self._ov_thumb_frame.pack(
                        fill="x", before=self._ov_divider)

                elif a == "thumbnail_clear":
                    self._ov_thumb_frame.pack_forget()
                    self._ov_thumb_photo = None

            root.after(50, poll)

        root.after(50, poll)
        root.after(33, self._animate)
        root.mainloop()

    # ── Animation ─────────────────────────────────────────────────────────────

    def _animate(self):
        self._ov_anim_tick = (self._ov_anim_tick + 1) % 3600
        if hasattr(self, "_ov_win") and self._ov_win.winfo_ismapped():
            self._draw_ring()
        self.root.after(33, self._animate)

    def _draw_ring(self):
        c    = self._ov_canvas
        t    = self._ov_anim_tick
        mode = self._ov_mode
        x1, y1, x2, y2 = _ARC_BBOX
        span = y2 - y1

        if mode == "idle":
            p     = (math.sin(t * 2 * math.pi / 120) + 1) / 2
            color = _lerp_color("#1a3050", "#2e5588", p)
            c.itemconfig(self._ov_ring_halo_id,
                         outline=_lerp_color("#030c1a", "#07152a", p), width=10)
            c.itemconfig(self._ov_ring_bg_id, outline=_CYAN_DIM, width=2)
            c.itemconfig(self._ov_ring_id, outline=color, width=4,
                         start=90, extent=359.9)
            c.itemconfig(self._ov_ring_inner_id,
                         outline=_lerp_color("#0a1828", "#142840", p))
            c.itemconfig(self._ov_center_outer_id,
                         fill=_lerp_color("#050d18", "#091525", p))
            c.itemconfig(self._ov_center_mid_id,
                         fill=_lerp_color("#080f1e", "#101e30", p))
            c.itemconfig(self._ov_center_id,
                         fill=_lerp_color("#0d2235", "#1a3550", p))
            c.itemconfig(self._ov_scan_glow_id, state="hidden")
            c.itemconfig(self._ov_scan_id, state="hidden")
            for bid in self._ov_bar_ids:
                c.itemconfig(bid, state="hidden")

        elif mode == "listening":
            p     = (math.sin(t * 2 * math.pi / 20) + 1) / 2
            color = _lerp_color("#994400", _ORANGE, p)
            c.itemconfig(self._ov_ring_halo_id,
                         outline=_lerp_color("#0d0500", "#220e00", p), width=12)
            c.itemconfig(self._ov_ring_bg_id, outline="#2d1800", width=2)
            c.itemconfig(self._ov_ring_id, outline=color, width=6,
                         start=90, extent=359.9)
            c.itemconfig(self._ov_ring_inner_id,
                         outline=_lerp_color("#1a0800", "#331400", p))
            c.itemconfig(self._ov_center_outer_id,
                         fill=_lerp_color("#0d0500", "#1a0a00", p))
            c.itemconfig(self._ov_center_mid_id,
                         fill=_lerp_color("#180a00", "#2d1200", p))
            c.itemconfig(self._ov_center_id,
                         fill=_lerp_color("#331800", _ORANGE, p))
            # Scan line glow: wide dim layer + narrow bright layer
            scan_y = y1 + (t % 50) * (span / 50)
            c.coords(self._ov_scan_glow_id, x1 + 4, scan_y, x2 - 4, scan_y)
            c.itemconfig(self._ov_scan_glow_id, state="normal",
                         fill="#1a0c00", width=3)
            c.coords(self._ov_scan_id, x1 + 4, scan_y, x2 - 4, scan_y)
            c.itemconfig(self._ov_scan_id, state="normal",
                         fill="#553300", width=1)
            # Waveform bars
            for i, (bid, (bx1, bx2)) in enumerate(
                    zip(self._ov_bar_ids, _BAR_XS)):
                phase = t * 0.15 + i * 0.8
                bar_h = max(4, int(22 * abs(math.sin(phase))))
                c.coords(bid, bx1, _CY - bar_h // 2, bx2, _CY + bar_h // 2)
                c.itemconfig(bid, state="normal", fill=color)

        elif mode == "processing":
            angle = (t * 6) % 360
            p     = (math.sin(t * 2 * math.pi / 30) + 1) / 2
            c.itemconfig(self._ov_ring_halo_id, outline="#001525", width=12)
            c.itemconfig(self._ov_ring_bg_id, outline=_CYAN_DIM, width=3)
            c.itemconfig(self._ov_ring_id, outline=_CYAN, width=5,
                         start=angle, extent=110)
            c.itemconfig(self._ov_ring_inner_id,
                         outline=_lerp_color(_CYAN_DIM, "#003355", p))
            c.itemconfig(self._ov_center_outer_id, fill="#001525")
            c.itemconfig(self._ov_center_mid_id,  fill="#002233")
            c.itemconfig(self._ov_center_id,       fill="#003d5c")
            # Fast scan glow
            scan_y = y1 + (t % 25) * (span / 25)
            c.coords(self._ov_scan_glow_id, x1 + 4, scan_y, x2 - 4, scan_y)
            c.itemconfig(self._ov_scan_glow_id, state="normal",
                         fill="#001a28", width=3)
            c.coords(self._ov_scan_id, x1 + 4, scan_y, x2 - 4, scan_y)
            c.itemconfig(self._ov_scan_id, state="normal",
                         fill="#003344", width=1)
            for bid in self._ov_bar_ids:
                c.itemconfig(bid, state="hidden")

        elif mode == "playing":
            p     = (math.sin(t * 2 * math.pi / 200) + 1) / 2
            color = _lerp_color("#006644", _GREEN, 0.5 + 0.5 * p)
            c.itemconfig(self._ov_ring_halo_id,
                         outline=_lerp_color("#001810", "#002e20", p), width=10)
            c.itemconfig(self._ov_ring_bg_id, outline="#002218", width=2)
            c.itemconfig(self._ov_ring_id, outline=color, width=5,
                         start=90, extent=359.9)
            c.itemconfig(self._ov_ring_inner_id,
                         outline=_lerp_color("#001810", "#003325", p))
            c.itemconfig(self._ov_center_outer_id,
                         fill=_lerp_color("#001810", "#002018", p))
            c.itemconfig(self._ov_center_mid_id,
                         fill=_lerp_color("#001e14", "#002e1e", p))
            c.itemconfig(self._ov_center_id,
                         fill=_lerp_color("#003322", _GREEN, p * 0.4 + 0.2))
            c.itemconfig(self._ov_scan_glow_id, state="hidden")
            c.itemconfig(self._ov_scan_id, state="hidden")
            for bid in self._ov_bar_ids:
                c.itemconfig(bid, state="hidden")

    # ── Log rows ──────────────────────────────────────────────────────────────

    def _append_log_row(self, kind: str, text: str):
        if self._ov_log_row_count >= 200:
            children = self._ov_log_inner.winfo_children()
            if children:
                children[0].destroy()
                self._ov_log_row_count -= 1

        ts  = datetime.now().strftime("%H:%M:%S")
        bg  = _LOG_ROW_BG[self._ov_log_row_count % 2]
        fg  = _KIND_COLOR.get(kind, "#1e3a52")
        ico = _KIND_ICON.get(kind, "·")

        row = tk.Frame(self._ov_log_inner, bg=bg)
        row.pack(fill="x")

        tk.Frame(row, bg=fg, width=2).pack(side="left", fill="y")
        tk.Label(row, text=ico, font=("Consolas", 9),
                 fg=fg, bg=bg, width=2).pack(side="left", padx=(4, 0), pady=3)
        tk.Label(row, text=ts, font=("Consolas", 8),
                 fg=_TEXT_DIM, bg=bg).pack(side="left", padx=(4, 0), pady=3)
        tk.Label(row, text=text, font=("Consolas", 9),
                 fg=fg, bg=bg, anchor="w",
                 wraplength=_OV_W - 120).pack(
            side="left", padx=(8, 4), pady=3, fill="x", expand=True)

        self._ov_log_row_count += 1
        self._ov_log_canvas.update_idletasks()
        self._ov_log_canvas.yview_moveto(1.0)

    # ── Drag ──────────────────────────────────────────────────────────────────

    def _drag_start(self, event):
        self._drag_x = event.x_root - self._ov_win.winfo_x()
        self._drag_y = event.y_root - self._ov_win.winfo_y()

    def _drag_move(self, event):
        self._ov_win.geometry(
            f"+{event.x_root - self._drag_x}+{event.y_root - self._drag_y}")

    # ── Thumbnail fetch ───────────────────────────────────────────────────────

    def _fetch_thumbnail(self, video_url: str, title: str):
        try:
            import re as _re
            from PIL import Image, ImageTk
            m = _re.search(r"[?&]v=([A-Za-z0-9_-]+)", video_url)
            if not m:
                return
            with urllib.request.urlopen(
                    f"https://i.ytimg.com/vi/{m.group(1)}/mqdefault.jpg",
                    timeout=5) as resp:
                data = resp.read()
            try:
                resample = Image.Resampling.LANCZOS
            except AttributeError:
                resample = Image.LANCZOS
            photo = ImageTk.PhotoImage(
                Image.open(io.BytesIO(data)).resize((160, 90), resample))
            self._pending_thumb = photo
            self._q.put({"action": "thumbnail_show",
                         "photo": photo, "title": title})
        except Exception:
            pass

    # ── Public API ────────────────────────────────────────────────────────────

    def show(self, status: str = "", text: str = "", color: str = "idle"):
        if status:
            self._q.put({"action": "status", "text": status, "color": color})
        self._q.put({"action": "text", "text": text})
        self._q.put({"action": "show"})

    def hide(self):
        self._q.put({"action": "hide"})

    def update(self, status: str = None, text: str = None, color: str = "idle"):
        if status is not None:
            self._q.put({"action": "status", "text": status, "color": color})
        if text is not None:
            self._q.put({"action": "text", "text": text})

    def show_list(self, items: list, status: str = "Which video?"):
        self._q.put({"action": "list",   "items": items})
        self._q.put({"action": "status", "text": status, "color": "listening"})
        self._q.put({"action": "text",   "text": ""})

    def hide_list(self):
        self._q.put({"action": "hide_list"})

    def log(self, kind: str, text: str):
        self._q.put({"action": "log_entry", "kind": kind, "text": text})

    def toggle_log(self):
        self._q.put({"action": "toggle_overlay"})

    def toggle_overlay(self):
        self._q.put({"action": "toggle_overlay"})

    def set_mode(self, mode: str):
        self._q.put({"action": "set_mode", "mode": mode})

    def show_thumbnail(self, video_url: str, title: str):
        self._q.put({"action": "thumbnail_clear"})
        threading.Thread(target=self._fetch_thumbnail,
                         args=(video_url, title), daemon=True).start()

    def clear_thumbnail(self):
        self._q.put({"action": "thumbnail_clear"})
