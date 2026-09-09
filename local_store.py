"""SQLite backend for the preserved Flashpoint prototype (flashpoint_app.py)."""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

COLS = ["A", "B", "C", "D", "E", "F", "G"]
DEFAULT_WEATHER = "Calm conditions. Awaiting ignition…"

_LOCK = threading.RLock()
_CLIENT: LocalClient | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def land_use_for(col_label: str, row_label: int) -> str:
    if col_label in ("F", "G") and row_label in (4, 5):
        return "Reservoir"
    if col_label in ("F", "G"):
        return "Urban Center"
    if col_label == "E" and 3 <= row_label <= 6:
        return "Urban Center"
    if col_label in ("D", "E"):
        return "Suburban WUI"
    if col_label == "C" and 2 <= row_label <= 7:
        return "Suburban WUI"
    if col_label in ("B", "C"):
        return "Shrubland"
    return "Dense Forest"


class LocalResult:
    def __init__(self, data: Any, count: int | None = None):
        self.data = data
        if count is not None:
            self.count = count
        elif isinstance(data, list):
            self.count = len(data)
        elif data is None:
            self.count = 0
        else:
            self.count = 1


class LocalQuery:
    def __init__(self, client: LocalClient, table: str):
        self.client = client
        self.table = table
        self._select = "*"
        self._count = False
        self._filters: list[tuple[str, str, Any]] = []
        self._orders: list[tuple[str, bool]] = []
        self._limit: int | None = None
        self._single = False
        self._update: dict[str, Any] | None = None
        self._delete = False
        self._embed_grid = False

    def select(self, cols: str, count: str | None = None) -> LocalQuery:
        self._select = cols
        self._count = count == "exact"
        if "grid(" in cols:
            self._embed_grid = True
            self._select = "*"
        return self

    def eq(self, column: str, value: Any) -> LocalQuery:
        self._filters.append(("=", column, value))
        return self

    def neq(self, column: str, value: Any) -> LocalQuery:
        self._filters.append(("!=", column, value))
        return self

    def gte(self, column: str, value: Any) -> LocalQuery:
        self._filters.append((">=", column, value))
        return self

    def order(self, column: str, desc: bool = False) -> LocalQuery:
        self._orders.append((column, desc))
        return self

    def limit(self, n: int) -> LocalQuery:
        self._limit = n
        return self

    def single(self) -> LocalQuery:
        self._single = True
        return self

    def update(self, data: dict[str, Any]) -> LocalQuery:
        self._update = data
        return self

    def delete(self) -> LocalQuery:
        self._delete = True
        return self

    def _where_sql(self) -> tuple[str, list[Any]]:
        if not self._filters:
            return "", []
        parts = []
        params: list[Any] = []
        for op, column, value in self._filters:
            self.client._check_ident(column)
            parts.append(f"{column} {op} ?")
            params.append(_to_sql_value(value))
        return " WHERE " + " AND ".join(parts), params

    def execute(self) -> LocalResult:
        with _LOCK:
            conn = self.client.conn
            where, params = self._where_sql()

            if self._delete:
                conn.execute(f"DELETE FROM {self.table}{where}", params)
                conn.commit()
                return LocalResult([])

            if self._update is not None:
                assignments = []
                update_params: list[Any] = []
                for key, value in self._update.items():
                    self.client._check_ident(key)
                    assignments.append(f"{key} = ?")
                    update_params.append(_to_sql_value(value))
                if "updated_at" not in self._update and self.table == "players":
                    assignments.append("updated_at = ?")
                    update_params.append(_now())
                sql = f"UPDATE {self.table} SET {', '.join(assignments)}{where}"
                conn.execute(sql, update_params + params)
                conn.commit()
                return LocalResult([])

            select_sql = self._select_sql()
            sql = f"SELECT {select_sql} FROM {self.table}{where}"
            if self._orders:
                order_bits = []
                for column, desc in self._orders:
                    self.client._check_ident(column)
                    order_bits.append(f"{column} {'DESC' if desc else 'ASC'}")
                sql += " ORDER BY " + ", ".join(order_bits)
            if self._limit is not None:
                sql += " LIMIT ?"
                params = [*params, self._limit]

            rows = [self.client._row_to_dict(self.table, r) for r in conn.execute(sql, params)]
            if self._embed_grid:
                for row in rows:
                    tile_id = row.get("tile_id")
                    land_use = None
                    if tile_id:
                        grid_row = conn.execute(
                            "SELECT land_use FROM grid WHERE tile_id = ?",
                            (tile_id,),
                        ).fetchone()
                        if grid_row:
                            land_use = grid_row["land_use"]
                    row["grid"] = {"land_use": land_use}

            count = len(rows)
            if self._single:
                return LocalResult(rows[0] if rows else None, count=count)
            return LocalResult(rows, count=count)

    def _select_sql(self) -> str:
        if self._select.strip() == "*":
            return "*"
        cols = [c.strip() for c in self._select.split(",") if c.strip()]
        for col in cols:
            self.client._check_ident(col)
        return ", ".join(cols) if cols else "*"


class LocalRpc:
    def __init__(self, client: LocalClient, name: str, params: dict[str, Any]):
        self.client = client
        self.name = name
        self.params = params or {}

    def execute(self) -> LocalResult:
        if self.name == "assign_random_tile":
            return LocalResult([self.client.assign_random_tile(str(self.params["p_player_id"]))])
        if self.name == "reset_game":
            self.client.reset_game()
            return LocalResult(None)
        raise ValueError(f"Unknown RPC: {self.name}")


