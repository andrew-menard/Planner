#!/usr/bin/env python3
"""Hex Grid Planner — place, move, and rotate unit icons on a cubic-coordinate hex grid."""

import tkinter as tk
from tkinter import filedialog, messagebox
import json
import math
import os

from PIL import Image, ImageTk, ImageDraw

SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
HEX_SIZE    = 42    # circumradius (centre-to-vertex), px
GRID_RADIUS = 5     # rings of hexes to display
ICON_SIZE   = 38    # unit token diameter, px

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
                 "rotation", "canvas_id", "tk_image")

    def __init__(self, unit_name: str, icon_path: str, color_hex: str,
                 q: int, r: int, rotation: float = 0.0):
        self.unit_name  = unit_name
        self.icon_path  = icon_path     # relative to SCRIPT_DIR
        self.color_hex  = color_hex     # 6-char hex, no '#'
        self.q          = q
        self.r          = r
        self.rotation   = rotation
        self.canvas_id: int | None               = None
        self.tk_image:  ImageTk.PhotoImage | None = None

    @property
    def s(self) -> int:
        return -self.q - self.r

    def to_dict(self) -> dict:
        return dict(unit_name=self.unit_name, icon_path=self.icon_path,
                    color_hex=self.color_hex, q=self.q, r=self.r,
                    rotation=self.rotation)

    @classmethod
    def from_dict(cls, d: dict) -> "PlacedUnit":
        return cls(d["unit_name"], d["icon_path"], d["color_hex"],
                   d["q"], d["r"], d.get("rotation", 0.0))


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
        self.hi_id:         int | None                 = None   # selection highlight
        self.drag_hi_id:    int | None                 = None   # hover highlight

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

        # Canvas area
        cf = tk.Frame(outer, bg=self.PAL["canvas"], bd=2, relief=tk.SUNKEN)
        cf.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.canvas = tk.Canvas(cf, bg=self.PAL["canvas"], highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)

        # Status bar
        self.status_var = tk.StringVar(value="")
        tk.Label(cf, textvariable=self.status_var, bg="#111122", fg=self.PAL["sub"],
                 font=("Consolas", 8), anchor=tk.W).pack(fill=tk.X, padx=4)

        self.canvas.bind("<Configure>",   self._on_resize)
        self.canvas.bind("<ButtonPress-1>", self._on_canvas_press)
        self.canvas.bind("<Motion>",        self._on_canvas_hover)

        # Sidebar
        sb = tk.Frame(outer, bg=self.PAL["sidebar"], width=210, bd=2, relief=tk.RAISED)
        sb.pack(side=tk.RIGHT, fill=tk.Y, padx=(4, 0))
        sb.pack_propagate(False)
        self._build_sidebar(sb)

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
        tk.Label(parent, text="UNITS  (drag to grid)", **head
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
        tk.Label(parent, text="SELECTED", **head).pack(pady=(0, 4), padx=10, anchor=tk.W)
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

    # ── Canvas helpers ────────────────────────────────────────────────────────

    def _origin(self) -> tuple[float, float]:
        return self.canvas.winfo_width() / 2.0, self.canvas.winfo_height() / 2.0

    def _draw_grid(self):
        ox, oy = self._origin()
        for q in range(-GRID_RADIUS, GRID_RADIUS + 1):
            for r in range(-GRID_RADIUS, GRID_RADIUS + 1):
                s = -q - r
                if max(abs(q), abs(r), abs(s)) > GRID_RADIUS:
                    continue
                cx, cy = hex_to_pixel(q, r, HEX_SIZE)
                cx += ox;  cy += oy
                pts = flat_hex_corners(cx, cy, HEX_SIZE)
                self.canvas.create_polygon(
                    pts, outline=self.PAL["hex_o"], fill=self.PAL["hex_f"],
                    width=1, tags="grid")
                self.canvas.create_text(
                    cx, cy + HEX_SIZE * 0.60,
                    text=f"{q},{r},{s}", fill=self.PAL["sub"],
                    font=("Helvetica", 7), tags="grid")

    def _on_resize(self, _event):
        self.canvas.delete("grid")
        self._draw_grid()
        self._redraw_units()

    def _redraw_units(self):
        for u in self.placed:
            if u.canvas_id:
                self.canvas.delete(u.canvas_id)
                u.canvas_id = None
            self._render_unit(u)
        self._refresh_hi()

    def _render_unit(self, unit: PlacedUnit):
        ox, oy = self._origin()
        cx, cy = hex_to_pixel(unit.q, unit.r, HEX_SIZE)
        cx += ox;  cy += oy
        img    = make_token(os.path.join(SCRIPT_DIR, unit.icon_path),
                            unit.color_hex, ICON_SIZE, unit.rotation)
        tki    = ImageTk.PhotoImage(img)
        cid    = self.canvas.create_image(cx, cy, image=tki, tags="unit")
        unit.canvas_id = cid
        unit.tk_image  = tki            # keep ref so GC doesn't collect it

    def _refresh_hi(self):
        if self.hi_id:
            self.canvas.delete(self.hi_id)
            self.hi_id = None
        if not self.selected:
            return
        ox, oy = self._origin()
        cx, cy = hex_to_pixel(self.selected.q, self.selected.r, HEX_SIZE)
        cx += ox;  cy += oy
        pts = flat_hex_corners(cx, cy, HEX_SIZE - 3)
        self.hi_id = self.canvas.create_polygon(
            pts, outline=self.PAL["accent"], fill="", width=3, tags="hi")
        self.canvas.tag_raise("hi")   # keep yellow outline above unit tokens

    # ── Canvas events ─────────────────────────────────────────────────────────

    def _on_canvas_press(self, event: tk.Event):
        ox, oy  = self._origin()
        q, r, _ = nearest_hex(event.x - ox, event.y - oy, HEX_SIZE)
        hit     = next((u for u in self.placed if u.q == q and u.r == r), None)
        self.selected = hit
        self._refresh_hi()
        self._update_sel()

    def _on_canvas_hover(self, event: tk.Event):
        ox, oy  = self._origin()
        q, r, s = nearest_hex(event.x - ox, event.y - oy, HEX_SIZE)
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

    # ── Drag from sidebar ─────────────────────────────────────────────────────

    def _start_drag(self, event: tk.Event, unit: dict):
        self.dragging_unit = unit
        img            = make_token(os.path.join(SCRIPT_DIR, unit["icon"]),
                                    self.selected_color, ICON_SIZE)
        self.drag_tk_img = ImageTk.PhotoImage(img)
        rx = event.x_root - self.root.winfo_rootx()
        ry = event.y_root - self.root.winfo_rooty()
        self.drag_label = tk.Label(self.root, image=self.drag_tk_img,
                                   bd=0, bg=self.PAL["bg"], cursor="fleur")
        self.drag_label.place(x=rx - ICON_SIZE // 2, y=ry - ICON_SIZE // 2)
        self.drag_label.lift()
        self.root.bind("<Motion>",          self._on_drag_motion)
        self.root.bind("<ButtonRelease-1>", self._on_drag_release)

    def _on_drag_motion(self, event: tk.Event):
        if not self.drag_label:
            return
        rx = event.x_root - self.root.winfo_rootx()
        ry = event.y_root - self.root.winfo_rooty()
        self.drag_label.place(x=rx - ICON_SIZE // 2, y=ry - ICON_SIZE // 2)

        # Snap-preview highlight on canvas
        if self.drag_hi_id:
            self.canvas.delete(self.drag_hi_id)
            self.drag_hi_id = None
        cx, cy = self._event_canvas_pos(event)
        if cx is not None:
            ox, oy  = self._origin()
            q, r, _ = nearest_hex(cx - ox, cy - oy, HEX_SIZE)
            if in_grid(q, r):
                hx, hy = hex_to_pixel(q, r, HEX_SIZE)
                hx += ox;  hy += oy
                pts = flat_hex_corners(hx, hy, HEX_SIZE - 2)
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

        cx, cy = self._event_canvas_pos(event)
        unit   = self.dragging_unit
        self.dragging_unit = None

        if cx is None:
            return
        ox, oy  = self._origin()
        q, r, _ = nearest_hex(cx - ox, cy - oy, HEX_SIZE)
        if not in_grid(q, r):
            return
        if any(u.q == q and u.r == r for u in self.placed):
            return     # hex occupied — silently reject

        pu = PlacedUnit(unit["name"], unit["icon"], self.selected_color, q, r)
        self.placed.append(pu)
        self._render_unit(pu)

    def _event_canvas_pos(self, event: tk.Event) -> tuple[float | None, float | None]:
        """Return canvas-local (x, y) if the event is over the canvas, else (None, None)."""
        cx0 = self.canvas.winfo_rootx()
        cy0 = self.canvas.winfo_rooty()
        cw  = self.canvas.winfo_width()
        ch  = self.canvas.winfo_height()
        ex, ey = event.x_root, event.y_root
        if cx0 <= ex <= cx0 + cw and cy0 <= ey <= cy0 + ch:
            return float(ex - cx0), float(ey - cy0)
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
        if any(u.q == nq and u.r == nr
               for u in self.placed if u is not self.selected):
            return
        self.selected.q, self.selected.r = nq, nr
        if self.selected.canvas_id:
            self.canvas.delete(self.selected.canvas_id)
            self.selected.canvas_id = None
        self._render_unit(self.selected)
        self._refresh_hi()
        self._update_sel()

    def _rotate(self, degrees: float):
        if not self.selected:
            return
        self.selected.rotation = (self.selected.rotation + degrees) % 360.0
        if self.selected.canvas_id:
            self.canvas.delete(self.selected.canvas_id)
            self.selected.canvas_id = None
        self._render_unit(self.selected)
        self._refresh_hi()
        self._update_sel()

    def _delete(self):
        if not self.selected:
            return
        if self.selected.canvas_id:
            self.canvas.delete(self.selected.canvas_id)
        self.placed.remove(self.selected)
        if self.hi_id:
            self.canvas.delete(self.hi_id)
            self.hi_id = None
        self.selected = None
        self._update_sel()

    # ── Keyboard shortcuts ────────────────────────────────────────────────────

    def _bind_keys(self):
        self.root.bind("<Delete>",    lambda _: self._delete())
        self.root.bind("<BackSpace>", lambda _: self._delete())
        self.root.bind("r",           lambda _: self._rotate( 30))
        self.root.bind("R",           lambda _: self._rotate(-30))
        self.root.bind("<Escape>",    lambda _: self._deselect())

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
                self._render_unit(pu)
            except Exception:
                errors += 1

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
