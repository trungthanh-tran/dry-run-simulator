# trading_bot.py
import asyncio
import logging
from datetime import datetime
from typing import Optional, Tuple
from contextlib import asynccontextmanager
import math

from config import (
    WSOL_MINT_ADDRESS, SOL_DECIMALS, PNL_WALLET_ADDRESS,
    SCHEDULED_TASK_INTERVAL_SECONDS, DRY_RUN
)
from jupiter_client import JupiterSwapClient
from telegram_notifier import TelegramNotifier
from database import get_db, Session # Import Session type hint
from models import TradeEntry # Import your TradeEntry model
from solders.pubkey import Pubkey


class TradingBot:
    def __init__(self):
        self.notifier = TelegramNotifier()
        self.jupiter_client = JupiterSwapClient()
        logging.info("TradingBot initialized.")

    @asynccontextmanager
    async def _get_db_session(self):
        """
        Provides an asynchronous context manager for database sessions.
        It runs the synchronous get_db() generator in a separate thread.
        """
        # Run the synchronous get_db() in a thread to avoid blocking the event loop
        db_generator = await asyncio.to_thread(get_db)
        try:
            db = next(db_generator) # Get the session from the generator
            yield db # Yield the session to the 'async with' block
        finally:
            try:
                # Attempt to advance the generator one more time to execute its finally block (db.close())
                next(db_generator)
            except StopIteration:
                pass # This is expected when the generator is exhausted


    async def handle_buy_command(self, ca_address: str, target_mc_usd: float, sol_in: float):
        """
        Handles the /buy command.
        Monitors the CA and executes a buy when conditions are met.
        """
        logging.info(f"Handling buy command for CA: {ca_address}, Target MC: ${target_mc_usd}, Percent of wallet: {sol_in}")
        await self.notifier.send_message(
            f"🟢 Initiating monitoring for buy on CA: `{ca_address}`"
            f"Target Market Cap: `${target_mc_usd:,.2f}`"
            f"Using: {sol_in} SOL"
            f"Waiting for favorable conditions..."
        )

        try:
            async with self._get_db_session() as db:
                existing_trade = db.query(TradeEntry).filter(
                    TradeEntry.ca_address == ca_address,
                    TradeEntry.status == "ACTIVE"
                ).first()
                if existing_trade:
                    await self.notifier.send_message(f"⚠️ A trade for CA `{ca_address}` is already ACTIVE. Please sell it first or wait for it to complete.")
                    return

            await self._monitor_and_buy(ca_address, target_mc_usd, sol_in)

        except Exception as e:
            logging.error(f"Error in handle_buy_command for {ca_address}: {e}", exc_info=True)
            await self.notifier.send_message(f"🚨 An error occurred during buy command for `{ca_address}`: `{e}`")

    async def _monitor_and_buy(self, ca_address: str, target_mc_usd: float, sol_in: float):
        """Monitors token market cap and executes a buy."""
        # Check if CA exists
        try:
            Pubkey.from_string(ca_address)
        except Exception:
            await self.notifier.send_message(f"🚫 Invalid CA Address: `{ca_address}`. Please check the address.")
            return

        buy_attempted = False
        while not buy_attempted:
            try:
                # Fetch token price and market cap (if possible)
                current_mc_usd, token_price_usd = await self.jupiter_client.get_token_market_cap_and_price(ca_address)
                
                if current_mc_usd is None and token_price_usd is None:
                    # If CoinGecko couldn't provide data, we can't monitor by MC.
                    # This needs a decision: either exit, or try to buy immediately without MC check
                    # For now, let's assume MC monitoring is critical.
                    logging.warning(f"Could not retrieve market cap/price for {ca_address}. Retrying...")
                    await self.notifier.send_message(f"🔍 Could not retrieve market cap/price for `{ca_address}`. Retrying in 30 seconds...")
                    await asyncio.sleep(30)
                    continue
                
                # Market cap check (if current_mc_usd is available, otherwise skip this condition or use price)
                if current_mc_usd is not None and current_mc_usd > target_mc_usd:
                    logging.info(f"Current MC (${current_mc_usd:,.2f}) for {ca_address} is higher than target (${target_mc_usd:,.2f}). Waiting...")
                    await self.notifier.send_message(f"Current MC for `{ca_address}` is `${current_mc_usd:,.2f}` \\(Target: `${target_mc_usd:,.2f}`\\)\\. Waiting for conditions\\.")
                    await asyncio.sleep(60) # Wait and re-check
                    continue

                # Proceed with buy logic if MC condition met or skipped
                sol_balance = await self.jupiter_client.get_sol_balance()
                sol_to_spend = sol_in
                if sol_balance < sol_in:
                    sol_to_spend = 0
                if sol_to_spend == 0:
                    await self.notifier.send_message("❌ Your wallet has enough SOL. Cannot proceed with buy.")
                    return

                # Convert SOL amount to lamports
                amount_in_lamports = int(sol_to_spend * (10**SOL_DECIMALS))
                if amount_in_lamports == 0:
                     await self.notifier.send_message("❌ Calculated SOL amount to spend is too small (less than 1 lamport). Cannot proceed with buy.")
                     return

                logging.info(f"Attempting to buy {ca_address} with {sol_to_spend:.6f} SOL ({amount_in_lamports} lamports)...")
                await self.notifier.send_message(f"💰 Attempting to buy `{ca_address}` with `{sol_to_spend:.6f}` SOL...")

                # Get the actual price for record keeping
                sol_price_usd = await self.jupiter_client._get_sol_price_usd() # Use internal to avoid circular logic

                buy_tx_sig, sol_spent, ca_amount_bought = await self.jupiter_client.get_quote_and_swap(
                    WSOL_MINT_ADDRESS, ca_address, amount_in_lamports
                )

                if buy_tx_sig:
                    initial_sol_value = sol_spent # The SOL amount actually spent
                    # Store trade in DB
                    new_trade = TradeEntry(
                        ca_address=ca_address,
                        buy_tx_signature=buy_tx_sig,
                        buy_price_sol=initial_sol_value, # SOL spent to acquire this CA amount
                        ca_amount_bought=ca_amount_bought,
                        target_mc_usd=target_mc_usd, # Still store target_mc_usd for reference
                        percent_of_wallet=sol_in,
                        status="ACTIVE",
                        initial_sol_value=initial_sol_value,
                        realized_pnl=0.0 # Initialize PnL to 0
                    )
                    async with self._get_db_session() as db:
                        db.add(new_trade)
                        db.commit()
                        db.refresh(new_trade) # Refresh to get ID and other defaults
                    logging.info(f"Buy successful for {ca_address}. Trade ID: {new_trade.id}")
                    await self.notifier.send_message(
                        f"✅ Buy successful for `{ca_address}`!\n"
                        f"Spent: `{initial_sol_value:.6f}` SOL\n"
                        f"Bought: `{ca_amount_bought:.4f}` tokens\n"
                        f"Transaction: `https://solscan.io/tx/{buy_tx_sig}`\n"
                        f"Trade Status: *ACTIVE*"
                    )
                    buy_attempted = True # Exit monitoring loop
                else:
                    logging.error(f"Buy failed for {ca_address}. Retrying...")
                    await self.notifier.send_message(f"❌ Buy failed for `{ca_address}`. Retrying in 30 seconds...")
                    await asyncio.sleep(30) # Wait and retry
            except Exception as e:
                logging.error(f"Error during buy monitoring for {ca_address}: {e}", exc_info=True)
                await self.notifier.send_message(f"🚨 An error occurred during buy monitoring for `{ca_address}`: `{e}`. Retrying in 60 seconds...")
                await asyncio.sleep(60)


    async def monitor_auto_sell_triggers(self):
        """Monitors active trades for auto-sell conditions (e.g., profit targets)."""
        active_trades = await self._get_active_trades()
        if not active_trades:
            logging.info("No active trades to monitor for auto-sell.")
            return

        logging.info(f"Monitoring {len(active_trades)} active trades for auto-sell triggers.")

        for trade in active_trades:
            try:
                # Current logic: No specific auto-sell triggers defined yet (e.g., 2x profit, stop loss)
                # You would add that logic here.
                # Example:
                # current_mc_usd, token_price_usd = await self.jupiter_client.get_token_market_cap_and_price(trade.ca_address)
                # if token_price_usd and (token_price_usd / trade.buy_price_token_unit) > 2.0: # Example 2x profit
                #    await self._execute_sell(trade)

                # For now, this function primarily serves as a placeholder for future auto-sell logic.
                pass # Placeholder for auto-sell logic
            except Exception as e:
                logging.error(f"Error monitoring auto-sell for trade {trade.id} ({trade.ca_address}): {e}", exc_info=True)
                await self.notifier.send_message(f"🚨 Error monitoring auto-sell for trade `{trade.id}` \\({trade.ca_address}\\): `{e}`")


    async def handle_manual_sell_command(self, ca_address: str):
        """Handles manual sell command from Telegram."""
        logging.info(f"Handling manual sell command for CA: {ca_address}")
        try:
            async with self._get_db_session() as db:
                trade = db.query(TradeEntry).filter(
                    TradeEntry.ca_address == ca_address,
                    TradeEntry.status == "ACTIVE"
                ).first()

            if not trade:
                await self.notifier.send_message(f"🚫 No *ACTIVE* trade found for CA: `{ca_address}`.")
                return

            await self._execute_sell(trade)

        except Exception as e:
            logging.error(f"Error in handle_manual_sell_command for {ca_address}: {e}", exc_info=True)
            await self.notifier.send_message(f"🚨 An error occurred during manual sell for `{ca_address}`: `{e}`")


    async def _execute_sell(self, trade: TradeEntry):
        """Executes a sell operation for a given trade."""
        logging.info(f"Executing sell for Trade ID: {trade.id}, CA: {trade.ca_address}")
        await self.notifier.send_message(f"🔴 Executing sell for `{trade.ca_address}` \\(Trade ID: `{trade.id}`\\)\\. This may take a moment...")

        try:
            ca_amount_to_sell = await self.jupiter_client.get_token_balance(trade.ca_address)
            # Ensure we only sell up to the amount we bought if current balance is higher due to other factors
            # Or consider selling all if that's the intention
            # For simplicity, let's aim to sell the amount we bought, or current balance if less.
            actual_amount_to_sell = min(trade.ca_amount_bought, ca_amount_to_sell)

            if actual_amount_to_sell <= 0:
                await self.notifier.send_message(f"⚠️ No `{trade.ca_address}` tokens found in wallet for Trade ID `{trade.id}` or amount is zero. Cannot sell.")
                # Mark as sold if no tokens left, assuming it was sold manually outside
                await self._update_trade_status(trade.id, "SOLD", trade.initial_sol_value, 0.0) # Mark as sold with 0 PnL if no tokens
                return

            # Get token decimals from chain
            response = self.jupiter_client.solana_client.get_mint(Pubkey.from_string(trade.ca_address))
            if not response.value:
                raise ValueError(f"Could not get mint info for {trade.ca_address}")
            ca_decimals = response.value.decimals

            amount_in_raw_units = int(actual_amount_to_sell * (10**ca_decimals))

            sell_tx_sig, sol_received, ca_amount_sold = await self.jupiter_client.get_quote_and_swap(
                trade.ca_address, WSOL_MINT_ADDRESS, amount_in_raw_units
            )

            if sell_tx_sig:
                final_sol_value = sol_received
                realized_pnl = final_sol_value - trade.initial_sol_value
                
                await self._update_trade_status(trade.id, "SOLD", final_sol_value, realized_pnl)
                logging.info(f"Sell successful for {trade.ca_address}. Trade ID: {trade.id}, PnL: {realized_pnl:.6f} SOL")
                await self.notifier.send_message(
                    f"✅ Sell successful for `{trade.ca_address}`!\n"
                    f"Sold: `{ca_amount_sold:.4f}` tokens\n"
                    f"Received: `{sol_received:.6f}` SOL\n"
                    f"Realized PnL: `{realized_pnl:+.6f}` SOL\n"
                    f"Transaction: `https://solscan.io/tx/{sell_tx_sig}`\n"
                    f"Trade Status: *SOLD*"
                )
            else:
                logging.error(f"Sell failed for {trade.ca_address}. Trade ID: {trade.id}")
                await self.notifier.send_message(f"❌ Sell failed for `{trade.ca_address}` \\(Trade ID: `{trade.id}`\\)\\. Please try again or check logs.")

        except Exception as e:
            logging.error(f"Error executing sell for trade {trade.id}: {e}", exc_info=True)
            await self.notifier.send_message(f"🚨 An error occurred during sell execution for trade `{trade.id}` \\({trade.ca_address}\\): `{e}`")


    async def generate_pnl_report(self):
        """Generates and sends a PnL report for all trades."""
        logging.info("Generating PnL report...")
        all_trades = await self._get_all_trades()

        if not all_trades:
            await self.notifier.send_message("📊 No trades recorded yet.")
            return

        total_realized_pnl = 0.0
        total_initial_investment = 0.0
        report_lines = ["📊 *PnL Report:*\n"]

        # Cache SOL price for report calculation
        sol_price_usd = await self.jupiter_client._get_sol_price_usd()
        if sol_price_usd == 0:
            sol_price_usd = 1.0 # Avoid division by zero, use 1 USD if price lookup fails
            logging.warning("Failed to get SOL price for PnL report. Using 1 USD/SOL.")

        for trade in all_trades:
            initial_value_usd = trade.initial_sol_value * sol_price_usd
            total_initial_investment += trade.initial_sol_value

            pnl_line = ""
            if trade.status == "SOLD":
                realized_pnl_sol = trade.realized_pnl
                total_realized_pnl += realized_pnl_sol
                realized_pnl_usd = realized_pnl_sol * sol_price_usd
                pnl_line = (
                    f"  `{trade.ca_address[:6]}...` \\(Sold\\): PnL: `{realized_pnl_sol:+.6f}` SOL "
                    f"\\(`${realized_pnl_usd:+.2f}` USD\\)\n"
                )
            elif trade.status == "ACTIVE":
                # Calculate unrealized PnL for active trades
                token_balance = await self.jupiter_client.get_token_balance(trade.ca_address)
                if token_balance > 0:
                    current_mc_usd, token_price_usd = await self.jupiter_client.get_token_market_cap_and_price(trade.ca_address)
                    if token_price_usd:
                        current_value_usd = token_balance * token_price_usd
                        current_value_sol = current_value_usd / sol_price_usd
                        unrealized_pnl_sol = current_value_sol - trade.initial_sol_value
                        unrealized_pnl_usd = unrealized_pnl_sol * sol_price_usd
                        pnl_line = (
                            f"  `{trade.ca_address[:6]}...` \\(Active\\): PnL: `{unrealized_pnl_sol:+.6f}` SOL "
                            f"\\(`${unrealized_pnl_usd:+.2f}` USD\\)\n"
                        )
                    else:
                        pnl_line = f"  `{trade.ca_address[:6]}...` \\(Active\\): Price data unavailable for unrealized PnL.\n"
                else:
                    # If token_balance is 0 but status is ACTIVE, it means token was moved or sold outside bot
                    pnl_line = f"  `{trade.ca_address[:6]}...` \\(Active, but 0 balance\\): Likely sold/moved outside bot. PnL not tracked.\n"
            else: # CANCELED or other status
                pnl_line = f"  `{trade.ca_address[:6]}...` \\({trade.status.capitalize()}\\)\n"

            report_lines.append(pnl_line)

        report_lines.append("\n--- Summary ---\n")
        total_realized_pnl_usd = total_realized_pnl * sol_price_usd
        report_lines.append(f"Total Realized PnL: `{total_realized_pnl:+.6f}` SOL \\(`${total_realized_pnl_usd:+.2f}` USD\\)\n")
        # report_lines.append(f"Total Initial Investment: `{total_initial_investment:.6f}` SOL\n") # Optional

        final_report = "".join(report_lines)
        await self.notifier.send_message(final_report)
        logging.info("PnL report sent.")


    async def _transfer_realized_pnl(self):
        """Transfers realized PnL from trades to a designated PNL_WALLET_ADDRESS."""
        # This function needs to identify SOL that is 'pure profit' from trades.
        # This is complex as it depends on how initial capital is tracked.
        # For simplicity, let's assume `realized_pnl` directly represents transferrable SOL.

        async with self._get_db_session() as db:
            profitable_trades_to_transfer = db.query(TradeEntry).filter(
                TradeEntry.status == "SOLD",
                TradeEntry.realized_pnl > 0, # Only transfer positive PnL
                TradeEntry.pnl_transferred == False
            ).all()

        if not profitable_trades_to_transfer:
            logging.info("No new realized PnL to transfer.")
            return

        total_pnl_to_transfer = sum(trade.realized_pnl for trade in profitable_trades_to_transfer)
        
        # In DRY_RUN, simply log the action
        if DRY_RUN:
            logging.info(f"DRY_RUN: Simulating transfer of {total_pnl_to_transfer:.6f} SOL PnL to {PNL_WALLET_ADDRESS}")
            for trade in profitable_trades_to_transfer:
                await self._update_trade_pnl_transferred_status(trade.id, True)
            await self.notifier.send_message(f"💰 DRY_RUN: Simulated transfer of `{total_pnl_to_transfer:.6f}` SOL PnL to `{PNL_WALLET_ADDRESS[:6]}...`")
            return

        # For real transfer, you'd need a separate transfer function, not get_quote_and_swap
        # This is a placeholder for actual SOL transfer
        logging.info(f"Attempting to transfer {total_pnl_to_transfer:.6f} SOL PnL to {PNL_WALLET_ADDRESS}")
        await self.notifier.send_message(f"💰 Attempting to transfer `{total_pnl_to_transfer:.6f}` SOL PnL to `{PNL_WALLET_ADDRESS[:6]}...`")

        try:
            # THIS IS A PLACEHOLDER FOR REAL SOL TRANSFER LOGIC
            # You would use self.solana_client.transfer() here, signing with self.jupiter_client.payer
            # Example (conceptual, requires proper SOLANA_CLIENT usage):
            # tx_sig = self.jupiter_client.solana_client.transfer(
            #     from_pubkey=self.jupiter_client.payer.pubkey(),
            #     to_pubkey=Pubkey.from_string(PNL_WALLET_ADDRESS),
            #     lamports=int(total_pnl_to_transfer * (10**SOL_DECIMALS)),
            #     signer=self.jupiter_client.payer
            # ).value
            # # Then confirm transaction...

            # For now, let's just mark as transferred if this part is reached
            # You MUST replace this with actual transfer logic
            logging.warning("REAL PNL TRANSFER LOGIC IS A PLACEHOLDER. NO ACTUAL SOL TRANSFERRED.")
            transfer_successful = True # Assume success for placeholder
            transfer_tx_sig = "REAL_TRANSFER_TX_PLACEHOLDER"

            if transfer_successful:
                for trade in profitable_trades_to_transfer:
                    await self._update_trade_pnl_transferred_status(trade.id, True)
                await self.notifier.send_message(
                    f"✅ Successfully transferred `{total_pnl_to_transfer:.6f}` SOL PnL to `{PNL_WALLET_ADDRESS[:6]}...`\n"
                    f"Transaction: `https://solscan.io/tx/{transfer_tx_sig}`"
                )
            else:
                await self.notifier.send_message(f"❌ Failed to transfer `{total_pnl_to_transfer:.6f}` SOL PnL.")
        except Exception as e:
            logging.error(f"Error during PnL transfer: {e}", exc_info=True)
            await self.notifier.send_message(f"🚨 An error occurred during PnL transfer: `{e}`")


    async def _update_trade_pnl_transferred_status(self, trade_id: int, status: bool):
        async with self._get_db_session() as db:
            trade_entry = db.query(TradeEntry).filter(TradeEntry.id == trade_id).first()
            if trade_entry:
                trade_entry.pnl_transferred = status
                db.commit()
                db.refresh(trade_entry)
                logging.info(f"Updated trade {trade_id} PnL transferred status to: {status}")
            else:
                logging.warning(f"Trade with ID {trade_id} not found for PnL transferred status update.")

    async def _update_trade_status(self, trade_id: int, status: str, final_sol_value: float = None, realized_pnl: float = None):
        async with self._get_db_session() as db:
            trade_entry = db.query(TradeEntry).filter(TradeEntry.id == trade_id).first()
            if trade_entry:
                trade_entry.status = status
                if final_sol_value is not None:
                    trade_entry.final_sol_value = final_sol_value
                if realized_pnl is not None:
                    trade_entry.realized_pnl = realized_pnl
                if status == "SOLD":
                    trade_entry.sell_time = datetime.now() # Set sell time only when status is SOLD
                db.commit()
                db.refresh(trade_entry)
                logging.info(f"Updated trade {trade_id} to status: {status}")
            else:
                logging.warning(f"Trade with ID {trade_id} not found for status update.")


    async def _get_active_trades(self) -> list[TradeEntry]:
        async with self._get_db_session() as db:
            return db.query(TradeEntry).filter(TradeEntry.status == "ACTIVE").all()

    async def _get_trade_by_ca(self, ca_address: str) -> Optional[TradeEntry]:
        async with self._get_db_session() as db:
            return db.query(TradeEntry).filter(TradeEntry.ca_address == ca_address).first()

    async def _get_all_trades(self) -> list[TradeEntry]:
        async with self._get_db_session() as db:
            return db.query(TradeEntry).all()

    async def run_scheduled_tasks(self):
        """Runs periodic tasks like PnL reporting and auto-sell monitoring."""
        logging.info("Starting scheduled tasks loop...")
        while True:
            try:
                logging.info("Running scheduled tasks...")
                await self.generate_pnl_report()
                await self.monitor_auto_sell_triggers()
                await self._transfer_realized_pnl() # Transfer PNL if any
                logging.info(f"Scheduled tasks completed. Next run in {SCHEDULED_TASK_INTERVAL_SECONDS} seconds.")
                await asyncio.sleep(SCHEDULED_TASK_INTERVAL_SECONDS)
            except Exception as e:
                logging.error(f"Error in scheduled task loop: {e}", exc_info=True)
                await self.notifier.send_message(f"🚨 An error occurred in scheduled tasks: `{e}`")
                await asyncio.sleep(60) # Wait a bit longer before retrying after an error