"""
Flashpoint: WUI Command
Synchronous multiplayer icebreaker for conference play.

Routing:
  /?role=moderator  → projector / control view
  /?role=player     → mobile player view
"""

from __future__ import annotations

import io
import math
import os
import uuid
from datetime import timedelta
from typing import Any

import qrcode
import streamlit as st
from PIL import Image, ImageDraw, ImageFont
from supabase import Client, create_client

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Flashpoint: WUI Command",
    page_icon="🔥",
    layout="wide",
    initial_sidebar_state="expanded",
)

COLS = ["A", "B", "C", "D", "E", "F", "G"]  # 7 columns
ROWS = list(range(1, 9))  # 8 rows
N_COLS, N_ROWS = 7, 8

LAND_USE_COLORS = {
    "Dense Forest": (34, 90, 40),
    "Shrubland": (140, 180, 70),
    "Suburban WUI": (220, 140, 40),
    "Urban Center": (110, 110, 120),
    "Reservoir": (40, 130, 200),
}

ACTION_LABELS = {
    "do_nothing": "Do Nothing",
    "mitigate_smoke": "Mitigate Smoke",
    "harden_evacuate": "Harden/Evacuate",
}

ACTION_COSTS = {
    "do_nothing": 0,
    "mitigate_smoke": 2000,
    "harden_evacuate": 5000,
}

# Pre-scripted hazards by round. Tile IDs under each polygon.
# Lobby has none; rounds escalate west → east into the WUI.
HAZARDS: dict[int, dict[str, Any]] = {
    0: {
        "phase": "lobby",
        "weather": "Calm conditions. Awaiting ignition…",
        "fire": [],
        "smoke": [],
    },
    1: {
        "phase": "round_1",
        "weather": (
            "Red Flag Warning — hot, dry west winds 15–20 mph. "
            "Ignition in the western Dense Forest; light smoke drifting east."
        ),
        "fire": [
            "A1", "A2", "A3",
            "B1", "B2",
        ],
        "smoke": [
            "B3", "B4",
            "C1", "C2", "C3",
            "D1", "D2",
        ],
    },
    2: {
        "phase": "round_2",
        "weather": (
            "Gusty SW winds 25 mph. Fire runs through shrubland; "
            "dense smoke plume tracking into the Suburban WUI."
        ),
        "fire": [
            "A1", "A2", "A3", "A4", "A5",
            "B1", "B2", "B3", "B4",
            "C2", "C3",
        ],
        "smoke": [
            "C1", "C4", "C5",
            "D1", "D2", "D3", "D4",
            "E2", "E3",
        ],
    },
    3: {
        "phase": "round_3",
        "weather": (
            "Extreme fire weather. Ember cast into suburban neighborhoods; "
            "heavy smoke blankets the urban fringe."
        ),
        "fire": [
            "A1", "A2", "A3", "A4", "A5", "A6",
            "B1", "B2", "B3", "B4", "B5",
            "C1", "C2", "C3", "C4",
            "D2", "D3",
        ],
        "smoke": [
            "C5", "C6",
            "D1", "D4", "D5",
            "E1", "E2", "E3", "E4",
            "F3", "F4",
        ],
    },
}


# ---------------------------------------------------------------------------
# Supabase helpers
# ---------------------------------------------------------------------------

def get_supabase() -> Client:
    """Create a Supabase client from secrets or environment variables."""
    url = None
    key = None
    try:
        url = st.secrets.get("SUPABASE_URL") or st.secrets.get("supabase", {}).get("url")
        key = (
            st.secrets.get("SUPABASE_KEY")
            or st.secrets.get("SUPABASE_ANON_KEY")
            or st.secrets.get("supabase", {}).get("key")
        )
    except Exception:
        pass

    url = url or os.environ.get("SUPABASE_URL")
    key = key or os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_ANON_KEY")

    if not url or not key:
        st.error(
            "Missing Supabase credentials. Set `SUPABASE_URL` and `SUPABASE_KEY` "
            "in `.streamlit/secrets.toml` or as environment variables."
        )
        st.stop()

    # Accept either project root URL or a pasted REST endpoint.
    url = url.strip().rstrip("/")
    for suffix in ("/rest/v1", "/rest/v1/"):
        if url.endswith(suffix.rstrip("/")):
            url = url[: -len(suffix.rstrip("/"))]
            break

    if "YOUR_" in url or "YOUR_" in key:
        st.error(
            "Replace the placeholders in `.streamlit/secrets.toml` with your real "
            "Supabase Project URL and anon key."
        )
        st.stop()

    return create_client(url, key)


