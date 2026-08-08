"""
/deletefood, /editprotein, /find — food handlers
"""

import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

import database

logger = logging.getLogger(__name__)

KEY_DELETEFOOD_PENDING = "deletefood_pending"
KEY_DELETEFOOD_CONFIRM = "deletefood_confirm"
DELETEFOOD_CB_PREFIX = "delfood|"

KEY_EDITPROTEIN_PENDING = "editprotein_pending"  # {"user_food_name": str}
EDITPROTEIN_CB_PREFIX = "editprot|"
EDITPROTEIN_PAGE_SIZE = 5


def _round_protein(val: float) -> float:
    """Round protein_per_100g to max 2 decimal places."""
    return round(val, 2)


async def find_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle /find: search standards by partial name to help user recall exact food names.
    Usage: /find chicken — returns all standards containing "chicken" (e.g. chicken breast, cooked chicken).
    """
    user_id = update.effective_user.id
    raw_text = update.message.text or ""
    args = raw_text[len("/find") :].strip()
    logger.info("find_handler received from user_id=%s, args=%r", user_id, args)

    if not args:
        await update.message.reply_text(
            "Enter a search term to find foods in your standards.\n"
            "Example: /find chicken — finds chicken breast, cooked chicken, etc."
        )
        return

    matches = await database.search_standards(user_id, args)
    if not matches:
        await update.message.reply_text(f"No foods containing '{args}' found in your standards.")
        logger.info("find_handler no matches for %r (user %s)", args, user_id)
        return

    lines = []
    for food_name, grams, protein in matches:
        protein_str = f"{_round_protein(protein)}g/100g" if protein is not None else "not set"
        lines.append(f"• {food_name.capitalize()} — {grams}g, {protein_str} protein")
    reply = f"Found {len(matches)} matching food(s):\n\n" + "\n".join(lines)
    await update.message.reply_text(reply)
    logger.info("find_handler found %d matches for %r (user %s)", len(matches), args, user_id)


async def deletefood_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle /deletefood: delete a food from the user's standards.
    Usage: /deletefood (prompt) or /deletefood chicken
    """
    user_id = update.effective_user.id
    raw_text = update.message.text or ""
    args = raw_text[len("/deletefood") :].strip()
    logger.info("deletefood_handler received from user_id=%s, raw_text=%r, args=%r", user_id, raw_text, args)

    if not args:
        context.user_data[KEY_DELETEFOOD_PENDING] = True
        await update.message.reply_text("Enter the food you want to delete from your foods database:")
        logger.info("deletefood_handler prompting for food name for user %s", user_id)
        return

    await _process_deletefood_request(update, context, user_id, args)


async def _process_deletefood_request(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    food_name: str,
) -> None:
    """Look up food in standards, show confirmation if found."""
    food_name = food_name.strip()
    if not food_name:
        await update.message.reply_text("Please enter a food name.")
        return

    result = await database.get_standard(user_id, food_name)
    if result is None:
        await update.message.reply_text(f"'{food_name.capitalize()}' not found in your foods database.")
        logger.info("deletefood food %r not found for user %s", food_name, user_id)
        return

    grams, stored_name = result
    context.user_data[KEY_DELETEFOOD_CONFIRM] = stored_name
    keyboard = [
        [
            InlineKeyboardButton("Yes", callback_data=f"{DELETEFOOD_CB_PREFIX}yes|{stored_name}"),
            InlineKeyboardButton("No", callback_data=f"{DELETEFOOD_CB_PREFIX}no|{stored_name}"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        f"Delete '{stored_name}' ({grams}g) from your food database?",
        reply_markup=reply_markup,
    )
    logger.info("deletefood asking confirmation for %r (user %s)", food_name, user_id)


async def deletefood_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle Yes/No button press for deletefood confirmation."""
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    data = query.data
    if not data or not data.startswith(DELETEFOOD_CB_PREFIX):
        return

    payload = data[len(DELETEFOOD_CB_PREFIX) :]
    parts = payload.split("|", 1)
    if len(parts) < 2:
        return

    choice = parts[0].strip().lower()
    food_name = parts[1].strip()

    if choice == "yes":
        await database.delete_standard(user_id, food_name)
        if KEY_DELETEFOOD_CONFIRM in context.user_data:
            del context.user_data[KEY_DELETEFOOD_CONFIRM]
        await query.edit_message_text(f"Deleted '{food_name}' from your food database.")
        logger.info("deletefood_callback deleted %r for user %s", food_name, user_id)
    else:
        if KEY_DELETEFOOD_CONFIRM in context.user_data:
            del context.user_data[KEY_DELETEFOOD_CONFIRM]
        await query.edit_message_text("Request cancelled.")
        logger.info("deletefood_callback cancelled for user %s", user_id)


async def deletefood_pending_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Handle follow-up message when user was prompted to enter food name for /deletefood.
    Returns True if the message was handled.
    """
    if not context.user_data.get(KEY_DELETEFOOD_PENDING):
        return False

    user_id = update.effective_user.id
    text = (update.message.text or "").strip()
    if not text:
        return False

    del context.user_data[KEY_DELETEFOOD_PENDING]
    await _process_deletefood_request(update, context, user_id, text)
    return True


# --- /editprotein ---


def _build_editprotein_keyboard(
    standards: list[tuple[str, int, float | None]], page: int
) -> InlineKeyboardMarkup:
    """Build paginated keyboard with 5 standards per page. Uses index in callback to avoid length limit."""
    total_pages = max(1, (len(standards) + EDITPROTEIN_PAGE_SIZE - 1) // EDITPROTEIN_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    start = page * EDITPROTEIN_PAGE_SIZE
    page_items = list(enumerate(standards[start : start + EDITPROTEIN_PAGE_SIZE], start=start))

    keyboard = []
    for idx, (user_food_name, grams, protein) in page_items:
        label = f"{user_food_name} ({grams}g"
        if protein is not None:
            label += f", {_round_protein(protein)}g/100g"
        label += ")"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"{EDITPROTEIN_CB_PREFIX}sel|{idx}")])

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"{EDITPROTEIN_CB_PREFIX}page|{page - 1}"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"{EDITPROTEIN_CB_PREFIX}page|{page + 1}"))
    if nav_row:
        keyboard.append(nav_row)

    return InlineKeyboardMarkup(keyboard)


