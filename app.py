"""
WUI Interactive
Synchronous multiplayer icebreaker for AAAR 2026 (WUI smoke, impacts, communication).

Local test (default SQLite, no network):
  streamlit run app.py
  Moderator lobby:  http://localhost:8501
  Player (QR / TinyURL only):  http://localhost:8501/?role=player
"""

from __future__ import annotations

import base64
import io
import json
import os
import ssl
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import plotly.graph_objects as go
import qrcode
import streamlit as st
from supabase import Client, create_client
from supabase.lib.client_options import ClientOptions
from wordcloud import WordCloud

from wui_store import WuiLocalClient, get_wui_client

try:
    from streamlit_autorefresh import st_autorefresh
except ImportError:  # pragma: no cover
    st_autorefresh = None

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SESSION_TITLE = "AAAR 2026: WUI Smoke Impacts"
APP_NAME = "WUI Interactive"
GAME4_PROMPT = "What is the biggest bottleneck in managing WUI smoke impacts?"
HANG_TIGHT = "The games will start soon. Hang tight..."
SESSION_SHORT_URL = "https://tinyurl.com/wuiaaar2026"
SHORT_URL_CACHE = Path(__file__).resolve().parent / "data" / "join_short.json"

PHASE_TITLES = {
    "lobby": "Lobby — Join the session",
    "game1": "Game 1 — Before, During, After",
    "game2": "Game 2 — Fact or Fiction",
    "game3": "Game 3 — Bottlenecks",
    "game4": "Game 4 — Panel topics",
    "end": "Session close",
}

PHASE_BUTTONS = (
    ("lobby", "Lobby"),
    ("game1", "Game 1"),
    ("game2", "Game 2"),
    ("game3", "Game 3"),
    ("game4", "Game 4"),
    ("end", "End"),
)
GAME_PHASES = ("game1", "game2", "game3", "game4")
TIMER_SECONDS = 120

# id, short chart label, full phone prompt
INTEREST_TOPICS: list[tuple[str, str, str]] = [
    (
        "fire_emission",
        "Fire Emission",
        "WUI fires emit smoke and ash, polluting air, soil, and water. Which pollutants should we focus on monitoring and why?",
    ),
    (
        "emissions_health",
        "Emissions & Health",
        "How does smoke composition, pollutant concentration, and population exposure (for example duration and spatial patterns) differ between a WUI fire and a wildfire?",
    ),
    (
        "public_health",
        "Public Health",
        "What is the current science regarding health effects associated with WUI fire smoke exposure compared to wildfire smoke?",
    ),
    (
        "forecasting",
        "Forecasting",
        "WUI fire conditions change rapidly. What new information could improve smoke forecasting systems and models?",
    ),
    (
        "risk_communication",
        "Risk Communication",
        "What new research data is needed to assist with public health messaging and risk communication for WUI smoke?",
    ),
    (
        "remediation",
        "Remediation",
        "Returning home varies by location and state, and there is a push for science-backed testing and remediation. Where do we stand, and what is needed for a remediation plan any affected resident can use?",
    ),
    (
        "resources",
        "Resources",
        "What single resource (for example building retrofits, DIY air cleaners, or updated warning systems) would make the biggest immediate impact on public health?",
    ),
    (
        "innovation",
        "Innovation",
        "What is one recent innovation, study, or community-led initiative that gives you hope that we are getting better at managing a WUI fire crisis?",
    ),
]
INTEREST_LABELS = [label for _topic_id, label, _prompt in INTEREST_TOPICS]
INTEREST_LEVELS = ("Low", "Medium", "High")

CONCEPTS = [
    "Sensor Networks",
    "Public Warnings",
    "Health Studies",
    "Prescribed Burns",
    "Indoor Filtration",
    "Defensible Space",
    "Risk Communication",
    "Smoke Forecasting",
]
MAPPER_PHASES = ("Before", "During", "After")

GAME3_QUESTIONS: dict[int, str] = {
    1: "Most homes lost in WUI fires ignite from wind-blown embers, not a wall of flame.",
    2: "If you can see wildfire smoke outside, the air inside your home is safe.",
    3: "Prescribed fire always means less smoke for nearby communities.",
    4: "Air-quality alerts reach every household in a WUI community within 10 minutes.",
    5: "Closing windows and running a portable HEPA filter can lower indoor smoke particles.",
    6: "A cloth mask protects against wildfire smoke as well as an N95 respirator.",
    7: "Once outdoor smoke clears, indoor smoke is gone too.",
    8: "Wildfire smoke only harms people who already have lung or heart disease.",
}

ASSETS_DIR = Path(__file__).resolve().parent / "assets"
HERO_IMAGE = ASSETS_DIR / "la_wui_smoke_2025.png"

GAMES_AND_RULES = """
### Games and rules

1. **Before / During / After** — For each tool, select every moment it matters. More than one of **Before**, **During**, and **After** is allowed.
2. **Fact or Fiction** — Vote on **8** claims about WUI fire and smoke.
3. **Bottlenecks** — Up to **3** words, separated by commas, for the biggest bottleneck.
4. **Panel topics** — Rate the priority (low, medium, or high) of the following panel topics to help us determine tonight's discussion agenda.
"""

GAME_RULES = {
    "game1": """
### Game 1 rules

For each tool, select every moment it matters: **Before**, **During**, and/or **After**. You can choose more than one. The chart shows whether the room treats each tool as preparedness, emergency response, recovery, or a mix of those.
""",
    "game2": """
### Game 2 rules

Read each claim about WUI fire and smoke and vote **Fact** or **Fiction**. There are eight claims, and each one needs a vote. The split shows which ideas the room shares and which are still contested.
""",
    "game3": """
### Game 3 rules

Name the biggest bottleneck in managing WUI smoke impacts using up to three words, separated by commas. Each entry must be a single word, and a hyphenated word counts as one. The cloud colors words by theme, such as science, engineering, and communication.
""",
    "game4": """
### Game 4 rules

Rate the priority (low, medium, or high) of the following panel topics to help us determine tonight's discussion agenda.
""",
}

