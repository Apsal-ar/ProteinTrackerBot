"""
/today, /removelog — summary handlers
"""

import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

import database

logger = logging.getLogger("protein_tracker.users")

REMOVELOG_CB_PREFIX = "removelog|"


async def today_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle /today: show today's logged foods, their protein, total vs goal, and target reached message.
    """
    user_id = update.effective_user.id
    logger.info("today_handler received from user_id=%s", user_id)

    logs = await database.get_today_logs(user_id)
    total = await database.get_today_total(user_id)
    target = await database.get_target(user_id)

    if target is None:
        await update.message.reply_text(
            "Set your daily protein target first with /target.\n"
            "Example: /target 150"
        )
        logger.info("today_handler no target set for user %s", user_id)
        return

    if not logs:
        await update.message.reply_text(
            "No foods logged today.\n"
            "Use /log to add foods."
        )
        logger.info("today_handler no logs for user %s", user_id)
        return

    lines = [f"• {food_name.capitalize()}: {grams:.1f}g, {protein_g:.1f}g protein" for food_name, grams, protein_g in logs]
    reply = "TODAY'S LOG:\n" + "\n".join(lines)

    reply += f"\n\nTOTAL PROTEIN CONSUMED: {total:.1f}g\nPROTEIN TARGET: {target}g"

    if total >= float(target):
        reply += "\n\n✅ You reached your protein target!"
        logger.info("today_handler target reached for user %s: %sg / %sg", user_id, total, target)
    else:
        remaining = float(target) - total
        reply += f"\n\n{remaining:.1f}g remaining to reach your goal."
        logger.info("today_handler summary for user %s: %sg / %sg", user_id, total, target)

    await update.message.reply_text(reply)


async def removelog_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle /removelog: show today's logs as buttons or remove directly by name.
    Usage: /removelog (shows all as buttons) or /removelog chicken (finds matching entry, asks to confirm)
    """
    user_id = update.effective_user.id
    raw_text = update.message.text or ""
    args = raw_text[len("/removelog") :].strip()
    logger.info("removelog_handler received from user_id=%s, args=%r", user_id, args)

    if args:
        logs = await database.get_today_logs_matching(user_id, args)
        if not logs:
            await update.message.reply_text(f"No log entry for '{args}' found today.")
            logger.info("removelog_handler no match for %r (user %s)", args, user_id)
            return
        if len(logs) == 1:
            log_id, food_name, grams, protein_g = logs[0]
            label = f"{food_name.capitalize()}: {grams}g, {protein_g:.1f}g protein"
            yes_cb = f"{REMOVELOG_CB_PREFIX}{log_id}|{args}|yes"
            no_cb = f"{REMOVELOG_CB_PREFIX}{log_id}|{args}|no"
            keyboard = [
                [
                    InlineKeyboardButton("Yes", callback_data=yes_cb),
                    InlineKeyboardButton("No", callback_data=no_cb),
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(f"Remove '{label}'?", reply_markup=reply_markup)
            logger.info("removelog_handler showing confirmation for %r (log_id=%s) user %s", args, log_id, user_id)
            return
        # 2+ matches: show buttons to pick (include search_term in callback so we can filter after delete)
    else:
        logs = await database.get_today_logs_with_ids(user_id)
        args = ""

    if not logs:
        await update.message.reply_text("No foods logged today.")
        logger.info("removelog_handler no logs for user %s", user_id)
        return

    keyboard = []
    for log_id, food_name, grams, protein_g in logs:
        label = f"{food_name.capitalize()}: {grams}g, {protein_g:.1f}g protein"
        cb_data = f"{REMOVELOG_CB_PREFIX}{log_id}|{args}" if args else f"{REMOVELOG_CB_PREFIX}{log_id}"
        keyboard.append([InlineKeyboardButton(label, callback_data=cb_data)])
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = "Select a log entry to remove" + (f" (matching '{args}'):" if args else ":")
    await update.message.reply_text(text, reply_markup=reply_markup)
    logger.info("removelog_handler showed %d log options for user %s", len(logs), user_id)


async def removelog_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle button press for /removelog: show confirmation or delete/cancel."""
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    data = query.data
    if not data or not data.startswith(REMOVELOG_CB_PREFIX):
        return

    payload = data[len(REMOVELOG_CB_PREFIX) :]
    parts = payload.split("|")
    if parts[0] == "removed":
        return

    try:
        log_id = int(parts[0])
    except (ValueError, IndexError):
        return

    # Parse search_term and choice: "log_id", "log_id|search_term", "log_id|yes", "log_id|no", "log_id|search_term|yes"
    search_term: str | None = None
    choice: str | None = None
    if len(parts) == 2:
        if parts[1].strip().lower() in ("yes", "no"):
            choice = parts[1].strip().lower()
        else:
            search_term = parts[1]
    elif len(parts) >= 3:
        search_term = parts[1]
        choice = parts[2].strip().lower()

    def _yes_no_buttons() -> tuple[str, str]:
        if search_term:
            return (
                f"{REMOVELOG_CB_PREFIX}{log_id}|{search_term}|yes",
                f"{REMOVELOG_CB_PREFIX}{log_id}|{search_term}|no",
            )
        return (
            f"{REMOVELOG_CB_PREFIX}{log_id}|yes",
            f"{REMOVELOG_CB_PREFIX}{log_id}|no",
        )

    # User clicked a log entry: show confirmation (no choice yet)
    if choice is None:
        row = await database.get_log_by_id(user_id, log_id)
        if not row:
            await query.edit_message_text("That log entry is no longer available.")
            return
        food_name, grams, protein_g = row
        label = f"{food_name.capitalize()}: {grams}g, {protein_g:.1f}g protein"
        yes_cb, no_cb = _yes_no_buttons()
        keyboard = [
            [
                InlineKeyboardButton("Yes", callback_data=yes_cb),
                InlineKeyboardButton("No", callback_data=no_cb),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(f"Delete '{label}'?", reply_markup=reply_markup)
        logger.info("removelog_callback showing confirmation for log_id=%s, user %s", log_id, user_id)
        return

    # User clicked Yes or No
    if choice == "yes":
        logs_before = (
            await database.get_today_logs_matching(user_id, search_term)
            if search_term
            else await database.get_today_logs_with_ids(user_id)
        )
        row = next((r for r in logs_before if r[0] == log_id), None)
        if not row:
            await query.edit_message_text("That log entry is no longer available.")
            return
        _, food_name, _, _ = row
        deleted = await database.delete_log(user_id, log_id)
        if deleted:
            # Refetch remaining logs (filtered by search_term if applicable)
            logs_after = (
                await database.get_today_logs_matching(user_id, search_term)
                if search_term
                else await database.get_today_logs_with_ids(user_id)
            )
            if not logs_after:
                msg = "No more matching entries to remove." if search_term else "No more log entries today."
                await query.edit_message_text(msg)
                return
            keyboard = []
            for lid, name, grams, protein_g in logs_after:
                label = f"{name.capitalize()}: {grams}g, {protein_g:.1f}g protein"
                cb_data = f"{REMOVELOG_CB_PREFIX}{lid}|{search_term}" if search_term else f"{REMOVELOG_CB_PREFIX}{lid}"
                keyboard.append([InlineKeyboardButton(label, callback_data=cb_data)])
            reply_markup = InlineKeyboardMarkup(keyboard)
            text = "Select a log entry to remove" + (f" (matching '{search_term}'):" if search_term else ":")
            await query.edit_message_text(text, reply_markup=reply_markup)
            logger.info("removelog_callback deleted log_id=%s (%s) for user %s", log_id, food_name, user_id)
        else:
            await query.edit_message_text("That log entry could not be removed.")
    elif choice == "no":
        # Go back to the list (filtered if we had a search term)
        logs = (
            await database.get_today_logs_matching(user_id, search_term)
            if search_term
            else await database.get_today_logs_with_ids(user_id)
        )
        if not logs:
            await query.edit_message_text("No matching log entries.")
            return
        keyboard = []
        for lid, name, grams, protein_g in logs:
            label = f"{name.capitalize()}: {grams}g, {protein_g:.1f}g protein"
            cb_data = f"{REMOVELOG_CB_PREFIX}{lid}|{search_term}" if search_term else f"{REMOVELOG_CB_PREFIX}{lid}"
            keyboard.append([InlineKeyboardButton(label, callback_data=cb_data)])
        reply_markup = InlineKeyboardMarkup(keyboard)
        text = "Select a log entry to remove" + (f" (matching '{search_term}'):" if search_term else ":")
        await query.edit_message_text(text, reply_markup=reply_markup)
    else:
        await query.edit_message_text("Cancelled.")
        logger.info("removelog_callback cancelled for user %s", user_id)
