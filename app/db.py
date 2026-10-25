"""Database access layer.

The application was written against ``sqlite3``. Rather than rewrite ~90
queries, this module exposes a small adapter that presents the same
``connect -> execute -> fetch`` surface on top of either SQLite or PostgreSQL,
so ``app.main`` can stay dialect-agnostic and the deployment can move to
PostgreSQL purely through configuration.

Select the backend with ``DATABASE_URL``:

    postgres://user:pass@host/db   -> PostgreSQL
    ./data/otp_inbox.db            -> SQLite (default, zero config)

The only non-portable SQL the app uses is handled here:

* ``?`` parameter markers             -> ``%s`` on PostgreSQL
* ``AUTOINCREMENT`` / ``lastrowid``  -> identity columns + ``RETURNING id``
* ``INSERT OR IGNORE``                -> ``INSERT ... ON CONFLICT DO NOTHING``
* ``executescript``                   -> statement splitter
* FTS5 virtual table                  -> ``tsvector`` column + GIN index
* ``BLOB``                            -> ``BYTEA``, always read back as ``bytes``
* integer flags (0/1)                 -> ``SMALLINT``, so the app's ``1 if x else 0``
                                         keeps working and ``bool(row[...])`` stays valid
"""

from __future__ import annotations

import os
import re
import sqlite3
from contextlib import contextmanager
from typing import Any, Iterator, Optional, Sequence

# --------------------------------------------------------------------- config

MIN_FULLTEXT_TERM = 3


def database_url() -> str:
    return (os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL") or "").strip()


def using_postgres() -> bool:
    url = database_url()
    return bool(url) and (
        url.startswith("postgres://")
        or url.startswith("postgresql://")
    )


# ------------------------------------------------------------ SQL translation

_IDENT_RE = re.compile(r'"[^"]*"|\'[^\']*\'|--[^\n]*|/\*.*?\*/', re.S)


def _outside_literals(sql: str, fn) -> str:
    """Apply `fn` to every character of `sql` that is not inside a literal.

    Comment and quote scanning is a deliberate simplification: the app's SQL is
    fully machine-written, so the only literals that appear are single-quoted
    string constants.
    """
    out = []
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]
        if ch in "\"'":
            quote = ch
            out.append(ch)
            i += 1
            while i < n:
                out.append(sql[i])
                if sql[i] == quote:
                    i += 1
                    break
                i += 1
            continue
        if ch == "-" and sql[i + 1:i + 2] == "-":
            j = sql.find("\n", i)
            j = n if j == -1 else j
            out.append(sql[i:j])
            i = j
            continue
        out.append(fn(ch))
        i += 1
    return "".join(out)


def to_pg_markers(sql: str) -> str:
    return _outside_literals(sql, lambda ch: "%s" if ch == "?" else ch)


def to_pg_insert(sql: str) -> str:
    """Rewrite SQLite-only INSERT forms to their PostgreSQL equivalents.

    `INSERT OR REPLACE` is deliberately *not* translated: its conflict-target
    semantics cannot be inferred from the statement, and guessing would silently
    write the wrong row. Use an explicit `ON CONFLICT ... DO UPDATE`, which is
    valid on both backends.
    """
    sql = to_pg_markers(sql)
    if re.search(r"INSERT\s+OR\s+REPLACE", sql, re.I):
        raise NotImplementedError(
            "INSERT OR REPLACE is SQLite-only and has no safe automatic "
            "translation. Rewrite it as INSERT ... ON CONFLICT (...) DO UPDATE."
        )
    if re.search(r"^\s*INSERT\s+OR\s+IGNORE\s+INTO", sql, re.I):
        sql = re.sub(r"^\s*INSERT\s+OR\s+IGNORE\s+INTO", "INSERT INTO", sql, flags=re.I)
        if "ON CONFLICT" not in sql.upper():
            sql += " ON CONFLICT DO NOTHING"
    return sql


def assert_portable(sql: str) -> None:
    """Reject SQL that only one backend understands, naming the construct.

    Called for every statement so a portability mistake surfaces at the call
    site instead of as an opaque driver error on the one backend that was not
    exercised during development.
    """
    lowered = _outside_literals(sql, lambda ch: ch).lower()
    for pattern, message in _SQLITE_ONLY:
        if re.search(pattern, lowered):
            raise NotImplementedError(f"{message}\nSQL: {sql.strip()[:200]}")


