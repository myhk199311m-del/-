"""
bot.py
Telegram trading-signal bot with a Basilisk-style dashboard:
  - Pair / Payout / Time panel
  - Manual "Scan" button that runs the 9-indicator + pattern engine
  - Confidence score bar + strength badge
  - Risk calculator (balance / risk% / payout%) with stake, P/L, break-even

Run with:  python bot.py
Env vars required:
  TELEGRAM_BOT_TOKEN
  TWELVE_DATA_API_KEY
"""

import os
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from data import fetch_candles, DataError
from indicators import analyze
import risk as risk_calc

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("basilisk_bot")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")

PAIRS = ["AUD/USD", "EUR/USD", "GBP/USD", "USD/JPY", "XAU/USD", "BTC/USD"]
TIMEFRAMES = [("1:00", "1min"), ("5:00", "5min"), ("15:00", "15min")]

DEFAULT_STATE = {
    "pair_idx": 0,
    "tf_idx": 1,
    "payout_pct": 92,
    "balance": 1000.0,
    "risk_pct": 1.0,
    "last_result": None,
}

ASK_BALANCE, ASK_RISK, ASK_PAYOUT = range(3)


def get_state(context: ContextTypes.DEFAULT_TYPE) -> dict:
    if "state" not in context.chat_data:
        context.chat_data["state"] = DEFAULT_STATE.copy()
    return context.chat_data["state"]


# ---------------------------------------------------------------------------
# UI builders
# ---------------------------------------------------------------------------

def main_text(state: dict) -> str:
    pair = PAIRS[state["pair_idx"]]
    tf_label, _ = TIMEFRAMES[state["tf_idx"]]
    result = state.get("last_result")

    lines = [
        "🐍 *BASILISK SIGNAL BOT*",
        "",
        f"*PAIR:* {pair}",
        f"*PAYOUT:* {state['payout_pct']}%",
        f"*TIME:* {tf_label}",
        "",
    ]

    if result is None:
        lines.append("_Press Scan to generate a signal._")
    else:
        decision = result["decision"]
        emoji = "🟢" if decision == "BUY" else "🔴" if decision == "SELL" else "⚪"
        bar_len = 20
        filled = round((result["score"] / 100) * bar_len)
        bar = "█" * filled + "░" * (bar_len - filled)

        lines += [
            f"SIGNAL: {emoji} *{decision}*",
            "",
            f"`{bar}` {result['score']}/100",
            f"Pattern: *{result['pattern']}*   Strength: *{result['strength']}*",
            f"Votes: {result['bull_votes']} bull / {result['bear_votes']} bear "
            f"(of {result['total_indicators']})",
        ]
        if result["sl"] is not None:
            lines += [
                "",
                f"Entry: `{result['price']:.5f}`",
                f"SL: `{result['sl']:.5f}`   TP: `{result['tp']:.5f}`",
            ]
        lines.append("\n_Not a trading recommendation._")

    return "\n".join(lines)


def main_keyboard(state: dict) -> InlineKeyboardMarkup:
    pair = PAIRS[state["pair_idx"]]
    tf_label, _ = TIMEFRAMES[state["tf_idx"]]
    rows = [
        [
            InlineKeyboardButton(f"Pair: {pair} ↻", callback_data="pair_next"),
            InlineKeyboardButton(f"Time: {tf_label} ↻", callback_data="tf_next"),
        ],
        [
            InlineKeyboardButton("🔍 Scan", callback_data="scan"),
            InlineKeyboardButton("♻️ Reset", callback_data="reset"),
        ],
        [InlineKeyboardButton("🧮 Risk Calculator", callback_data="risk_menu")],
    ]
    return InlineKeyboardMarkup(rows)


def risk_text(state: dict) -> str:
    r = risk_calc.calculate(state["balance"], state["risk_pct"], state["payout_pct"])
    return (
        "🧮 *RISK CALCULATOR*\n\n"
        f"Balance: `{state['balance']:.2f}`\n"
        f"Risk per trade: `{state['risk_pct']}%`\n"
        f"Payout: `{state['payout_pct']}%`\n\n"
        f"Stake size: `{r['stake']:.2f}`\n"
        f"Potential profit: `+{r['potential_profit']:.2f}`\n"
        f"Potential loss: `-{r['potential_loss']:.2f}`\n\n"
        f"Break-even win rate: *≥ {r['break_even_win_rate']}%*"
    )


def risk_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton("💰 Set Balance", callback_data="set_balance"),
            InlineKeyboardButton("⚠️ Set Risk %", callback_data="set_risk"),
        ],
        [InlineKeyboardButton("🏦 Set Payout %", callback_data="set_payout")],
        [InlineKeyboardButton("⬅️ Back", callback_data="back_main")],
    ]
    return InlineKeyboardMarkup(rows)


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = get_state(context)
    await update.message.reply_text(
        main_text(state), reply_markup=main_keyboard(state), parse_mode=ParseMode.MARKDOWN
    )