THEME_COLORS: list[tuple[str, str, tuple[str, ...]]] = [
    ("Science", "#0D47A1", ("research", "data", "model", "forecast", "monitor", "science", "evidence")),
    ("Engineering", "#1B5E20", ("filter", "retrofit", "building", "ventilation", "sensor", "design", "infrastructure")),
    ("Communication", "#4A148C", ("warning", "alert", "message", "communication", "trust", "outreach")),
    ("Health", "#880E4F", ("health", "exposure", "respiratory", "asthma", "hospital")),
    ("Policy", "#1A237E", ("policy", "funding", "regulation", "code", "insurance", "standard")),
    ("Operations", "#3E2723", ("response", "staffing", "coordination", "evacuation", "resource", "capacity")),
]
OTHER_THEME_COLOR = "#212121"

st.set_page_config(
    page_title=APP_NAME,
    page_icon="🔥",
    layout="wide",
    initial_sidebar_state="collapsed",
)


def inject_theme(role: str) -> None:
    player_shell = ""
    if role == "player":
        player_shell = """
        [data-testid="stAppViewContainer"] .main .block-container {
            max-width: 480px;
            padding-top: 0.6rem;
            padding-bottom: 2.5rem;
            margin-left: auto;
            margin-right: auto;
        }
        [data-testid="stAppViewContainer"] .main p,
        [data-testid="stAppViewContainer"] .main li,
        [data-testid="stAppViewContainer"] .main label,
        [data-testid="stAppViewContainer"] .main span,
        [data-testid="stAppViewContainer"] .main .stMarkdown {
            font-size: 17px !important;
        }
        [data-testid="stAppViewContainer"] .main h2,
        [data-testid="stAppViewContainer"] .main h3 {
            font-size: 1.35rem !important;
        }
        .wui-card-title {
            font-size: 1.05rem !important;
        }
        """
    st.markdown(
        f"""
        <style>
        html {{
            color-scheme: light !important;
        }}
        html, body, [data-testid="stAppViewContainer"],
        [data-testid="stAppViewContainer"] .main,
        [data-testid="stHeader"] {{
            background-color: #FFFFFF !important;
            color: #1A1A1A !important;
            font-family: Arial, system-ui, sans-serif !important;
            font-size: 22px !important;
        }}
        [data-testid="stSidebar"],
        [data-testid="stSidebarCollapsedControl"],
        [data-testid="stSidebarCollapseButton"],
        [data-testid="stExpandSidebarButton"] {{
            display: none !important;
            visibility: hidden !important;
            width: 0 !important;
            height: 0 !important;
            pointer-events: none !important;
        }}
        [data-testid="stToolbar"], [data-testid="stDecoration"],
        [data-testid="stStatusWidget"], [data-testid="stHeader"] {{
            visibility: hidden;
            height: 0;
            pointer-events: none !important;
        }}
        [role="tooltip"],
        [data-baseweb="tooltip"],
        [data-baseweb="popover"],
        [data-testid="stTooltipContent"],
        [data-testid="stTooltip"] {{
            display: none !important;
            visibility: hidden !important;
            opacity: 0 !important;
            pointer-events: none !important;
        }}
        iframe[title*="autorefresh"],
        iframe[title*="streamlit_autorefresh"] {{
            display: none !important;
            height: 0 !important;
        }}
        p, li, label, .stMarkdown, .stCaption, .stAlert, div[data-testid="stText"] {{
            font-size: 22px !important;
            line-height: 1.35 !important;
            color: #1A1A1A !important;
            font-family: Arial, system-ui, sans-serif !important;
        }}
        [data-testid="stIconMaterial"] {{
            font-family: "Material Symbols Rounded" !important;
        }}
        h1, .wui-title {{
            font-family: Arial, system-ui, sans-serif !important;
            font-size: 2.4rem !important;
            font-weight: 800 !important;
            color: #1A1A1A !important;
            line-height: 1.15 !important;
        }}
        h2, h3 {{
            font-family: Arial, system-ui, sans-serif !important;
            font-size: 1.6rem !important;
            font-weight: 800 !important;
            color: #1A1A1A !important;
        }}
        .wui-kicker {{
            color: #FF5722;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.12em;
            font-size: 1.1rem !important;
            margin-bottom: 0.2rem;
        }}
        .wui-title {{
            margin: 0 0 0.25rem 0;
        }}
        .wui-subtitle {{
            color: #424242;
            font-size: 1.5rem !important;
            font-weight: 700;
            margin-bottom: 0.6rem;
        }}
        .wui-login {{
            color: #E64A19;
            font-size: 1.35rem !important;
            font-weight: 800;
            margin: 0 0 0.5rem 0;
        }}
        .wui-legend {{
            font-size: 16px !important;
            line-height: 1.8 !important;
            margin: 0.35rem 0 0 0;
        }}
        .wui-swatch {{
            display: inline-block;
            width: 0.85rem;
            height: 0.85rem;
            margin-right: 0.35rem;
            border-radius: 2px;
            vertical-align: -0.05rem;
        }}
        .wui-card-title {{
            font-size: 1.25rem !important;
            font-weight: 800;
            line-height: 1.3;
            color: #1A1A1A;
            margin: 0 0 0.35rem 0;
        }}
        .wui-join-url {{
            font-size: 1.15rem !important;
            font-weight: 700;
            word-break: break-all;
            background: #F5F5F5;
            color: #1A1A1A;
            border: 2px solid #FF5722;
            padding: 0.55rem 0.7rem;
            border-radius: 10px;
        }}
        .wui-timer {{
            font-size: 3.2rem !important;
            font-weight: 800;
            color: #E64A19;
            line-height: 1;
            margin: 0 0 0.4rem 0;
        }}
        .wui-timer.low {{
            color: #B71C1C;
        }}
        .wui-hero {{
            width: 100%;
            max-width: 240px;
            height: auto;
            max-height: 26vh;
            object-fit: contain;
            object-position: left top;
            display: block;
        }}
        [data-testid="stColumn"]:has(.wui-rules-mark) h3 {{
            font-size: 1.35rem !important;
            margin: 0.2rem 0 0.35rem 0;
        }}
        [data-testid="stColumn"]:has(.wui-rules-mark) p,
        [data-testid="stColumn"]:has(.wui-rules-mark) li {{
            font-size: 18px !important;
            margin-bottom: 0.15rem;
        }}
        .wui-hero-cap {{
            color: #616161 !important;
            font-size: 0.95rem !important;
            margin: 0.25rem 0 0 0;
        }}
        [data-testid="stAppViewContainer"] .main .block-container {{
            padding-top: 0.6rem;
            padding-bottom: 0.4rem;
        }}
        button[data-testid="stBaseButton-secondary"],
        button[data-testid="stBaseButton-primary"] {{
            font-weight: 800 !important;
            font-size: 1.05rem !important;
            min-height: 3rem;
            border-radius: 12px;
            font-family: Arial, system-ui, sans-serif !important;
        }}
        button[data-testid="stBaseButton-secondary"] {{
            background-color: #FFFFFF !important;
            color: #1A1A1A !important;
            border: 2px solid #BDBDBD !important;
        }}
        button[data-testid="stBaseButton-primary"] {{
            background-color: #FF5722 !important;
            color: #FFFFFF !important;
            border: 2px solid #FF5722 !important;
        }}
        [data-testid="stTextInput"] input {{
            background-color: #FFFFFF !important;
            color: #1A1A1A !important;
            border: 2px solid #BDBDBD !important;
            -webkit-text-fill-color: #1A1A1A !important;
        }}
        [data-testid="stTextInput"] input::placeholder {{
            color: #616161 !important;
            -webkit-text-fill-color: #616161 !important;
            opacity: 1;
        }}
        [data-testid="stTextInput"] input::-webkit-input-placeholder {{
            color: #616161 !important;
            -webkit-text-fill-color: #616161 !important;
            opacity: 1;
        }}
        [data-testid="stTextInput"] input:focus::placeholder {{
            color: transparent !important;
            -webkit-text-fill-color: transparent !important;
            opacity: 0 !important;
        }}
        [data-testid="stTextInput"] input:focus::-webkit-input-placeholder {{
            color: transparent !important;
            -webkit-text-fill-color: transparent !important;
            opacity: 0 !important;
        }}
        [data-testid="stTextInput"] input:not(:placeholder-shown)::placeholder {{
            color: transparent !important;
            -webkit-text-fill-color: transparent !important;
            opacity: 0 !important;
        }}
        [data-testid="stTextInput"] input:not(:placeholder-shown)::-webkit-input-placeholder {{
            color: transparent !important;
            -webkit-text-fill-color: transparent !important;
            opacity: 0 !important;
        }}
        div[data-testid="stAlert"] {{
            background-color: #FFF8F5 !important;
            color: #1A1A1A !important;
        }}
        div[data-testid="stVerticalBlockBorderWrapper"] {{
            background: #FFFFFF !important;
            border: 1px solid #E0E0E0 !important;
            border-radius: 12px !important;
        }}
        {player_shell}
        </style>
        """,
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Backend
# ---------------------------------------------------------------------------

Backend = Client | WuiLocalClient


def _secret_or_env(*names: str) -> str | None:
    for name in names:
        try:
            value = st.secrets.get(name)
        except Exception:
            value = None
        if value:
            return str(value).strip()
        env = os.environ.get(name)
        if env:
            return env.strip()
    return None


def _flag(name: str, default: bool = False) -> bool:
    raw: Any = None
    found = False
    try:
        if name in st.secrets:
            raw = st.secrets[name]
            found = True
    except Exception:
        pass
    if not found and name in os.environ:
        raw = os.environ[name]
        found = True
    if not found:
        return default
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _mark_local_mode(reason: str) -> WuiLocalClient:
    st.session_state["local_mode"] = True
    st.session_state["local_mode_reason"] = reason
    return get_wui_client()


def get_backend() -> Backend:
    if _flag("USE_LOCAL_DB", default=True):
        return _mark_local_mode(
            "Local play mode — no internet required. WUI Interactive data is stored on this computer."
        )

    url = _secret_or_env("SUPABASE_URL")
    key = _secret_or_env("SUPABASE_KEY", "SUPABASE_ANON_KEY")
    if not url:
        try:
            url = (st.secrets.get("supabase", {}) or {}).get("url")  # type: ignore[attr-defined]
        except Exception:
            url = None
    if not key:
        try:
            key = (st.secrets.get("supabase", {}) or {}).get("key")  # type: ignore[attr-defined]
        except Exception:
            key = None
    if not url or not key or "YOUR_" in str(url) or "YOUR_" in str(key):
        return _mark_local_mode("Supabase credentials are missing; using local SQLite.")

    url = str(url).strip().rstrip("/")
    key = str(key).strip()
    for suffix in ("/rest/v1", "/rest/v1/"):
        if url.endswith(suffix.rstrip("/")):
            url = url[: -len(suffix.rstrip("/"))]
            break

    try:
        client = create_client(
            url,
            key,
            options=ClientOptions(postgrest_client_timeout=5),
        )
        client.table("app_state").select("id").eq("id", 1).limit(1).execute()
        st.session_state["local_mode"] = False
        return client
    except Exception as exc:
        return _mark_local_mode(
            f"Supabase unreachable ({type(exc).__name__}); using local SQLite."
        )


def _db_call(label: str, fn):
    try:
        return fn()
    except Exception as exc:
        msg = str(exc)
        st.error(f"Database error while {label}: `{type(exc).__name__}: {exc}`")
        if "ProxyError" in type(exc).__name__ or "403" in msg:
            st.warning(
                "Outbound requests to Supabase are being blocked by an HTTP proxy. "
                "Unset HTTP_PROXY/HTTPS_PROXY and restart Streamlit, or keep USE_LOCAL_DB = true."
            )
        else:
            st.exception(exc)
        st.stop()


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "t", "yes", "on"}


