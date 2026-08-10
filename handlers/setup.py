"""
/target, /addfood, /myfoods — setup handlers
"""

import logging
import re

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

import database

logger = logging.getLogger("protein_tracker.users")

# Callback data prefix for standard portion USDA choice
STD_CB_PREFIX = "std|"
PAGE_SIZE = 4
STANDARDS_CB_PREFIX = "myfoods|"
STANDARDS_PAGE_SIZE = 10
MAX_DECIMALS = 2


def _round_protein(val: float) -> float:
    """Round protein_per_100g to max 2 decimal places."""
    return round(val, MAX_DECIMALS)


def _round_grams(val: float | int) -> int:
    """Round grams to max 2 decimal places and return as int for storage."""
    return int(round(float(val), MAX_DECIMALS))
# user_data keys for pending standard flow
KEY_STANDARD_MANUAL = "standard_manual"  # search_term for manual add (portion + protein)
KEY_STANDARD_PENDING_GRAMS = "standard_pending_grams"  # {"user_food_name": str, "protein_per_100g": float}
KEY_STANDARD_MANUAL_PROTEIN = "standard_manual_protein"  # {"user_food_name": str, "grams": int} - waiting for protein per 100g


def _parse_standard_args(args: str) -> tuple[str | None, int | None]:
    """
    Parse '/addfood chicken 200', '/addfood chicken 200g', or '/addfood chicken 200 g' style input.
    Returns (food_name, grams). Returns (None, None) if args empty or invalid.
    """
    args = args.strip()
    if not args:
        return None, None

    parts = args.split()
    grams = None
    food_parts = list(parts)

    # Check if last token is "g" and second-to-last is a number: "chicken 200 g"
    if len(parts) >= 2 and parts[-1].lower() == "g":
        try:
            grams = int(parts[-2])
            food_parts = parts[:-2]
        except ValueError:
            pass
    # Check if last token ends with "g" and prefix is a number: "chicken 200g"
    elif parts[-1].lower().endswith("g"):
        match = re.match(r"^(\d+)\s*g?$", parts[-1], re.IGNORECASE)
        if match:
            grams = int(match.group(1))
            food_parts = parts[:-1]
    # Check if last token is a bare number: "chicken 200"
    elif len(parts) >= 2:
        try:
            grams = int(parts[-1])
            food_parts = parts[:-1]
        except ValueError:
            pass

    food_name = " ".join(food_parts).strip() if food_parts and grams is not None else None
    if food_name and grams is not None:
        return food_name, grams
    return None, None


def _parse_standard_args_full(args: str) -> tuple[str | None, int | None, float | None]:
    """
    Parse '/addfood chicken 200 20' or '/addfood pork loin 150 33.5' style input.
    Returns (food_name, grams, protein_per_100g). All three must be present and valid.
    Supports multi-word food names.
    """
    args = args.strip()
    if not args:
        return None, None, None

    parts = args.split()
    if len(parts) < 3:
        return None, None, None

    try:
        protein_val = _round_protein(float(parts[-1]))
    except ValueError:
        return None, None, None

    grams_str = parts[-2].lower()
    if grams_str.endswith("g"):
        grams_str = grams_str[:-1].strip()
    try:
        grams_val = _round_grams(float(grams_str))
    except ValueError:
        return None, None, None

    if grams_val <= 0 or protein_val < 0:
        return None, None, None

    food_name = " ".join(parts[:-2]).strip()
    if not food_name:
        return None, None, None

    return food_name, grams_val, protein_val