async def button_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    state = get_state(context)
    data = query.data

    if data == "pair_next":
        state["pair_idx"] = (state["pair_idx"] + 1) % len(PAIRS)
        state["last_result"] = None
        await query.edit_message_text(
            main_text(state), reply_markup=main_keyboard(state), parse_mode=ParseMode.MARKDOWN
        )

    elif data == "tf_next":
        state["tf_idx"] = (state["tf_idx"] + 1) % len(TIMEFRAMES)
        state["last_result"] = None
        await query.edit_message_text(
            main_text(state), reply_markup=main_keyboard(state), parse_mode=ParseMode.MARKDOWN
        )

    elif data == "reset":
        chat_data_state = DEFAULT_STATE.copy()
        context.chat_data["state"] = chat_data_state
        await query.edit_message_text(
            main_text(chat_data_state),
            reply_markup=main_keyboard(chat_data_state),
            parse_mode=ParseMode.MARKDOWN,
        )

    elif data == "scan":
        pair = PAIRS[state["pair_idx"]]
        _, interval = TIMEFRAMES[state["tf_idx"]]
        await query.edit_message_text(
            main_text(state) + "\n\n⏳ _Scanning..._",
            reply_markup=main_keyboard(state),
            parse_mode=ParseMode.MARKDOWN,
        )
        try:
            df = fetch_candles(pair, interval, output_size=100)
            result = analyze(df)
            state["last_result"] = result
        except DataError as e:
            await query.edit_message_text(
                main_text(state) + f"\n\n⚠️ Data error: {e}",
                reply_markup=main_keyboard(state),
                parse_mode=ParseMode.MARKDOWN,
            )
            return
        except Exception as e:
            log.exception("Scan failed")
            await query.edit_message_text(
                main_text(state) + f"\n\n⚠️ Unexpected error: {e}",
                reply_markup=main_keyboard(state),
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        await query.edit_message_text(
            main_text(state), reply_markup=main_keyboard(state), parse_mode=ParseMode.MARKDOWN
        )

    elif data == "risk_menu":
        await query.edit_message_text(
            risk_text(state), reply_markup=risk_keyboard(), parse_mode=ParseMode.MARKDOWN
        )

    elif data == "back_main":
        await query.edit_message_text(
            main_text(state), reply_markup=main_keyboard(state), parse_mode=ParseMode.MARKDOWN
        )


# --- Risk calculator numeric input flow (ConversationHandler) --------------

async def ask_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("Enter new balance (number):")
    return ASK_BALANCE


async def ask_risk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("Enter risk per trade % (e.g. 1.5):")
    return ASK_RISK


async def ask_payout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("Enter payout % (e.g. 92):")
    return ASK_PAYOUT


async def receive_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = get_state(context)
    try:
        state["balance"] = float(update.message.text.replace(",", "."))
    except ValueError:
        await update.message.reply_text("Invalid number, try again:")
        return ASK_BALANCE
    await update.message.reply_text(
        risk_text(state), reply_markup=risk_keyboard(), parse_mode=ParseMode.MARKDOWN
    )
    return ConversationHandler.END


async def receive_risk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = get_state(context)
    try:
        state["risk_pct"] = float(update.message.text.replace(",", "."))
    except ValueError:
        await update.message.reply_text("Invalid number, try again:")
        return ASK_RISK
    await update.message.reply_text(
        risk_text(state), reply_markup=risk_keyboard(), parse_mode=ParseMode.MARKDOWN
    )
    return ConversationHandler.END


async def receive_payout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = get_state(context)
    try:
        state["payout_pct"] = float(update.message.text.replace(",", "."))
    except ValueError:
        await update.message.reply_text("Invalid number, try again:")
        return ASK_PAYOUT
    await update.message.reply_text(
        risk_text(state), reply_markup=risk_keyboard(), parse_mode=ParseMode.MARKDOWN
    )
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

def main():
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN environment variable is not set.")

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))

    conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(ask_balance, pattern="^set_balance$"),
            CallbackQueryHandler(ask_risk, pattern="^set_risk$"),
            CallbackQueryHandler(ask_payout, pattern="^set_payout$"),
        ],
        states={
            ASK_BALANCE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_balance)],
            ASK_RISK: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_risk)],
            ASK_PAYOUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_payout)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    app.add_handler(conv)

    app.add_handler(CallbackQueryHandler(button_router))

    log.info("Basilisk bot starting (polling)...")
    app.run_polling()


if __name__ == "__main__":
    main()