def _supabase_call(label: str, fn):
    """Run a Supabase call and surface connection errors clearly in the UI."""
    try:
        return fn()
    except Exception as exc:
        msg = str(exc)
        st.error(f"Supabase error while {label}: `{type(exc).__name__}`")
        if "ProxyError" in type(exc).__name__ or "403" in msg:
            st.warning(
                "Outbound requests to Supabase are being blocked by an HTTP proxy "
                "(403 Forbidden). In the terminal where you launch Streamlit, try:\n\n"
                "`unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy`\n\n"
                "Then run `streamlit run app.py` again."
            )
        else:
            st.exception(exc)
        st.stop()


@st.cache_data(ttl=2)
def fetch_global_state(_sb: Client) -> dict[str, Any]:
    def _run():
        return _sb.table("global_state").select("*").eq("id", 1).single().execute().data

    return _supabase_call("loading global state", _run)


@st.cache_data(ttl=5)
def fetch_grid(_sb: Client) -> list[dict[str, Any]]:
    def _run():
        return _sb.table("grid").select("*").order("row_idx").order("col_idx").execute().data or []

    return _supabase_call("loading the grid", _run)


def fetch_player_count(sb: Client) -> int:
    def _run():
        return sb.table("players").select("player_id", count="exact").execute().count or 0

    return _supabase_call("counting players", _run)


def fetch_player(sb: Client, player_id: str) -> dict[str, Any] | None:
    res = (
        sb.table("players")
        .select("*, grid(land_use)")
        .eq("player_id", player_id)
        .limit(1)
        .execute()
    )
    rows = res.data or []
    return rows[0] if rows else None


def clear_data_caches() -> None:
    fetch_global_state.clear()
    fetch_grid.clear()


# ---------------------------------------------------------------------------
# Fictional hexagonal board (Catan-style, no basemap)
# ---------------------------------------------------------------------------

def _hex_corners(cx: float, cy: float, size: float) -> list[tuple[float, float]]:
    """Pointy-top hexagon vertices."""
    return [
        (
            cx + size * math.sin(math.radians(60 * i)),
            cy - size * math.cos(math.radians(60 * i)),
        )
        for i in range(6)
    ]


def _hex_center(col_idx: int, row_idx: int, size: float, origin_x: float, origin_y: float) -> tuple[float, float]:
    """Odd-row horizontal offset layout (pointy-top)."""
    w = math.sqrt(3) * size
    h = 2 * size
    x = origin_x + col_idx * w + (row_idx % 2) * (w / 2)
    y = origin_y + row_idx * (h * 0.75)
    return x, y


def _load_font(size: int) -> ImageFont.ImageFont:
    for name in (
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/Library/Fonts/Arial.ttf",
        "DejaVuSans-Bold.ttf",
        "DejaVuSans.ttf",
    ):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _draw_land_icon(draw: ImageDraw.ImageDraw, land_use: str, cx: float, cy: float, size: float) -> None:
    """Simple Catan-like pictograms inside each hex."""
    s = size * 0.28

    if land_use == "Dense Forest":
        # Three pine trees
        for dx, scale in ((-0.55, 0.85), (0.0, 1.0), (0.55, 0.8)):
            tx, ty = cx + dx * s * 1.4, cy + 0.15 * s
            h = s * 1.6 * scale
            draw.polygon(
                [(tx, ty - h), (tx - s * 0.55 * scale, ty + h * 0.15), (tx + s * 0.55 * scale, ty + h * 0.15)],
                fill=(20, 60, 25),
            )
            draw.rectangle(
                [tx - s * 0.1, ty + h * 0.1, tx + s * 0.1, ty + h * 0.55],
                fill=(70, 45, 25),
            )

    elif land_use == "Shrubland":
        for dx, dy, r in ((-0.5, 0.15, 0.45), (0.15, -0.2, 0.55), (0.55, 0.25, 0.4)):
            bx, by = cx + dx * s * 1.3, cy + dy * s * 1.2
            rr = s * r
            draw.ellipse([bx - rr, by - rr * 0.7, bx + rr, by + rr * 0.7], fill=(90, 130, 40))

    elif land_use == "Suburban WUI":
        # Two small houses
        for dx in (-0.55, 0.45):
            hx, hy = cx + dx * s * 1.2, cy + 0.2 * s
            body = [hx - s * 0.45, hy - s * 0.1, hx + s * 0.45, hy + s * 0.55]
            draw.rectangle(body, fill=(245, 230, 200))
            draw.polygon(
                [
                    (hx, hy - s * 0.65),
                    (hx - s * 0.55, hy - s * 0.05),
                    (hx + s * 0.55, hy - s * 0.05),
                ],
                fill=(160, 60, 40),
            )

    elif land_use == "Urban Center":
        # Skyline blocks
        for dx, h_frac in ((-0.7, 0.9), (-0.15, 1.25), (0.4, 0.75)):
            bx = cx + dx * s
            top = cy - h_frac * s
            bot = cy + 0.55 * s
            draw.rectangle([bx - s * 0.28, top, bx + s * 0.28, bot], fill=(55, 55, 65))
            # windows
            for wy in (0.2, 0.45, 0.7):
                yy = top + (bot - top) * wy
                draw.rectangle([bx - s * 0.12, yy - s * 0.08, bx + s * 0.12, yy + s * 0.08], fill=(220, 210, 120))

    elif land_use == "Reservoir":
        # Concentric wave arcs
        for i, r in enumerate((0.35, 0.55, 0.75)):
            rr = s * r * 1.4
            y0 = cy - rr * 0.15 + i * s * 0.08
            draw.arc(
                [cx - rr, y0 - rr * 0.35, cx + rr, y0 + rr * 0.55],
                start=200,
                end=340,
                fill=(200, 230, 255),
                width=max(2, int(size * 0.04)),
            )


