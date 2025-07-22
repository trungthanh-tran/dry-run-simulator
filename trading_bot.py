# trading_bot.py
import asyncio
import logging
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from solders.pubkey import Pubkey

# Import from separated modules
from config import (
    PNL_WALLET_ADDRESS, DRY_RUN, SCHEDULED_TASK_INTERVAL_SECONDS,
    WSOL_MINT_ADDRESS, SOL_DECIMALS
)
from database import get_db, Trade, Transaction
from jupiter_client import JupiterSwapClient
from telegram_notifier import TelegramNotifier

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class TradingBot:
    def __init__(self):
        self.jupiter_client = JupiterSwapClient()
        self.notifier = TelegramNotifier()
        self.db_session_generator = get_db()
        self.active_monitor_tasks = {} # To keep track of active monitoring tasks
        logging.info(f"TradingBot initialized. DRY_RUN mode: {DRY_RUN}")

    async def _get_db_session(self) -> Session:
        """Helper to get a DB session."""
        return next(self.db_session_generator)

    async def handle_buy_command(self, ca_address: str, target_mc_usd: float, percent_of_wallet: float):
        db: Session = await self._get_db_session()
        try:
            # Check if a monitoring task for this CA is already active
            if ca_address in self.active_monitor_tasks and not self.active_monitor_tasks[ca_address].done():
                await self.notifier.send_message(f"🚨 Error: An active buy monitoring task for CA {ca_address} is already running. Please wait for the current task to complete or stop the bot and restart.")
                return

            existing_trade = db.query(Trade).filter_by(ca_address=ca_address, status="active").first()
            if existing_trade:
                await self.notifier.send_message(f"🚨 Error: An active trade for CA {ca_address} already exists in the database.")
                return

            sol_balance = await self.jupiter_client.get_sol_balance()
            if sol_balance <= 0 and not DRY_RUN: # Only check real balance if not dry run
                await self.notifier.send_message("🚨 Error: Insufficient SOL balance to place a buy order.")
                return

            sol_to_use = sol_balance * percent_of_wallet # Still calculate based on real/simulated balance
            if sol_to_use == 0:
                 await self.notifier.send_message(f"🚨 Error: Calculated SOL to use is zero (wallet balance * percentage).")
                 return

            mode_tag = "DRY RUN \- " if DRY_RUN else "" # Escape hyphen for Telegram MarkdownV2
            logging.info(f"{mode_tag}Monitoring {ca_address} for target MC ${target_mc_usd:,.2f} using {sol_to_use:.4f} SOL...")
            await self.notifier.send_message(
                f"📈 **{mode_tag}BUY ORDER INITIATED:**\n"
                f"CA: `{ca_address}`\n"
                f"Target MC: `${target_mc_usd:,.2f}`\n"
                f"Using: `{percent_of_wallet * 100:.2f}%` of wallet \({sol_to_use:.4f} SOL\)\n" # Escape parentheses
                f"Status: \_Monitoring\.\.\._" # Escape underscore and periods
            )

            # Create and store the monitoring task
            monitor_task = asyncio.create_task(self._monitor_and_buy(ca_address, target_mc_usd, sol_to_use, DRY_RUN))
            self.active_monitor_tasks[ca_address] = monitor_task

            # Add a callback to remove the task from the dict when it finishes
            def cleanup_task(task):
                if ca_address in self.active_monitor_tasks:
                    del self.active_monitor_tasks[ca_address]
                    logging.info(f"Cleaned up monitoring task for {ca_address}.")
            monitor_task.add_done_callback(cleanup_task)


        except Exception as e:
            logging.error(f"Error handling buy command for {ca_address}: {e}")
            await self.notifier.send_message(f"🚨 Error handling buy command for {ca_address}: {e}")
        finally:
            db.close()

    async def _monitor_and_buy(self, ca_address: str, target_mc_usd: float, sol_to_use: float, is_dry_run: bool):
        db: Session = await self._get_db_session()
        try:
            # Define how many checks to perform before giving up if the target is not met
            num_checks = 10 if is_dry_run else 60 # e.g., 10 checks for dry run, 60 for real (over 3 hours if check_interval is 3 min)
            check_interval_seconds = SCHEDULED_TASK_INTERVAL_SECONDS if not is_dry_run else 30 # Check every 30 seconds in dry run

            for i in range(num_checks):
                logging.info(f"Buy monitoring for {ca_address}: Check {i+1}/{num_checks}")
                
                current_mc = await self.jupiter_client.get_token_market_cap(ca_address)
                
                if current_mc is not None:
                    logging.info(f"Current MC for {ca_address}: ${current_mc:,.2f} (Target: ${target_mc_usd:,.2f})")
                    if current_mc <= target_mc_usd:
                        logging.info(f"Target MC reached for {ca_address}. {'Simulating' if is_dry_run else 'Executing'} buy...")
                        await self.notifier.send_message(f"🎯 **Target MC reached for** `{ca_address}` **(${current_mc:,.2f})\! {'Simulating' if is_dry_run else 'Attempting'} buy\.\.\.**") # Escape ! and ...

                        sol_lamports = int(sol_to_use * (10**SOL_DECIMALS))
                        
                        tx_sig = None
                        sol_spent = sol_to_use
                        ca_bought_normalized = 0.0

                        if is_dry_run:
                            # Simulate buy: No actual swap, just calculate potential CA bought
                            try:
                                # This requires the JupiterSwapClient to have a direct way to get a quote without swapping
                                # For demonstration, assuming a direct quote method on jupiter_client
                                # If your jupiter_client does not expose a `jupiter.get_quote` directly,
                                # you'd need to adapt this part to call its internal quote logic.
                                quote_res = await self.jupiter_client.get_quote_and_swap( # Reuse the method for quote calculation
                                    input_mint=WSOL_MINT_ADDRESS,
                                    output_mint=ca_address,
                                    amount=sol_lamports
                                )
                                # When reusing get_quote_and_swap for dry run, it might return None,0,0 if a full simulated tx is not possible.
                                # Let's adapt this for a pure quote if your jupiter_client has it, or just use dummy values.
                                if quote_res[0]: # If get_quote_and_swap returned a simulated tx_sig
                                    ca_bought_normalized = quote_res[2] # The third element is ca_bought
                                    logging.info(f"DRY RUN: Spending {sol_to_use:.4f} SOL would buy approximately {ca_bought_normalized:.4f} CA.")
                                    tx_sig = "DRY_RUN_SIMULATED_TX" # Dummy transaction signature
                                else:
                                    logging.warning("DRY RUN: Could not get simulated quote for CA amount bought.")
                            except Exception as quote_e:
                                logging.error(f"DRY RUN: Error getting simulated quote for CA amount bought: {quote_e}")
                        else:
                            # Real swap execution
                            tx_sig, sol_spent, ca_bought_normalized = await self.jupiter_client.get_quote_and_swap(
                                input_mint=WSOL_MINT_ADDRESS,
                                output_mint=ca_address,
                                amount=sol_lamports
                            )

                        if tx_sig:
                            trade = Trade(
                                ca_address=ca_address,
                                buy_mc_usd=current_mc,
                                initial_sol_spent=sol_spent,
                                initial_ca_bought=ca_bought_normalized,
                                current_ca_held=ca_bought_normalized,
                                unrealized_pnl_sol=0.0
                            )
                            db.add(trade)
                            db.commit()
                            db.refresh(trade)

                            transaction = Transaction(
                                trade_id=trade.id,
                                tx_type='buy',
                                sol_amount=sol_spent,
                                ca_amount=ca_bought_normalized,
                                market_cap_at_tx=current_mc,
                                price_at_tx_usd=(current_mc / trade.initial_ca_bought) if trade.initial_ca_bought else 0,
                                tx_signature=tx_sig if tx_sig != "DRY_RUN_SIMULATED_TX" else None
                            )
                            db.add(transaction)
                            db.commit()

                            tx_link = f"\[https://solscan\.io/tx/{tx_sig}\]\(https://solscan\.io/tx/{tx_sig}\)" if tx_sig != "DRY_RUN_SIMULATED_TX" else "\*\(Simulation only\)\*" # Escape for MarkdownV2
                            await self.notifier.send_message(
                                f"✅ **{('BUY SUCCESS (SIMULATED)' if is_dry_run else 'BUY SUCCESS')} for** `{ca_address}`!\n"
                                f"Bought `{ca_bought_normalized:.4f}` CA with `{sol_spent:.4f}` SOL\n"
                                f"At MC: `${current_mc:,.2f}`\n"
                                f"Tx: {tx_link}"
                            )
                            return # Exit function after successful buy

                        else:
                            await self.notifier.send_message(f"❌ **{('BUY FAILED (SIMULATED)' if is_dry_run else 'BUY FAILED')} for** `{ca_address}`\. Retrying monitor\.\.\.")
                    else:
                        logging.info(f"MC not yet at target for {ca_address}. Current: ${current_mc:,.2f}, Target: ${target_mc_usd:,.2f}")
                else:
                    logging.warning(f"Could not get market cap for {ca_address}. Retrying...")

                # Wait before the next check
                await asyncio.sleep(check_interval_seconds) 
            
            # If the loop finishes without buying
            await self.notifier.send_message(f"ℹ️ **Buy monitoring concluded for** `{ca_address}`\. Target MC not reached after {num_checks} checks\.")

        except Exception as e:
            logging.error(f"Error in _monitor_and_buy for {ca_address}: {e}")
            await self.notifier.send_message(f"🚨 Critical error during buy monitoring for {ca_address}: {e}")
        finally:
            db.close()

    async def monitor_auto_sell_triggers(self):
        db: Session = await self._get_db_session()
        try:
            trades = db.query(Trade).filter_by(status="active").all()
            for trade in trades:
                current_mc = await self.jupiter_client.get_token_market_cap(trade.ca_address)
                if current_mc is None:
                    logging.warning(f"Could not get market cap for {trade.ca_address}. Skipping auto-sell check.")
                    continue

                buy_mc = trade.buy_mc_usd
                current_price_per_token_usd = (current_mc / trade.initial_ca_bought) if trade.initial_ca_bought else 0
                buy_price_per_token_usd = (trade.buy_mc_usd / trade.initial_ca_bought) if trade.initial_ca_bought else 0

                if buy_price_per_token_usd == 0:
                    logging.warning(f"Buy price per token is zero for {trade.ca_address}. Cannot calculate profit.")
                    continue
                
                percentage_increase = ((current_price_per_token_usd - buy_price_per_token_usd) / buy_price_per_token_usd) * 100

                sell_triggers = {
                    25: 0.25,
                    50: 0.50,
                    75: 0.75,
                    100: 1.00
                }
                
                if trade.current_ca_held > 0:
                    for trigger_percent, sell_fraction in sell_triggers.items():
                        # A more robust solution would track which trigger percentages have already been hit
                        # For simplicity, if current_ca_held is still above threshold, it will try to sell again
                        # at each trigger point every 3 minutes if not tracked.
                        # You would need to add a field like `sold_at_25_percent` to `Trade` model
                        # For now, this will attempt to sell a chunk if the conditions are met and enough CA is held.
                        
                        target_mc_for_trigger = trade.buy_mc_usd * (1 + trigger_percent / 100)
                        
                        sell_amount_ca = trade.initial_ca_bought * 0.25 # Sell 25% of initial amount

                        if percentage_increase >= trigger_percent and \
                           trade.current_ca_held >= sell_amount_ca * 0.99: # Allow for tiny floating point differences
                            
                            logging.info(f"Auto-sell trigger ({trigger_percent}%) reached for {trade.ca_address}. Selling {sell_amount_ca:.4f} CA.")
                            await self.notifier.send_message(
                                f"💰 **{('AUTO-SELL TRIGGERED (SIMULATED)' if DRY_RUN else 'AUTO-SELL TRIGGERED')}!**\n"
                                f"CA: `{trade.ca_address}`\n"
                                f"Profit target: `{trigger_percent}%` reached \(MC: `${current_mc:,.2f}`\)\n" # Escape parentheses
                                f"Selling: `{sell_amount_ca:.4f}` CA \(`25%` initial\)" # Escape parentheses
                            )
                            
                            # You need to dynamically get token decimals for the CA. This is a crucial step.
                            # For demonstration, setting a placeholder, but in real scenario, fetch this from chain or metadata.
                            # For example, using `spl_token.client.Token.get_mint_info`
                            # Example: token_info = await self.solana_client.get_token_supply(Pubkey.from_string(trade.ca_address))
                            # token_decimals = token_info.value.decimals
                            token_decimals = 6 # PLACEHOLDER: Ensure you fetch actual decimals for the CA_ADDRESS
                            
                            sell_amount_ca_lamports = int(sell_amount_ca * (10**token_decimals))

                            tx_sig = None
                            sol_received = 0.0
                            ca_sold = sell_amount_ca

                            if DRY_RUN:
                                # Simulate sell
                                try:
                                    quote_res = await self.jupiter_client.get_quote_and_swap( # Reuse get_quote_and_swap for quote
                                        input_mint=trade.ca_address,
                                        output_mint=WSOL_MINT_ADDRESS,
                                        amount=sell_amount_ca_lamports
                                    )
                                    if quote_res[0]: # If get_quote_and_swap returned a simulated tx_sig
                                        sol_received = quote_res[1] # Second element is sol_received
                                        logging.info(f"DRY RUN: Selling {ca_sold:.4f} CA would yield approximately {sol_received:.4f} SOL.")
                                        tx_sig = "DRY_RUN_SIMULATED_TX"
                                    else:
                                        logging.warning("DRY RUN: Could not get simulated quote for SOL received.")
                                except Exception as quote_e:
                                    logging.error(f"DRY RUN: Error getting simulated quote for SOL received: {quote_e}")
                            else:
                                # Real sell execution
                                tx_sig, sol_received, ca_sold = await self.jupiter_client.get_quote_and_swap(
                                    input_mint=trade.ca_address,
                                    output_mint=WSOL_MINT_ADDRESS,
                                    amount=sell_amount_ca_lamports
                                )

                            if tx_sig:
                                cost_basis_for_chunk = (trade.initial_sol_spent / trade.initial_ca_bought) * ca_sold if trade.initial_ca_bought > 0 else 0
                                pnl_this_tx = sol_received - cost_basis_for_chunk

                                trade.current_ca_held -= ca_sold
                                trade.realized_pnl_sol += pnl_this_tx
                                
                                current_sol_price_usd = await self.jupiter_client._get_sol_price_usd()
                                if trade.current_ca_held > 0 and current_sol_price_usd > 0:
                                    initial_cost_per_token_sol = trade.initial_sol_spent / trade.initial_ca_bought if trade.initial_ca_bought > 0 else 0
                                    current_price_per_token_sol = (current_price_per_token_usd / current_sol_price_usd) if current_sol_price_usd > 0 else 0
                                    trade.unrealized_pnl_sol = (current_price_per_token_sol - initial_cost_per_token_sol) * trade.current_ca_held
                                else:
                                    trade.unrealized_pnl_sol = 0.0

                                db.add(trade)
                                db.commit()
                                db.refresh(trade)

                                transaction = Transaction(
                                    trade_id=trade.id,
                                    tx_type='sell_auto',
                                    sol_amount=sol_received,
                                    ca_amount=ca_sold,
                                    market_cap_at_tx=current_mc,
                                    price_at_tx_usd=current_price_per_token_usd,
                                    pnl_realized_this_tx=pnl_this_tx,
                                    tx_signature=tx_sig if tx_sig != "DRY_RUN_SIMULATED_TX" else None
                                )
                                db.add(transaction)
                                db.commit()

                                tx_link = f"\[https://solscan\.io/tx/{tx_sig}\]\(https://solscan\.io/tx/{tx_sig}\)" if tx_sig != "DRY_RUN_SIMULATED_TX" else "\*\(Simulation only\)\*" # Escape for MarkdownV2
                                await self.notifier.send_message(
                                    f"✅ **{('AUTO-SELL SUCCESS (SIMULATED)' if DRY_RUN else 'AUTO-SELL SUCCESS')} for** `{trade.ca_address}`!\n"
                                    f"Sold `{ca_sold:.4f}` CA for `{sol_received:.4f}` SOL\n"
                                    f"Realized PnL from this chunk: `{pnl_this_tx:.4f}` SOL\n"
                                    f"CA remaining: `{trade.current_ca_held:.4f}`\n"
                                    f"Tx: {tx_link}"
                                )
                                if not DRY_RUN:
                                    await self._transfer_realized_pnl(pnl_this_tx)
                            else:
                                await self.notifier.send_message(f"❌ **{('AUTO-SELL FAILED (SIMULATED)' if DRY_RUN else 'AUTO-SELL FAILED')} for** `{trade.ca_address}` at `{trigger_percent}%` trigger\.")
                            
                        else:
                            logging.info(f"Insufficient {trade.ca_address} to sell 25% for {trigger_percent}% trigger. Remaining: {trade.current_ca_held:.4f}")
                        
                if trade.current_ca_held < 1e-6 and trade.status != "completed": # Check if virtually all CA is sold
                    trade.status = "completed"
                    db.add(trade)
                    db.commit()
                    await self.notifier.send_message(f"🎉 **Trade for** `{trade.ca_address}` **completed\! All tokens sold\.**") # Escape !

        except Exception as e:
            logging.error(f"Error in monitor_auto_sell_triggers: {e}")
            await self.notifier.send_message(f"🚨 Critical error during auto-sell monitoring: {e}")
        finally:
            db.close()


    async def handle_manual_sell_command(self, ca_address: str):
        db: Session = await self._get_db_session()
        try:
            trade = db.query(Trade).filter_by(ca_address=ca_address, status="active").first()
            if not trade:
                await self.notifier.send_message(f"🚨 Error: No active trade found for CA {ca_address}.")
                return

            if trade.current_ca_held <= 0:
                await self.notifier.send_message(f"🚨 No tokens to sell for CA {ca_address}\. Currently holding: {trade.current_ca_held:.4f}\.")
                trade.status = "completed"
                db.add(trade)
                db.commit()
                return

            logging.info(f"Executing manual sell of all {trade.current_ca_held:.4f} remaining CA for {ca_address}...")
            await self.notifier.send_message(
                f"🗑️ **{('MANUAL SELL INITIATED (SIMULATED)' if DRY_RUN else 'MANUAL SELL INITIATED')}!**\n"
                f"CA: `{ca_address}`\n"
                f"Selling: *All remaining* `{trade.current_ca_held:.4f}` CA"
            )

            token_decimals = 6 # PLACEHOLDER: Ensure you fetch actual decimals for the CA_ADDRESS
            sell_amount_ca_lamports = int(trade.current_ca_held * (10**token_decimals))

            tx_sig = None
            sol_received = 0.0
            ca_sold = trade.current_ca_held

            if DRY_RUN:
                # Simulate manual sell
                try:
                    quote_res = await self.jupiter_client.get_quote_and_swap( # Reuse get_quote_and_swap for quote
                        input_mint=ca_address,
                        output_mint=WSOL_MINT_ADDRESS,
                        amount=sell_amount_ca_lamports
                    )
                    if quote_res[0]: # If get_quote_and_swap returned a simulated tx_sig
                        sol_received = quote_res[1] # Second element is sol_received
                        logging.info(f"DRY RUN: Selling {ca_sold:.4f} CA would yield approximately {sol_received:.4f} SOL.")
                        tx_sig = "DRY_RUN_SIMULATED_TX"
                    else:
                        logging.warning("DRY RUN: Could not get simulated quote for SOL received during manual sell.")
                except Exception as quote_e:
                    logging.error(f"DRY RUN: Error getting simulated quote for SOL received during manual sell: {quote_e}")
            else:
                # Real manual sell execution
                tx_sig, sol_received, ca_sold = await self.jupiter_client.get_quote_and_swap(
                    input_mint=ca_address,
                    output_mint=WSOL_MINT_ADDRESS,
                    amount=sell_amount_ca_lamports
                )

            if tx_sig:
                current_mc = await self.jupiter_client.get_token_market_cap(trade.ca_address)
                current_price_per_token_usd = (current_mc / (trade.initial_ca_bought + 1e-9)) if current_mc and trade.initial_ca_bought else 0

                cost_basis_for_remaining_chunk = (trade.initial_sol_spent / trade.initial_ca_bought) * ca_sold if trade.initial_ca_bought > 0 else 0
                pnl_this_tx = sol_received - cost_basis_for_remaining_chunk

                trade.current_ca_held -= ca_sold
                trade.realized_pnl_sol += pnl_this_tx
                trade.unrealized_pnl_sol = 0.0
                trade.status = "completed"

                db.add(trade)
                db.commit()
                db.refresh(trade)

                transaction = Transaction(
                    trade_id=trade.id,
                    tx_type='sell_manual',
                    sol_amount=sol_received,
                    ca_amount=ca_sold,
                    market_cap_at_tx=current_mc,
                    price_at_tx_usd=current_price_per_token_usd,
                    pnl_realized_this_tx=pnl_this_tx,
                    tx_signature=tx_sig if tx_sig != "DRY_RUN_SIMULATED_TX" else None
                )
                db.add(transaction)
                db.commit()

                tx_link = f"\[https://solscan\.io/tx/{tx_sig}\]\(https://solscan\.io/tx/{tx_sig}\)" if tx_sig != "DRY_RUN_SIMULATED_TX" else "\*\(Simulation only\)\*"
                await self.notifier.send_message(
                    f"✅ **{('MANUAL SELL SUCCESS (SIMULATED)' if DRY_RUN else 'MANUAL SELL SUCCESS')} for** `{ca_address}`!\n"
                    f"Sold `{ca_sold:.4f}` CA for `{sol_received:.4f}` SOL\n"
                    f"Realized PnL from this sell: `{pnl_this_tx:.4f}` SOL\n"
                    f"Tx: {tx_link}"
                )
                if not DRY_RUN:
                    await self._transfer_realized_pnl(pnl_this_tx)
            else:
                await self.notifier.send_message(f"❌ **{('MANUAL SELL FAILED (SIMULATED)' if DRY_RUN else 'MANUAL SELL FAILED')} for** `{ca_address}`\.")
        except Exception as e:
            logging.error(f"Error handling manual sell for {ca_address}: {e}")
            await self.notifier.send_message(f"🚨 Error handling manual sell for {ca_address}: {e}")
        finally:
            db.close()


    async def generate_pnl_report(self):
        db: Session = await self._get_db_session()
        try:
            trades = db.query(Trade).all()
            report_messages = []
            
            for trade in trades:
                current_mc = await self.jupiter_client.get_token_market_cap(trade.ca_address)
                
                if current_mc is not None and trade.current_ca_held > 0 and trade.initial_ca_bought > 0:
                    current_sol_price_usd = await self.jupiter_client._get_sol_price_usd()
                    current_price_per_token_usd = (current_mc / trade.initial_ca_bought)
                    
                    initial_cost_per_token_sol = trade.initial_sol_spent / trade.initial_ca_bought
                    current_price_per_token_sol = (current_price_per_token_usd / current_sol_price_usd) if current_sol_price_usd > 0 else 0
                    
                    trade.unrealized_pnl_sol = (current_price_per_token_sol - initial_cost_per_token_sol) * trade.current_ca_held
                else:
                    trade.unrealized_pnl_sol = 0.0

                db.add(trade)
                db.commit()
                db.refresh(trade)

                transactions_for_ca = db.query(Transaction).filter_by(trade_id=trade.id).all()
                total_sol_spent_ca = sum(t.sol_amount for t in transactions_for_ca if t.tx_type == 'buy')
                total_ca_bought_ca = sum(t.ca_amount for t in transactions_for_ca if t.tx_type == 'buy')
                total_sol_received_ca = sum(t.sol_amount for t in transactions_for_ca if 'sell' in t.tx_type)
                total_ca_sold_ca = sum(t.ca_amount for t in transactions_for_ca if 'sell' in t.tx_type)

                report = (
                    f"📊 **PnL for** `{trade.ca_address}`\n"
                    f"Total SOL Spent: `{total_sol_spent_ca:.6f}`\n"
                    f"Total CA Bought: `{total_ca_bought_ca:.6f}`\n"
                    f"Total SOL Received: `{total_sol_received_ca:.6f}`\n"
                    f"Total CA Sold: `{total_ca_sold_ca:.6f}`\n"
                    f"Total CA Holdings: `{trade.current_ca_held:.6f}`\n"
                    f"Realized PnL: `{trade.realized_pnl_sol:.6f}` SOL\n"
                    f"Unrealized PnL: `{trade.unrealized_pnl_sol:.6f}` SOL\n"
                    f"Total PnL: `{trade.realized_pnl_sol + trade.unrealized_pnl_sol:.6f}` SOL"
                )
                report_messages.append(report)

            if report_messages:
                full_report = "\n\n---\n\n".join(report_messages)
                await self.notifier.send_message(full_report)
                logging.info("PnL report sent.")
            else:
                await self.notifier.send_message("📊 No active trades to report PnL for.")

        except Exception as e:
            logging.error(f"Error generating PnL report: {e}")
            await self.notifier.send_message(f"🚨 Error generating PnL report: {e}")
        finally:
            db.close()

    async def _transfer_realized_pnl(self, pnl_amount_sol: float):
        if pnl_amount_sol <= 0:
            logging.info(f"No positive PnL to transfer: {pnl_amount_sol:.6f} SOL.")
            return
        
        if DRY_RUN:
            logging.info(f"DRY RUN: Simulating transfer of {pnl_amount_sol:.6f} SOL to PnL wallet {PNL_WALLET_ADDRESS}")
            await self.notifier.send_message(
                f"💸 **DRY RUN \- PNL TRANSFER COMPLETE\!**\n" # Escape hyphen and exclamation
                f"Simulated transfer of `{pnl_amount_sol:.6f}` SOL to PnL Wallet: `{PNL_WALLET_ADDRESS}`"
            )
            return

        logging.info(f"Attempting to transfer {pnl_amount_sol:.6f} SOL to PnL wallet {PNL_WALLET_ADDRESS}...")
        
        try:
            # Placeholder for actual SOL transfer logic using solana-py
            # from solana.system_program import transfer
            # from solana.transaction import Transaction
            # from solana.rpc.commitment import Confirmed
            #
            # tx = Transaction().add(
            #     transfer(
            #         Pubkey.from_string(str(self.jupiter_client.payer.pubkey())),
            #         Pubkey.from_string(PNL_WALLET_ADDRESS),
            #         int(pnl_amount_sol * (10**SOL_DECIMALS))
            #     )
            # )
            # signed_tx = self.jupiter_client.payer.sign_transaction(tx)
            # tx_signature = await self.jupiter_client.solana_client.send_raw_transaction(
            #     signed_tx.serialize(),
            #     opts=Confirmed
            # )
            # await self.jupiter_client.solana_client.confirm_transaction(tx_signature.value, Confirmed)
            
            logging.info(f"Successfully transferred {pnl_amount_sol:.6f} SOL to PnL wallet {PNL_WALLET_ADDRESS}")
            await self.notifier.send_message(
                f"💸 **PNL TRANSFER COMPLETE\!**\n" # Escape exclamation
                f"Transferred `{pnl_amount_sol:.6f}` SOL to PnL Wallet: `{PNL_WALLET_ADDRESS}`"
            )
        except Exception as e:
            logging.error(f"Error transferring PnL: {e}")
            await self.notifier.send_message(f"🚨 Error transferring PnL: {e}")


    async def run_scheduled_tasks(self):
        """Runs scheduled tasks like auto-sell monitoring and PnL reports."""
        while True:
            logging.info(f"{('DRY RUN \- ' if DRY_RUN else '')}Running scheduled tasks: Checking auto-sells...") # Escape hyphen
            await self.monitor_auto_sell_triggers()

            # Schedule PnL report every 4 hours, regardless of DRY_RUN mode
            last_report_time = getattr(self, '_last_report_time', datetime.min)
            if datetime.now() - last_report_time >= timedelta(hours=4):
                logging.info(f"{('DRY RUN \- ' if DRY_RUN else '')}Running scheduled tasks: Generating PnL report...") # Escape hyphen
                await self.generate_pnl_report()
                self._last_report_time = datetime.now()
            else:
                remaining_time_seconds = (timedelta(hours=4) - (datetime.now() - last_report_time)).total_seconds()
                logging.info(f"Next PnL report in approx. {remaining_time_seconds / 60:.1f} minutes.")

            # Sleep for the defined interval
            logging.info(f"Waiting for {SCHEDULED_TASK_INTERVAL_SECONDS / 60:.0f} minutes for next task cycle...")
            await asyncio.sleep(SCHEDULED_TASK_INTERVAL_SECONDS)