"""
/summary — show today's log or a chosen date's log (same format as /today).
Accepts: /summary (today), /summary 12.02, /summary 12.02.2025, /summary 12.02.25
/week — show weekly protein totals (Mon–Sun).
Accepts: /week (current week), /week 15.01, /week 15.01.25
/logyesterday — log foods for yesterday (same format as /log)
"""

import logging
import re
from datetime import date, timedelta

from telegram import Update
from telegram.ext import ContextTypes

import database
from handlers.bot_logger import _parse_log_item

logger = logging.getLogger("protein_tracker.users")

WEEKDAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

# Matches DD.MM, DD.MM.YYYY, DD.MM.YY (day and month required)
SUMMARY_DATE_RE = re.compile(
    r"^(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?$"
)

def _monday_of_week(d: date) -> date:
    """Return Monday of the week containing d (week = Mon–Sun)."""
    return d - timedelta(days=d.weekday())


def parse_summary_date(text: str, default_year: int) -> date | None:
    """
    Parse date string: 12.02, 12.02.2025, 12.02.25.
    Returns date or None if invalid.
    """
    text = text.strip()
    m = SUMMARY_DATE_RE.match(text)
    if not m:
        return None
    day = int(m.group(1))
    month = int(m.group(2))
    year_str = m.group(3)
    if year_str:
        year = int(year_str)
        if year < 100:
            year += 2000 if year < 50 else 1900
    else:
        year = default_year
    try:
        return date(year, month, day)
    except ValueError:
        return None


async def summary_week_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE, anchor_date: date
) -> None:
    """Show protein totals per day for Mon–Sun of the week containing anchor_date."""
    user_id = update.effective_user.id
    target = await database.get_target(user_id)
    if target is None:
        await update.message.reply_text(
            "Set your daily protein target first with /target.\n"
            "Example: /target 150"
        )
        return

    monday = _monday_of_week(anchor_date)
    lines = []
    target_float = float(target)
    all_reached = True

    for i in range(7):
        d = monday + timedelta(days=i)
        total = await database.get_total_for_date(user_id, d)
        day_name = WEEKDAY_NAMES[i]
        date_str = d.strftime("%d.%m")
        lines.append(f"{day_name} {date_str}: {total:.1f}g")
        if total < target_float:
            all_reached = False

    sunday = monday + timedelta(days=6)
    date_range = f"{monday.strftime('%d.%m')}–{sunday.strftime('%d.%m.%Y')}"
    reply = f"WEEK SUMMARY ({date_range})\n\n" + "\n".join(lines)
    if all_reached:
        reply += "\n\n🎉 Congratulations! You reached your protein target every day this week!"
    await update.message.reply_text(reply)
    logger.info("summary_week sent for user %s, week of %s", user_id, monday)


