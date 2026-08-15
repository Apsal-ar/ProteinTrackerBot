# ProteinTrackerBot

A Telegram bot that helps users track daily protein intake. Users set a protein target, save personal food portions (with protein values from USDA data or entered manually), log meals, and review daily/weekly summaries. The bot can also send an evening reminder if the daily target has not been met.

## Purpose

ProteinTrackerBot makes protein tracking quick in chat:

1. Set a daily protein target (`/target`)
2. Save standard portions for foods you eat often (`/addfood`)
3. Log meals with short commands (`/log`, `/quicklog`, `/logyesterday`)
4. Check progress (`/today`, `/summary`, `/week`)

Food protein values can come from a built-in USDA foundation foods list or from values the user enters manually.

## Stack

| Layer | Technology |
| --- | --- |
| Language | Python 3 |
| Bot framework | [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot) v20.7 (with job-queue extras) |
| Database | PostgreSQL |
| DB driver | [asyncpg](https://magicstack.github.io/asyncpg/) (async connection pool) |
| Config | [python-dotenv](https://github.com/theskumar/python-dotenv) (`.env`) |
| Food reference data | Local CSV (`usda_protein_foundation.csv`), loaded into PostgreSQL on first startup |

Entry point: `bot.py`. Handlers live under `handlers/`. Database access is centralized in `database.py`. Scheduled reminders are in `jobs.py`.

## Data storage

All persistent user and food data is stored in **PostgreSQL**. The connection string is read from the `DATABASE_URL` environment variable (typically set in a local `.env` file that is not committed).

Secrets and credentials (bot token, database URL) are **not** stored in the repository. They belong only in environment variables / `.env`:

| Variable | Purpose |
| --- | --- |
| `BOT_TOKEN` | Telegram Bot API token |
| `DATABASE_URL` | PostgreSQL connection URL |

On startup, the bot creates a connection pool, ensures tables exist, and seeds the USDA foods table from `usda_protein_foundation.csv` if that table is empty.

## Database structure

### `users`

One row per Telegram user. Created on `/start` (and before other writes so foreign keys succeed).

| Column | Type | Notes |
| --- | --- | --- |
| `user_id` | `BIGINT` | Telegram user id (primary key) |
| `username` | `TEXT` | Telegram @handle (nullable) |
| `first_name` | `TEXT` | Display name (nullable) |
| `created_at` | `TIMESTAMPTZ` | Row creation time |
| `timezone` | `TEXT` | IANA timezone (default `UTC`), from a one-time location share |

`standards`, `logs`, and `targets` reference `users(user_id)` with `ON DELETE CASCADE`.

### `targets`

One daily protein goal per user.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `SERIAL` | Primary key |
| `user_id` | `BIGINT` | Telegram user id (unique) |
| `target` | `INTEGER` | Daily protein target in grams |
| `last_reminder_date` | `DATE` | Last day a reminder was sent (optional) |

### `standards`

User-saved “standard portions” (food name + typical serving size + protein density).

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `SERIAL` | Primary key |
| `user_id` | `BIGINT` | Telegram user id |
| `user_food_name` | `TEXT` | Name the user uses when logging |
| `grams` | `INTEGER` | Standard portion size in grams |
| `protein_per_100_g` | `REAL` | Protein per 100 g |
| | | Unique on `(user_id, user_food_name)` |

### `logs`

Individual intake entries (meals / quick protein adds).

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `SERIAL` | Primary key |
| `user_id` | `BIGINT` | Telegram user id |
| `date` | `DATE` | Day the entry applies to |
| `food_name` | `TEXT` | Food name (or `-` for `/quicklog`) |
| `grams` | `INTEGER` | Portion size logged |
| `protein_g` | `REAL` | Protein grams for that entry |

### `usda_foods`

Reference table of USDA foundation foods used when adding standards via search.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `SERIAL` | Primary key |
| `fdc_id` | `BIGINT` | USDA FDC id |
| `food_name` | `TEXT` | Food description |
| `protein_per_100g` | `REAL` | Protein per 100 g |

Seeded from `usda_protein_foundation.csv` when the table is empty.

## Bot commands

| Command | Description |
| --- | --- |
| `/start` | Welcome message, command overview, and location prompt for timezone |
| `/setlocation` | Share location again to update timezone |
| `/target` | Set daily protein target (grams) |
| `/addfood` | Save a food with portion size and protein (USDA match or manual) |
| `/myfoods` | List saved foods |
| `/deletefood` | Remove a saved food |
| `/find` | Search saved foods by name |
| `/editprotein` | Edit protein content of a saved food |
| `/log` | Log foods eaten today (from saved standards) |
| `/quicklog` | Log a protein amount directly (no food entry) |
| `/logyesterday` | Log foods for yesterday |
| `/today` | Today’s protein summary |
| `/summary` | Summary for a specific day |
| `/week` | Protein summary for a specific week |
| `/removelog` | Remove one or more of today’s log entries |

## Reminders

On `/start`, the bot asks the user to share their location (best from the phone app). Coordinates are not stored; they are converted to an IANA timezone (for example `Europe/Berlin`) and saved on `users.timezone`. Skip keeps the default `UTC`. `/setlocation` updates it later.

Each user who has a protein target gets one daily job at **19:00 in their timezone**. If that day’s total (user’s local date) is already at or above the target, no message is sent. `targets.last_reminder_date` prevents a second send on the same local day.

## Project layout

```
bot.py                      # Entry point, handler registration, polling
logging_config.py           # Console + rotating users.log
database.py                 # Pool, schema, CRUD
jobs.py                     # Per-user 19:00 local reminder jobs
nutrition.py                # Placeholder for future food-lookup helpers
usda_protein_foundation.csv # USDA seed data
handlers/
  setup.py                  # /start, /setlocation, /target, /addfood, /myfoods
  bot_logger.py             # /log, /quicklog
  foods.py                  # /find, /deletefood, /editprotein
  summary.py                # /today, /removelog
  diff_days.py              # /summary, /week, /logyesterday
```

## Setup

1. Create a Telegram bot with [@BotFather](https://t.me/BotFather) and copy the token.
2. Provision a PostgreSQL database and copy its connection URL.
3. Copy environment variables into a `.env` file in the project root:

   ```env
   BOT_TOKEN=your_telegram_bot_token
   DATABASE_URL=postgresql://user:password@host:5432/dbname
   ```

4. Install dependencies and run:

   ```bash
   pip install -r requirements.txt
   pip install asyncpg   # used by database.py
   python bot.py
   ```

Tables are created automatically on first run.

## Logs

A rotating log file is written in the project root (kept ~7 days; not committed to git):

| File | Contents |
| --- | --- |
| `users.log` | User-driven actions and reminder send/failure lines (includes `user_id`) |

Console output continues as usual. Example:

```bash
grep 'user_id=123' users.log
```
