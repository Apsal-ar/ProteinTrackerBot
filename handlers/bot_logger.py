"""
/log, /quicklog — food logging handlers
"""

import logging

from telegram import Update
from telegram.ext import ContextTypes

import database

logger = logging.getLogger("protein_tracker.users")

FOOD_NAME_QUICKLOG = "-"


def _parse_log_item(item: str) -> tuple[float, str]:
    """
    Parse a log item like "1.5 milk" or "chicken".
    Returns (multiplier, food_name). Multiplier defaults to 1.0 if not specified.
    """
    item = item.strip()
    if not item:
        return 1.0, ""

    parts = item.split(None, 1)
    if len(parts) >= 2:
        try:
            mult = float(parts[0])
            if mult > 0:
                return mult, parts[1].strip()
        except ValueError:
            pass
    return 1.0, item


async def log_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle /log: log foods from standards to today's log.
    Usage: /log chicken, rice  or  /log 1.5 milk, chicken, 1.65 egg
    Optional multiplier (e.g. 1.5) overrides standard portion size. No multiplier = standard portion.
    """
    user_id = update.effective_user.id
    raw_text = update.message.text or ""
    args = raw_text[len("/log") :].strip()
    logger.info("log_handler received from user_id=%s, raw_text=%r, args=%r", user_id, raw_text, args)

    if not args:
        await update.message.reply_text(
            "Enter foods to log, separated by commas.\nOptional: prefix with multiplier (e.g. 1.5 milk). The multiplier will be applied to the standard portion size.\n"
            "Examples:\n/log chicken, rice\n/log 1.5 milk, chicken, 0.5 buscuits"
        )
        return

    items = [item.strip() for item in args.split(",") if item.strip()]
    if not items:
        await update.message.reply_text("No foods entered. Example: /log chicken, rice")
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
            await database.add_log(user_id, stored_name, actual_grams, protein_g)
            if multiplier != 1.0:
                logged.append(f"{stored_name} ({actual_grams}g = {multiplier}×{grams}g, {protein_g}g protein)")
            else:
                logged.append(f"{stored_name} ({actual_grams}g, {protein_g}g protein)")
            total_protein += protein_g
            logger.info("log_handler logged %s: %s×%sg=%sg, %sg protein for user %s", food_name, multiplier, grams, actual_grams, protein_g, user_id)
        else:
            missing.append(food_name)
            logger.info("log_handler food %r not found for user %s", food_name, user_id)

    if logged:
        reply = f"Logged:\n" + "\n".join(f"• {e}" for e in logged)
        reply += f"\n\nTotal: {total_protein:.1f}g protein"
    else:
        reply = "No foods were logged."

    if missing:
        reply += f"\n\nNot found in your food database: {', '.join(m.capitalize() for m in missing)}\nAdd them with /addfood\nRemember to separate items by a comma, such as: /log chicken, rice"

    await update.message.reply_text(reply)

    # Send congratulations if user just reached their protein target for today
    if logged:
        target = await database.get_target(user_id)
        if target is not None:
            today_total = await database.get_today_total(user_id)
            total_before = today_total - total_protein
            target_float = float(target)
            if total_before < target_float <= today_total:
                await update.message.reply_text(
                    "🎉 You reached your protein target for today! Keep up the good work!"
                )
                logger.info("log_handler target reached for user %s: %sg total, target %sg", user_id, today_total, target)


async def quicklog_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle /quicklog: add a quantity of protein directly without adding food to standards.
    Usage: /quicklog 30  or  /quicklog 15.5
    Saves to logs table with food_name "-".
    """
    user_id = update.effective_user.id
    raw_text = update.message.text or ""
    args = raw_text[len("/quicklog") :].strip()
    logger.info("quicklog_handler received from user_id=%s, args=%r", user_id, args)

    if not args:
        await update.message.reply_text(
            "Enter the amount of protein in grams.\nExamples: /quicklog 30  or  /quicklog 15.5"
        )
        return

    try:
        protein_g = float(args)
    except ValueError:
        await update.message.reply_text("Please enter a number. Example: /quicklog 30")
        return

    if protein_g <= 0:
        await update.message.reply_text("Please enter a positive number. Example: /quicklog 30")
        return

    await database.add_log(user_id, FOOD_NAME_QUICKLOG, 0, protein_g)
    today_total = await database.get_today_total(user_id)
    await update.message.reply_text(
        f"Logged {protein_g}g protein. Today's total: {today_total:.1f}g"
    )
    logger.info("quicklog_handler logged %sg protein for user %s", protein_g, user_id)

    target = await database.get_target(user_id)
    if target is not None:
        total_before = today_total - protein_g
        target_float = float(target)
        if total_before < target_float <= today_total:
            await update.message.reply_text(
                "🎉 You reached your protein target for today! Keep up the good work!"
            )
