"""
WUI Interactive
Synchronous multiplayer icebreaker for AAAR 2026 (WUI smoke, impacts, communication).

Local test (default SQLite, no network):
  streamlit run app.py
  Moderator:  http://localhost:8501/?role=moderator
  Player:     http://localhost:8501/?role=player
"""

from __future__ import annotations

import io
import os
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
GAME3_PROMPT = "What is the single biggest bottleneck in managing WUI smoke impacts?"

PHASE_TITLES = {
    "lobby": "Lobby — Join the session",
    "game1": "Game 1 — Before, During, After",
    "game2": "Game 2 — Fact or Fiction",
    "game3": "Game 3 — Bottlenecks",
    "end": "Session close",
}

CONCEPTS = [
    "Sensor Networks",
    "Public Warnings",
    "Health Studies",
    "Prescribed Burns",
    "Indoor Filtration",
    "Defensible Space",
    "Risk Communication",
]

GAME2_QUESTIONS: dict[int, str] = {
    1: "Most homes lost in WUI fires ignite from wind-blown embers, not a wall of flame.",
    2: "If you can see wildfire smoke outdoors, indoor air is already protected.",
    3: "Prescribed fire always reduces smoke exposure for nearby communities.",
    4: "Air-quality alerts typically reach every household in a WUI zone within 10 minutes.",
    5: "Closing windows and running a portable HEPA filter can cut indoor smoke.",
}

ASSETS_DIR = Path(__file__).resolve().parent / "assets"
HERO_IMAGE = ASSETS_DIR / "la_wui_smoke_2025.jpg"
NASA_LOGO = ASSETS_DIR / "nasa_logo.png"

GAMES_AND_RULES = """
### Games and Rules

1. **Before / During / After** — Map **7** WUI tools to when they matter most. The room pattern can show whether people treat smoke as a **preparedness** problem, an **emergency-response** problem, or a **recovery and health** problem.

2. **Fact or Fiction** — Vote on claims about WUI fire and smoke. The split can reveal which ideas are **shared knowledge** in the room and which are still **contested or misunderstood**.

3. **Bottlenecks** — Up to **three** words or short phrases for the biggest bottleneck in managing WUI smoke impacts. The cloud can show whether the constraint is seen as **science**, **operations**, **communication**, or **policy**.
"""

GAME_RULES = {
    "game1": """
### Game 1 rules

Map **7** WUI tools to when they matter most: **Before**, **During**, or **After**. One choice per tool. The pattern can show whether the room treats smoke as preparedness, emergency response, or recovery and health.
""",
    "game2": """
### Game 2 rules

One claim at a time. Tap **Fact** or **Fiction** once. The room split can show which ideas are shared knowledge and which are still contested.
""",
    "game3": """
### Game 3 rules

Enter up to **three** words or short phrases for the biggest bottleneck in managing WUI smoke impacts. The cloud can show whether the constraint is seen as science, operations, communication, or policy.
""",
}

st.set_page_config(
    page_title=APP_NAME,
    page_icon="🔥",
    layout="wide",
    initial_sidebar_state="expanded",
)


