"""
Entry point: registers all handlers and runs the bot.
"""

import logging
import os

from dotenv import load_dotenv
from telegram.ext import ApplicationBuilder, CallbackQueryHandler, CommandHandler, MessageHandler, filters

import database
import jobs
from logging_config import setup_logging
from handlers.bot_logger import log_handler, quicklog_handler
from handlers.foods import (
    deletefood_callback_handler,
    deletefood_handler,
    deletefood_pending_message_handler,
    editprotein_callback_handler,
    editprotein_handler,
    editprotein_pending_message_handler,
    find_handler,
)
from handlers.diff_days import summary_handler, week_handler, yesterday_handler
from handlers.summary import removelog_callback_handler, removelog_handler, today_handler
from handlers.setup import (
    start_handler,
    standard_callback_handler,
    standard_handler,
    standard_pending_message_handler,
    standards_callback_handler,
    standards_handler,
    target_handler,
)

load_dotenv()
setup_logging()
logger = logging.getLogger(__name__)


async def post_init(application):
    """Initialize database connection pool, create tables, and schedule jobs."""
    await database.init_db()
    if application.job_queue is not None:
        application.job_queue.run_repeating(jobs.send_reminders_job, interval=3600, first=60)
        logger.info("Scheduled hourly reminder job (19:00 UTC, once per day if target not met)")
    else:
        logger.warning(
            "Job queue is not available. Install the optional dependency: "
            'pip install "python-telegram-bot[job-queue]==20.7"  (then restart). Reminders will not run.'
        )


async def post_shutdown(application):
    """Close database connection pool on shutdown."""
    await database.close_db()


async def error_handler(update, context):
    """Log full exception traceback."""
    logger.error(
        "Error in handler: %s",
        context.error,
        exc_info=(type(context.error), context.error, context.error.__traceback__),
    )


async def catch_all_handler(update, context):
    """Handle pending standard flow or log every incoming update."""
    if update.message and await standard_pending_message_handler(update, context):
        return
    if update.message and await deletefood_pending_message_handler(update, context):
        return
    if update.message and await editprotein_pending_message_handler(update, context):
        return
    logger.info("catch_all update_id=%s, update=%r", update.update_id, update)


def main():
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise ValueError("BOT_TOKEN not found in environment")
    logger.info("BOT_TOKEN loaded (len=%d)", len(token))

    application = (
        ApplicationBuilder()
        .token(token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    application.add_handler(CommandHandler("start", start_handler))
    application.add_handler(CommandHandler("target", target_handler))
    application.add_handler(CommandHandler("addfood", standard_handler))
    application.add_handler(CommandHandler("myfoods", standards_handler))
    application.add_handler(CommandHandler("log", log_handler))
    application.add_handler(CommandHandler("quicklog", quicklog_handler))
    application.add_handler(CommandHandler("logyesterday", yesterday_handler))
    application.add_handler(CommandHandler("today", today_handler))
    application.add_handler(CommandHandler("summary", summary_handler))
    application.add_handler(CommandHandler("week", week_handler))
    application.add_handler(CommandHandler("removelog", removelog_handler))
    application.add_handler(CommandHandler("deletefood", deletefood_handler))
    application.add_handler(CommandHandler("editprotein", editprotein_handler))
    application.add_handler(CommandHandler("find", find_handler))
    application.add_handler(CallbackQueryHandler(standard_callback_handler, pattern="^std\|"))
    application.add_handler(CallbackQueryHandler(standards_callback_handler, pattern="^myfoods\|"))
    application.add_handler(CallbackQueryHandler(deletefood_callback_handler, pattern="^delfood\|"))
    application.add_handler(CallbackQueryHandler(editprotein_callback_handler, pattern="^editprot\|"))
    application.add_handler(CallbackQueryHandler(removelog_callback_handler, pattern="^removelog\|"))
    application.add_handler(MessageHandler(filters.ALL, catch_all_handler))
    application.add_error_handler(error_handler)
    logger.info("Handlers registered: start, target, addfood, myfoods, log, quicklog, logyesterday, today, summary, week, removelog, deletefood, editprotein, find, catch_all, error_handler")

    application.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