def _blend_hex(
    base: Image.Image,
    overlay_rgba: tuple[int, int, int, int],
    corners: list[tuple[float, float]],
) -> None:
    """Paint a translucent hex overlay onto the board."""
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.polygon(corners, fill=overlay_rgba)
    base.alpha_composite(layer)


def _draw_hatch(
    draw: ImageDraw.ImageDraw,
    cx: float,
    cy: float,
    size: float,
    color: tuple[int, int, int, int],
    angle_deg: float = 45,
) -> None:
    """Diagonal hatch clipped roughly to the hex interior."""
    step = max(6, int(size * 0.18))
    half = size * 0.85
    rad = math.radians(angle_deg)
    dx, dy = math.cos(rad), math.sin(rad)
    px, py = -dy, dx  # perpendicular
    for i in range(-8, 9):
        ox = cx + px * i * step
        oy = cy + py * i * step
        draw.line(
            [
                (ox - dx * half, oy - dy * half),
                (ox + dx * half, oy + dy * half),
            ],
            fill=color,
            width=max(2, int(size * 0.045)),
        )


def _draw_smoke_marker(draw: ImageDraw.ImageDraw, cx: float, cy: float, size: float) -> None:
    """Grey cloud puffs + dashed ring — readable at projector distance."""
    # Soft wash is applied separately; this is the icon language.
    for dx, dy, r in ((-0.35, -0.35, 0.32), (0.05, -0.5, 0.38), (0.4, -0.28, 0.3)):
        bx, by = cx + dx * size * 0.55, cy + dy * size * 0.45
        rr = size * r
        draw.ellipse(
            [bx - rr, by - rr * 0.75, bx + rr, by + rr * 0.75],
            fill=(210, 210, 215, 210),
            outline=(90, 90, 100, 230),
            width=max(1, int(size * 0.03)),
        )
    # Badge
    bw = size * 0.55
    bh = size * 0.28
    bx0, by0 = cx - bw / 2, cy - size * 0.72
    draw.rounded_rectangle(
        [bx0, by0, bx0 + bw, by0 + bh],
        radius=8,
        fill=(55, 55, 60, 230),
        outline=(220, 220, 225, 255),
        width=2,
    )
    font = _load_font(max(11, int(size * 0.22)))
    label = "SMOKE"
    bbox = draw.textbbox((0, 0), label, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    draw.text((cx - tw / 2, by0 + (bh - th) / 2 - 1), label, fill=(240, 240, 245), font=font)


def _draw_fire_marker(draw: ImageDraw.ImageDraw, cx: float, cy: float, size: float) -> None:
    """Bold flame cluster + FIRE badge."""
    for dx, scale, color in (
        (-0.28, 0.85, (255, 140, 30, 230)),
        (0.0, 1.1, (255, 70, 20, 240)),
        (0.3, 0.9, (255, 190, 50, 230)),
    ):
        fx = cx + dx * size * 0.55
        top = cy - size * 0.55 * scale
        mid = cy - size * 0.05
        base_y = cy + size * 0.2
        draw.polygon(
            [
                (fx, top),
                (fx - size * 0.18 * scale, mid),
                (fx - size * 0.08 * scale, base_y),
                (fx + size * 0.08 * scale, base_y),
                (fx + size * 0.18 * scale, mid),
            ],
            fill=color,
        )
        # Hot core
        draw.polygon(
            [
                (fx, top + size * 0.18 * scale),
                (fx - size * 0.07 * scale, mid),
                (fx + size * 0.07 * scale, mid),
            ],
            fill=(255, 240, 160, 230),
        )

    bw = size * 0.48
    bh = size * 0.28
    bx0, by0 = cx - bw / 2, cy - size * 0.78
    draw.rounded_rectangle(
        [bx0, by0, bx0 + bw, by0 + bh],
        radius=8,
        fill=(180, 25, 15, 235),
        outline=(255, 220, 120, 255),
        width=2,
    )
    font = _load_font(max(11, int(size * 0.22)))
    label = "FIRE"
    bbox = draw.textbbox((0, 0), label, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    draw.text((cx - tw / 2, by0 + (bh - th) / 2 - 1), label, fill=(255, 255, 255), font=font)


def _draw_legend_panel(
    img: Image.Image,
    board_w: int,
    board_h: int,
    legend_h: int,
) -> None:
    """Large projector-readable legend under the hex field."""
    draw = ImageDraw.Draw(img)
    top = board_h
    draw.rectangle([0, top, board_w, top + legend_h], fill=(32, 40, 48, 255))
    draw.line([(0, top), (board_w, top)], fill=(90, 110, 100, 255), width=3)

    title_font = _load_font(28)
    item_font = _load_font(22)
    draw.text((28, top + 16), "LEGEND", fill=(245, 245, 245), font=title_font)

    # Land-use swatches (mini hexes)
    land_items = [
        ("Dense Forest", LAND_USE_COLORS["Dense Forest"]),
        ("Shrubland", LAND_USE_COLORS["Shrubland"]),
        ("Suburban WUI", LAND_USE_COLORS["Suburban WUI"]),
        ("Urban Center", LAND_USE_COLORS["Urban Center"]),
        ("Reservoir", LAND_USE_COLORS["Reservoir"]),
    ]
    x = 28
    y = top + 62
    mini = 18
    for name, col in land_items:
        corners = _hex_corners(x + mini, y + mini, mini)
        draw.polygon(corners, fill=col, outline=(245, 235, 210))
        draw.text((x + mini * 2 + 10, y + 6), name, fill=(235, 235, 235), font=item_font)
        x += mini * 2 + 10 + draw.textbbox((0, 0), name, font=item_font)[2] + 36

    # Hazard callouts on second row
    y2 = top + 118
    # Fire sample
    fx = 48
    draw.ellipse([fx - 16, y2 - 4, fx + 16, y2 + 28], fill=(220, 50, 25))
    draw.polygon(
        [(fx, y2 - 18), (fx - 12, y2 + 8), (fx + 12, y2 + 8)],
        fill=(255, 170, 40),
    )
    draw.text((fx + 28, y2), "FIRE — red badge + flames + orange border", fill=(255, 210, 180), font=item_font)

    # Smoke sample
    sx = board_w // 2 + 20
    for dx, dy, r in ((-10, 0, 12), (6, -8, 14), (18, 2, 11)):
        draw.ellipse([sx + dx - r, y2 + dy - r + 8, sx + dx + r, y2 + dy + r + 8], fill=(190, 190, 195))
    draw.text((sx + 40, y2), "SMOKE — grey badge + clouds + dashed border", fill=(210, 210, 215), font=item_font)


def render_hex_board(
    grid_rows: list[dict],
    fire_tiles: list[str] | None = None,
    smoke_tiles: list[str] | None = None,
    hex_size: float = 58.0,
) -> Image.Image:
    """
    Draw a fictional pointy-top hex board with land-use icons and hazard overlays.
    """
    fire_set = set(fire_tiles or [])
    smoke_set = set(smoke_tiles or [])

    size = hex_size
    w = math.sqrt(3) * size
    h = 2 * size
    pad = size * 0.85
    origin_x = pad + w / 2
    origin_y = pad + size
    legend_h = 170

    board_w = int(origin_x + (N_COLS - 1) * w + w / 2 + pad + w / 2)
    field_h = int(origin_y + (N_ROWS - 1) * (h * 0.75) + size + pad * 0.6)
    board_h = field_h + legend_h

    img = Image.new("RGBA", (board_w, board_h), (28, 36, 44, 255))
    draw = ImageDraw.Draw(img)
    font = _load_font(max(12, int(size * 0.28)))

    # Soft vignette / table felt (hex field only)
    draw.rounded_rectangle(
        [8, 8, board_w - 9, field_h - 9],
        radius=24,
        fill=(42, 58, 48, 255),
        outline=(70, 95, 75, 255),
        width=3,
    )

    for g in grid_rows:
        col_idx = int(g["col_idx"])
        row_idx = int(g["row_idx"])
        tile_id = g["tile_id"]
        land_use = g["land_use"]
        color = LAND_USE_COLORS.get(land_use, (160, 160, 160))

        cx, cy = _hex_center(col_idx, row_idx, size, origin_x, origin_y)
        corners = _hex_corners(cx, cy, size * 0.96)

        # Base fill + rim (Catan tile look)
        draw.polygon(corners, fill=color, outline=(245, 235, 210), width=2)
        inner = _hex_corners(cx, cy, size * 0.88)
        draw.polygon(inner, outline=(255, 255, 255, 60))

        _draw_land_icon(draw, land_use, cx, cy - size * 0.02, size)

        under_fire = tile_id in fire_set
        under_smoke = tile_id in smoke_set and not under_fire  # fire wins visually

        if under_smoke:
            _blend_hex(img, (55, 55, 60, 90), corners)
            d2 = ImageDraw.Draw(img)
            _draw_hatch(d2, cx, cy, size, (180, 180, 185, 110), angle_deg=35)
            # Dashed-looking thick grey border via double stroke
            d2.polygon(corners, outline=(200, 200, 210, 255), width=max(4, int(size * 0.08)))
            ring = _hex_corners(cx, cy, size * 0.90)
            d2.polygon(ring, outline=(80, 80, 90, 220), width=max(2, int(size * 0.04)))
            _draw_smoke_marker(d2, cx, cy, size)

        if under_fire:
            _blend_hex(img, (255, 60, 20, 85), corners)
            d2 = ImageDraw.Draw(img)
            _draw_hatch(d2, cx, cy, size, (255, 120, 40, 130), angle_deg=-40)
            d2.polygon(corners, outline=(255, 210, 60, 255), width=max(5, int(size * 0.1)))
            ring = _hex_corners(cx, cy, size * 0.88)
            d2.polygon(ring, outline=(200, 30, 15, 240), width=max(3, int(size * 0.05)))
            _draw_fire_marker(d2, cx, cy, size)

        # Tile ID badge (drawn last so it stays readable)
        d3 = ImageDraw.Draw(img)
        label = tile_id
        bbox = d3.textbbox((0, 0), label, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        lx, ly = cx - tw / 2, cy + size * 0.48 - th / 2
        badge_fill = (120, 20, 10, 210) if under_fire else ((40, 40, 45, 200) if under_smoke else (20, 20, 20, 170))
        d3.rounded_rectangle(
            [lx - 6, ly - 2, lx + tw + 6, ly + th + 2],
            radius=6,
            fill=badge_fill,
        )
        d3.text((lx, ly), label, fill=(255, 255, 255), font=font)

    _draw_legend_panel(img, board_w, field_h, legend_h)
    return img.convert("RGB")


def show_board(
    grid_rows: list[dict],
    fire_tiles: list[str] | None = None,
    smoke_tiles: list[str] | None = None,
) -> None:
    board = render_hex_board(grid_rows, fire_tiles, smoke_tiles)
    st.image(board, width="stretch")


# ---------------------------------------------------------------------------
# Game resolution
# ---------------------------------------------------------------------------

def resolve_round(sb: Client, round_number: int) -> int:
    """
    Apply hazard outcomes for `round_number` to every player who has not yet
    been resolved for that round. Returns number of players updated.
    """
    if round_number < 1:
        return 0

    hazard = HAZARDS[round_number]
    fire_set = set(hazard["fire"])
    smoke_set = set(hazard["smoke"])

    players = sb.table("players").select("*").execute().data or []
    updated = 0

    for p in players:
        if (p.get("last_resolved") or 0) >= round_number:
            continue

        tile = p.get("tile_id")
        action = p.get("current_action")
        # Only count an action if it was submitted for this round
        if p.get("action_round") != round_number:
            action = "do_nothing"  # no submission ≡ do nothing

        capital = float(p.get("capital") or 10000)
        trust = float(p.get("trust") or 100)

        under_fire = tile in fire_set
        under_smoke = tile in smoke_set

        # Fire takes precedence when a tile is in both (shouldn't happen in script).
        # Action costs are deducted on submit; resolution only applies hazard effects.
        if under_fire:
            if action == "harden_evacuate":
                trust = max(0.0, trust - 10.0)
                # capital unchanged (already paid harden cost)
            else:
                # do_nothing or mitigate_smoke
                capital = 0.0
                trust = max(0.0, trust - 50.0)
        elif under_smoke:
            if action == "mitigate_smoke":
                pass  # 0% trust loss
            else:
                # do_nothing or harden_evacuate
                trust = max(0.0, trust - 30.0)

        sb.table("players").update(
            {
                "capital": round(capital, 2),
                "trust": round(trust, 2),
                "current_action": None,
                "action_round": None,
                "last_resolved": round_number,
            }
        ).eq("player_id", p["player_id"]).execute()
        updated += 1

    return updated


def advance_to_round(sb: Client, target_round: int) -> None:
    """Resolve the previous round (if any), then set global state to target_round."""
    state = fetch_global_state(sb)
    current = int(state.get("round_number") or 0)

    if target_round > current >= 1:
        resolve_round(sb, current)
    elif target_round == 0:
        pass

    # Clear any lingering actions when entering a new live round.
    # PostgREST requires a WHERE clause on UPDATE (even for "all rows").
    if target_round >= 1:
        sb.table("players").update(
            {"current_action": None, "action_round": None}
        ).gte("capital", 0).execute()

    hazard = HAZARDS.get(target_round, HAZARDS[0])
    sb.table("global_state").update(
        {
            "phase": hazard["phase"],
            "round_number": target_round,
            "weather_text": hazard["weather"],
        }
    ).eq("id", 1).execute()
    clear_data_caches()


def end_game(sb: Client) -> None:
    state = fetch_global_state(sb)
    current = int(state.get("round_number") or 0)
    if current >= 1:
        resolve_round(sb, current)

    sb.table("global_state").update(
        {
            "phase": "ended",
            "weather_text": "Incident terminated. Review outcomes with your table.",
        }
    ).eq("id", 1).execute()
    clear_data_caches()


def reset_game(sb: Client) -> None:
    """Reset to lobby. Prefer RPC; fall back to filtered client updates."""
    try:
        sb.rpc("reset_game", {}).execute()
    except Exception:
        # Supabase blocks DELETE/UPDATE without a WHERE clause — use filters.
        sb.table("players").delete().neq(
            "player_id", "00000000-0000-0000-0000-000000000000"
        ).execute()
        sb.table("grid").update({"is_assigned": False}).gte("col_idx", 0).execute()
        sb.table("global_state").update(
            {
                "phase": "lobby",
                "round_number": 0,
                "weather_text": "Calm conditions. Awaiting ignition…",
            }
        ).eq("id", 1).execute()
    clear_data_caches()


# ---------------------------------------------------------------------------
# QR code
# ---------------------------------------------------------------------------

def make_qr_image(url: str):
    qr = qrcode.QRCode(version=1, box_size=12, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    return qr.make_image(fill_color="black", back_color="white")


def detect_lan_base_url(port: int = 8501) -> str:
    """Best-effort LAN URL so phones on the same Wi‑Fi can join."""
    import socket

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
        if ip and not ip.startswith("127."):
            return f"http://{ip}:{port}"
    except OSError:
        pass
    return f"http://localhost:{port}"


def player_join_url() -> str:
    """Build the player URL for the QR code."""
    base = None
    try:
        base = st.secrets.get("APP_BASE_URL") or st.secrets.get("app", {}).get("base_url")
    except Exception:
        pass
    base = (
        base
        or os.environ.get("APP_BASE_URL")
        or st.session_state.get("app_base_url")
        or detect_lan_base_url()
    )
    return f"{base.rstrip('/')}/?role=player"


# ---------------------------------------------------------------------------
# Moderator view
# ---------------------------------------------------------------------------

def render_moderator(sb: Client) -> None:
    st.title("🔥 Flashpoint: WUI Command")
    st.caption("Moderator / Projector View")

    # Sidebar controls — refetch state so buttons stay accurate after actions
    with st.sidebar:
        clear_data_caches()
        state = fetch_global_state(sb)
        phase = state.get("phase", "lobby")
        round_number = int(state.get("round_number") or 0)

        st.header("Round Controls")
        st.write(f"**Phase:** `{phase}`")
        st.write(f"**Round:** {round_number}")

        if st.button("▶ Start Round 1", use_container_width=True, disabled=phase != "lobby"):
            advance_to_round(sb, 1)
            st.rerun()

        if st.button("▶ Start Round 2", use_container_width=True, disabled=phase != "round_1"):
            advance_to_round(sb, 2)
            st.rerun()

        if st.button("▶ Start Round 3", use_container_width=True, disabled=phase != "round_2"):
            advance_to_round(sb, 3)
            st.rerun()

        if st.button(
            "⏹ End Game",
            use_container_width=True,
            type="primary",
            disabled=phase in ("lobby", "ended"),
        ):
            end_game(sb)
            st.rerun()

        st.divider()
        if st.button("↺ Reset to Lobby", use_container_width=True):
            reset_game(sb)
            st.rerun()

        st.divider()
        st.subheader("QR Base URL")
        default_base = st.session_state.get(
            "app_base_url",
            os.environ.get("APP_BASE_URL") or detect_lan_base_url(),
        )
        base_url = st.text_input(
            "Public app URL (for QR)",
            value=default_base,
            help=(
                "Phones must use your computer's LAN IP on the same Wi‑Fi, "
                "e.g. http://192.168.1.10:8501 — not localhost."
            ),
        )
        st.session_state["app_base_url"] = base_url.rstrip("/")
        st.caption(f"Players open: `{base_url.rstrip('/')}/?role=player`")

        st.divider()
        st.markdown(
            """
            **Legend**
            - 🌲 Dense Forest
            - 🌿 Shrubland
            - 🏠 Suburban WUI
            - 🏢 Urban Center
            - 💧 Reservoir
            - 🔥 Fire overlay
            - ☁ Smoke overlay
            """
        )

    @st.fragment(run_every=timedelta(seconds=2))
    def live_body():
        clear_data_caches()
        state = fetch_global_state(sb)
        phase = state.get("phase", "lobby")
        round_number = int(state.get("round_number") or 0)
        weather = state.get("weather_text", "")
        count = fetch_player_count(sb)

        c1, c2, c3 = st.columns(3)
        c1.metric("Connected Players", count)
        c2.metric("Phase", phase.replace("_", " ").title())
        c3.metric("Round", round_number)

        st.info(f"**Forecast:** {weather}")

        if phase == "lobby":
            left, right = st.columns([1, 1.6])
            with left:
                st.subheader("Scan to Join")
                join_url = player_join_url()
                st.code(join_url, language=None)
                img = make_qr_image(join_url)
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                st.image(buf.getvalue(), width=280)
                st.caption("Players open the link on their phones and receive a random hex tile.")
            with right:
                st.subheader("Flashpoint Hex Board")
                grid_rows = fetch_grid(sb)
                show_board(grid_rows)
            return

        grid_rows = fetch_grid(sb)
        hazard = HAZARDS.get(round_number, HAZARDS[0])
        show_board(grid_rows, hazard["fire"], hazard["smoke"])

        if phase == "ended":
            st.success("Game ended. Outcomes have been resolved for the final round.")
            players = (
                sb.table("players")
                .select("tile_id, capital, trust")
                .order("capital", desc=True)
                .limit(15)
                .execute()
                .data
                or []
            )
            if players:
                st.subheader("Top tiles by remaining capital")
                st.dataframe(players, width="stretch", hide_index=True)
        else:
            with st.expander("Hazard tiles this round", expanded=False):
                st.write("**Fire:**", ", ".join(hazard["fire"]) or "—")
                st.write("**Smoke:**", ", ".join(hazard["smoke"]) or "—")

    live_body()


# ---------------------------------------------------------------------------
# Player view
# ---------------------------------------------------------------------------

def ensure_player(sb: Client) -> dict[str, Any]:
    """Assign a tile once per browser session and return the player row."""
    if "player_id" not in st.session_state:
        st.session_state["player_id"] = str(uuid.uuid4())

    player_id = st.session_state["player_id"]
    existing = fetch_player(sb, player_id)
    if existing and existing.get("tile_id"):
        return existing

    try:
        res = sb.rpc("assign_random_tile", {"p_player_id": player_id}).execute()
    except Exception as exc:
        st.error(f"Could not assign a tile: {exc}")
        st.stop()

    data = res.data
    if isinstance(data, list) and data:
        row = data[0]
    elif isinstance(data, dict):
        row = data
    else:
        st.error("No tiles available — the grid is full (56 players max).")
        st.stop()

    player = fetch_player(sb, player_id)
    if not player:
        return {
            "player_id": player_id,
            "tile_id": row["tile_id"],
            "capital": row["capital"],
            "trust": row["trust"],
            "current_action": None,
            "action_round": None,
            "last_resolved": 0,
            "grid": {"land_use": row["land_use"]},
        }
    return player


def submit_action(sb: Client, player_id: str, action: str, round_number: int) -> bool:
    """Lock in an action and deduct its cost immediately. Returns False if rejected."""
    cost = ACTION_COSTS[action]
    player = fetch_player(sb, player_id)
    if not player:
        return False

    if player.get("current_action") and player.get("action_round") == round_number:
        return False

    capital = float(player.get("capital") or 0)
    if capital < cost:
        st.warning(f"Not enough capital for {ACTION_LABELS[action]} (${cost:,}).")
        return False

    sb.table("players").update(
        {
            "current_action": action,
            "action_round": round_number,
            "capital": round(capital - cost, 2),
        }
    ).eq("player_id", player_id).execute()
    return True


def render_player(sb: Client) -> None:
    st.title("🔥 Flashpoint")
    st.caption("Player Command Console")

    player = ensure_player(sb)
    tile_id = player.get("tile_id", "?")
    land_use = (player.get("grid") or {}).get("land_use") or "Unknown"
    player_id = player["player_id"]

    st.markdown(f"### Tile **{tile_id}** · {land_use}")

    @st.fragment(run_every=timedelta(seconds=2))
    def player_phase_panel():
        clear_data_caches()
        state = fetch_global_state(sb)
        phase = state.get("phase", "lobby")
        round_number = int(state.get("round_number") or 0)
        weather = state.get("weather_text", "")
        fresh = fetch_player(sb, player_id) or player
        fresh_capital = float(fresh.get("capital") or 10000)
        fresh_trust = float(fresh.get("trust") or 100)

        m1, m2 = st.columns(2)
        m1.metric("Capital", f"${fresh_capital:,.0f}")
        m2.metric("Trust", f"{fresh_trust:.0f}%")

        if phase == "lobby":
            st.success("You're connected. Wait for the Moderator to start Round 1.")
            st.info(weather)
            st.caption("Listening for round start…")
            return

        if phase == "ended":
            st.warning("Game over. Check the projector for the final map.")
            st.write(
                f"Final capital: **${fresh_capital:,.0f}** · Trust: **{fresh_trust:.0f}%**"
            )
            return

        st.info(f"**Round {round_number}** — {weather}")

        hazard = HAZARDS.get(round_number, HAZARDS[0])
        if tile_id in hazard["fire"]:
            st.error("⚠ Your tile is in the projected **FIRE** path this round.")
        elif tile_id in hazard["smoke"]:
            st.warning("☁ Your tile is under the projected **SMOKE** plume this round.")
        else:
            st.caption("Your tile is outside the current fire/smoke polygons.")

        already_acted = (
            fresh.get("current_action") is not None
            and fresh.get("action_round") == round_number
        )

        if already_acted:
            label = ACTION_LABELS.get(fresh["current_action"], fresh["current_action"])
            st.success(f"Action locked in: **{label}**")
            st.caption("Waiting for Moderator to advance the round…")
            return

        st.subheader("Choose your action")
        st.caption("You may submit once per round. Costs are deducted immediately.")

        if st.button("① Do Nothing  ($0)", use_container_width=True, key="act_nothing"):
            submit_action(sb, player_id, "do_nothing", round_number)
            st.rerun()

        if st.button(
            "② Mitigate Smoke  ($2,000)",
            use_container_width=True,
            key="act_smoke",
            disabled=fresh_capital < 2000,
        ):
            submit_action(sb, player_id, "mitigate_smoke", round_number)
            st.rerun()

        if st.button(
            "③ Harden / Evacuate  ($5,000)",
            use_container_width=True,
            key="act_harden",
            disabled=fresh_capital < 5000,
        ):
            submit_action(sb, player_id, "harden_evacuate", round_number)
            st.rerun()

        with st.expander("What do these do?"):
            st.markdown(
                """
                - **Do Nothing** — no cost. Smoke hurts trust; fire wipes capital.
                - **Mitigate Smoke** — $2,000. Blocks smoke trust loss; does **not** protect against fire.
                - **Harden/Evacuate** — $5,000. Keeps capital if fire hits (small trust hit); does **not** block smoke.
                """
            )

    player_phase_panel()


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------

def main() -> None:
    role = (st.query_params.get("role") or "").lower().strip()
    sb = get_supabase()

    if role == "moderator":
        render_moderator(sb)
    elif role == "player":
        render_player(sb)
    else:
        st.title("Flashpoint: WUI Command")
        st.write("Choose a role:")
        col_a, col_b = st.columns(2)
        with col_a:
            st.link_button("Moderator (projector)", "?role=moderator", use_container_width=True)
        with col_b:
            st.link_button("Player (mobile)", "?role=player", use_container_width=True)


if __name__ == "__main__":
    main()
else:
    main()