async def week_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle /week: show weekly protein totals (Mon–Sun).
    Usage: /week (current week) or /week 15.01 or /week 15.01.25
    """
    user_id = update.effective_user.id
    raw_text = update.message.text or ""
    args = raw_text[len("/week") :].strip()
    logger.info("week_handler received from user_id=%s, args=%r", user_id, args)

    if not args:
        anchor_date = date.today()
    else:
        anchor_date = parse_summary_date(args, date.today().year)
        if anchor_date is None:
            await update.message.reply_text(
                "Invalid date. Use DD.MM, DD.MM.YYYY, or DD.MM.YY.\n"
                "Example: /week 15.01"
            )
            return

    await summary_week_handler(update, context, anchor_date)


async def summary_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle /summary: show today's log, or /summary DD.MM for a chosen date.
    Same format as /today.
    """
    user_id = update.effective_user.id
    logger.info("summary_handler received from user_id=%s", user_id)

    text = update.message.text or ""
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        chosen_date = date.today()
    else:
        arg = parts[1].strip()
        chosen_date = parse_summary_date(arg, date.today().year)
        if chosen_date is None:
            await update.message.reply_text(
                "Invalid date. Use DD.MM, DD.MM.YYYY, or DD.MM.YY.\n"
                "Examples:\n/summary (today)\n/summary 12.02\n/summary 12.02.2025"
            )
            return

    logs = await database.get_logs_for_date(user_id, chosen_date)
    total = await database.get_total_for_date(user_id, chosen_date)
    target = await database.get_target(user_id)

    if target is None:
        await update.message.reply_text(
            "Set your daily protein target first with /target.\n"
            "Example: /target 150"
        )
        return

    date_str = chosen_date.strftime("%d.%m.%Y")
    if not logs:
        await update.message.reply_text(
            f"No foods logged on {date_str}."
        )
        logger.info("summary_handler no logs for user %s on %s", user_id, date_str)
        return

    lines = [f"• {food_name.capitalize()}: {grams:.1f}g, {protein_g:.1f}g protein" for food_name, grams, protein_g in logs]
    reply = f"LOG FOR {date_str}:\n" + "\n".join(lines)
    reply += f"\n\nTOTAL PROTEIN CONSUMED: {total:.1f}g\nPROTEIN TARGET: {target}g"

    if total >= float(target):
        reply += "\n\n✅ You reached your protein target!"
    else:
        remaining = float(target) - total
        reply += f"\n\n{remaining:.1f}g remaining to reach your goal."

    await update.message.reply_text(reply)
    logger.info("summary_handler sent summary for user %s on %s", user_id, date_str)


async def yesterday_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle /logyesterday: log foods for yesterday. Same format as /log.
    Usage: /logyesterday chicken, rice  or  /logyesterday 1.5 milk, chicken
    """
    user_id = update.effective_user.id
    raw_text = update.message.text or ""
    args = raw_text[len("/logyesterday") :].strip()
    logger.info("yesterday_handler received from user_id=%s, args=%r", user_id, args)

    yesterday_date = date.today() - timedelta(days=1)

    if not args:
        await update.message.reply_text(
            "Enter foods to log for yesterday, separated by commas.\n"
            "Optional: prefix with multiplier (e.g. 1.5 milk).\n"
            "Examples:\n/logyesterday chicken, rice\n/logyesterday 1.5 milk, chicken"
        )
        return

    items = [item.strip() for item in args.split(",") if item.strip()]
    if not items:
        await update.message.reply_text("No foods entered. Example: /logyesterday chicken, rice")
        return

    parsed = [_parse_log_item(item) for item in items]
    parsed = [(m, f) for m, f in parsed if f]

    logged = []
    missing = []
    total_protein = 0.0

    for multiplier, food_name in parsed:
        row = await database.get_standard_full(user_id, food_name)
        if row:
            grams, protein_per_100g, stored_name = row
            actual_grams = int(round(grams * multiplier, 0))
            protein_g = round(actual_grams * (protein_per_100g / 100.0), 2)
            await database.add_log_for_date(user_id, stored_name, actual_grams, protein_g, yesterday_date)
            if multiplier != 1.0:
                logged.append(f"{stored_name} ({actual_grams}g = {multiplier}×{grams}g, {protein_g}g protein)")
            else:
                logged.append(f"{stored_name} ({actual_grams}g, {protein_g}g protein)")
            total_protein += protein_g
            logger.info("yesterday_handler logged %s for %s: %s×%sg=%sg, %sg protein for user %s",
                        yesterday_date, food_name, multiplier, grams, actual_grams, protein_g, user_id)
        else:
            missing.append(food_name)
            logger.info("yesterday_handler food %r not found for user %s", food_name, user_id)

    if logged:
        reply = f"Logged for yesterday ({yesterday_date.strftime('%d.%m.%Y')}):\n" + "\n".join(f"• {e}" for e in logged)
        reply += f"\n\nTotal: {total_protein:.1f}g protein"
    else:
        reply = "No foods were logged."

    if missing:
        reply += f"\n\nNot found in your standards: {', '.join(m.capitalize() for m in missing)}\nAdd them with /addfood"

    await update.message.reply_text(reply)