class LocalClient:
    is_local = True

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self._init_schema()

    @staticmethod
    def _check_ident(name: str) -> None:
        if not name.replace("_", "").isalnum():
            raise ValueError(f"Invalid identifier: {name}")

    def table(self, name: str) -> LocalQuery:
        self._check_ident(name)
        return LocalQuery(self, name)

    def rpc(self, name: str, params: dict[str, Any] | None = None) -> LocalRpc:
        return LocalRpc(self, name, params or {})

    def _init_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS global_state (
              id INTEGER PRIMARY KEY CHECK (id = 1),
              phase TEXT NOT NULL DEFAULT 'lobby',
              round_number INTEGER NOT NULL DEFAULT 0,
              weather_text TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS grid (
              tile_id TEXT PRIMARY KEY,
              col_idx INTEGER NOT NULL,
              row_idx INTEGER NOT NULL,
              col_label TEXT NOT NULL,
              row_label INTEGER NOT NULL,
              land_use TEXT NOT NULL,
              is_assigned INTEGER NOT NULL DEFAULT 0,
              UNIQUE (col_idx, row_idx)
            );
            CREATE TABLE IF NOT EXISTS players (
              player_id TEXT PRIMARY KEY,
              tile_id TEXT UNIQUE,
              display_name TEXT,
              capital REAL NOT NULL DEFAULT 10000.0,
              trust REAL NOT NULL DEFAULT 100.0,
              current_action TEXT,
              action_round INTEGER,
              last_resolved INTEGER NOT NULL DEFAULT 0,
              connected_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              FOREIGN KEY (tile_id) REFERENCES grid(tile_id)
            );
            """
        )
        existing = self.conn.execute("SELECT id FROM global_state WHERE id = 1").fetchone()
        if not existing:
            self.conn.execute(
                """
                INSERT INTO global_state (id, phase, round_number, weather_text, updated_at)
                VALUES (1, 'lobby', 0, ?, ?)
                """,
                (DEFAULT_WEATHER, _now()),
            )
        grid_count = self.conn.execute("SELECT COUNT(*) AS n FROM grid").fetchone()["n"]
        if grid_count == 0:
            rows = []
            for col_idx, col_label in enumerate(COLS):
                for row_idx in range(8):
                    row_label = row_idx + 1
                    tile_id = f"{col_label}{row_label}"
                    rows.append(
                        (
                            tile_id,
                            col_idx,
                            row_idx,
                            col_label,
                            row_label,
                            land_use_for(col_label, row_label),
                            0,
                        )
                    )
            self.conn.executemany(
                """
                INSERT INTO grid (tile_id, col_idx, row_idx, col_label, row_label, land_use, is_assigned)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        self.conn.commit()

    def _row_to_dict(self, table: str, row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        if "is_assigned" in data:
            data["is_assigned"] = bool(data["is_assigned"])
        return data

    def assign_random_tile(self, player_id: str) -> dict[str, Any]:
        with _LOCK:
            existing = self.conn.execute(
                """
                SELECT p.tile_id, g.land_use, p.capital, p.trust
                FROM players p
                JOIN grid g ON g.tile_id = p.tile_id
                WHERE p.player_id = ? AND p.tile_id IS NOT NULL
                """,
                (player_id,),
            ).fetchone()
            if existing:
                return dict(existing)

            tile = self.conn.execute(
                """
                SELECT tile_id, land_use FROM grid
                WHERE is_assigned = 0
                ORDER BY RANDOM()
                LIMIT 1
                """
            ).fetchone()
            if tile is None:
                raise RuntimeError("No available tiles remaining")

            now = _now()
            self.conn.execute(
                "UPDATE grid SET is_assigned = 1 WHERE tile_id = ?",
                (tile["tile_id"],),
            )
            self.conn.execute(
                """
                INSERT INTO players (player_id, tile_id, capital, trust, last_resolved, connected_at, updated_at)
                VALUES (?, ?, 10000.0, 100.0, 0, ?, ?)
                ON CONFLICT(player_id) DO UPDATE SET
                    tile_id = excluded.tile_id,
                    updated_at = excluded.updated_at
                """,
                (player_id, tile["tile_id"], now, now),
            )
            self.conn.commit()
            return {
                "tile_id": tile["tile_id"],
                "land_use": tile["land_use"],
                "capital": 10000.0,
                "trust": 100.0,
            }

    def reset_game(self) -> None:
        with _LOCK:
            self.conn.execute("DELETE FROM players")
            self.conn.execute("UPDATE grid SET is_assigned = 0")
            self.conn.execute(
                """
                UPDATE global_state
                SET phase = 'lobby',
                    round_number = 0,
                    weather_text = ?,
                    updated_at = ?
                WHERE id = 1
                """,
                (DEFAULT_WEATHER, _now()),
            )
            self.conn.commit()


def _to_sql_value(value: Any) -> Any:
    if isinstance(value, bool):
        return int(value)
    return value


def get_local_client(path: Path | None = None) -> LocalClient:
    global _CLIENT
    if _CLIENT is None:
        db_path = path or Path(__file__).resolve().parent / "data" / "local_game.sqlite"
        _CLIENT = LocalClient(db_path)
    return _CLIENT
