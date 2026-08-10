"""
Scheduled jobs for the bot.
"""

import logging
from datetime import date, datetime, timezone

from telegram.ext import ContextTypes

import database

logger = logging.getLogger("protein_tracker.reminders")

REMINDER_HOUR = 19  # 19:00 UTC


async def send_reminders_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Run every hour: at 19:00 UTC, for each user with a target, send a reminder
    once per day if they haven't met today's target.
    """
    logger.info("send_reminders_job started")
    now = datetime.now(timezone.utc)
    if now.hour != REMINDER_HOUR:
        logger.info("send_reminders_job skipped (not 19:00 UTC)")
        return
    user_ids = await database.get_user_ids_with_targets()
    today = date.today()
    sent = 0
    errors = 0
    for user_id in user_ids:
        try:
            last = await database.get_last_reminder_date(user_id)
            if last is not None and last >= today:
                continue
            target = await database.get_target(user_id)
            if target is None:
                continue
            total = await database.get_today_total(user_id)
            if total >= float(target):
                continue
            remaining = float(target) - total
            message = (
                f"Reminder: you haven't reached your protein target today. "
                f"{remaining:.1f}g still to go (current: {total:.1f}g / {target}g). "
                f"Use /log to add foods."
            )
            await context.bot.send_message(chat_id=user_id, text=message)
            await database.update_last_reminder_date(user_id, today)
            sent += 1
            logger.info("send_reminders_job sent reminder to user_id=%s", user_id)
        except Exception as e:
            errors += 1
            logger.warning("send_reminders_job failed for user_id=%s: %s", user_id, e)
    logger.info("send_reminders_job finished: sent=%d, errors=%d, users_checked=%d", sent, errors, len(user_ids))
