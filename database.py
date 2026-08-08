"""
Database module: connection pool, table creation, and all CRUD operations.
Uses asyncpg for async PostgreSQL access.
"""

import csv
import os
from datetime import date
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

load_dotenv()

# Module-level connection pool, initialized by init_db()
pool: asyncpg.Pool | None = None


async def _load_usda_foods(conn: asyncpg.Connection) -> None:
    """
    Load usda_protein_foundation.csv into usda_foods table.
    Skips loading if the table already has data.
    """
    count = await conn.fetchval("SELECT COUNT(*) FROM usda_foods")
    if count and count > 0:
        return

    csv_path = Path(__file__).parent / "usda_protein_foundation.csv"
    if not csv_path.exists():
        return

    rows = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                fdc_id = int(row["fdc_id"])
                food_name = row["description"]
                protein_per_100g = float(row["protein_per_100g"])
                rows.append((fdc_id, food_name, protein_per_100g))
            except (KeyError, ValueError):
                continue

    if rows:
        await conn.executemany(
            "INSERT INTO usda_foods (fdc_id, food_name, protein_per_100g) VALUES ($1, $2, $3)",
            rows,
        )


async def init_db() -> None:
    """
    Create the connection pool and create all tables if they don't exist.
    Must be called once at application startup.
    """
    global pool
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise ValueError("DATABASE_URL not found in environment")

    pool = await asyncpg.create_pool(
        database_url,
        min_size=2,
        max_size=10,
        command_timeout=60,
    )

    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS standards (
                id              SERIAL PRIMARY KEY,
                user_id         BIGINT NOT NULL,
                user_food_name  TEXT NOT NULL,
                grams           INTEGER NOT NULL,
                UNIQUE(user_id, user_food_name)
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS logs (
                id          SERIAL PRIMARY KEY,
                user_id     BIGINT NOT NULL,
                date        DATE NOT NULL,
                food_name   TEXT NOT NULL,
                grams       INTEGER NOT NULL,
                protein_g   REAL NOT NULL
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS targets (
                id          SERIAL PRIMARY KEY,
                user_id     BIGINT NOT NULL UNIQUE,
                target      INTEGER NOT NULL
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS usda_foods (
                id              SERIAL PRIMARY KEY,
                fdc_id          BIGINT NOT NULL,
                food_name       TEXT NOT NULL,
                protein_per_100g REAL NOT NULL
            )
        """)
        await _load_usda_foods(conn)
        await conn.execute("""
            ALTER TABLE standards
            ADD COLUMN IF NOT EXISTS protein_per_100_g REAL
        """)
        await conn.execute("""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'standards' AND column_name = 'usda_food_id'
                ) THEN
                    UPDATE standards s SET protein_per_100_g = u.protein_per_100g
                    FROM usda_foods u WHERE s.usda_food_id = u.id;
                    ALTER TABLE standards DROP COLUMN usda_food_id;
                END IF;
            END $$;
        """)
        await conn.execute("""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'standards' AND column_name = 'food_name'
                ) THEN
                    ALTER TABLE standards RENAME COLUMN food_name TO user_food_name;
                END IF;
            END $$;
        """)
        await conn.execute("""
            ALTER TABLE targets
            ADD COLUMN IF NOT EXISTS last_reminder_date DATE
        """)
        await conn.execute("DROP TABLE IF EXISTS user_notifications")


async def close_db() -> None:
    """Close the connection pool cleanly. Call on application shutdown."""
    global pool
    if pool:
        await pool.close()
        pool = None


# --- Targets ---


async def set_target(user_id: int, target_g: int) -> None:
    """
    Upsert the user's daily protein target in grams (targets table).
    """
    if pool is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO targets (user_id, target)
            VALUES ($1, $2)
            ON CONFLICT (user_id) DO UPDATE SET target = EXCLUDED.target
            """,
            user_id,
            target_g,
        )


async def get_target(user_id: int) -> int | None:
    """
    Return the user's daily protein target in grams.
    Reads from targets table. Returns None if not set.
    """
    if pool is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT target FROM targets WHERE user_id = $1",
            user_id,
        )
        return row["target"] if row else None


async def get_user_ids_with_targets() -> list[int]:
    """Return list of user_ids that have a target set (for reminder job)."""
    if pool is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT user_id FROM targets")
        return [r["user_id"] for r in rows]


async def get_last_reminder_date(user_id: int) -> date | None:
    """Return last date a reminder was sent for this user (from targets), or None."""
    if pool is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT last_reminder_date FROM targets WHERE user_id = $1",
            user_id,
        )
        return row["last_reminder_date"] if row and row["last_reminder_date"] else None


async def update_last_reminder_date(user_id: int, d: date) -> None:
    """Set last_reminder_date for user in targets (after sending a reminder)."""
    if pool is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE targets SET last_reminder_date = $1 WHERE user_id = $2",
            d,
            user_id,
        )


# --- USDA foods ---