_SQLITE_ONLY = [
    (r"\bpragma\b", "PRAGMA is SQLite-only; guard it with USING_POSTGRES."),
    (r"\bauto_increment\b", "AUTOINCREMENT is SQLite-only; use an identity column."),
    (r"\bgroup_concat\b", "GROUP_CONCAT is SQLite-only; use string_agg()."),
    (r"\bstrftime\b|\bdate\s*\(", "strftime/date() are SQLite-only; use to_char()."),
    (r"\bjson_extract\b", "json_extract is SQLite-only; use ->> operators."),
    (r"\bsqlite_master\b|\bsqlite_sequence\b", "SQLite internals are not available."),
    (r"insert\s+or\s+replace", "Use INSERT ... ON CONFLICT (...) DO UPDATE."),
]


# Tables whose primary key is a single `id` column, and which therefore support
# `RETURNING id`. `mailbox_access` and `message_reads` are deliberately absent:
# they use composite keys (mailbox_id, user_id) / (message_id, user_id) and have
# no `id` column, so appending `RETURNING id` to them is a hard error.
_PG_PRIMARY_KEY = {
    "users", "mailboxes", "messages", "routing_rules", "sessions",
    "notifications", "audit_logs", "attachments", "referral_codes",
    "registrations", "outbound_messages",
}


def inject_returning(sql: str) -> str:
    """Add `RETURNING id` to a plain INSERT so `cursor.lastrowid` works.

    Only for tables known to have a single-column `id` primary key, and only
    when the statement does not already return rows.
    """
    stripped = sql.lstrip()
    if not re.match(r"INSERT\s+INTO", stripped, re.I):
        return sql
    if re.search(r"\bRETURNING\b", sql, re.I):
        return sql
    m = re.match(r"INSERT\s+INTO\s+[\"']?(\w+)", stripped, re.I)
    if not m or m.group(1).lower() not in _PG_PRIMARY_KEY:
        return sql
    return sql.rstrip().rstrip(";") + " RETURNING id"


# --------------------------------------------------------------- BLOB helpers


def as_bytes(value: Any) -> Optional[bytes]:
    """Normalise a BLOB read from either backend to ``bytes``.

    SQLite hands back a ``memoryview``; PostgreSQL hands back ``bytes``.
    Callers such as the raw-.eml view call ``.decode()`` directly, so the
    memoryview has to be converted or the endpoint raises.
    """
    if value is None:
        return None
    if isinstance(value, memoryview):
        return value.tobytes()
    if isinstance(value, bytearray):
        return bytes(value)
    return value


# ------------------------------------------------------------ SQLite backend


class _CursorProxy:
    """Passes everything through, and normalises only what the two drivers
    disagree on. `__getattr__` keeps the proxy transparent, and the sequence
    dunders are forwarded explicitly because attribute lookup does not cover
    them -- `for row in conn.execute(...)` is used throughout app.py, and
    `__getattr__` alone would leave the proxy non-iterable."""

    def __init__(self, cur):
        self._cur = cur

    def __getattr__(self, name: str):
        return getattr(self._cur, name)

    def __iter__(self):
        return iter(self._cur)

    def __next__(self):
        return next(self._cur)

    def __len__(self):
        return len(self._cur)

    def __getitem__(self, item):
        return self._cur[item]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def close(self):
        self._cur.close()


class _SQLiteCursor(_CursorProxy):
    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    @property
    def lastrowid(self):
        return self._cur.lastrowid

    @property
    def rowcount(self):
        return self._cur.rowcount


class _ConnectionProxy:
    """Transparent pass-through to the driver connection.

    The app only calls `execute` and `executescript`, but delegating everything
    else means a future call to `cursor()` or a driver attribute still works
    instead of raising AttributeError.
    """

    def __init__(self, conn):
        self._conn = conn

    def __getattr__(self, name: str):
        return getattr(self._conn, name)

    def __repr__(self):
        return f"<{type(self).__name__} {self._conn!r}>"


class _SQLiteConnection(_ConnectionProxy):
    backend = "sqlite"

    def execute(self, sql: str, params: Sequence[Any] = ()) -> _SQLiteCursor:
        return _SQLiteCursor(self._conn.execute(sql, tuple(params)))

    def executescript(self, script: str) -> None:
        self._conn.executescript(script)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()


# --------------------------------------------------------- PostgreSQL backend