async def editprotein_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle /editprotein: edit protein content of a standard.
    Usage: /editprotein (shows list) or /editprotein chicken (direct)
    """
    user_id = update.effective_user.id
    raw_text = update.message.text or ""
    args = raw_text[len("/editprotein") :].strip()
    logger.info("editprotein_handler received from user_id=%s, args=%r", user_id, args)

    if args:
        row = await database.get_standard_for_edit(user_id, args)
        if row is None:
            await update.message.reply_text(f"'{args.capitalize()}' not found in your standards.")
            return
        grams, protein_per_100g, stored_name = row
        context.user_data[KEY_EDITPROTEIN_PENDING] = {"user_food_name": stored_name}
        current = f"{_round_protein(protein_per_100g)}g/100g" if protein_per_100g is not None else "not set"
        await update.message.reply_text(
            f"Enter new protein content (g per 100g) for {stored_name}:\n"
            f"Current: {current}"
        )
        return

    standards = await database.get_all_standards_full(user_id)
    if not standards:
        await update.message.reply_text("You have no standard portions. Add some with /addfood")
        return

    reply_markup = _build_editprotein_keyboard(standards, 0)
    await update.message.reply_text("Select a food to edit protein content:", reply_markup=reply_markup)
    logger.info("editprotein showed list for user %s", user_id)


async def editprotein_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle button press for /editprotein: page nav or food selection."""
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    data = query.data
    if not data or not data.startswith(EDITPROTEIN_CB_PREFIX):
        return

    payload = data[len(EDITPROTEIN_CB_PREFIX) :]
    if payload.startswith("page|"):
        try:
            page = int(payload.split("|", 1)[1])
        except (ValueError, IndexError):
            return
        standards = await database.get_all_standards_full(user_id)
        if not standards:
            await query.edit_message_text("You have no standard portions.")
            return
        reply_markup = _build_editprotein_keyboard(standards, page)
        await query.edit_message_text("Select a food to edit protein content:", reply_markup=reply_markup)
        return

    if not payload.startswith("sel|"):
        return
    try:
        idx = int(payload.split("|", 1)[1])
    except (ValueError, IndexError):
        return
    standards = await database.get_all_standards_full(user_id)
    if idx < 0 or idx >= len(standards):
        await query.edit_message_text("Invalid selection.")
        return
    stored_name, grams, protein_per_100g = standards[idx]

    context.user_data[KEY_EDITPROTEIN_PENDING] = {"user_food_name": stored_name}
    current = f"{_round_protein(protein_per_100g)}g/100g" if protein_per_100g is not None else "not set"
    await query.edit_message_text(
        f"Enter new protein content (g per 100g) for {stored_name}:\n"
        f"Current: {current}"
    )
    logger.info("editprotein prompting for new protein: %s (user %s)", stored_name, user_id)


async def editprotein_pending_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Handle follow-up message when user enters new protein value for /editprotein."""
    pending = context.user_data.get(KEY_EDITPROTEIN_PENDING)
    if not pending:
        return False

    user_id = update.effective_user.id
    text = (update.message.text or "").strip()
    if not text:
        return False

    try:
        protein_per_100g = _round_protein(float(text))
        if protein_per_100g < 0 or protein_per_100g > 100:
            await update.message.reply_text("Please enter a number between 0 and 100.")
            return True
    except ValueError:
        await update.message.reply_text("Please enter a number (e.g. 31 or 18.5).")
        return True

    del context.user_data[KEY_EDITPROTEIN_PENDING]
    stored_name = pending["user_food_name"]
    updated = await database.update_standard_protein(user_id, stored_name, protein_per_100g)
    if updated:
        await update.message.reply_text(f"Updated {stored_name}: {_round_protein(protein_per_100g)}g protein per 100g.")
        logger.info("editprotein updated %s to %sg/100g for user %s", stored_name, protein_per_100g, user_id)
    else:
        await update.message.reply_text(f"Could not update '{stored_name}'.")
    return True
