# telegram_notifier.py
import logging
from telegram import Bot
from telegram.error import TelegramError

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class TelegramNotifier:
    def __init__(self):
        if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
            logging.error("Telegram bot token or chat ID not set in config. Notifications will be disabled.")
            self.enabled = False
            self.bot = None
        else:
            self.bot = Bot(token=TELEGRAM_BOT_TOKEN)
            self.chat_id = TELEGRAM_CHAT_ID
            self.enabled = True
            logging.info("TelegramNotifier initialized with python-telegram-bot.")

    async def send_message(self, message: str):
        if not self.enabled or self.bot is None:
            logging.warning("Telegram notifications are disabled or bot not initialized. Message not sent.")
            return

        try:
            # Escape special characters for MarkdownV2
            escaped_message = self._escape_markdown_v2(message)

            # Use async with for proper bot lifecycle management
            async with self.bot:
                await self.bot.send_message(
                    chat_id=self.chat_id,
                    text=escaped_message,
                    parse_mode="MarkdownV2"
                )
            logging.info("Telegram notification sent successfully.")
        except TelegramError as e:
            logging.error(f"Telegram API error sending message: {e}")
            if e.message == "Bad Request: chat not found":
                logging.error(f"Double-check your TELEGRAM_CHAT_ID: {self.chat_id}")
            elif "blocked by the user" in e.message:
                logging.warning(f"Bot blocked by user/chat for chat_id: {self.chat_id}")
        except Exception as e:
            logging.error(f"Error sending Telegram message: {e}")

    def _escape_markdown_v2(self, text: str) -> str:
        """Escape characters for MarkdownV2 to prevent parsing errors."""
        # Characters that need to be escaped in MarkdownV2:
        # _, *, [, ], (, ), ~, `, >, #, +, -, =, |, {, }, ., !
        # Need to escape backslash itself first
        escape_chars = '_*[]()~`>#+-=|{}.!'
        text = text.replace('\\', '\\\\')
        for char in escape_chars:
            text = text.replace(char, f'\\{char}')
        return text