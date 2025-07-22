# main.py (No changes needed, just for context)
import asyncio
import logging

from trading_bot import TradingBot
from database import create_db_and_tables

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

async def main():
    # Ensure database tables are created on startup
    create_db_and_tables()
    logging.info("Database tables checked/created.")

    bot = TradingBot()

    # Create the scheduled tasks as a background task
    asyncio.create_task(bot.run_scheduled_tasks())

    print("Bot is running. Type commands like:")
    print("/buy <CA_ADDRESS> <TARGET_MC_USD> <PERCENT_OF_WALLET>")
    print("/sell <CA_ADDRESS>")
    print("/report (to force a PnL report)")
    print("/exit (to stop the bot)")

    while True: # This loop is for handling user input and keeps the bot responsive
        try:
            command_input = await asyncio.to_thread(input, "Enter command: ")
            parts = command_input.split()
            if not parts:
                continue

            command = parts[0].lower()

            if command == "/buy" and len(parts) == 4:
                try:
                    ca_address = parts[1]
                    target_mc_usd = float(parts[2])
                    percent_of_wallet = float(parts[3])
                    if not (0 < percent_of_wallet <= 1):
                        print("Percentage of wallet must be between 0 and 1.")
                        continue
                    await bot.handle_buy_command(ca_address, target_mc_usd, percent_of_wallet)
                except ValueError:
                    print("Invalid arguments for /buy. Usage: /buy <CA_ADDRESS> <TARGET_MC_USD> <PERCENT_OF_WALLET>")
                except Exception as e:
                    logging.error(f"Error parsing buy command: {e}")
            elif command == "/sell" and len(parts) == 2:
                ca_address = parts[1]
                await bot.handle_manual_sell_command(ca_address)
            elif command == "/report":
                await bot.generate_pnl_report()
            elif command == "/exit":
                print("Exiting bot.")
                break
            else:
                print("Unknown command or invalid format.")
        except EOFError: # Handles Ctrl+D or similar input stream end
            print("Input ended. Exiting bot.")
            break
        except KeyboardInterrupt: # Handles Ctrl+C
            print("Bot stopped by user.")
            break
        except Exception as e:
            logging.critical(f"Unexpected error in main bot loop: {e}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Bot stopped by user.")
    except Exception as e:
        logging.critical(f"Unhandled exception in main: {e}")