@st.cache_data(ttl=1)
def fetch_app_state(_sb: Backend) -> dict[str, Any]:
    def _run():
        return _sb.table("app_state").select("*").eq("id", 1).single().execute().data

    data = _db_call("loading app state", _run) or {}
    return {
        "current_phase": data.get("current_phase") or "lobby",
        "active_question": int(data.get("active_question") or 1),
        "results_open": _as_bool(data.get("results_open")),
        "revealed_phases": _revealed_list(data.get("revealed_phases")),
        "phase_timers": _parse_timers(data.get("phase_timers")),
    }


def _revealed_list(raw: Any) -> list[str]:
    return [part for part in str(raw or "").split(",") if part]


def _parse_timers(raw: Any) -> dict[str, float]:
    if isinstance(raw, dict):
        payload = raw
    else:
        try:
            payload = json.loads(raw or "{}")
        except (TypeError, json.JSONDecodeError):
            return {}
    if not isinstance(payload, dict):
        return {}
    timers: dict[str, float] = {}
    for key, value in payload.items():
        try:
            timers[str(key)] = float(value)
        except (TypeError, ValueError):
            continue
    return timers


def seconds_remaining(timers: dict[str, float], phase: str) -> int:
    started = timers.get(phase)
    if started is None:
        return TIMER_SECONDS
    return max(0, TIMER_SECONDS - int(time.time() - started))


