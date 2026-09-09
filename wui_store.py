"""SQLite backend matching the WUI Interactive Supabase tables."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any

_LOCK = threading.RLock()
_CLIENT: "WuiLocalClient | None" = None


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
    def __init__(self, client: "WuiLocalClient", table: str):
        self.client = client
        self.table = table
        self._select = "*"
        self._count = False
        self._filters: list[tuple[str, str, Any]] = []
        self._orders: list[tuple[str, bool]] = []
        self._limit: int | None = None
        self._single = False
        self._update: dict[str, Any] | None = None
        self._insert: dict[str, Any] | list[dict[str, Any]] | None = None
        self._delete = False

    def select(self, cols: str, count: str | None = None) -> "LocalQuery":
        self._select = cols
        self._count = count == "exact"
        return self

    def insert(self, data: dict[str, Any] | list[dict[str, Any]]) -> "LocalQuery":
        self._insert = data
        return self

    def eq(self, column: str, value: Any) -> "LocalQuery":
        self._filters.append(("=", column, value))
        return self

    def neq(self, column: str, value: Any) -> "LocalQuery":
        self._filters.append(("!=", column, value))
        return self

    def gte(self, column: str, value: Any) -> "LocalQuery":
        self._filters.append((">=", column, value))
        return self

    def order(self, column: str, desc: bool = False) -> "LocalQuery":
        self._orders.append((column, desc))
        return self

    def limit(self, n: int) -> "LocalQuery":
        self._limit = n
        return self

    def single(self) -> "LocalQuery":
        self._single = True
        return self

    def update(self, data: dict[str, Any]) -> "LocalQuery":
        self._update = data
        return self

    def delete(self) -> "LocalQuery":
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

            if self._insert is not None:
                rows = self._insert if isinstance(self._insert, list) else [self._insert]
                inserted: list[dict[str, Any]] = []
                for row in rows:
                    cols = list(row.keys())
                    for col in cols:
                        self.client._check_ident(col)
                    placeholders = ", ".join("?" for _ in cols)
                    sql = (
                        f"INSERT INTO {self.table} ({', '.join(cols)}) "
                        f"VALUES ({placeholders})"
                    )
                    conn.execute(sql, [_to_sql_value(row[c]) for c in cols])
                    inserted.append(row)
                conn.commit()
                return LocalResult(inserted)

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

            fetched = conn.execute(sql, params).fetchall()
            rows = [self.client._row_to_dict(r) for r in fetched]
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


class WuiLocalClient:
    is_local = True

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    @staticmethod
    def _check_ident(name: str) -> None:
        if not name.replace("_", "").isalnum():
            raise ValueError(f"Invalid identifier: {name}")

    def table(self, name: str) -> LocalQuery:
        self._check_ident(name)
        return LocalQuery(self, name)

    def _init_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS app_state (
              id INTEGER PRIMARY KEY CHECK (id = 1),
              current_phase TEXT NOT NULL DEFAULT 'lobby',
              active_question INTEGER NOT NULL DEFAULT 1,
              results_open INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS game1_mapper (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              concept TEXT NOT NULL,
              assigned_phase TEXT NOT NULL,
              player_name TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS game2_trivia (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              question_id INTEGER NOT NULL,
              vote TEXT NOT NULL,
              player_name TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS game3_cloud (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              word TEXT NOT NULL,
              player_name TEXT NOT NULL DEFAULT ''
            );
            """
        )
        self._ensure_column("app_state", "results_open", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("game1_mapper", "player_name", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("game2_trivia", "player_name", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("game3_cloud", "player_name", "TEXT NOT NULL DEFAULT ''")
        existing = self.conn.execute("SELECT id FROM app_state WHERE id = 1").fetchone()
        if not existing:
            self.conn.execute(
                """
                INSERT INTO app_state (id, current_phase, active_question, results_open)
                VALUES (1, 'lobby', 1, 0)
                """
            )
        self.conn.commit()

    def _ensure_column(self, table: str, column: str, ddl: str) -> None:
        self._check_ident(table)
        self._check_ident(column)
        info = self.conn.execute(f"PRAGMA table_info({table})").fetchall()
        names = {row["name"] for row in info}
        if column not in names:
            self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

    def _row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        if "results_open" in data:
            data["results_open"] = bool(data["results_open"])
        return data


def _to_sql_value(value: Any) -> Any:
    if isinstance(value, bool):
        return int(value)
    return value


def get_wui_client(path: Path | None = None) -> WuiLocalClient:
    global _CLIENT
    if _CLIENT is None:
        db_path = path or Path(__file__).resolve().parent / "data" / "wui_interactive.sqlite"
        _CLIENT = WuiLocalClient(db_path)
    return _CLIENT
