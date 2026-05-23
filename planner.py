#!/usr/bin/env python3
"""Hex Grid Planner — place, move, and rotate unit icons on a cubic-coordinate hex grid."""

import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog
import json
import math
import os

from PIL import Image, ImageTk, ImageDraw

SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
BASE_HEX    = 42     # circumradius at zoom 1.0
GRID_RADIUS = 6      # rings of hexes to display
BASE_ICON   = 38     # token diameter at zoom 1.0
ZOOM_MIN    = 0.35
ZOOM_MAX    = 3.00
ZOOM_STEP   = 0.15
GRID_PAD    = 3      # extra hex-radii of padding around the scrollregion

# ── Hex geometry (flat-top orientation) ──────────────────────────────────────

def hex_to_pixel(q: int, r: int, size: float) -> tuple[float, float]:
    """Flat-top cubic → screen pixel (origin = grid centre)."""
    x = size * 1.5 * q
    y = size * math.sqrt(3) * (r + q / 2.0)
    return x, y


def pixel_to_hex_frac(px: float, py: float, size: float) -> tuple[float, float, float]:
    q = (2.0 / 3.0) * px / size
    r = (-(1.0 / 3.0) * px + (math.sqrt(3) / 3.0) * py) / size
    return q, r, -q - r


def cube_round(fq: float, fr: float, fs: float) -> tuple[int, int, int]:
    q, r, s = round(fq), round(fr), round(fs)
    dq, dr, ds = abs(q - fq), abs(r - fr), abs(s - fs)
    if dq > dr and dq > ds:
        q = -r - s
    elif dr > ds:
        r = -q - s
    else:
        s = -q - r
    return int(q), int(r), int(s)


def nearest_hex(px: float, py: float, size: float) -> tuple[int, int, int]:
    return cube_round(*pixel_to_hex_frac(px, py, size))


def in_grid(q: int, r: int) -> bool:
    return max(abs(q), abs(r), abs(-q - r)) <= GRID_RADIUS


def flat_hex_corners(cx: float, cy: float, size: float) -> list[float]:
    """Return flat list [x0,y0, x1,y1, ...] for the 6 corners of a flat-top hex."""
    pts: list[float] = []
    for i in range(6):
        a = math.radians(60.0 * i)
        pts += [cx + size * math.cos(a), cy + size * math.sin(a)]
    return pts


# ── Image utilities ───────────────────────────────────────────────────────────

def _load_icon(path: str, size: int) -> Image.Image:
    try:
        img = Image.open(path).convert("RGBA")
        return img.resize((size, size), Image.LANCZOS)
    except Exception:
        return Image.new("RGBA", (size, size), (190, 190, 190, 200))


def make_token(icon_path: str, color_hex: str, size: int,
               rotation: float = 0.0) -> Image.Image:
    """Coloured disc + icon overlay, optionally rotated."""
    color_hex = color_hex.lstrip("#")
    r = int(color_hex[0:2], 16)
    g = int(color_hex[2:4], 16)
    b = int(color_hex[4:6], 16)

    token = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw  = ImageDraw.Draw(token)
    m = 2
    draw.ellipse([m, m, size - m - 1, size - m - 1],
                 fill=(r, g, b, 220), outline=(20, 20, 20, 255), width=2)

    icon_px = int(size * 0.64)
    icon    = _load_icon(icon_path, icon_px)
    off     = (size - icon_px) // 2
    token.paste(icon, (off, off), icon)

    if rotation % 360.0 != 0.0:
        token = token.rotate(-rotation, resample=Image.BICUBIC, expand=False)
    return token


# ── Data model ────────────────────────────────────────────────────────────────

class PlacedUnit:
    __slots__ = ("unit_name", "icon_path", "color_hex", "q", "r",
                 "rotation", "label", "canvas_id", "label_id", "tk_image")

    def __init__(self, unit_name: str, icon_path: str, color_hex: str,
                 q: int, r: int, rotation: float = 0.0, label: str = ""):
        self.unit_name  = unit_name
        self.icon_path  = icon_path     # relative to SCRIPT_DIR
        self.color_hex  = color_hex     # 6-char hex, no '#'
        self.q          = q
        self.r          = r
        self.rotation   = rotation
        self.label      = label
        self.canvas_id: int | None               = None
        self.label_id:  int | None               = None
        self.tk_image:  ImageTk.PhotoImage | None = None

    @property
    def s(self) -> int:
        return -self.q - self.r

    def to_dict(self) -> dict:
        return dict(unit_name=self.unit_name, icon_path=self.icon_path,
                    color_hex=self.color_hex, q=self.q, r=self.r,
                    rotation=self.rotation, label=self.label)

    @classmethod
    def from_dict(cls, d: dict) -> "PlacedUnit":
        return cls(d["unit_name"], d["icon_path"], d["color_hex"],
                   d["q"], d["r"], d.get("rotation", 0.0), d.get("label", ""))