async def search_usda_foods(query: str, limit: int = 15) -> list[tuple[int, str, float]]:
    """
    Search usda_foods by partial match on food_name (case-insensitive).
    Returns list of (id, food_name, protein_per_100g).
    """
    if pool is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    q = query.strip().lower()
    if not q:
        return []
    pattern = f"%{q}%"
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, food_name, protein_per_100g
            FROM usda_foods
            WHERE LOWER(food_name) LIKE $1
            ORDER BY food_name
            LIMIT $2
            """,
            pattern,
            limit,
        )
        return [(r["id"], r["food_name"], r["protein_per_100g"]) for r in rows]


async def get_usda_food_by_id(usda_food_id: int) -> tuple[str, float] | None:
    """
    Return (food_name, protein_per_100g) for the given usda_foods id, or None.
    """
    if pool is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT food_name, protein_per_100g FROM usda_foods WHERE id = $1",
            usda_food_id,
        )
        return (row["food_name"], row["protein_per_100g"]) if row else None


# --- Standards ---


async def set_standard(
    user_id: int,
    food_name: str,
    grams: int,
    protein_per_100g: float | None = None,
) -> None:
    """
    Upsert a standard portion for this user.
    food_name (user_food_name) is the name the user assigns, e.g. "chicken breast".
    Stored with only the first letter capitalized.
    protein_per_100g is optional (grams of protein per 100g of food).
    """
    if pool is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    name = food_name.strip().capitalize()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO standards (user_id, user_food_name, grams, protein_per_100_g)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (user_id, user_food_name) DO UPDATE SET
                grams = EXCLUDED.grams,
                protein_per_100_g = EXCLUDED.protein_per_100_g
            """,
            user_id,
            name,
            grams,
            protein_per_100g,
        )


async def get_standard(user_id: int, food_name: str) -> tuple[int, str] | None:
    """
    Return (grams, stored_name) for the given user_food_name's standard portion, or None if not found.
    Lookup is case-insensitive.
    """
    if pool is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    name = food_name.strip()
    if not name:
        return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT grams, user_food_name FROM standards WHERE user_id = $1 AND LOWER(user_food_name) = LOWER($2)",
            user_id,
            name,
        )
        return (row["grams"], row["user_food_name"]) if row else None


async def get_standard_full(user_id: int, food_name: str) -> tuple[int, float, str] | None:
    """
    Return (grams, protein_per_100_g, stored_name) for the given user_food_name's standard, or None if not found.
    Lookup is case-insensitive.
    """
    if pool is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    name = food_name.strip()
    if not name:
        return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT grams, protein_per_100_g, user_food_name FROM standards WHERE user_id = $1 AND LOWER(user_food_name) = LOWER($2)",
            user_id,
            name,
        )
        if not row or row["protein_per_100_g"] is None:
            return None
        return row["grams"], float(row["protein_per_100_g"]), row["user_food_name"]


async def get_standard_for_edit(user_id: int, food_name: str) -> tuple[int, float | None, str] | None:
    """
    Return (grams, protein_per_100_g, stored_name) for the given standard, or None if not found.
    protein_per_100_g can be None (allows editing standards that don't have protein set yet).
    Lookup is case-insensitive.
    """
    if pool is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    name = food_name.strip()
    if not name:
        return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT grams, protein_per_100_g, user_food_name FROM standards WHERE user_id = $1 AND LOWER(user_food_name) = LOWER($2)",
            user_id,
            name,
        )
        if not row:
            return None
        protein = float(row["protein_per_100_g"]) if row["protein_per_100_g"] is not None else None
        return row["grams"], protein, row["user_food_name"]


async def get_all_standards(user_id: int) -> list[tuple[str, int]]:
    """
    Return all standards for the user as a list of (user_food_name, grams).
    """
    if pool is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT user_food_name, grams FROM standards WHERE user_id = $1 ORDER BY user_food_name",
            user_id,
        )
        return [(r["user_food_name"], r["grams"]) for r in rows]


async def search_standards(user_id: int, query: str) -> list[tuple[str, int, float | None]]:
    """
    Search standards by partial name match (case-insensitive).
    Returns (user_food_name, grams, protein_per_100_g) for all matches containing the query.
    """
    if pool is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    search = query.strip()
    if not search:
        return []
    pattern = f"%{search}%"
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT user_food_name, grams, protein_per_100_g FROM standards WHERE user_id = $1 AND user_food_name ILIKE $2 ORDER BY user_food_name",
            user_id,
            pattern,
        )
        return [(r["user_food_name"], r["grams"], r["protein_per_100_g"]) for r in rows]


async def get_all_standards_full(user_id: int) -> list[tuple[str, int, float | None]]:
    """
    Return all standards for the user as (user_food_name, grams, protein_per_100_g).
    protein_per_100_g can be None.
    """
    if pool is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT user_food_name, grams, protein_per_100_g FROM standards WHERE user_id = $1 ORDER BY user_food_name",
            user_id,
        )
        return [(r["user_food_name"], r["grams"], r["protein_per_100_g"]) for r in rows]


async def update_standard_protein(user_id: int, food_name: str, protein_per_100g: float) -> bool:
    """
    Update protein_per_100_g for a standard. Lookup is case-insensitive.
    Returns True if updated, False if not found.
    """
    if pool is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    name = food_name.strip()
    if not name:
        return False
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE standards SET protein_per_100_g = $3 WHERE user_id = $1 AND LOWER(user_food_name) = LOWER($2)",
            user_id,
            name,
            protein_per_100g,
        )
        return result.split()[-1] == "1"