def inject_theme(role: str) -> None:
    player_shell = ""
    if role == "player":
        player_shell = """
        [data-testid="stAppViewContainer"] .main .block-container {
            max-width: 480px;
            padding-top: 1rem;
            padding-bottom: 2.5rem;
            margin-left: auto;
            margin-right: auto;
        }
        """
    st.markdown(
        f"""
        <style>
        html, body, [data-testid="stAppViewContainer"],
        [data-testid="stAppViewContainer"] .main,
        [data-testid="stHeader"] {{
            background-color: #FFFFFF !important;
            color: #1A1A1A !important;
            font-family: Arial, system-ui, sans-serif !important;
            font-size: 22px !important;
        }}
        [data-testid="stSidebar"] {{
            background-color: #F5F5F5 !important;
        }}
        [data-testid="stSidebar"] * {{
            font-size: 20px !important;
            font-family: Arial, system-ui, sans-serif !important;
        }}
        p, li, label, span, .stMarkdown, .stCaption, .stAlert, div[data-testid="stText"] {{
            font-size: 22px !important;
            line-height: 1.35 !important;
            color: #1A1A1A !important;
            font-family: Arial, system-ui, sans-serif !important;
        }}
        h1, .wui-title {{
            font-family: Arial, system-ui, sans-serif !important;
            font-size: 3rem !important;
            font-weight: 800 !important;
            color: #1A1A1A !important;
            line-height: 1.15 !important;
        }}
        h2, h3 {{
            font-family: Arial, system-ui, sans-serif !important;
            font-size: 2.2rem !important;
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
            font-size: 2.2rem !important;
            font-weight: 700;
            margin-bottom: 1.2rem;
        }}
        .wui-question {{
            font-size: 2.4rem !important;
            font-weight: 800;
            line-height: 1.25;
            color: #1A1A1A;
            margin: 0 0 1rem 0;
        }}
        .wui-join-url {{
            font-size: 1.6rem !important;
            font-weight: 700;
            word-break: break-all;
            background: #F5F5F5;
            border: 2px solid #FF5722;
            padding: 0.8rem 1rem;
            border-radius: 10px;
        }}
        .stButton > button, .stFormSubmitButton > button {{
            font-weight: 800 !important;
            font-size: 1.35rem !important;
            min-height: 3.2rem;
            border-radius: 12px;
            font-family: Arial, system-ui, sans-serif !important;
        }}
        div[data-testid="stForm"] {{
            background: #FAFAFA;
            padding: 1rem 1.1rem 0.4rem 1.1rem;
            border-radius: 12px;
            border: 1px solid #E0E0E0;
        }}
        div[data-testid="stRadio"] label {{
            font-size: 22px !important;
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
        st.error(f"Database error while {label}: `{type(exc).__name__}`")
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
    }


@st.cache_data(ttl=1)
def fetch_game1(_sb: Backend) -> list[dict[str, Any]]:
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
def fetch_game2(_sb: Backend, question_id: int) -> list[dict[str, Any]]:
    def _run():
        return (
            _sb.table("game2_trivia")
            .select("question_id, vote, player_name")
            .eq("question_id", question_id)
            .execute()
            .data
            or []
        )

    return _db_call("loading Game 2 votes", _run)


@st.cache_data(ttl=1)
def fetch_game2_all(_sb: Backend) -> list[dict[str, Any]]:
    def _run():
        return (
            _sb.table("game2_trivia")
            .select("question_id, vote, player_name")
            .execute()
            .data
            or []
        )

    return _db_call("loading all Game 2 votes", _run)


@st.cache_data(ttl=1)
def fetch_game3(_sb: Backend) -> list[dict[str, Any]]:
    def _run():
        return _sb.table("game3_cloud").select("word, player_name").execute().data or []

    return _db_call("loading Game 3 words", _run)


def clear_caches() -> None:
    fetch_app_state.clear()
    fetch_game1.clear()
    fetch_game2.clear()
    fetch_game2_all.clear()
    fetch_game3.clear()


def set_phase(sb: Backend, phase: str, question: int | None = None) -> None:
    payload: dict[str, Any] = {"current_phase": phase, "results_open": False}
    if question is not None:
        payload["active_question"] = question
    elif phase == "game2":
        payload["active_question"] = 1
    sb.table("app_state").update(payload).eq("id", 1).execute()
    clear_caches()


def set_results_open(sb: Backend, open_: bool) -> None:
    sb.table("app_state").update({"results_open": open_}).eq("id", 1).execute()
    clear_caches()


def reset_session(sb: Backend) -> None:
    sb.table("game1_mapper").delete().neq("concept", "").execute()
    sb.table("game2_trivia").delete().gte("question_id", 0).execute()
    sb.table("game3_cloud").delete().neq("word", "").execute()
    sb.table("app_state").update(
        {"current_phase": "lobby", "active_question": 1, "results_open": False}
    ).eq("id", 1).execute()
    clear_caches()


# ---------------------------------------------------------------------------
# QR / URLs
# ---------------------------------------------------------------------------

def make_qr_image(url: str):
    qr = qrcode.QRCode(version=1, box_size=10, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    return qr.make_image(fill_color="#1A1A1A", back_color="white")


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
    secret_base = None
    try:
        secret_base = st.secrets.get("APP_BASE_URL") or (st.secrets.get("app", {}) or {}).get("base_url")
        secret_base = str(secret_base).strip() if secret_base else None
        if secret_base and ("YOUR_" in secret_base):
            secret_base = None
    except Exception:
        secret_base = None

    live = request_public_base_url()
    env_base = os.environ.get("APP_BASE_URL")
    if live and "streamlit.app" in live:
        base = live
    else:
        base = secret_base or env_base or live or detect_lan_base_url()
    return f"{base.rstrip('/')}/?role=player"


def render_hero_image() -> None:
    if HERO_IMAGE.exists():
        st.image(str(HERO_IMAGE), width="stretch")
        cap_l, cap_r = st.columns([6, 1])
        with cap_l:
            st.caption(
                "NASA Earth Observatory — smoke drifting off the Southern California coast, 9 January 2025."
            )
        with cap_r:
            if NASA_LOGO.exists():
                st.image(str(NASA_LOGO), width=72)


def render_qr(url: str, width: int) -> None:
    img = make_qr_image(url)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    st.image(buf.getvalue(), width=width)


def game1_player_count(rows: list[dict[str, Any]]) -> int:
    names = {str(r.get("player_name") or "").strip() for r in rows if r.get("player_name")}
    if names:
        return len(names)
    return len(rows) // max(len(CONCEPTS), 1)


def game3_words(rows: list[dict[str, Any]]) -> list[str]:
    return [str(r.get("word") or "").strip() for r in rows if str(r.get("word") or "").strip()]


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------

PLOTLY_LAYOUT = dict(
    paper_bgcolor="#FFFFFF",
    plot_bgcolor="#FFFFFF",
    font=dict(family="Arial, system-ui, sans-serif", color="#1A1A1A", size=22),
    legend=dict(
        orientation="h",
        yanchor="bottom",
        y=1.04,
        x=0,
        font=dict(size=22),
        bgcolor="#FFFFFF",
        itemwidth=80,
    ),
    margin=dict(l=220, r=40, t=72, b=56),
)


def game1_chart(rows: list[dict[str, Any]], *, compact: bool = False) -> go.Figure:
    counts = {c: {"Before": 0, "During": 0, "After": 0} for c in CONCEPTS}
    for row in rows:
        concept = row.get("concept")
        phase = row.get("assigned_phase")
        if concept in counts and phase in counts[concept]:
            counts[concept][phase] += 1

    fig = go.Figure(
        data=[
            go.Bar(
                name="Before",
                y=CONCEPTS,
                x=[counts[c]["Before"] for c in CONCEPTS],
                orientation="h",
                marker_color="#2E7D32",
                width=0.42,
            ),
            go.Bar(
                name="During",
                y=CONCEPTS,
                x=[counts[c]["During"] for c in CONCEPTS],
                orientation="h",
                marker_color="#FF5722",
                width=0.42,
            ),
            go.Bar(
                name="After",
                y=CONCEPTS,
                x=[counts[c]["After"] for c in CONCEPTS],
                orientation="h",
                marker_color="#9E9E9E",
                width=0.42,
            ),
        ]
    )
    max_x = 0
    for c in CONCEPTS:
        max_x = max(max_x, counts[c]["Before"], counts[c]["During"], counts[c]["After"])
    layout = dict(PLOTLY_LAYOUT)
    tick = 14 if compact else 20
    if compact:
        layout["font"] = dict(family="Arial, system-ui, sans-serif", color="#1A1A1A", size=14)
        layout["legend"] = dict(
            orientation="h", yanchor="bottom", y=1.02, x=0, font=dict(size=14), bgcolor="#FFFFFF"
        )
        layout["margin"] = dict(l=150, r=16, t=40, b=32)
    fig.update_layout(
        **layout,
        barmode="group",
        bargap=0.18,
        bargroupgap=0.08,
        xaxis_title="Votes",
        yaxis_title="",
        height=420 if compact else 880,
        xaxis=dict(
            dtick=1,
            range=[0, max(max_x, 1) + 0.75],
            automargin=True,
            tickfont=dict(size=tick),
            gridcolor="#EEEEEE",
            zeroline=True,
            zerolinecolor="#BDBDBD",
        ),
        showlegend=True,
    )
    fig.update_yaxes(
        autorange="reversed",
        automargin=True,
        tickfont=dict(size=tick),
        ticksuffix="  ",
        categoryorder="array",
        categoryarray=CONCEPTS,
    )
    return fig


def game2_chart(rows: list[dict[str, Any]], title: str | None = None) -> go.Figure:
    fact = sum(1 for r in rows if str(r.get("vote")) == "Fact")
    fiction = sum(1 for r in rows if str(r.get("vote")) == "Fiction")
    total = fact + fiction
    fact_pct = (100.0 * fact / total) if total else 0.0
    fiction_pct = (100.0 * fiction / total) if total else 0.0

    fig = go.Figure(
        data=[
            go.Bar(
                name=f"Fact ({fact_pct:.0f}%)",
                y=["Room"],
                x=[fact_pct],
                orientation="h",
                marker_color="#FF5722",
                text=[f"Fact {fact_pct:.0f}%" if fact_pct >= 18 else ""],
                textposition="inside",
                insidetextanchor="middle",
                insidetextfont=dict(size=22, color="#FFFFFF"),
                cliponaxis=False,
            ),
            go.Bar(
                name=f"Fiction ({fiction_pct:.0f}%)",
                y=["Room"],
                x=[fiction_pct],
                orientation="h",
                marker_color="#9E9E9E",
                text=[f"Fiction {fiction_pct:.0f}%" if fiction_pct >= 18 else ""],
                textposition="inside",
                insidetextanchor="middle",
                insidetextfont=dict(size=22, color="#1A1A1A"),
                cliponaxis=False,
            ),
        ]
    )
    layout = {k: v for k, v in PLOTLY_LAYOUT.items() if k != "margin"}
    fig.update_layout(
        **layout,
        barmode="stack",
        xaxis=dict(range=[0, 100], ticksuffix="%", dtick=25),
        yaxis=dict(showticklabels=False),
        height=280,
        title=dict(text=title or f"n = {total} votes", font=dict(size=24), y=0.95),
        margin=dict(l=24, r=24, t=80, b=48),
        legend=dict(orientation="h", yanchor="bottom", y=1.12, x=0, font=dict(size=22)),
    )
    return fig


def game2_summary_chart(all_rows: list[dict[str, Any]]) -> go.Figure:
    labels: list[str] = []
    fact_vals: list[float] = []
    fiction_vals: list[float] = []
    hovers: list[str] = []
    for qnum, text in GAME2_QUESTIONS.items():
        subset = [r for r in all_rows if int(r.get("question_id") or 0) == qnum]
        fact = sum(1 for r in subset if str(r.get("vote")) == "Fact")
        fiction = sum(1 for r in subset if str(r.get("vote")) == "Fiction")
        total = fact + fiction
        fact_pct = (100.0 * fact / total) if total else 0.0
        fiction_pct = (100.0 * fiction / total) if total else 0.0
        labels.append(f"Q{qnum}")
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
    fig.update_layout(
        paper_bgcolor="#FFFFFF",
        plot_bgcolor="#FFFFFF",
        font=dict(family="Arial, system-ui, sans-serif", color="#1A1A1A", size=14),
        barmode="stack",
        height=420,
        margin=dict(l=48, r=16, t=40, b=32),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, font=dict(size=14)),
        xaxis=dict(range=[0, 100], ticksuffix="%", dtick=25),
        yaxis=dict(autorange="reversed"),
    )
    return fig


def game3_image(words: list[str], *, compact: bool = False) -> Any:
    text = " ".join(w for w in words if w)
    if not text:
        return None
    wc = WordCloud(
        width=900 if compact else 1600,
        height=700 if compact else 800,
        background_color="#FFFFFF",
        colormap="YlOrRd",
        max_words=80,
        collocations=False,
        prefer_horizontal=0.85,
        min_font_size=16 if compact else 22,
        max_font_size=90 if compact else 140,
        relative_scaling=0.45,
        margin=10,
        scale=2,
    ).generate(text)
    fig, ax = plt.subplots(
        figsize=(7, 5.5) if compact else (16, 8),
        facecolor="#FFFFFF",
        dpi=120 if compact else 140,
    )
    ax.imshow(wc, interpolation="bilinear")
    ax.axis("off")
    fig.tight_layout(pad=0.2)
    return fig


def render_rules(phase: str = "lobby") -> None:
    if phase == "lobby":
        st.markdown(GAMES_AND_RULES)
        return
    st.markdown(GAME_RULES.get(phase, ""))


# ---------------------------------------------------------------------------
# Moderator
# ---------------------------------------------------------------------------

def render_moderator_header(phase: str) -> None:
    st.markdown(f'<div class="wui-kicker">{APP_NAME}</div>', unsafe_allow_html=True)
    st.markdown(f'<p class="wui-title">{SESSION_TITLE}</p>', unsafe_allow_html=True)
    st.markdown(
        f'<p class="wui-subtitle">{PHASE_TITLES.get(phase, phase)}</p>',
        unsafe_allow_html=True,
    )


def render_moderator(sb: Backend) -> None:
    if st_autorefresh is not None:
        st_autorefresh(interval=2000, key="wui_moderator_refresh")

    with st.sidebar:
        st.header("Admin controls")
        clear_caches()
        state = fetch_app_state(sb)
        phase = state["current_phase"]
        qid = int(state["active_question"])

        st.subheader("Session")
        st.write(PHASE_TITLES.get(phase, phase))

        st.subheader("Games")
        for key, label in (
            ("lobby", "Lobby"),
            ("game1", "Game 1"),
            ("game2", "Game 2"),
            ("game3", "Game 3"),
            ("end", "End"),
        ):
            if st.button(label, width="stretch", disabled=phase == key, key=f"phase_{key}"):
                if key == "game2":
                    set_phase(sb, key, 1)
                else:
                    set_phase(sb, key)
                st.rerun()

        st.divider()
        if st.button("Reset all responses", width="stretch"):
            reset_session(sb)
            st.rerun()

    clear_caches()
    state = fetch_app_state(sb)
    phase = state["current_phase"]
    qid = int(state["active_question"])
    revealed = bool(state["results_open"])
    render_moderator_header(phase)

    if phase == "lobby":
        left, right = st.columns([1.05, 1.2])
        with left:
            render_hero_image()
            st.subheader("Join on your phone")
            join_url = player_join_url()
            st.markdown(f'<div class="wui-join-url">{join_url}</div>', unsafe_allow_html=True)
            st.write(
                "Open this URL on your phone. On Streamlit Cloud the QR code should work "
                "on any network. A laptop-only address often fails on conference Wi‑Fi."
            )
            with st.expander("QR code"):
                render_qr(join_url, width=240)
        with right:
            render_rules("lobby")
        return

    if phase == "game1":
        g1 = fetch_game1(sb)
        n = game1_player_count(g1)
        if not revealed:
            render_rules("game1")
            st.info(f"Collecting allocations. **{n}** player submissions so far.")
            if st.button("Reveal results", width="stretch", type="primary"):
                set_results_open(sb, True)
                st.rerun()
            return
        st.plotly_chart(game1_chart(g1), width="stretch")
        st.write(f"**{n}** players submitted.")
        return

    if phase == "game2":
        left, right = st.columns([1, 2.4])
        rows = fetch_game2(sb, qid)
        n = len(rows)
        last_q = qid >= max(GAME2_QUESTIONS)
        with left:
            if not revealed:
                if st.button("Reveal this question", width="stretch", type="primary"):
                    set_results_open(sb, True)
                    st.rerun()
            elif not last_q:
                if st.button("Next question", width="stretch", type="primary"):
                    set_phase(sb, "game2", qid + 1)
                    st.rerun()
            else:
                st.write("Last question. Use **Game 3** in Admin controls when you are ready.")
        with right:
            prompt = GAME2_QUESTIONS.get(qid, "Question unavailable.")
            st.markdown(f'<p class="wui-question">Q{qid}. {prompt}</p>', unsafe_allow_html=True)
            if not revealed:
                st.info(f"Collecting votes. **{n}** responses so far. Results hidden.")
            else:
                st.plotly_chart(game2_chart(rows), width="stretch")
        if not revealed:
            st.markdown("---")
            render_rules("game2")
        return

    if phase == "game3":
        rows = fetch_game3(sb)
        words = game3_words(rows)
        st.markdown(f'<p class="wui-question">{GAME3_PROMPT}</p>', unsafe_allow_html=True)
        if not revealed:
            render_rules("game3")
            st.info(f"Collecting phrases. **{len(words)}** entries so far.")
            if st.button("Reveal results", width="stretch", type="primary"):
                set_results_open(sb, True)
                st.rerun()
            return
        fig = game3_image(words)
        if fig is None:
            st.info("No phrases were submitted.")
        else:
            st.pyplot(fig, width="stretch")
            plt.close(fig)
        st.write(f"**{len(words)}** phrases submitted.")
        return

    st.markdown(
        """
        <style>
        [data-testid="stAppViewContainer"] .main .block-container {
            padding-top: 0.35rem;
            padding-bottom: 0.35rem;
            max-width: 100%;
        }
        .wui-title { font-size: 1.7rem !important; }
        .wui-subtitle { font-size: 1.25rem !important; margin-bottom: 0.4rem !important; }
        </style>
        """,
        unsafe_allow_html=True,
    )
    render_full_summary(sb)


def render_full_summary(sb: Backend) -> None:
    g1, g2, g3 = st.columns(3, gap="small")
    with g1:
        st.markdown("**Game 1 — Before / During / After**")
        st.plotly_chart(
            game1_chart(fetch_game1(sb), compact=True),
            width="stretch",
            config={"displayModeBar": False},
        )
    with g2:
        st.markdown("**Game 2 — Fact or Fiction**")
        st.plotly_chart(
            game2_summary_chart(fetch_game2_all(sb)),
            width="stretch",
            config={"displayModeBar": False},
        )
    with g3:
        st.markdown("**Game 3 — Bottlenecks**")
        words = game3_words(fetch_game3(sb))
        fig = game3_image(words, compact=True)
        if fig is None:
            st.info("No phrases yet.")
        else:
            st.pyplot(fig, width="stretch")
            plt.close(fig)


# ---------------------------------------------------------------------------
# Player
# ---------------------------------------------------------------------------

def player_lock_key(phase: str, qid: int) -> str:
    return f"{phase}:{qid}"


def ensure_player_name() -> str | None:
    existing = str(st.session_state.get("player_name") or "").strip()
    if existing:
        st.write(f"Playing as **{existing}**")
        return existing

    with st.form("player_name_form"):
        st.write("Enter your name before Game 1.")
        name = st.text_input("Your name", max_chars=40)
        posted = st.form_submit_button("Continue", width="stretch")
    if posted:
        cleaned = (name or "").strip()
        if not cleaned:
            st.warning("Please enter your name.")
            return None
        st.session_state["player_name"] = cleaned
        st.rerun()
    return None


def render_player(sb: Backend) -> None:
    st.markdown(f'<div class="wui-kicker">{APP_NAME}</div>', unsafe_allow_html=True)
    st.markdown(f"### {SESSION_TITLE}")

    clear_caches()
    state = fetch_app_state(sb)
    phase = state["current_phase"]
    qid = int(state["active_question"])
    lock = player_lock_key(phase, qid)
    submitted = st.session_state.get("wui_submitted_lock") == lock

    waiting = phase in ("lobby", "end") or submitted
    if waiting and st_autorefresh is not None:
        st_autorefresh(interval=5000, key="wui_player_refresh")

    if waiting:
        if st.button("Refresh to next game", width="stretch"):
            clear_caches()
            st.rerun()

    st.write(PHASE_TITLES.get(phase, phase))

    if phase == "lobby":
        name = ensure_player_name()
        if name:
            st.success("You're in. Watch the main screen — Game 1 will start here.")
        return

    if phase == "end":
        st.info("That's a wrap. Look at the main screen!")
        return

    player_name = ensure_player_name()
    if not player_name:
        return

    if submitted:
        st.success("Got it — look at the main screen!")
        st.write("Inputs unlock when the moderator changes the game or question.")
        return

    if phase == "game1":
        with st.form("game1_form"):
            st.write("When does each tool matter most?")
            answers: dict[str, Any] = {}
            for concept in CONCEPTS:
                answers[concept] = st.radio(
                    concept,
                    options=["Before", "During", "After"],
                    index=None,
                    horizontal=False,
                    key=f"g1_{concept}",
                )
            posted = st.form_submit_button("Submit allocations", width="stretch")

        if posted:
            missing = [c for c, v in answers.items() if not v]
            if missing:
                st.warning("Choose Before, During, or After for every tool.")
            else:
                for concept, assigned in answers.items():
                    sb.table("game1_mapper").insert(
                        {
                            "concept": concept,
                            "assigned_phase": assigned,
                            "player_name": player_name,
                        }
                    ).execute()
                st.session_state["wui_submitted_lock"] = lock
                clear_caches()
                st.rerun()
        return

    if phase == "game2":
        st.markdown(f"#### {GAME2_QUESTIONS.get(qid, '')}")
        fact = st.button("Fact", width="stretch", type="primary")
        fiction = st.button("Fiction", width="stretch")
        vote = "Fact" if fact else ("Fiction" if fiction else None)
        if vote:
            sb.table("game2_trivia").insert(
                {"question_id": qid, "vote": vote, "player_name": player_name}
            ).execute()
            st.session_state["wui_submitted_lock"] = lock
            clear_caches()
            st.rerun()
        return

    if phase == "game3":
        st.write(GAME3_PROMPT)
        with st.form("game3_form"):
            w1 = st.text_input("Phrase 1", max_chars=40)
            w2 = st.text_input("Phrase 2 (optional)", max_chars=40)
            w3 = st.text_input("Phrase 3 (optional)", max_chars=40)
            posted = st.form_submit_button("Submit", width="stretch")
        if posted:
            phrases = [p.strip() for p in (w1, w2, w3) if (p or "").strip()]
            if not phrases:
                st.warning("Enter at least one word or short phrase.")
            else:
                for phrase in phrases:
                    sb.table("game3_cloud").insert(
                        {"word": phrase, "player_name": player_name}
                    ).execute()
                st.session_state["wui_submitted_lock"] = lock
                clear_caches()
                st.rerun()


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------

def main() -> None:
    role = (st.query_params.get("role") or "").lower().strip()
    inject_theme(role)
    sb = get_backend()
    if role != "player" and st.session_state.get("local_mode"):
        st.info(
            st.session_state.get("local_mode_reason")
            or "Local SQLite play mode — no internet required."
        )

    if role == "moderator":
        render_moderator(sb)
    elif role == "player":
        render_player(sb)
    else:
        st.markdown(f'<div class="wui-kicker">{APP_NAME}</div>', unsafe_allow_html=True)
        st.markdown(f'<p class="wui-title">{SESSION_TITLE}</p>', unsafe_allow_html=True)
        render_hero_image()
        st.write("Choose a view:")
        col_a, col_b = st.columns(2)
        with col_a:
            st.link_button("Moderator (projector)", "?role=moderator", width="stretch")
        with col_b:
            st.link_button("Player (mobile)", "?role=player", width="stretch")


if __name__ == "__main__":
    main()
else:
    main()
