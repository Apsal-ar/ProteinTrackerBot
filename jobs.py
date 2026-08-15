"""
Per-user daily reminder jobs at 19:00 local time.
"""

import logging
from datetime import datetime, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from telegram.ext import ContextTypes, JobQueue

import database

logger = logging.getLogger("protein_tracker.users")

REMINDER_HOUR = 19
REMINDER_JOB_PREFIX = "reminder_"


def reminder_job_name(user_id: int) -> str:
    return f"{REMINDER_JOB_PREFIX}{user_id}"


def schedule_user_reminder(job_queue: JobQueue | None, user_id: int, tz_name: str | None) -> None:
    """Replace any existing daily reminder job for this user with one at 19:00 in tz_name."""
    if job_queue is None:
        return
    name = reminder_job_name(user_id)
    for job in job_queue.get_jobs_by_name(name):
        job.schedule_removal()
    zone_key = tz_name or "UTC"
    try:
        tz = ZoneInfo(zone_key)
    except (ZoneInfoNotFoundError, KeyError, ValueError):
        logger.warning(
            "Invalid timezone %r for user_id=%s; skipping reminder schedule",
            zone_key,
            user_id,
        )
        return
    job_queue.run_daily(
        send_user_reminder,
        time=time(hour=REMINDER_HOUR, minute=0, tzinfo=tz),
        chat_id=user_id,
        name=name,
        data=user_id,
    )
    logger.info("Scheduled 19:00 %s reminder for user_id=%s", zone_key, user_id)


async def schedule_user_reminder_if_target(job_queue: JobQueue | None, user_id: int) -> None:
    """Schedule a daily reminder if this user has a protein target."""
    if job_queue is None:
        return
    target = await database.get_target(user_id)
    if target is None:
        return
    tz_name = await database.get_timezone(user_id)
    schedule_user_reminder(job_queue, user_id, tz_name)


async def schedule_all_user_reminders(job_queue: JobQueue | None) -> None:
    """Schedule daily reminders for every user who has a target (called on startup)."""
    if job_queue is None:
        return
    users = await database.get_users_with_targets()
    for user_id, tz_name in users:
        schedule_user_reminder(job_queue, user_id, tz_name)


async def send_user_reminder(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Send a reminder to one user at 19:00 local time if they have a target
    and have not met it for their local today.
    """
    job = context.job
    if job is None:
        return
    user_id = job.data if job.data is not None else job.chat_id
    if user_id is None:
        return
    try:
        target = await database.get_target(user_id)
        if target is None:
            return
        tz_name = await database.get_timezone(user_id)
        try:
            tz = ZoneInfo(tz_name)
        except (ZoneInfoNotFoundError, KeyError, ValueError):
            logger.warning("Invalid timezone %r for user_id=%s; not sending reminder", tz_name, user_id)
            return
        today = datetime.now(tz).date()
        last = await database.get_last_reminder_date(user_id)
        if last is not None and last >= today:
            return
        total = await database.get_total_for_date(user_id, today)
        if total >= float(target):
            return
        remaining = float(target) - total
        message = (
            f"Reminder: you haven't reached your protein target today. "
            f"{remaining:.1f}g still to go (current: {total:.1f}g / {target}g). "
            f"Use /log to add foods."
        )
        await context.bot.send_message(chat_id=user_id, text=message)
        await database.update_last_reminder_date(user_id, today)
        logger.info("Sent reminder to user_id=%s", user_id)
    except Exception as e:
        logger.warning("Reminder failed for user_id=%s: %s", user_id, e)