async def delete_standard(user_id: int, food_name: str) -> None:
    """Delete a standard portion for this user. Lookup is case-insensitive."""
    if pool is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    name = food_name.strip()
    if not name:
        return
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM standards WHERE user_id = $1 AND LOWER(user_food_name) = LOWER($2)",
            user_id,
            name,
        )


# --- Logs ---


async def add_log(user_id: int, food_name: str, grams: int, protein_g: float) -> None:
    """
    Insert a log entry for today's date.
    food_name is stored with only the first letter capitalized.
    """
    await add_log_for_date(user_id, food_name, grams, protein_g, date.today())


async def add_log_for_date(user_id: int, food_name: str, grams: int, protein_g: float, d: date) -> None:
    """
    Insert a log entry for the given date.
    food_name is stored with only the first letter capitalized.
    """
    if pool is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    name = food_name.strip().capitalize()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO logs (user_id, date, food_name, grams, protein_g)
            VALUES ($1, $2, $3, $4, $5)
            """,
            user_id,
            d,
            name,
            grams,
            protein_g,
        )


async def get_today_logs(user_id: int) -> list[tuple[str, int, float]]:
    """
    Return all log entries for today as a list of (food_name, grams, protein_g).
    """
    if pool is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    today = date.today()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT food_name, grams, protein_g FROM logs WHERE user_id = $1 AND date = $2 ORDER BY id",
            user_id,
            today,
        )
        return [(r["food_name"], r["grams"], r["protein_g"]) for r in rows]


async def get_today_logs_matching(user_id: int, search_term: str) -> list[tuple[int, str, int, float]]:
    """
    Return today's log entries matching search_term (case-insensitive partial match).
    Returns (id, food_name, grams, protein_g).
    """
    if pool is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    search = search_term.strip()
    if not search:
        return []
    today = date.today()
    pattern = f"%{search}%"
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, food_name, grams, protein_g FROM logs WHERE user_id = $1 AND date = $2 AND food_name ILIKE $3 ORDER BY id",
            user_id,
            today,
            pattern,
        )
        return [(r["id"], r["food_name"], r["grams"], r["protein_g"]) for r in rows]


async def get_today_logs_with_ids(user_id: int) -> list[tuple[int, str, int, float]]:
    """
    Return all log entries for today as a list of (id, food_name, grams, protein_g).
    """
    if pool is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    today = date.today()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, food_name, grams, protein_g FROM logs WHERE user_id = $1 AND date = $2 ORDER BY id",
            user_id,
            today,
        )
        return [(r["id"], r["food_name"], r["grams"], r["protein_g"]) for r in rows]


async def get_log_by_id(user_id: int, log_id: int) -> tuple[str, int, float] | None:
    """Return (food_name, grams, protein_g) for the log entry, or None if not found or not today."""
    if pool is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    today = date.today()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT food_name, grams, protein_g FROM logs WHERE id = $1 AND user_id = $2 AND date = $3",
            log_id,
            user_id,
            today,
        )
        return (row["food_name"], row["grams"], row["protein_g"]) if row else None


async def delete_log(user_id: int, log_id: int) -> bool:
    """Delete a log entry by id. Returns True if deleted, False if not found."""
    if pool is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    today = date.today()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM logs WHERE id = $1 AND user_id = $2 AND date = $3",
            log_id,
            user_id,
            today,
        )
        return result.split()[-1] == "1"


async def get_logs_for_date(user_id: int, d: date) -> list[tuple[str, int, float]]:
    """
    Return all log entries for a given date as a list of (food_name, grams, protein_g).
    """
    if pool is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT food_name, grams, protein_g FROM logs WHERE user_id = $1 AND date = $2 ORDER BY id",
            user_id,
            d,
        )
        return [(r["food_name"], r["grams"], r["protein_g"]) for r in rows]


async def get_total_for_date(user_id: int, d: date) -> float:
    """
    Return the sum of protein_g for a given date as a float.
    Returns 0.0 if no logs for that date.
    """
    if pool is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    async with pool.acquire() as conn:
        val = await conn.fetchval(
            "SELECT COALESCE(SUM(protein_g), 0) FROM logs WHERE user_id = $1 AND date = $2",
            user_id,
            d,
        )
        return float(val)


async def get_today_total(user_id: int) -> float:
    """
    Return the sum of protein_g for today as a float.
    Returns 0.0 if no logs for today.
    """
    if pool is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    today = date.today()
    async with pool.acquire() as conn:
        val = await conn.fetchval(
            "SELECT COALESCE(SUM(protein_g), 0) FROM logs WHERE user_id = $1 AND date = $2",
            user_id,
            today,
        )
        return float(val)


async def clear_today_logs(user_id: int) -> None:
    """Delete all log entries for today."""
    if pool is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    today = date.today()
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM logs WHERE user_id = $1 AND date = $2",
            user_id,
            today,
        )