class _PostgresCursor(_CursorProxy):
    def __init__(self, cur, inject_id: bool):
        super().__init__(cur)
        self._inject_id = inject_id
        self._rows: list[Any] | None = None
        self._pos = 0
        self.lastrowid: Optional[int] = None
        if inject_id:
            # Buffer the RETURNING rows so `lastrowid` is readable straight
            # away, the way sqlite3 exposes it. Callers throughout the app read
            # `cur.lastrowid` and never fetch, and psycopg3 only surfaces the
            # id once a row has been consumed, so without this buffer
            # `lastrowid` would silently be None.
            self._rows = list(self._cur.fetchall() or [])
            if self._rows:
                self.lastrowid = self._rows[-1]["id"]

    def _buffered(self):
        if self._rows is None:
            return []
        return self._rows

    def fetchone(self):
        if not self._inject_id:
            return self._cur.fetchone()
        rows = self._buffered()
        if self._pos >= len(rows):
            return None
        row = rows[self._pos]
        self._pos += 1
        return row

    def fetchall(self):
        if not self._inject_id:
            return self._cur.fetchall()
        rest = self._buffered()[self._pos:]
        self._pos = len(self._buffered())
        return rest

    def __iter__(self):
        if not self._inject_id:
            return iter(self._cur)
        return iter(self.fetchall())

    def __len__(self):
        if self._inject_id:
            return len(self._buffered())
        return len(self._cur)

    def __getitem__(self, item):
        if self._inject_id:
            return self._buffered()[item]
        return self._cur[item]

    @property
    def rowcount(self) -> int:
        return self._cur.rowcount


class _PostgresConnection(_ConnectionProxy):
    backend = "postgres"

    def execute(self, sql: str, params: Sequence[Any] = ()) -> _PostgresCursor:
        if sql.lstrip().upper().startswith("PRAGMA"):
            # init_db and table_columns both guard their PRAGMA calls. Reaching
            # here means a guard is missing, which must not pass silently.
            raise NotImplementedError("PRAGMA is SQLite-only; guard it with USING_POSTGRES.")
        assert_portable(sql)
        pg_sql = to_pg_insert(sql)
        inject = bool(re.match(r"INSERT\s+INTO", pg_sql.lstrip(), re.I)) and \
            "RETURNING" not in pg_sql.upper()
        if inject:
            pg_sql = inject_returning(pg_sql)
        from psycopg.rows import dict_row

        def run(statement: str) -> "_PostgresCursor":
            cur = self._conn.cursor(row_factory=dict_row)
            cur.execute(statement, tuple(params) or None)
            return _PostgresCursor(cur, inject and "RETURNING" in statement.upper())

        try:
            return run(pg_sql)
        except Exception as exc:
            # Safety net for the RETURNING target: if the guessed primary key is
            # not a real column, run the statement without it. This keeps a
            # schema change (or a table with a composite key) from turning every
            # insert into a 500.
            if not (inject and "RETURNING" in pg_sql.upper() and _is_unknown_column(exc)):
                raise
            bare = pg_sql.rsplit(" RETURNING ", 1)[0]
            return run(bare)

    def executescript(self, script: str) -> None:
        for statement in split_statements(script):
            if statement.strip():
                self._conn.execute(statement)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()


def _is_unknown_column(exc: Exception) -> bool:
    """True when PostgreSQL rejected a column name we supplied."""
    text = str(exc).lower()
    return "column" in text and ("does not exist" in text or "unknown" in text)


def split_statements(script: str) -> list[str]:
    """Split a DDL script on top-level semicolons, respecting quotes."""
    statements: list[str] = []
    buf: list[str] = []
    i = 0
    n = len(script)
    while i < n:
        ch = script[i]
        if ch in "\"'":
            quote = ch
            buf.append(ch)
            i += 1
            while i < n:
                buf.append(script[i])
                if script[i] == quote:
                    i += 1
                    break
                i += 1
            continue
        if ch == "-" and script[i + 1:i + 2] == "-":
            j = script.find("\n", i)
            j = n if j == -1 else j
            buf.append(script[i:j])
            i = j
            continue
        if ch == ";":
            statements.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    if "".join(buf).strip():
        statements.append("".join(buf))
    return statements


# ------------------------------------------------------------------ connect