@st.cache_data(ttl=1)
def fetch_interest(_sb: Backend) -> list[dict[str, Any]]:
    def _run():
        return (
            _sb.table("game1_interest")
            .select("topic, interest, player_name")
            .execute()
            .data
            or []
        )

    return _db_call("loading Game 4 votes", _run)


@st.cache_data(ttl=1)
def fetch_mapper(_sb: Backend) -> list[dict[str, Any]]:
    def _run():
        return (
            _sb.table("game1_mapper")
            .select("concept, assigned_phase, player_name")
            .execute()
            .data
            or []
        )

    return _db_call("loading Game 1 votes", _run)


@st.cache_data(ttl=1)
def fetch_trivia(_sb: Backend) -> list[dict[str, Any]]:
    def _run():
        return (
            _sb.table("game2_trivia")
            .select("question_id, vote, player_name")
            .execute()
            .data
            or []
        )

    return _db_call("loading Game 2 votes", _run)


@st.cache_data(ttl=1)
def fetch_cloud(_sb: Backend) -> list[dict[str, Any]]:
    def _run():
        return _sb.table("game3_cloud").select("word, player_name").execute().data or []

    return _db_call("loading Game 3 words", _run)


@st.cache_data(ttl=1)
def fetch_logged_in(_sb: Backend) -> int:
    def _run():
        return _sb.table("session_players").select("player_name").execute().data or []

    rows = _db_call("counting logged-in players", _run)
    names = {str(row.get("player_name") or "").strip() for row in rows}
    names.discard("")
    return len(names)


def register_player(sb: Backend, player_name: str) -> None:
    name = player_name.strip()
    if not name:
        return
    existing = (
        sb.table("session_players").select("player_name").eq("player_name", name).limit(1).execute().data
        or []
    )
    if existing:
        return
    sb.table("session_players").insert({"player_name": name}).execute()
    clear_caches()


def clear_caches() -> None:
    fetch_app_state.clear()
    fetch_interest.clear()
    fetch_mapper.clear()
    fetch_trivia.clear()
    fetch_cloud.clear()
    fetch_logged_in.clear()


def set_phase(sb: Backend, phase: str) -> None:
    state = fetch_app_state(sb)
    revealed = set(state["revealed_phases"])
    timers = dict(state["phase_timers"])
    payload: dict[str, Any] = {"current_phase": phase}
    if phase in revealed:
        payload["results_open"] = True
    else:
        payload["results_open"] = False
        if phase in GAME_PHASES and phase not in timers:
            timers[phase] = time.time()
            payload["phase_timers"] = json.dumps(timers)
    sb.table("app_state").update(payload).eq("id", 1).execute()
    clear_caches()


def mark_game_revealed(sb: Backend, phase: str) -> None:
    state = fetch_app_state(sb)
    revealed = set(state["revealed_phases"])
    revealed.add(phase)
    order = [key for key, _label in PHASE_BUTTONS if key in revealed]
    sb.table("app_state").update(
        {"results_open": True, "revealed_phases": ",".join(order)}
    ).eq("id", 1).execute()
    clear_caches()


def reset_session(sb: Backend) -> None:
    sb.table("game1_interest").delete().neq("topic", "").execute()
    sb.table("game1_mapper").delete().neq("concept", "").execute()
    sb.table("game2_trivia").delete().gte("question_id", 0).execute()
    sb.table("game3_cloud").delete().neq("word", "").execute()
    sb.table("session_players").delete().neq("player_name", "").execute()
    sb.table("app_state").update(
        {
            "current_phase": "lobby",
            "active_question": 1,
            "results_open": False,
            "revealed_phases": "",
            "phase_timers": "{}",
        }
    ).eq("id", 1).execute()
    clear_caches()


def replace_player_rows(sb: Backend, table: str, player_name: str, rows: list[dict[str, Any]]) -> None:
    sb.table(table).delete().eq("player_name", player_name).execute()
    for row in rows:
        sb.table(table).insert(row).execute()
    clear_caches()


def count_players(rows: list[dict[str, Any]]) -> int:
    names = {str(r.get("player_name") or "").strip() for r in rows if str(r.get("player_name") or "").strip()}
    return len(names)


def cloud_words(rows: list[dict[str, Any]]) -> list[str]:
    return [str(r.get("word") or "").strip() for r in rows if str(r.get("word") or "").strip()]


def one_word(text: str) -> str | None:
    token = (text or "").strip()
    if not token or any(ch.isspace() for ch in token):
        return None
    return token


def cloud_tokens(text: str) -> list[str] | None:
    """Up to three single words, separated by commas. Hyphens are allowed."""
    parts = [part.strip() for part in (text or "").split(",")]
    parts = [part for part in parts if part]
    if not parts or len(parts) > 3:
        return None
    words: list[str] = []
    seen: set[str] = set()
    for part in parts:
        token = one_word(part)
        if token is None:
            return None
        key = token.lower()
        if key in seen:
            continue
        seen.add(key)
        words.append(token)
    return words or None


