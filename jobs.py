"""
Per-user daily reminder jobs at 19:00 local time (opt-in only).
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


def unschedule_user_reminder(job_queue: JobQueue | None, user_id: int) -> None:
    """Remove any daily reminder job for this user."""
    if job_queue is None:
        return
    name = reminder_job_name(user_id)
    for job in job_queue.get_jobs_by_name(name):
        job.schedule_removal()
    logger.info("Unscheduled reminder for user_id=%s", user_id)


def schedule_user_reminder(job_queue: JobQueue | None, user_id: int, tz_name: str | None) -> None:
    """Replace any existing daily reminder job for this user with one at 19:00 in tz_name."""
    if job_queue is None or not tz_name:
        return
    name = reminder_job_name(user_id)
    for job in job_queue.get_jobs_by_name(name):
        job.schedule_removal()
    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, KeyError, ValueError):
        logger.warning(
            "Invalid timezone %r for user_id=%s; skipping reminder schedule",
            tz_name,
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
    logger.info("Scheduled 19:00 %s reminder for user_id=%s", tz_name, user_id)


async def schedule_user_reminder_if_enabled(job_queue: JobQueue | None, user_id: int) -> None:
    """Schedule a daily reminder if the user opted in, has a timezone, and has a target."""
    if job_queue is None:
        return
    if not await database.get_reminders_enabled(user_id):
        unschedule_user_reminder(job_queue, user_id)
        return
    target = await database.get_target(user_id)
    tz_name = await database.get_timezone(user_id)
    if target is None or not tz_name:
        unschedule_user_reminder(job_queue, user_id)
        return
    schedule_user_reminder(job_queue, user_id, tz_name)


async def schedule_all_user_reminders(job_queue: JobQueue | None) -> None:
    """Schedule daily reminders for every opted-in user with a target (called on startup)."""
    if job_queue is None:
        return
    users = await database.get_users_with_reminders_enabled()
    for user_id, tz_name in users:
        schedule_user_reminder(job_queue, user_id, tz_name)


async def send_user_reminder(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Send a reminder to one user at 19:00 local time if they have opted in,
    have a target, and have not met it for their local today.
    """
    job = context.job
    if job is None:
        return
    user_id = job.data if job.data is not None else job.chat_id
    if user_id is None:
        return
    try:
        if not await database.get_reminders_enabled(user_id):
            return
        target = await database.get_target(user_id)
        if target is None:
            return
        tz_name = await database.get_timezone(user_id)
        if not tz_name:
            return
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