@contextmanager
def connect() -> Iterator[Any]:
    """Open a connection and yield the *adapter*, not the raw driver handle.

    The adapter is what normalises placeholders, `lastrowid`, `executescript`
    and the row factory. Yielding the raw handle would silently skip all of that
    on PostgreSQL, which fails on the first `executescript` with an opaque
    AttributeError, so the wrapper is what callers always get.

    Commit on success, roll back on error, always close. SQLite needs a fresh
    handle per unit of work: a long-lived one would pin the read snapshot and
    block the ingest writer.
    """
    if using_postgres():
        import psycopg
        from psycopg.rows import dict_row

        raw = psycopg.connect(database_url(), row_factory=dict_row,
                              connect_timeout=15, autocommit=False)
        conn = _PostgresConnection(raw)
        try:
            yield conn
            raw.commit()
        except Exception:
            raw.rollback()
            raise
        finally:
            raw.close()
    else:
        path = os.getenv("DATABASE_PATH", "./data/otp_inbox.db")
        SQLITE_CACHE_SIZE_KB = int(os.getenv("SQLITE_CACHE_SIZE_KB", "2000"))
        raw = sqlite3.connect(path, timeout=30)
        raw.row_factory = sqlite3.Row
        raw.execute("PRAGMA foreign_keys = ON")
        raw.execute("PRAGMA busy_timeout = 30000")
        raw.execute(f"PRAGMA cache_size = -{SQLITE_CACHE_SIZE_KB}")
        raw.execute("PRAGMA synchronous = NORMAL")
        raw.execute("PRAGMA temp_store = MEMORY")
        raw.execute("PRAGMA threads = 1")
        conn = _SQLiteConnection(raw)
        try:
            yield conn
            raw.commit()
        except Exception:
            raw.rollback()
            raise
        finally:
            raw.close()


def search_capable() -> bool:
    """True when the active backend can do indexed full-text search."""
    return True if using_postgres() else bool(os.getenv("DISABLE_FTS") != "1")


# ------------------------------------------------------------ search queries


def fts_query(text: str) -> str:
    """SQLite FTS5 MATCH expression built from free-form user input.

    Every term is stripped to letters/digits and double-quoted, so punctuation
    that is FTS5 operator syntax (`*`, `^`, `OR`, `NEAR`, `-`) is inert.
    """
    terms = [t for t in re.findall(r"[0-9A-Za-zÀ-ỿ]+", text) if len(t) >= MIN_FULLTEXT_TERM]
    return " AND ".join(f'"{t}"*' for t in terms[:16])


def like_operator() -> str:
    """`ILIKE` on PostgreSQL, `LIKE` on SQLite.

    PostgreSQL's LIKE is case-sensitive while SQLite's is not, so an unchanged
    `LIKE` would match a different row set on each backend. Usernames and
    sender addresses keep their original case, so that divergence is user
    visible: searching "Invoice" would find nothing on PostgreSQL but would work
    on SQLite. ILIKE makes the two agree.
    """
    return "ILIKE" if using_postgres() else "LIKE"


def build_search_clause(query: str, scope: str = "all", alias: str = "msg") -> tuple[str, list[Any]]:
    """Build the WHERE fragment for a search, choosing the fastest correct path.

    Returns ``(sql_fragment, params)``. `alias` is the SQL alias of the
    ``messages`` table in the surrounding query.
    """
    query = (query or "").strip()
    if not query:
        return "", []

    headers_only = scope == "headers"
    like = f"%{query}%"
    op = like_operator()

    if headers_only or not search_capable():
        if headers_only:
            return (
                f"({alias}.envelope_from {op} ? OR {alias}.envelope_to {op} ? "
                f"OR {alias}.subject {op} ? OR {alias}.otp {op} ?)",
                [like, like, like, like],
            )
        return (
            f"({alias}.envelope_from {op} ? OR {alias}.envelope_to {op} ? "
            f"OR {alias}.subject {op} ? OR {alias}.body_text {op} ? OR {alias}.otp {op} ?)",
            [like, like, like, like, like],
        )

    if using_postgres():
        # `websearch_to_tsquery` never raises on arbitrary input, so user text
        # cannot break the query. The ILIKE arm restores recall for diacritic
        # and short-term cases that a tsvector cannot match.
        return (
            f"({alias}.search_vector @@ websearch_to_tsquery('simple', ?) "
            f"OR {alias}.subject ILIKE ?)",
            [query, like],
        )

    match = fts_query(query)
    if match:
        return (
            f"{alias}.id IN (SELECT message_id FROM messages_fts WHERE messages_fts MATCH ?)",
            [match],
        )
    return (
        f"({alias}.envelope_from {op} ? OR {alias}.envelope_to {op} ? "
        f"OR {alias}.subject {op} ? OR {alias}.body_text {op} ? OR {alias}.otp {op} ?)",
        [like, like, like, like, like],
    )


def search_engine() -> str:
    return "fts" if search_capable() else "like"