def _parse_standard_args_optional_grams(args: str) -> tuple[str | None, int | None]:
    """
    Parse '/addfood chicken' or '/addfood chicken 200' style input.
    Returns (search_term, grams). grams may be None if not provided.
    """
    args = args.strip()
    if not args:
        return None, None

    parts = args.split()
    grams = None
    food_parts = list(parts)

    if len(parts) >= 2 and parts[-1].lower() == "g":
        try:
            grams = _round_grams(float(parts[-2]))
            food_parts = parts[:-2]
        except ValueError:
            pass
    elif parts[-1].lower().endswith("g"):
        match = re.match(r"^([\d.]+)\s*g?$", parts[-1], re.IGNORECASE)
        if match:
            try:
                grams = _round_grams(float(match.group(1)))
                food_parts = parts[:-1]
            except ValueError:
                pass
    elif len(parts) >= 2:
        try:
            grams = _round_grams(float(parts[-1]))
            food_parts = parts[:-1]
        except ValueError:
            pass

    search_term = " ".join(food_parts).strip() if food_parts else None
    return search_term, grams


def _parse_target_arg(args: str) -> int | None:
    """Parse target grams from args. Returns int or None if invalid."""
    args = args.strip()
    if not args:
        return None
    try:
        val = int(args.split()[0])
        return val if val > 0 else None
    except (ValueError, IndexError):
        return None


async def target_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle /target: set daily protein target in grams.
    Usage: /target (prompt) or /target 150
    """
    user_id = update.effective_user.id
    raw_text = update.message.text or ""
    args = raw_text[len("/target"):].strip()
    logger.info("target_handler received from user_id=%s, raw_text=%r, args=%r", user_id, raw_text, args)

    if not args:
        logger.info("target_handler no args, prompting user")
        await update.message.reply_text(
            "Enter your daily protein target in grams.\nExample: /target 150"
        )
        return

    target = _parse_target_arg(args)
    if target is None:
        logger.info("target_handler parse failed")
        await update.message.reply_text(
            "Please enter a valid number (grams).\nExample: /target 150"
        )
        return

    await database.set_target(user_id, target)
    await update.message.reply_text(f"Daily protein target set to {target}g")
    logger.info("target_handler set target=%sg for user %s", target, user_id)


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start: send welcome message and prompt to set protein target first."""
    user_id = update.effective_user.id
    logger.info("start_handler received /start from user_id=%s", user_id)

    welcome = """Welcome to Protein Tracker! 🥩

First, set your daily protein target in grams. Example:
/target 150

Then use these commands:
/addfood — save a food with portion size and protein content
/myfoods — list your foods
/deletefood — remove a food from standards
/find - search your saved standards by name
/log — log foods you've eaten today
/quicklog — add protein directly (e.g. /quicklog 30)
/logyesterday — log foods you've eaten yesterday
/today — see today's summary
/summary — see summary of all logs for a specific day
/week - see protein summary for a specific week
/removelog — remove one or more log entries from today
/editprotein - edit the protein content of a saved food"""

    await update.message.reply_text(welcome)
    logger.info("start_handler replied to user %s", user_id)


