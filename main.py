# main.py
import asyncio
import logging
import sys
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from config import TELEGRAM_BOT_TOKEN
from trading_bot import TradingBot
from database import create_db_and_tables

# --- Configure Logging ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
# Suppress noisy httpx logs from python-telegram-bot and jupiter_client
logging.getLogger("httpx").setLevel(logging.WARNING)

# Initialize TradingBot (global instance for easy access in handlers)
trading_bot_instance = TradingBot()

# --- Telegram Command Handlers ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends a welcome message when the /start command is issued."""
    user = update.effective_user
    # Escape markdown for user mention, as it might contain special chars
    escaped_username = user.mention_markdown_v2().replace('_', '\\_').replace('*', '\\*').replace('[', '\\[').replace('`', '\\`')
    await update.message.reply_markdown_v2(
        f"Hi {escaped_username}!\n\n"
        "I'm your Solana trading bot\\. Use /help to see available commands\\."
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends a help message when the /help command is issued."""
    help_text = (
        "Here are the commands you can use:\n\n"
        "*/buy* `<CA_ADDRESS>` `<TARGET_MC_USD>` `<PERCENT_OF_WALLET>`\n"
        "  _Example:_ `/buy EoNnC... 100000 0.5` \\(Monitors CA until MC is \\$100k, then buys with 50% of wallet's SOL\\)\n\n"
        "*/sell* `<CA_ADDRESS>`\n"
        "  _Example:_ `/sell EoNnC...` \\(Manually sells all of a specific CA you hold\\)\n\n"
        "*/report*\n"
        "  \\(Generates a PnL report for all active and completed trades\\)\n\n"
        "*/balance*\n"
        "  \\(Shows your current SOL balance in the bot's wallet\\)\n\n"
        "*/help*\n"
        "  \\(Shows this help message\\)"
    )
    await update.message.reply_markdown_v2(help_text)


async def buy_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the /buy command from Telegram."""
    args = context.args
    logging.error(f"{args}")
    if len(args) != 3:
        await update.message.reply_text(
            "Usage: `/buy <CA_ADDRESS> <TARGET_MC_USD> <SOL>`\n"
            "Example: `/buy EoNnC... 100000 0.5`",
            parse_mode="HTML"
        )
        return

    try:
        ca_address = args[0]
        target_mc_usd = float(args[1])
        sol_in = float(args[2])

        if not (0 < sol_in <= 1):
            await update.message.reply_text("Percentage of wallet must be between 0 (exclusive) and 1 (inclusive).")
            return

        # Acknowledge the command immediately
        await update.message.reply_text(
            f"Received buy command for CA: `{ca_address}`\n"
            f"Target MC: `${target_mc_usd:,.2f}`\n"
            f"Using: {sol_in} SOL\n"
            f"Initiating monitoring...",
            parse_mode="HTML"
        )
        # Call the TradingBot's method to handle the buy logic
        await trading_bot_instance.handle_buy_command(ca_address, target_mc_usd, sol_in)

    except ValueError:
        await update.message.reply_text(
            "Invalid arguments. Please ensure `TARGET_MC_USD` and `PERCENT_OF_WALLET` are valid numbers.",
            parse_mode="HTML"
        )
    except Exception as e:
        logging.error(f"Error in /buy command: {e}", exc_info=True)
        await update.message.reply_text(f"An unexpected error occurred while processing your buy command: `{e}`", parse_mode="HTML")

async def sell_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the /sell command from Telegram."""
    args = context.args
    if len(args) != 1:
        await update.message.reply_text("Usage: `/sell <CA_ADDRESS>`\nExample: `/sell EoNnC...`", parse_mode="HTML")
        return

    ca_address = args[0]
    await update.message.reply_text(f"Initiating manual sell for CA: `{ca_address}`...", parse_mode="HTML")
    try:
        await trading_bot_instance.handle_manual_sell_command(ca_address)
    except Exception as e:
        logging.error(f"Error in /sell command: {e}", exc_info=True)
        await update.message.reply_text(f"An unexpected error occurred while processing your sell command: `{e}`", parse_mode="HTML")

async def report_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the /report command from Telegram."""
    await update.message.reply_text("Generating PnL report... This might take a moment.", parse_mode="HTML")
    try:
        await trading_bot_instance.generate_pnl_report()
    except Exception as e:
        logging.error(f"Error in /report command: {e}", exc_info=True)
        await update.message.reply_text(f"An unexpected error occurred while generating the report: `{e}`", parse_mode="HTML")

async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the /balance command from Telegram."""
    await update.message.reply_text("Fetching SOL balance...", parse_mode="HTML")
    try:
        sol_balance = await trading_bot_instance.jupiter_client.get_sol_balance()
        await update.message.reply_text(f"Current SOL balance: `{sol_balance:.6f}` SOL", parse_mode="HTML")
    except Exception as e:
        logging.error(f"Error in /balance command: {e}", exc_info=True)
        await update.message.reply_text(f"An unexpected error occurred while fetching balance: `{e}`", parse_mode="HTML")


# --- Main Bot Execution Function ---
async def start_and_run_bot() -> None:
    """
    Sets up the Telegram bot application, registers handlers,
    starts background tasks, and then runs the bot's polling.
    """
    

if __name__ == "__main__":
    try:
        # The main entry point: asyncio.run() directly calls the function
        # that sets up the bot and then calls application.run_polling().
        # This is the standard and correct way for python-telegram-bot v20+.
        # Ensure database tables are created on startup
        create_db_and_tables()
        logging.info("Database tables checked/created.")

        # Create the Application and pass your bot's token.
        if not TELEGRAM_BOT_TOKEN:
            logging.critical("TELEGRAM_BOT_TOKEN is not set. Please check your .env file.")
            sys.ext(1)

        application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

        # Register command handlers
        application.add_handler(CommandHandler("start", start_command))
        application.add_handler(CommandHandler("help", help_command))
        application.add_handler(CommandHandler("buy", buy_command))
        application.add_handler(CommandHandler("sell", sell_command))
        application.add_handler(CommandHandler("report", report_command))
        application.add_handler(CommandHandler("balance", balance_command))

        # Run scheduled tasks (like auto-sell monitoring) as a background asyncio task
        # This task will run concurrently with the Telegram polling.

        logging.info("Starting Telegram bot polling...")
        # This is the awaitable that will be passed directly to asyncio.run().
        # It takes control of the event loop.
        application.run_polling(allowed_updates=Update.ALL_TYPES)

    except KeyboardInterrupt:
        logging.info("Bot stopped by user (Ctrl+C).")
    except Exception as e:
        logging.critical(f"Unhandled exception in main: {e}", exc_info=True)