# ── Application ───────────────────────────────────────────────────────────────

class HexPlannerApp:
    PAL = dict(
        bg      = "#2b2b2b",
        canvas  = "#16161e",
        sidebar = "#323232",
        hex_f   = "#1e1e2e",
        hex_o   = "#3a3a5c",
        button  = "#4a4a5a",
        danger  = "#883333",
        io      = "#3a5570",
        text    = "#dddddd",
        sub     = "#7788aa",
        accent  = "#ffcc44",
    )

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Hex Grid Planner")
        self.root.configure(bg=self.PAL["bg"])

        # Runtime state — must be set before _build_ui (Configure may fire early)
        self.placed:        list[PlacedUnit]           = []
        self.selected:      PlacedUnit | None          = None
        self.dragging_unit: dict | None                = None
        self.drag_label:    tk.Label | None            = None
        self.drag_tk_img:   ImageTk.PhotoImage | None  = None
        self.hi_id:         int | None                 = None
        self.drag_hi_id:    int | None                 = None
        self.moving_unit:   PlacedUnit | None          = None
        self._first_resize: bool                       = True

        # Zoom state
        self.zoom:      float = 1.0
        self.hex_size:  float = float(BASE_HEX)
        self.icon_size: int   = BASE_ICON

        self._load_data()
        self._build_ui()
        self._bind_keys()

    # ── Data ──────────────────────────────────────────────────────────────────

    def _load_data(self):
        with open(os.path.join(SCRIPT_DIR, "units.json")) as f:
            self.units: list[dict] = json.load(f)
        with open(os.path.join(SCRIPT_DIR, "teams.json")) as f:
            self.teams: list[dict] = json.load(f)
        self.selected_color: str = (
            self.teams[0]["color"].lstrip("#") if self.teams else "ffffff"
        )

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        outer = tk.Frame(self.root, bg=self.PAL["bg"])
        outer.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        # Left column: zoom bar + canvas + status
        cf = tk.Frame(outer, bg=self.PAL["bg"])
        cf.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._build_zoom_toolbar(cf)

        # Canvas frame with scrollbars
        inner = tk.Frame(cf, bg=self.PAL["canvas"], bd=2, relief=tk.SUNKEN)
        inner.pack(fill=tk.BOTH, expand=True)
        inner.rowconfigure(0, weight=1)
        inner.columnconfigure(0, weight=1)

        self.canvas = tk.Canvas(
            inner, bg=self.PAL["canvas"], highlightthickness=0,
            xscrollincrement=1, yscrollincrement=1,
        )
        xsb = tk.Scrollbar(inner, orient=tk.HORIZONTAL, command=self.canvas.xview,
                            bg=self.PAL["sidebar"], troughcolor="#222")
        ysb = tk.Scrollbar(inner, orient=tk.VERTICAL,   command=self.canvas.yview,
                            bg=self.PAL["sidebar"], troughcolor="#222")
        self.canvas.configure(xscrollcommand=xsb.set, yscrollcommand=ysb.set)

        self.canvas.grid(row=0, column=0, sticky="nsew")
        ysb.grid(row=0, column=1, sticky="ns")
        xsb.grid(row=1, column=0, sticky="ew")

        # Status bar
        self.status_var = tk.StringVar(value="")
        tk.Label(cf, textvariable=self.status_var, bg="#111122", fg=self.PAL["sub"],
                 font=("Consolas", 8), anchor=tk.W).pack(fill=tk.X)

        self.canvas.bind("<Configure>",          self._on_resize)
        self.canvas.bind("<ButtonPress-1>",       self._on_canvas_press)
        self.canvas.bind("<B1-Motion>",           self._on_canvas_move_drag)
        self.canvas.bind("<ButtonRelease-1>",     self._on_canvas_move_drop)
        self.canvas.bind("<Motion>",              self._on_canvas_hover)
        self.canvas.bind("<MouseWheel>",          self._on_mousewheel)
        self.canvas.bind("<Shift-MouseWheel>",    self._on_mousewheel_h)
        self.canvas.bind("<Control-MouseWheel>",  self._on_ctrl_wheel)
        self.canvas.bind("<ButtonPress-2>",       self._on_pan_start)
        self.canvas.bind("<B2-Motion>",           self._on_pan_motion)

        # Sidebar
        sb = tk.Frame(outer, bg=self.PAL["sidebar"], width=210, bd=2, relief=tk.RAISED)
        sb.pack(side=tk.RIGHT, fill=tk.Y, padx=(4, 0))
        sb.pack_propagate(False)
        self._build_sidebar(sb)

    def _build_zoom_toolbar(self, parent: tk.Frame):
        P  = self.PAL
        tb = tk.Frame(parent, bg=P["canvas"], height=28)
        tb.pack(fill=tk.X, pady=(0, 2))

        bf = dict(bg=P["button"], fg="#fff", font=("Helvetica", 11, "bold"),
                  width=2, relief=tk.FLAT, padx=4, pady=0,
                  activebackground="#6a6a8a", activeforeground="#fff")

        tk.Label(tb, text="Zoom:", bg=P["canvas"], fg=P["sub"],
                 font=("Helvetica", 9)).pack(side=tk.LEFT, padx=(8, 4), pady=2)
        tk.Button(tb, text="−", command=self._zoom_out, **bf).pack(side=tk.LEFT, padx=2, pady=2)
        self.zoom_var = tk.StringVar(value="100%")
        tk.Label(tb, textvariable=self.zoom_var, bg=P["canvas"], fg=P["text"],
                 font=("Consolas", 10), width=5).pack(side=tk.LEFT, padx=2)
        tk.Button(tb, text="+", command=self._zoom_in, **bf).pack(side=tk.LEFT, padx=2, pady=2)
        tk.Button(tb, text="Reset", command=self._zoom_reset,
                  bg=P["canvas"], fg=P["sub"], font=("Helvetica", 8), relief=tk.FLAT,
                  activebackground=P["button"], activeforeground="#fff",
                  ).pack(side=tk.LEFT, padx=(6, 2), pady=2)

    def _sep(self, parent):
        tk.Frame(parent, bg="#555566", height=1).pack(fill=tk.X, padx=10, pady=6)

    def _build_sidebar(self, parent: tk.Frame):
        P    = self.PAL
        head = dict(bg=P["sidebar"], fg=P["sub"],  font=("Helvetica", 9, "bold"))
        norm = dict(bg=P["sidebar"], fg=P["text"], font=("Helvetica", 10))

        # ── Team color ────────────────────────────────────────────────────────
        tk.Label(parent, text="TEAM COLOR", **head).pack(pady=(12, 4), padx=10, anchor=tk.W)

        self.color_var = tk.StringVar(
            value=self.teams[0]["name"] if self.teams else ""
        )
        for team in self.teams:
            c   = team["color"].lstrip("#")
            row = tk.Frame(parent, bg=P["sidebar"])
            row.pack(fill=tk.X, padx=10, pady=2)
            tk.Canvas(row, bg=f"#{c}", width=18, height=18,
                      highlightthickness=1, highlightbackground="#777"
                      ).pack(side=tk.LEFT, padx=(0, 6))
            tk.Radiobutton(
                row, text=team["name"], variable=self.color_var, value=team["name"],
                command=lambda t=team: self._pick_team(t),
                bg=P["sidebar"], fg=P["text"], selectcolor="#555",
                activebackground=P["sidebar"], activeforeground="#fff",
                font=("Helvetica", 10),
            ).pack(side=tk.LEFT, fill=tk.X)

        self._sep(parent)

        # ── Unit list ─────────────────────────────────────────────────────────
        tk.Label(parent, text="CREATE UNITS  (drag to grid)", **head
                 ).pack(pady=(0, 6), padx=10, anchor=tk.W)

        self._sb_icons: dict[str, ImageTk.PhotoImage] = {}
        for unit in self.units:
            full = os.path.join(SCRIPT_DIR, unit["icon"])
            img  = _load_icon(full, 28)
            tki  = ImageTk.PhotoImage(img)
            self._sb_icons[unit["name"]] = tki

            row = tk.Frame(parent, bg="#404040", relief=tk.GROOVE, bd=1, cursor="hand2")
            row.pack(fill=tk.X, padx=10, pady=3)

            li = tk.Label(row, image=tki,         bg="#404040", cursor="hand2")
            ln = tk.Label(row, text=unit["name"], bg="#404040", fg=P["text"],
                          font=("Helvetica", 10), cursor="hand2")
            li.pack(side=tk.LEFT, padx=6, pady=5)
            ln.pack(side=tk.LEFT, pady=5)

            for w in (li, ln, row):
                w.bind("<ButtonPress-1>", lambda e, u=unit: self._start_drag(e, u))

        self._sep(parent)

        # ── Selected unit info ────────────────────────────────────────────────
        tk.Label(parent, text="MOVE SELECTED", **head).pack(pady=(0, 4), padx=10, anchor=tk.W)
        self.sel_var = tk.StringVar(value="—")
        tk.Label(parent, textvariable=self.sel_var, bg=P["sidebar"], fg=P["text"],
                 font=("Helvetica", 9), wraplength=180,
                 justify=tk.CENTER).pack(pady=(0, 4), padx=10)

        # Move buttons — hex directions
        # Layout:  NW(0,-1)  NE(+1,-1)
        #           W(-1,0)   E(+1, 0)
        #          SW(-1,+1) SE( 0,+1)
        bf = dict(bg=P["button"], fg="#ffffff", width=4, height=1,
                  relief=tk.RAISED, font=("Helvetica", 8, "bold"),
                  activebackground="#6a6a8a", activeforeground="#fff")

        mg = tk.Frame(parent, bg=P["sidebar"])
        mg.pack(pady=4)
        tk.Button(mg, text="N", command=lambda: self._move( 0, -1), **bf).grid(row=0, column=0, padx=2, pady=2)
        tk.Button(mg, text="NE", command=lambda: self._move( 1, -1), **bf).grid(row=0, column=1, padx=2, pady=2)
        tk.Button(mg, text="NW",  command=lambda: self._move(-1,  0), **bf).grid(row=1, column=0, padx=2, pady=2)
        tk.Button(mg, text="SE",  command=lambda: self._move( 1,  0), **bf).grid(row=1, column=1, padx=2, pady=2)
        tk.Button(mg, text="SW", command=lambda: self._move(-1,  1), **bf).grid(row=2, column=0, padx=2, pady=2)
        tk.Button(mg, text="S", command=lambda: self._move( 0,  1), **bf).grid(row=2, column=1, padx=2, pady=2)

        # Rotate buttons
        rf = tk.Frame(parent, bg=P["sidebar"])
        rf.pack(pady=4)
        tk.Button(rf, text="↺ CCW", command=lambda: self._rotate(-30), **bf).pack(side=tk.LEFT, padx=4)
        tk.Button(rf, text="↻ CW",  command=lambda: self._rotate( 30), **bf).pack(side=tk.LEFT, padx=4)

        tk.Button(parent, text="✕  Delete", command=self._delete,
                  bg=P["danger"], fg="#fff", font=("Helvetica", 10),
                  activebackground="#aa4444", relief=tk.RAISED,
                  ).pack(pady=(4, 8), padx=14, fill=tk.X)

        self._sep(parent)

        # ── Export / Import ───────────────────────────────────────────────────
        io_row = tk.Frame(parent, bg=P["sidebar"])
        io_row.pack(fill=tk.X, padx=10, pady=6)
        bio = dict(bg=P["io"], fg="#fff", font=("Helvetica", 10),
                   activebackground="#5577aa", relief=tk.RAISED)
        tk.Button(io_row, text="⬆ Export", command=self._export, **bio
                  ).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 3))
        tk.Button(io_row, text="⬇ Import", command=self._import, **bio
                  ).pack(side=tk.LEFT, expand=True, fill=tk.X)

    # ── Scroll / zoom helpers ─────────────────────────────────────────────────

    def _scrollregion(self) -> tuple[float, float, float, float]:
        """Symmetric bounding box around the full grid in canvas coords."""
        h   = self.hex_size
        R   = GRID_RADIUS
        pad = GRID_PAD * h
        hw  = h * (1.5 * R + 1.0) + pad
        hh  = h * math.sqrt(3) * (R + 0.5) + pad
        return (-hw, -hh, hw, hh)

    def _center_view(self):
        """Scroll so that canvas coord (0, 0) — the grid centre — is centred in the viewport."""
        sr = self._scrollregion()
        tw = sr[2] - sr[0]
        th = sr[3] - sr[1]
        vw = self.canvas.winfo_width()
        vh = self.canvas.winfo_height()
        fx = (-sr[0] - vw / 2) / tw
        fy = (-sr[1] - vh / 2) / th
        self.canvas.xview_moveto(max(0.0, min(1.0 - vw / tw, fx)))
        self.canvas.yview_moveto(max(0.0, min(1.0 - vh / th, fy)))

    def _apply_zoom(self, focus_cx: float = 0.0, focus_cy: float = 0.0):
        """Recompute sizes, redraw everything, and keep focus_cx/cy at the viewport centre."""
        self.hex_size  = BASE_HEX * self.zoom
        self.icon_size = max(8, int(BASE_ICON * self.zoom))
        self.zoom_var.set(f"{int(round(self.zoom * 100))}%")

        sr = self._scrollregion()
        self.canvas.configure(scrollregion=sr)

        self.canvas.delete("all")
        self.hi_id      = None
        self.drag_hi_id = None
        self._draw_grid()
        for u in self.placed:
            u.canvas_id = None
            u.label_id  = None
        seen: set[tuple[int, int]] = set()
        for u in self.placed:
            if (u.q, u.r) not in seen:
                seen.add((u.q, u.r))
                self._rerender_hex(u.q, u.r)
        self._refresh_hi()

        # Scroll so focus point stays at viewport centre
        tw = sr[2] - sr[0]
        th = sr[3] - sr[1]
        vw = self.canvas.winfo_width()
        vh = self.canvas.winfo_height()
        fx = (focus_cx - sr[0] - vw / 2) / tw
        fy = (focus_cy - sr[1] - vh / 2) / th
        self.canvas.xview_moveto(max(0.0, min(1.0 - vw / tw, fx)))
        self.canvas.yview_moveto(max(0.0, min(1.0 - vh / th, fy)))

    def _zoom_in(self):
        cx, cy = self._viewport_centre_canvas()
        self.zoom = min(ZOOM_MAX, round(self.zoom + ZOOM_STEP, 4))
        self._apply_zoom(cx, cy)

    def _zoom_out(self):
        cx, cy = self._viewport_centre_canvas()
        self.zoom = max(ZOOM_MIN, round(self.zoom - ZOOM_STEP, 4))
        self._apply_zoom(cx, cy)

    def _zoom_reset(self):
        self.zoom = 1.0
        self._apply_zoom()
        self._center_view()

    def _viewport_centre_canvas(self) -> tuple[float, float]:
        vw = self.canvas.winfo_width()
        vh = self.canvas.winfo_height()
        return self.canvas.canvasx(vw / 2), self.canvas.canvasy(vh / 2)

    # ── Canvas events ─────────────────────────────────────────────────────────

    def _on_resize(self, _event):
        if self._first_resize:
            self._first_resize = False
            sr = self._scrollregion()
            self.canvas.configure(scrollregion=sr)
            self._draw_grid()
            self._center_view()
        # After first draw, the grid lives in canvas coords — no redraw needed on resize.

    def _on_mousewheel(self, event: tk.Event):
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _on_mousewheel_h(self, event: tk.Event):
        self.canvas.xview_scroll(int(-1 * (event.delta / 120)), "units")

    def _on_ctrl_wheel(self, event: tk.Event):
        cx = self.canvas.canvasx(event.x)
        cy = self.canvas.canvasy(event.y)
        if event.delta > 0:
            self.zoom = min(ZOOM_MAX, round(self.zoom + ZOOM_STEP, 4))
        else:
            self.zoom = max(ZOOM_MIN, round(self.zoom - ZOOM_STEP, 4))
        self._apply_zoom(cx, cy)

    def _on_pan_start(self, event: tk.Event):
        self.canvas.scan_mark(event.x, event.y)

    def _on_pan_motion(self, event: tk.Event):
        self.canvas.scan_dragto(event.x, event.y, gain=1)

    # ── Draw helpers ──────────────────────────────────────────────────────────

    def _draw_grid(self):
        h         = self.hex_size
        font_size = max(5, int(7 * self.zoom))
        for q in range(-GRID_RADIUS, GRID_RADIUS + 1):
            for r in range(-GRID_RADIUS, GRID_RADIUS + 1):
                s = -q - r
                if max(abs(q), abs(r), abs(s)) > GRID_RADIUS:
                    continue
                cx, cy = hex_to_pixel(q, r, h)
                pts = flat_hex_corners(cx, cy, h)
                self.canvas.create_polygon(
                    pts, outline=self.PAL["hex_o"], fill=self.PAL["hex_f"],
                    width=1, tags="grid")
                self.canvas.create_text(
                    cx, cy + h * 0.60,
                    text=f"{q},{r},{s}", fill=self.PAL["sub"],
                    font=("Helvetica", font_size), tags="grid")

    def _token_layout(self, n: int) -> tuple[int, list[tuple[float, float]]]:
        """Return (token_px_size, [(dx,dy),...]) for n tokens sharing a hex."""
        if n == 0:
            return self.icon_size, []
        if n == 1:
            return self.icon_size, [(0.0, 0.0)]
        # Pack into a cols×rows grid centred at the hex centre.
        cols  = math.ceil(math.sqrt(n))
        rows  = math.ceil(n / cols)
        # Available diameter ≈ inscribed-circle diameter * 0.85
        avail = self.hex_size * math.sqrt(3) * 0.65
        sz    = max(8, int(avail / cols))
        spacing = sz * 1.05
        positions: list[tuple[float, float]] = []
        idx = 0
        for row in range(rows):
            count = min(cols, n - idx)
            oy    = (row - (rows - 1) / 2.0) * spacing
            for col in range(count):
                ox = (col - (count - 1) / 2.0) * spacing
                positions.append((ox, oy))
                idx += 1
        return sz, positions

    def _rerender_hex(self, q: int, r: int):
        """Delete and redraw every unit on hex (q, r) with stacking layout."""
        units = [u for u in self.placed if u.q == q and u.r == r]
        for u in units:
            if u.canvas_id is not None:
                self.canvas.delete(u.canvas_id)
                u.canvas_id = None
            if u.label_id is not None:
                self.canvas.delete(u.label_id)
                u.label_id = None
        if not units:
            return
        cx, cy   = hex_to_pixel(q, r, self.hex_size)
        sz, offs = self._token_layout(len(units))
        for u, (dx, dy) in zip(units, offs):
            self._render_unit(u, cx + dx, cy + dy, sz)

    def _render_unit(self, unit: PlacedUnit,
                     cx: float | None = None, cy: float | None = None,
                     sz: int | None = None):
        if cx is None or cy is None:
            cx, cy = hex_to_pixel(unit.q, unit.r, self.hex_size)
        if sz is None:
            sz = self.icon_size
        img = make_token(os.path.join(SCRIPT_DIR, unit.icon_path),
                         unit.color_hex, sz, unit.rotation)
        tki = ImageTk.PhotoImage(img)
        cid = self.canvas.create_image(cx, cy, image=tki, tags="unit")
        unit.canvas_id = cid
        unit.tk_image  = tki

        if unit.label:
            font_size  = max(6, int(sz * 0.2))
            fill_color = f"#{unit.color_hex}"
            lid = self.canvas.create_text(
                cx, cy + sz // 2 + font_size*0.7,
                text=unit.label, fill=fill_color,
                font=("Helvetica", font_size, "bold"),
                tags="unitlabel")
            unit.label_id = lid

    def _refresh_hi(self):
        if self.hi_id:
            self.canvas.delete(self.hi_id)
            self.hi_id = None
        if not self.selected:
            return
        h = self.hex_size
        cx, cy = hex_to_pixel(self.selected.q, self.selected.r, h)
        pts = flat_hex_corners(cx, cy, h - 3)
        self.hi_id = self.canvas.create_polygon(
            pts, outline=self.PAL["accent"], fill="", width=3, tags="hi")
        self.canvas.tag_raise("hi")
        self.canvas.tag_raise("unit")
        self.canvas.tag_raise("unitlabel")

    def _on_canvas_press(self, event: tk.Event):
        cx = self.canvas.canvasx(event.x)
        cy = self.canvas.canvasy(event.y)
        q, r, _ = nearest_hex(cx, cy, self.hex_size)
        stack = [u for u in self.placed if u.q == q and u.r == r]

        if not stack:
            hit = None
        elif len(stack) == 1:
            hit = stack[0]
        else:
            # Select the token whose rendered centre is closest to the click.
            hx, hy   = hex_to_pixel(q, r, self.hex_size)
            _, offs  = self._token_layout(len(stack))
            hit = min(
                zip(stack, offs),
                key=lambda p: (cx - (hx + p[1][0])) ** 2 + (cy - (hy + p[1][1])) ** 2,
            )[0]

        self.selected = hit
        self._refresh_hi()
        self._update_sel()

        # Begin move-drag if a placed unit was clicked
        self.moving_unit = None
        if hit:
            self.moving_unit = hit
            img = make_token(os.path.join(SCRIPT_DIR, hit.icon_path),
                             hit.color_hex, self.icon_size, hit.rotation)
            self.drag_tk_img = ImageTk.PhotoImage(img)
            sz = self.icon_size
            rx = event.x_root - self.root.winfo_rootx()
            ry = event.y_root - self.root.winfo_rooty()
            self.drag_label = tk.Label(self.root, image=self.drag_tk_img,
                                       bd=0, bg=self.PAL["bg"], cursor="fleur")
            self.drag_label.place(x=rx - sz // 2, y=ry - sz // 2)
            self.drag_label.lift()

    def _on_canvas_move_drag(self, event: tk.Event):
        if not self.moving_unit or not self.drag_label:
            return
        sz = self.icon_size
        rx = event.x_root - self.root.winfo_rootx()
        ry = event.y_root - self.root.winfo_rooty()
        self.drag_label.place(x=rx - sz // 2, y=ry - sz // 2)

        if self.drag_hi_id:
            self.canvas.delete(self.drag_hi_id)
            self.drag_hi_id = None
        ccx = self.canvas.canvasx(event.x)
        ccy = self.canvas.canvasy(event.y)
        h = self.hex_size
        q, r, _ = nearest_hex(ccx, ccy, h)
        if in_grid(q, r) and not (q == self.moving_unit.q and r == self.moving_unit.r):
            hx, hy = hex_to_pixel(q, r, h)
            pts = flat_hex_corners(hx, hy, h - 2)
            self.drag_hi_id = self.canvas.create_polygon(
                pts, outline=self.PAL["accent"],
                fill="#ffcc44", width=2, tags="draghighlight")

    def _on_canvas_move_drop(self, event: tk.Event):
        if self.drag_label:
            self.drag_label.destroy()
            self.drag_label = None
        if self.drag_hi_id:
            self.canvas.delete(self.drag_hi_id)
            self.drag_hi_id = None

        unit = self.moving_unit
        self.moving_unit = None
        if not unit:
            return

        ccx = self.canvas.canvasx(event.x)
        ccy = self.canvas.canvasy(event.y)
        q, r, _ = nearest_hex(ccx, ccy, self.hex_size)
        if not in_grid(q, r) or (q == unit.q and r == unit.r):
            return  # off-grid or same hex — cancel

        old_q, old_r = unit.q, unit.r
        unit.q, unit.r = q, r
        self._rerender_hex(old_q, old_r)
        self._rerender_hex(q, r)
        self._refresh_hi()
        self._update_sel()

    def _on_canvas_hover(self, event: tk.Event):
        cx = self.canvas.canvasx(event.x)
        cy = self.canvas.canvasy(event.y)
        q, r, s = nearest_hex(cx, cy, self.hex_size)
        if in_grid(q, r):
            self.status_var.set(f"hex  q={q}  r={r}  s={s}")
        else:
            self.status_var.set("")

    def _update_sel(self):
        if self.selected:
            u = self.selected
            self.sel_var.set(f"{u.unit_name}\n({u.q}, {u.r}, {u.s})\nrot {u.rotation:.0f}°")
        else:
            self.sel_var.set("—")

    def _start_drag(self, event: tk.Event, unit: dict):
        self.dragging_unit = unit
        img = make_token(os.path.join(SCRIPT_DIR, unit["icon"]),
                         self.selected_color, self.icon_size)
        self.drag_tk_img = ImageTk.PhotoImage(img)
        sz = self.icon_size
        rx = event.x_root - self.root.winfo_rootx()
        ry = event.y_root - self.root.winfo_rooty()
        self.drag_label = tk.Label(self.root, image=self.drag_tk_img,
                                   bd=0, bg=self.PAL["bg"], cursor="fleur")
        self.drag_label.place(x=rx - sz // 2, y=ry - sz // 2)
        self.drag_label.lift()
        self.root.bind("<Motion>",          self._on_drag_motion)
        self.root.bind("<ButtonRelease-1>", self._on_drag_release)

    def _on_drag_motion(self, event: tk.Event):
        if not self.drag_label:
            return
        sz = self.icon_size
        rx = event.x_root - self.root.winfo_rootx()
        ry = event.y_root - self.root.winfo_rooty()
        self.drag_label.place(x=rx - sz // 2, y=ry - sz // 2)

        if self.drag_hi_id:
            self.canvas.delete(self.drag_hi_id)
            self.drag_hi_id = None
        ccx, ccy = self._root_to_canvas(event.x_root, event.y_root)
        if ccx is not None:
            h = self.hex_size
            q, r, _ = nearest_hex(ccx, ccy, h)
            if in_grid(q, r):
                hx, hy = hex_to_pixel(q, r, h)
                pts = flat_hex_corners(hx, hy, h - 2)
                self.drag_hi_id = self.canvas.create_polygon(
                    pts, outline=self.PAL["accent"],
                    fill="#ffcc44", width=2, tags="draghighlight")

    def _on_drag_release(self, event: tk.Event):
        self.root.unbind("<Motion>")
        self.root.unbind("<ButtonRelease-1>")

        if self.drag_hi_id:
            self.canvas.delete(self.drag_hi_id)
            self.drag_hi_id = None
        if self.drag_label:
            self.drag_label.destroy()
            self.drag_label = None

        if not self.dragging_unit:
            return

        ccx, ccy = self._root_to_canvas(event.x_root, event.y_root)
        unit = self.dragging_unit
        self.dragging_unit = None

        if ccx is None:
            return
        q, r, _ = nearest_hex(ccx, ccy, self.hex_size)
        if not in_grid(q, r):
            return

        label = simpledialog.askstring(
            "Unit Name", f"Enter a name for this {unit['name']}:",
            parent=self.root) or ""

        pu = PlacedUnit(unit["name"], unit["icon"], self.selected_color, q, r,
                        label=label)
        self.placed.append(pu)
        self._rerender_hex(q, r)
        self.selected = pu
        self._refresh_hi()
        self._update_sel()

    def _root_to_canvas(self, rx: int, ry: int) -> tuple[float | None, float | None]:
        """Convert root-window pixel coords to canvas coords; None if not over canvas."""
        cx0 = self.canvas.winfo_rootx()
        cy0 = self.canvas.winfo_rooty()
        cw  = self.canvas.winfo_width()
        ch  = self.canvas.winfo_height()
        if cx0 <= rx <= cx0 + cw and cy0 <= ry <= cy0 + ch:
            return (self.canvas.canvasx(rx - cx0),
                    self.canvas.canvasy(ry - cy0))
        return None, None

    # ── Selection actions ─────────────────────────────────────────────────────

    def _pick_team(self, team: dict):
        self.selected_color = team["color"].lstrip("#")

    def _move(self, dq: int, dr: int):
        if not self.selected:
            return
        nq, nr = self.selected.q + dq, self.selected.r + dr
        if not in_grid(nq, nr):
            return
        old_q, old_r = self.selected.q, self.selected.r
        self.selected.q, self.selected.r = nq, nr
        self._rerender_hex(old_q, old_r)
        self._rerender_hex(nq, nr)
        self._refresh_hi()
        self._update_sel()

    def _rotate(self, degrees: float):
        if not self.selected:
            return
        self.selected.rotation = (self.selected.rotation + degrees) % 360.0
        self._rerender_hex(self.selected.q, self.selected.r)
        self._refresh_hi()
        self._update_sel()

    def _delete(self):
        if not self.selected:
            return
        q, r = self.selected.q, self.selected.r
        if self.selected.canvas_id:
            self.canvas.delete(self.selected.canvas_id)
            self.selected.canvas_id = None
        if self.selected.label_id:
            self.canvas.delete(self.selected.label_id)
            self.selected.label_id = None
        self.placed.remove(self.selected)
        if self.hi_id:
            self.canvas.delete(self.hi_id)
            self.hi_id = None
        self.selected = None
        self._rerender_hex(q, r)
        self._update_sel()

    # ── Keyboard shortcuts ────────────────────────────────────────────────────

    def _bind_keys(self):
        self.root.bind("<Delete>",    lambda _: self._delete())
        self.root.bind("<BackSpace>", lambda _: self._delete())
        self.root.bind("r",           lambda _: self._rotate( 30))
        self.root.bind("R",           lambda _: self._rotate(-30))
        self.root.bind("<Escape>",    lambda _: self._deselect())
        self.root.bind("+",           lambda _: self._zoom_in())
        self.root.bind("=",           lambda _: self._zoom_in())
        self.root.bind("-",           lambda _: self._zoom_out())
        self.root.bind("0",           lambda _: self._zoom_reset())

    def _deselect(self):
        self.selected = None
        self._refresh_hi()
        self._update_sel()

    # ── Export / Import ───────────────────────────────────────────────────────

    def _export(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON", "*.json"), ("All files", "*.*")],
            title="Export grid state",
        )
        if not path:
            return
        with open(path, "w") as f:
            json.dump({"units": [u.to_dict() for u in self.placed]}, f, indent=2)
        messagebox.showinfo("Exported",
                            f"Saved {len(self.placed)} unit(s) to:\n{path}")

    def _import(self):
        path = filedialog.askopenfilename(
            filetypes=[("JSON", "*.json"), ("All files", "*.*")],
            title="Import grid state",
        )
        if not path:
            return
        try:
            with open(path) as f:
                data = json.load(f)
        except Exception as exc:
            messagebox.showerror("Import Error", f"Could not read file:\n{exc}")
            return

        # Clear board
        for u in self.placed:
            if u.canvas_id:
                self.canvas.delete(u.canvas_id)
        self.placed.clear()
        self.selected = None
        if self.hi_id:
            self.canvas.delete(self.hi_id)
            self.hi_id = None
        self._update_sel()

        errors = 0
        for item in data.get("units", []):
            try:
                pu = PlacedUnit.from_dict(item)
                self.placed.append(pu)
            except Exception:
                errors += 1

        seen: set[tuple[int, int]] = set()
        for u in self.placed:
            if (u.q, u.r) not in seen:
                seen.add((u.q, u.r))
                self._rerender_hex(u.q, u.r)

        msg = f"Loaded {len(self.placed)} unit(s)."
        if errors:
            msg += f"\n({errors} item(s) skipped due to errors)"
        messagebox.showinfo("Imported", msg)


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    root = tk.Tk()
    root.geometry("1150x780")
    root.minsize(820, 600)
    HexPlannerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