def _truncate_button_label(text: str, max_len: int = 40) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def _build_standard_keyboard(
    matches: list[tuple[int, str, float]],
    page: int,
    grams: int | None,
    search_term: str,
) -> tuple[InlineKeyboardMarkup, str]:
    """Build paginated keyboard for USDA food options. Max PAGE_SIZE options per page."""
    cb_grams = str(grams) if grams is not None else ""
    total_pages = max(1, (len(matches) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    start = page * PAGE_SIZE
    page_matches = matches[start : start + PAGE_SIZE]

    keyboard = []
    for usda_id, food_name, protein_per_100g in page_matches:
        protein_str = f"{_round_protein(protein_per_100g)}g/100g"
        label = _truncate_button_label(food_name, max_len=38) + f" ({protein_str})"
        callback_data = f"{STD_CB_PREFIX}{usda_id}|{cb_grams}|{search_term}"
        keyboard.append([InlineKeyboardButton(label, callback_data=callback_data)])

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ Previous page", callback_data=f"{STD_CB_PREFIX}page|{page - 1}|{cb_grams}|{search_term}"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("Next page ➡️", callback_data=f"{STD_CB_PREFIX}page|{page + 1}|{cb_grams}|{search_term}"))
    if nav_row:
        keyboard.append(nav_row)

    keyboard.append([InlineKeyboardButton("Enter protein per 100g manually", callback_data=f"{STD_CB_PREFIX}manual|{cb_grams}|{search_term}")])
    reply_markup = InlineKeyboardMarkup(keyboard)

    text = "Choose a food:"
    if grams is not None:
        text = f"Choose a food (portion {grams}g will be saved):"
    if total_pages > 1:
        text += f" (page {page + 1}/{total_pages})"
    return reply_markup, text


async def standard_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle /addfood: save a standard portion.
    If the entered name matches USDA foods, show buttons to choose; otherwise prompt for manual add.
    """
    user_id = update.effective_user.id
    raw_text = update.message.text or ""
    args = raw_text[len("/addfood") :].strip()
    logger.info("standard_handler received from user_id=%s, raw_text=%r, args=%r", user_id, raw_text, args)

    if not args:
        logger.info("standard_handler no args, prompting user")
        await update.message.reply_text(
            "Enter food name and optionally portion size (g) and protein content (g/100g food).\n\n"
            "OPTION 1: Fast\nEnter food name, portion size (g), protein content (g/100g food):\n"
            "• /addfood chicken 200 18.5\n"
            "• /addfood pork loin 150 27\n\n"
            "OPTION 2\nEnter food name and optionally portion size, then choose protein content from the database:\n"
            "• /addfood chicken\n"
            "• /addfood chicken 200"
        )
        return

    food_name_full, grams_full, protein_full = _parse_standard_args_full(args)
    if food_name_full is not None and grams_full is not None and protein_full is not None:
        await database.set_standard(user_id, food_name_full, grams_full, protein_per_100g=protein_full)
        await update.message.reply_text(f"Saved: {food_name_full.capitalize()} = {grams_full}g ({protein_full}g protein/100g)")
        logger.info("standard_handler fast save %s=%sg protein=%s for user %s", food_name_full, grams_full, protein_full, user_id)
        return

    search_term, grams = _parse_standard_args_optional_grams(args)
    logger.info("standard_handler parsed: search_term=%r, grams=%s", search_term, grams)
    if not search_term:
        await update.message.reply_text(
            "Use: /addfood <food> [portion] [protein/100g]\n"
            "Examples: /addfood chicken 200 31  or  /addfood chicken  or  /addfood chicken 200"
        )
        return

    matches = await database.search_usda_foods(search_term)
    if matches:
        reply_markup, text = _build_standard_keyboard(matches, 0, grams, search_term)
        await update.message.reply_text(text, reply_markup=reply_markup)
        logger.info("standard_handler showed %d USDA options for user %s", len(matches), user_id)
        return

    context.user_data[KEY_STANDARD_MANUAL] = search_term
    await update.message.reply_text(
        "No matching records. Add this food with standard portion size and protein per 100g.\n"
        "Reply with two numbers, e.g.: 200 31 (200g portion, 31g protein per 100g)"
    )
    logger.info("standard_handler no USDA match, prompted manual add for %r", search_term)


async def standard_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle button press when user selects a USDA food or manual option for /addfood."""
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    data = query.data
    if not data or not data.startswith(STD_CB_PREFIX):
        return
    payload = data[len(STD_CB_PREFIX) :]
    parts = payload.split("|", 2)
    if len(parts) < 2:
        return
    usda_or_manual = parts[0].strip()
    grams_str = parts[1].strip()
    user_food_name = (parts[2].strip() if len(parts) > 2 else "").lower() or ""

    if usda_or_manual == "page":
        parts_full = payload.split("|")
        if len(parts_full) < 4:
            return
        try:
            page_num = int(parts_full[1])
        except ValueError:
            return
        cb_grams = parts_full[2].strip()
        search_term_for_page = (parts_full[3].strip() if len(parts_full) > 3 else "").lower() or ""
        if not search_term_for_page:
            return
        matches = await database.search_usda_foods(search_term_for_page)
        if not matches:
            await query.edit_message_text("No options available.")
            return
        try:
            grams_for_page = _round_grams(float(cb_grams)) if cb_grams else None
        except ValueError:
            grams_for_page = None
        reply_markup, text = _build_standard_keyboard(matches, page_num, grams_for_page, search_term_for_page)
        await query.edit_message_text(text, reply_markup=reply_markup)
        logger.info("standard_callback page nav to page %s for user %s", page_num, user_id)
        return

    if usda_or_manual == "manual":
        try:
            grams_val = _round_grams(float(grams_str)) if grams_str else None
        except ValueError:
            grams_val = None
        context.user_data[KEY_STANDARD_MANUAL_PROTEIN] = {
            "user_food_name": user_food_name or "unknown",
            "grams": grams_val,
        }
        prompt = "Enter protein per 100g (e.g. 25):"
        if grams_str:
            prompt = f"Enter protein per 100g for your {grams_str}g portion (e.g. 25):"
        await query.edit_message_text(prompt)
        logger.info("standard_callback manual protein requested for user_food_name=%s, grams=%s, user %s", user_food_name, grams_str, user_id)
        return

    try:
        usda_id = int(usda_or_manual)
    except ValueError:
        return

    row = await database.get_usda_food_by_id(usda_id)
    if not row:
        await query.edit_message_text("That option is no longer available.")
        return

    usda_food_name, protein_per_100g = row
    if not user_food_name:
        user_food_name = usda_food_name.strip().lower()
    protein_per_100g = _round_protein(protein_per_100g)
    if grams_str:
        grams = _round_grams(float(grams_str))
        await database.set_standard(user_id, user_food_name, grams, protein_per_100g=protein_per_100g)
        await query.edit_message_text(f"Saved: {user_food_name.capitalize()} = {grams}g, {protein_per_100g}g protein/100g")
        logger.info("standard_callback saved %s=%sg (protein=%sg/100g) for user %s", user_food_name, grams, protein_per_100g, user_id)
    else:
        context.user_data[KEY_STANDARD_PENDING_GRAMS] = {"user_food_name": user_food_name, "protein_per_100g": _round_protein(protein_per_100g)}
        await query.edit_message_text("Enter portion size in grams:")
        logger.info("standard_callback waiting for grams (user_food_name=%s) for user %s", user_food_name, user_id)


async def standard_pending_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Handle follow-up message: grams for chosen USDA food, or "grams protein" for manual add.
    Returns True if the message was handled (pending flow), False otherwise.
    """
    user_id = update.effective_user.id
    text = (update.message.text or "").strip()
    if not text:
        return False

    pending = context.user_data.get(KEY_STANDARD_PENDING_GRAMS)
    if pending:
        try:
            grams = _round_grams(float(text.split()[0]))
        except (ValueError, IndexError):
            await update.message.reply_text("Please enter a number (grams).")
            return True
        user_food_name = pending["user_food_name"]
        protein_per_100g = _round_protein(pending["protein_per_100g"])
        del context.user_data[KEY_STANDARD_PENDING_GRAMS]
        await database.set_standard(user_id, user_food_name, grams, protein_per_100g=protein_per_100g)
        await update.message.reply_text(f"Saved: {user_food_name.capitalize()} = {grams}g, {protein_per_100g}g protein/100g")
        logger.info("standard_pending_grams saved %s=%sg (protein=%sg/100g) for user %s", user_food_name, grams, protein_per_100g, user_id)
        return True

    manual_protein = context.user_data.get(KEY_STANDARD_MANUAL_PROTEIN)
    if manual_protein:
        try:
            protein_per_100g = _round_protein(float(text.split()[0]))
        except (ValueError, IndexError):
            await update.message.reply_text("Please enter a number (protein per 100g).")
            return True
        user_food_name = manual_protein["user_food_name"]
        grams = manual_protein.get("grams")
        del context.user_data[KEY_STANDARD_MANUAL_PROTEIN]
        if grams is None:
            await update.message.reply_text("Enter portion size in grams:")
            context.user_data[KEY_STANDARD_PENDING_GRAMS] = {"user_food_name": user_food_name, "protein_per_100g": _round_protein(protein_per_100g)}
            return True
        await database.set_standard(user_id, user_food_name, grams, protein_per_100g=protein_per_100g)
        await update.message.reply_text(f"Saved: {user_food_name.capitalize()} = {grams}g (protein {protein_per_100g}g/100g)")
        logger.info("standard_manual_protein saved %s=%sg, protein=%sg/100g for user %s", user_food_name, grams, protein_per_100g, user_id)
        return True

    manual_term = context.user_data.get(KEY_STANDARD_MANUAL)
    if manual_term:
        parts = text.split()
        if len(parts) < 2:
            await update.message.reply_text("Reply with two numbers: <grams> <protein_per_100g>, e.g. 200 31")
            return True
        try:
            grams = _round_grams(float(parts[0]))
            protein_per_100g = _round_protein(float(parts[1]))
        except (ValueError, IndexError):
            await update.message.reply_text("Use numbers only, e.g. 200 31")
            return True
        del context.user_data[KEY_STANDARD_MANUAL]
        food_name = manual_term.strip().lower()
        await database.set_standard(user_id, food_name, grams, protein_per_100g=protein_per_100g)
        await update.message.reply_text(f"Saved: {food_name.title()} = {grams}g (protein {protein_per_100g}g/100g)")
        logger.info("standard_manual saved %s=%sg, protein=%s for user %s", food_name, grams, protein_per_100g, user_id)
        return True

    return False


STANDARDS_HEADING = "FOOD - PORTION SIZE (G) - PROTEIN/100G"


def _build_standards_message(standards: list[tuple[str, int, float | None]], page: int) -> tuple[str, InlineKeyboardMarkup | None]:
    """Build message text and optional nav keyboard for standards list. Returns (text, reply_markup or None)."""
    total_pages = max(1, (len(standards) + STANDARDS_PAGE_SIZE - 1) // STANDARDS_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    start = page * STANDARDS_PAGE_SIZE
    page_items = standards[start : start + STANDARDS_PAGE_SIZE]
    lines = [STANDARDS_HEADING, ""]
    for food_name, grams, protein in page_items:
        protein_str = str(_round_protein(protein)) if protein is not None else "not set"
        lines.append(f"{food_name.capitalize()} - {grams} - {protein_str}/100g")
    text = "\n".join(lines)
    if total_pages <= 1:
        return text, None
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"{STANDARDS_CB_PREFIX}page|{page - 1}"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"{STANDARDS_CB_PREFIX}page|{page + 1}"))
    reply_markup = InlineKeyboardMarkup([nav_row]) if nav_row else None
    text += f"\n\n(page {page + 1}/{total_pages})"
    return text, reply_markup


async def standards_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /myfoods: list standard portions for this user. Paginated if more than 10 entries."""
    user_id = update.effective_user.id
    logger.info("standards_handler listing standards for user_id=%s", user_id)
    standards = await database.get_all_standards_full(user_id)
    if not standards:
        await update.message.reply_text("You have no foods. Add some with /addfood")
        return
    text, reply_markup = _build_standards_message(standards, 0)
    await update.message.reply_text(text, reply_markup=reply_markup)


async def standards_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle pagination button press for /myfoods."""
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    data = query.data
    if not data or not data.startswith(STANDARDS_CB_PREFIX):
        return
    payload = data[len(STANDARDS_CB_PREFIX) :]
    if not payload.startswith("page|"):
        return
    try:
        page = int(payload.split("|", 1)[1])
    except (ValueError, IndexError):
        return
    standards = await database.get_all_standards_full(user_id)
    if not standards:
        await query.edit_message_text("You have no foods. Add some with /addfood")
        return
    text, reply_markup = _build_standards_message(standards, page)
    await query.edit_message_text(text, reply_markup=reply_markup)
