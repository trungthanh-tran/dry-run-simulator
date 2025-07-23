# telegram_notifier.py
import logging
import httpx # Import httpx for direct HTTP requests
from telegram.error import TelegramError # Keep for specific Telegram API errors, though direct httpx will handle HTTP errors

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class TelegramNotifier:
    def __init__(self):
        if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
            logging.error("Telegram bot token or chat ID not set in config. Notifications will be disabled.")
            self.enabled = False
            self.http_client = None
            self.base_url = None
        else:
            self.http_client = httpx.AsyncClient() # Initialize httpx client
            self.base_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
            self.chat_id = TELEGRAM_CHAT_ID
            self.enabled = True
            logging.info("TelegramNotifier initialized with direct HTTP requests.")

    async def send_message(self, message: str):
        if not self.enabled or self.http_client is None:
            logging.warning("Telegram notifications are disabled or client not initialized. Message not sent.")
            return

        try:
            # Escape special characters for MarkdownV2
            escaped_message = self._escape_markdown_v2(message)

            payload = {
                "chat_id": self.chat_id,
                "text": escaped_message,
                "parse_mode": "HTML"
            }

            response = await self.http_client.post(f"{self.base_url}/sendMessage", json=payload)
            response.raise_for_status() # Raise an exception for HTTP errors (4xx or 5xx)

            response_data = response.json()
            if response_data.get("ok"):
                logging.info("Telegram notification sent successfully via HTTP.")
            else:
                error_description = response_data.get("description", "Unknown error")
                logging.error(f"Telegram API error sending message (HTTP): {error_description}")
                # You can still use TelegramError for specific cases if you want to map HTTP errors
                # to Telegram's error messages for consistency.
                if "chat not found" in error_description.lower():
                    logging.error(f"Double-check your TELEGRAM_CHAT_ID: {self.chat_id}")
                elif "blocked by the user" in error_description.lower():
                    logging.warning(f"Bot blocked by user/chat for chat_id: {self.chat_id}")

        except httpx.HTTPStatusError as e:
            logging.error(f"HTTP error sending Telegram message: {e.response.status_code} - {e.response.text}")
        except httpx.RequestError as e:
            logging.error(f"Network error sending Telegram message: {e}")
        except Exception as e:
            logging.error(f"An unexpected error occurred while sending Telegram message: {e}", exc_info=True)


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