def word_theme_color(word: str) -> str:
    token = word.lower().strip()
    parts = [part for part in token.replace("_", "-").split("-") if part]
    folded = token.replace("-", "").replace("_", "")
    for _name, color, keywords in THEME_COLORS:
        for keyword in keywords:
            if (
                token == keyword
                or folded == keyword
                or keyword in parts
                or folded.startswith(keyword)
                or any(part.startswith(keyword) for part in parts)
            ):
                return color
    return OTHER_THEME_COLOR


# ---------------------------------------------------------------------------
# QR / URLs
# ---------------------------------------------------------------------------

def make_qr_image(url: str):
    qr = qrcode.QRCode(version=1, box_size=10, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    return qr.make_image(fill_color="#1A1A1A", back_color="white")


def _is_loopback(url: str) -> bool:
    host = urllib.parse.urlparse(url).hostname or ""
    return host in {"localhost", "127.0.0.1", "::1"}


def detect_lan_base_url(port: int = 8501) -> str:
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


def request_public_base_url() -> str | None:
    try:
        headers = st.context.headers
    except Exception:
        return None
    host = (headers.get("Host") or headers.get("host") or "").split(",")[0].strip()
    if not host:
        return None
    hostname = host.split(":")[0]
    if "streamlit.app" in hostname:
        return f"https://{hostname}"
    proto = headers.get("X-Forwarded-Proto") or headers.get("x-forwarded-proto")
    if proto and hostname not in {"localhost", "127.0.0.1"}:
        return f"{proto}://{host}"
    return None


def player_join_url() -> str:
    secret_base = _secret_or_env("APP_BASE_URL")
    if secret_base and "YOUR_" in secret_base:
        secret_base = None

    live = request_public_base_url()
    if live and "streamlit.app" in live:
        base = live
    else:
        base = secret_base or live or detect_lan_base_url()
    if _is_loopback(base):
        lan = detect_lan_base_url()
        if not _is_loopback(lan):
            base = lan
    return f"{base.rstrip('/')}/?role=player"


def _read_short_cache(long_url: str) -> str | None:
    try:
        payload = json.loads(SHORT_URL_CACHE.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if payload.get("long") == long_url and str(payload.get("short") or "").startswith("http"):
        return str(payload["short"])
    return None


def _write_short_cache(long_url: str, short_url: str) -> None:
    SHORT_URL_CACHE.parent.mkdir(parents=True, exist_ok=True)
    SHORT_URL_CACHE.write_text(json.dumps({"long": long_url, "short": short_url}))


def cached_short_url(long_url: str) -> str:
    pinned = _secret_or_env("APP_SHORT_URL")
    if pinned and "YOUR_" not in pinned:
        return pinned
    if SESSION_SHORT_URL:
        return SESSION_SHORT_URL
    cached = _read_short_cache(long_url)
    if cached:
        return cached
    if _is_loopback(long_url) or st.session_state.get("short_url_miss_v2") == long_url:
        return long_url
    try:
        endpoint = "https://tinyurl.com/api-create.php?url=" + urllib.parse.quote(long_url, safe="")
        request = urllib.request.Request(endpoint, headers={"User-Agent": "WUI-Interactive"})
        try:
            import certifi

            context = ssl.create_default_context(cafile=certifi.where())
        except Exception:
            context = ssl._create_unverified_context()
        with urllib.request.urlopen(request, timeout=6, context=context) as response:
            short_url = response.read().decode().strip()
        if short_url.startswith("http") and "error" not in short_url.lower():
            _write_short_cache(long_url, short_url)
            return short_url
    except Exception:
        pass
    st.session_state["short_url_miss_v2"] = long_url
    return long_url


def render_hero_image() -> None:
    if not HERO_IMAGE.exists():
        return
    encoded = base64.b64encode(HERO_IMAGE.read_bytes()).decode("ascii")
    st.markdown(
        f'<img class="wui-hero" alt="Smoke over Southern California" src="data:image/png;base64,{encoded}">'
        '<p class="wui-hero-cap">NASA Earth Observatory, 9 January 2025.</p>',
        unsafe_allow_html=True,
    )


def render_qr(url: str, width: int) -> None:
    img = make_qr_image(url)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    st.image(buf.getvalue(), width=width)


def render_join(width: int) -> None:
    long_url = player_join_url()
    short_url = cached_short_url(long_url)
    render_qr(short_url, width)
    st.markdown(f'<div class="wui-join-url">{short_url}</div>', unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------

PLOTLY_LAYOUT = dict(
    paper_bgcolor="#FFFFFF",
    plot_bgcolor="#FFFFFF",
    font=dict(family="Arial, system-ui, sans-serif", color="#1A1A1A", size=18),
    legend=dict(
        orientation="h",
        yanchor="bottom",
        y=1.02,
        x=0,
        font=dict(size=16),
        bgcolor="#FFFFFF",
    ),
    margin=dict(l=160, r=24, t=48, b=40),
)


def _compact_layout(compact: bool, left: int) -> tuple[dict[str, Any], int]:
    layout = dict(PLOTLY_LAYOUT)
    tick = 13 if compact else 16
    if compact:
        layout["font"] = dict(family="Arial, system-ui, sans-serif", color="#1A1A1A", size=12)
        layout["legend"] = dict(
            orientation="h", yanchor="bottom", y=1.02, x=0, font=dict(size=12), bgcolor="#FFFFFF"
        )
        layout["margin"] = dict(l=left, r=8, t=36, b=24)
    return layout, tick


def interest_chart(rows: list[dict[str, Any]], *, compact: bool = False) -> go.Figure:
    counts = {label: {level: 0 for level in INTEREST_LEVELS} for label in INTEREST_LABELS}
    for row in rows:
        label = row.get("topic")
        level = row.get("interest")
        if label in counts and level in counts[label]:
            counts[label][level] += 1

    colors = {"Low": "#BDBDBD", "Medium": "#FFB300", "High": "#E64A19"}
    fig = go.Figure(
        data=[
            go.Bar(
                name=level,
                y=INTEREST_LABELS,
                x=[counts[label][level] for label in INTEREST_LABELS],
                orientation="h",
                marker_color=colors[level],
            )
            for level in INTEREST_LEVELS
        ]
    )
    layout, tick = _compact_layout(compact, 110)
    fig.update_layout(
        **layout,
        barmode="stack",
        height=340 if compact else 560,
        xaxis_title="" if compact else "Votes",
        xaxis=dict(dtick=1, gridcolor="#EEEEEE", tickfont=dict(size=tick)),
        showlegend=True,
    )
    fig.update_yaxes(
        autorange="reversed",
        automargin=True,
        tickfont=dict(size=tick, color="#1A1A1A"),
        categoryorder="array",
        categoryarray=INTEREST_LABELS,
    )
    return fig


def mapper_chart(rows: list[dict[str, Any]], *, compact: bool = False) -> go.Figure:
    counts = {c: {phase: 0 for phase in MAPPER_PHASES} for c in CONCEPTS}
    for row in rows:
        concept = row.get("concept")
        phase = row.get("assigned_phase")
        if concept in counts and phase in counts[concept]:
            counts[concept][phase] += 1

    colors = {"Before": "#2E7D32", "During": "#FF5722", "After": "#9E9E9E"}
    fig = go.Figure(
        data=[
            go.Bar(
                name=phase,
                y=CONCEPTS,
                x=[counts[c][phase] for c in CONCEPTS],
                orientation="h",
                marker_color=colors[phase],
            )
            for phase in MAPPER_PHASES
        ]
    )
    layout, tick = _compact_layout(compact, 120)
    fig.update_layout(
        **layout,
        barmode="stack",
        height=340 if compact else 560,
        xaxis_title="" if compact else "Votes",
        xaxis=dict(dtick=1, gridcolor="#EEEEEE", tickfont=dict(size=tick)),
        showlegend=True,
    )
    fig.update_yaxes(
        autorange="reversed",
        automargin=True,
        tickfont=dict(size=tick, color="#1A1A1A"),
        categoryorder="array",
        categoryarray=CONCEPTS,
    )
    return fig


def trivia_chart(all_rows: list[dict[str, Any]], *, compact: bool = False) -> go.Figure:
    labels: list[str] = []
    fact_vals: list[float] = []
    fiction_vals: list[float] = []
    hovers: list[str] = []
    for qnum, text in GAME3_QUESTIONS.items():
        subset = [r for r in all_rows if int(r.get("question_id") or 0) == qnum]
        fact = sum(1 for r in subset if str(r.get("vote")) == "Fact")
        fiction = sum(1 for r in subset if str(r.get("vote")) == "Fiction")
        total = fact + fiction
        fact_pct = (100.0 * fact / total) if total else 0.0
        fiction_pct = (100.0 * fiction / total) if total else 0.0
        labels.append(f"Q{qnum}" if compact else f"{qnum}. {text}")
        fact_vals.append(fact_pct)
        fiction_vals.append(fiction_pct)
        hovers.append(f"{text}<br>n={total}")

    fig = go.Figure(
        data=[
            go.Bar(
                name="Fact",
                y=labels,
                x=fact_vals,
                orientation="h",
                marker_color="#FF5722",
                customdata=hovers,
                hovertemplate="%{customdata}<br>Fact %{x:.0f}%<extra></extra>",
            ),
            go.Bar(
                name="Fiction",
                y=labels,
                x=fiction_vals,
                orientation="h",
                marker_color="#9E9E9E",
                customdata=hovers,
                hovertemplate="%{customdata}<br>Fiction %{x:.0f}%<extra></extra>",
            ),
        ]
    )
    layout, tick = _compact_layout(compact, 48 if compact else 280)
    fig.update_layout(
        **layout,
        barmode="stack",
        height=360 if compact else 640,
        xaxis=dict(range=[0, 100], ticksuffix="%", dtick=25, tickfont=dict(size=tick)),
        showlegend=True,
    )
    fig.update_yaxes(
        autorange="reversed",
        automargin=True,
        tickfont=dict(size=tick, color="#1A1A1A"),
    )
    return fig


@st.cache_data(ttl=7)
def cloud_png(words: tuple[str, ...], compact: bool = False) -> bytes | None:
    text = " ".join(w for w in words if w)
    if not text:
        return None
    wc = WordCloud(
        width=900 if compact else 1400,
        height=520 if compact else 700,
        background_color="#FFFFFF",
        color_func=lambda word, font_size, position, orientation, random_state=None, **kwargs: word_theme_color(
            word
        ),
        max_words=80,
        collocations=False,
        prefer_horizontal=0.9,
        min_font_size=14 if compact else 18,
        max_font_size=72 if compact else 120,
        relative_scaling=0.45,
        margin=8,
        scale=2,
        random_state=42,
    ).generate(text)
    fig, ax = plt.subplots(
        figsize=(6.2, 3.6) if compact else (12, 6),
        facecolor="#FFFFFF",
        dpi=120,
    )
    ax.imshow(wc, interpolation="bilinear")
    ax.axis("off")
    fig.tight_layout(pad=0.15)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", facecolor="#FFFFFF")
    plt.close(fig)
    return buf.getvalue()


def render_theme_legend() -> None:
    bits = [
        f'<span><span class="wui-swatch" style="background:{color};"></span>{name}</span>'
        for name, color, _keywords in THEME_COLORS
    ]
    bits.append(
        f'<span><span class="wui-swatch" style="background:{OTHER_THEME_COLOR};"></span>Other</span>'
    )
    st.markdown(
        f'<p class="wui-legend">{" &nbsp;&nbsp; ".join(bits)}</p>',
        unsafe_allow_html=True,
    )


def show_cloud(words: list[str], *, compact: bool = False) -> None:
    png = cloud_png(tuple(words), compact)
    if png is None:
        st.info("No words yet.")
        return
    st.image(png, width="stretch")
    render_theme_legend()


def render_rules(phase: str = "lobby") -> None:
    if phase == "lobby":
        st.markdown(GAMES_AND_RULES)
        return
    st.markdown(GAME_RULES.get(phase, ""))


# ---------------------------------------------------------------------------
# Moderator
# ---------------------------------------------------------------------------

def render_login_count(n: int) -> None:
    label = "person logged in" if n == 1 else "people logged in"
    st.markdown(f'<p class="wui-login">{n} {label}</p>', unsafe_allow_html=True)


def render_moderator_header(phase: str, logged_in: int) -> None:
    st.markdown(f'<div class="wui-kicker">{APP_NAME}</div>', unsafe_allow_html=True)
    st.markdown(f'<p class="wui-title">{SESSION_TITLE}</p>', unsafe_allow_html=True)
    st.markdown(
        f'<p class="wui-subtitle">{PHASE_TITLES.get(phase, phase)}</p>',
        unsafe_allow_html=True,
    )
    render_login_count(logged_in)


def render_phase_bar(sb: Backend, phase: str) -> None:
    columns = st.columns([1, 1, 1, 1, 1, 1, 1.15])
    for column, (key, label) in zip(columns, PHASE_BUTTONS):
        with column:
            if st.button(label, width="stretch", disabled=phase == key, key=f"phase_{key}"):
                st.session_state["confirm_reset"] = False
                set_phase(sb, key)
                st.rerun()
    with columns[-1]:
        if st.session_state.get("confirm_reset"):
            if st.button("Confirm reset", width="stretch", type="primary", key="confirm_reset_btn"):
                st.session_state["confirm_reset"] = False
                reset_session(sb)
                st.rerun()
        elif st.button("Reset", width="stretch", key="ask_reset"):
            st.session_state["confirm_reset"] = True
            st.rerun()


def render_timer(seconds: int) -> None:
    minutes, secs = divmod(max(seconds, 0), 60)
    tone = "wui-timer low" if seconds <= 20 else "wui-timer"
    st.markdown(f'<p class="{tone}">{minutes}:{secs:02d}</p>', unsafe_allow_html=True)


def render_collecting(sb: Backend, phase: str, n: int, unit: str) -> bool:
    """Return True when results should be drawn."""
    state = fetch_app_state(sb)
    if state["results_open"] or phase in state["revealed_phases"]:
        return True
    remaining = seconds_remaining(state["phase_timers"], phase)
    render_timer(remaining)
    render_rules(phase)
    label = unit if n == 1 else f"{unit}s"
    if phase in state["phase_timers"] and remaining <= 0:
        st.info(
            f"Time is up. Still collecting responses until you reveal results. **{n}** {label} so far."
        )
    else:
        st.info(f"Collecting responses. **{n}** {label} so far.")
    if st.button("Reveal results", width="stretch", type="primary", key=f"reveal_{phase}"):
        mark_game_revealed(sb, phase)
        st.rerun()
    return False


def render_moderator(sb: Backend) -> None:
    if st_autorefresh is not None:
        st_autorefresh(interval=1000, key="wui_moderator_refresh")

    clear_caches()
    state = fetch_app_state(sb)
    phase = state["current_phase"]
    logged_in = fetch_logged_in(sb)
    render_moderator_header(phase, logged_in)
    render_phase_bar(sb, phase)

    if phase == "lobby":
        left, right = st.columns([1, 1], gap="large")
        with left:
            photo, code = st.columns([1, 1], gap="small")
            with photo:
                render_hero_image()
            with code:
                render_join(width=210)
        with right:
            st.markdown('<div class="wui-rules-mark"></div>', unsafe_allow_html=True)
            render_login_count(logged_in)
            render_rules("lobby")
        return

    main, side = st.columns([5.2, 1.15], gap="small")
    with side:
        render_join(width=225)
    with main:
        if phase == "game1":
            rows = fetch_mapper(sb)
            if render_collecting(sb, phase, count_players(rows), "player"):
                st.plotly_chart(mapper_chart(rows), width="stretch", theme=None)
                st.write(f"**{count_players(rows)}** players submitted.")
        elif phase == "game2":
            rows = fetch_trivia(sb)
            if render_collecting(sb, phase, count_players(rows), "player"):
                st.plotly_chart(trivia_chart(rows), width="stretch", theme=None)
                st.write(f"**{count_players(rows)}** players submitted.")
        elif phase == "game3":
            rows = fetch_cloud(sb)
            words = cloud_words(rows)
            if render_collecting(sb, phase, count_players(rows), "player"):
                show_cloud(words)
                st.caption("1 word" if len(words) == 1 else f"{len(words)} words")
        elif phase == "game4":
            rows = fetch_interest(sb)
            if render_collecting(sb, phase, count_players(rows), "player"):
                st.plotly_chart(interest_chart(rows), width="stretch", theme=None)
                st.write(f"**{count_players(rows)}** players submitted.")
        else:
            render_full_summary(sb)


def render_full_summary(sb: Backend) -> None:
    top_left, top_right = st.columns(2, gap="small")
    bottom_left, bottom_right = st.columns(2, gap="small")
    with top_left:
        st.markdown("**Game 1 — Before / During / After**")
        st.plotly_chart(
            mapper_chart(fetch_mapper(sb), compact=True),
            width="stretch",
            theme=None,
            config={"displayModeBar": False},
        )
    with top_right:
        st.markdown("**Game 2 — Fact or Fiction**")
        st.plotly_chart(
            trivia_chart(fetch_trivia(sb), compact=True),
            width="stretch",
            theme=None,
            config={"displayModeBar": False},
        )
    with bottom_left:
        st.markdown("**Game 3 — Bottlenecks**")
        show_cloud(cloud_words(fetch_cloud(sb)), compact=True)
    with bottom_right:
        st.markdown("**Game 4 — Panel topics**")
        st.plotly_chart(
            interest_chart(fetch_interest(sb), compact=True),
            width="stretch",
            theme=None,
            config={"displayModeBar": False},
        )


# ---------------------------------------------------------------------------
# Player
# ---------------------------------------------------------------------------

def show_pending_warning() -> None:
    warning = st.session_state.pop("wui_warn", None)
    if warning:
        st.warning(warning)


def ensure_player_name() -> str | None:
    existing = str(st.session_state.get("player_name") or "").strip()
    if existing:
        return existing

    with st.form("player_name_form"):
        name = st.text_input("Name", max_chars=40, label_visibility="collapsed", placeholder="Name")
        posted = st.form_submit_button("Continue", width="stretch")
    if posted:
        cleaned = (name or "").strip()
        if not cleaned:
            st.session_state["wui_warn"] = "Please enter your name."
            st.rerun()
        st.session_state["player_name"] = cleaned
        st.rerun()
    return None


def render_choice_cards(
    items: list[tuple[str, str, str]],
    key_prefix: str,
    options: tuple[str, ...],
    *,
    multi: bool = False,
) -> dict[str, Any]:
    answers: dict[str, Any] = {}
    for item_id, title, body in items:
        key = f"{key_prefix}_{item_id}"
        current = st.session_state.get(key)
        selected = [option for option in options if isinstance(current, list) and option in current]
        with st.container(border=True):
            st.markdown(f'<p class="wui-card-title">{title}</p>', unsafe_allow_html=True)
            if body:
                st.markdown(body)
            columns = st.columns(len(options))
            for column, option in zip(columns, options):
                with column:
                    active = option in selected if multi else current == option
                    if st.button(
                        option,
                        key=f"{key}_btn_{option}",
                        type="primary" if active else "secondary",
                        width="stretch",
                    ):
                        if multi:
                            if option in selected:
                                st.session_state[key] = [item for item in selected if item != option]
                            else:
                                st.session_state[key] = [
                                    item for item in options if item in selected or item == option
                                ]
                        else:
                            st.session_state[key] = option
                        st.rerun()
        if multi:
            answers[item_id] = selected
        else:
            answers[item_id] = current if current in options else None
    return answers


def submit_page(
    answers: dict[str, str | None],
    warning: str,
    save,
) -> None:
    answered = sum(1 for value in answers.values() if value)
    st.write(f"**{answered} of {len(answers)}** answered")
    if st.button("Submit", width="stretch", type="primary", key="submit_page"):
        if answered < len(answers):
            st.session_state["wui_warn"] = warning
            st.rerun()
        save()
        st.session_state["wui_submitted_lock"] = st.session_state.get("_answer_phase")
        clear_caches()
        st.rerun()


def render_player(sb: Backend) -> None:
    st.markdown(f'<div class="wui-kicker">{APP_NAME}</div>', unsafe_allow_html=True)
    st.markdown(f"### {SESSION_TITLE}")
    show_pending_warning()

    clear_caches()
    state = fetch_app_state(sb)
    phase = state["current_phase"]
    st.session_state["_answer_phase"] = phase
    submitted = st.session_state.get("wui_submitted_lock") == phase
    game_closed = phase in GAME_PHASES and (
        state["results_open"] or phase in state["revealed_phases"]
    )

    if st_autorefresh is not None:
        st_autorefresh(interval=5000, key="wui_player_refresh")

    if phase == "lobby":
        name = ensure_player_name()
        if name:
            register_player(sb, name)
            st.success(HANG_TIGHT)
        return

    if phase == "end" or game_closed or submitted:
        st.success("Look at the main screen.")
        return

    player_name = ensure_player_name()
    if not player_name:
        return
    register_player(sb, player_name)
    render_rules(phase)

    if phase == "game1":
        answers = render_choice_cards(
            [(concept, concept, "") for concept in CONCEPTS],
            "g1",
            MAPPER_PHASES,
            multi=True,
        )

        def save() -> None:
            replace_player_rows(
                sb,
                "game1_mapper",
                player_name,
                [
                    {
                        "concept": concept,
                        "assigned_phase": assigned,
                        "player_name": player_name,
                    }
                    for concept in CONCEPTS
                    for assigned in answers[concept]
                ],
            )

        submit_page(answers, "Choose at least one of Before, During, or After for every tool.", save)
        return

    if phase == "game2":
        answers = render_choice_cards(
            [(str(qnum), text, "") for qnum, text in GAME3_QUESTIONS.items()],
            "g2",
            ("Fact", "Fiction"),
        )

        def save() -> None:
            replace_player_rows(
                sb,
                "game2_trivia",
                player_name,
                [
                    {
                        "question_id": qnum,
                        "vote": answers[str(qnum)],
                        "player_name": player_name,
                    }
                    for qnum in GAME3_QUESTIONS
                ],
            )

        submit_page(answers, "Choose Fact or Fiction for every claim.", save)
        return

    if phase == "game3":
        st.markdown(f'<p class="wui-card-title">{GAME4_PROMPT}</p>', unsafe_allow_html=True)
        st.caption("Up to 3 words, separated by commas. A hyphenated word counts as one.")
        with st.form("game3_form"):
            raw = st.text_input(
                "Words",
                max_chars=80,
                label_visibility="collapsed",
                placeholder="word, word, word",
                key="g3_words",
            )
            posted = st.form_submit_button("Submit", width="stretch")
        if posted:
            tokens = cloud_tokens(raw)
            if tokens is None:
                st.session_state["wui_warn"] = (
                    "Enter up to 3 words, separated by commas. Each one must be a single word. "
                    "A hyphenated word is fine."
                )
                st.rerun()
            replace_player_rows(
                sb,
                "game3_cloud",
                player_name,
                [{"word": token, "player_name": player_name} for token in tokens],
            )
            st.session_state["wui_submitted_lock"] = phase
            clear_caches()
            st.rerun()
        return

    if phase == "game4":
        answers = render_choice_cards(
            [(topic_id, label, prompt) for topic_id, label, prompt in INTEREST_TOPICS],
            "g4",
            INTEREST_LEVELS,
        )

        def save() -> None:
            replace_player_rows(
                sb,
                "game1_interest",
                player_name,
                [
                    {
                        "topic": label,
                        "interest": answers[topic_id],
                        "player_name": player_name,
                    }
                    for topic_id, label, _prompt in INTEREST_TOPICS
                ],
            )

        submit_page(answers, "Choose low, medium, or high for every panel topic.", save)
        return


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------

def main() -> None:
    role = (st.query_params.get("role") or "").lower().strip()
    if role != "player":
        role = "moderator"
    inject_theme(role)
    sb = get_backend()

    if role == "player":
        render_player(sb)
    else:
        render_moderator(sb)


if __name__ == "__main__":
    main()
else:
    main()
