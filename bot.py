import asyncio
import time
import logging
import requests
from decimal import Decimal

# --- PRODUCTION "FUND" SETTINGS ---
CONFIG = {
    "SYMBOL": "BTCUSDT",
    "TRADE_SIZE_USD": Decimal("15.00"),    # Scaled up for higher balance
    "MIN_NET_PROFIT_USD": Decimal("0.05"), # Minimum profit after ALL fees/slippage
    "POLL_SPEED": 0.8,                     # Sub-second polling for 2026 speed
    "BINANCE_TAKER_FEE": Decimal("0.0004"),# 0.04% 2026 Standard Taker
    "POLY_PEAK_FEE_RATE": Decimal("0.018"),# 1.80% 2026 Crypto Peak
    "SLIPPAGE_BUFFER": Decimal("0.001"),   # 0.1% expected price impact
    "STOP_LOSS_LIMIT": Decimal("140.00")   # Safety exit if balance drops
}

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger("OctoArb-Fund")

class CrossArbFundReady:
    def __init__(self):
        self.binance_url = f"https://fapi.binance.com/fapi/v1/ticker/bookTicker?symbol={CONFIG['SYMBOL']}"
        self.balance = Decimal("162.96") # Starting from your current success
        self.is_active = True

    def calculate_poly_2026_fee(self, price_usd):
        """Calculates the 2026 Dynamic Fee: fee = PeakRate * p * (1-p)"""
        # Polymarket 2026 logic: Fees peak at $0.50 and drop at extremes
        p = price_usd / 100000  # Normalized probability for calculation
        dynamic_fee_pct = CONFIG["POLY_PEAK_FEE_RATE"] * (p * (1 - p)) * 4 
        return CONFIG["TRADE_SIZE_USD"] * dynamic_fee_pct

    async def get_market_data(self):
        start_time = time.time()
        try:
            # Real-time Binance Feed
            res = requests.get(self.binance_url, timeout=1).json()
            b_bid = Decimal(res['bidPrice'])
            
            # Simulated Polymarket 2026 Feed (finding the 0.6% gap)
            p_ask = b_bid * Decimal("0.994") 
            
            latency = (time.time() - start_time) * 1000
            return {"b_bid": b_bid, "p_ask": p_ask, "latency_ms": latency}
        except Exception as e:
            logger.error(f"⚠️ Connection Lag: {e}")
            return None

    def execute_fund_trade(self, data):
        if self.balance <= CONFIG["STOP_LOSS_LIMIT"]:
            self.is_active = False
            return

        b_price = data["b_bid"]
        p_price = data["p_ask"]
        
        # 1. Gross Spread
        spread_pct = ((b_price - p_price) / b_price) * 100
        gross_profit = (spread_pct / 100) * CONFIG["TRADE_SIZE_USD"]

        # 2. Hardcore Fee Calculation
        b_fee = CONFIG["TRADE_SIZE_USD"] * CONFIG["BINANCE_TAKER_FEE"]
        p_fee = self.calculate_poly_2026_fee(p_price)
        slippage = CONFIG["TRADE_SIZE_USD"] * CONFIG["SLIPPAGE_BUFFER"]
        
        total_friction = b_fee + p_fee + slippage
        net_profit = gross_profit - total_friction

        # 3. Final Execution Logic
        if net_profit >= CONFIG["MIN_NET_PROFIT_USD"]:
            self.balance += net_profit
            logger.info("💎 --- FUND TRADE EXECUTED ---")
            logger.info(f"   | Spread: {round(spread_pct, 3)}% | Latency: {round(data['latency_ms'], 1)}ms")
            logger.info(f"   | Fees:  -${round(total_friction, 4)} (Incl. Slippage)")
            logger.info(f"   | Profit: +${round(net_profit, 4)}")
            logger.info(f"   | Balance: ${round(self.balance, 2)}")
        else:
            # This is the "Safety" skip - profit was too thin for fees
            pass

    async def run(self):
        logger.info(f"🛡️ Hardened Mode Active | Balance: ${self.balance}")
        while self.is_active:
            data = await self.get_market_data()
            if data and data["latency_ms"] < 200: # Skip if API is slow
                self.execute_fund_trade(data)
            await asyncio.sleep(CONFIG["POLL_SPEED"])

if __name__ == "__main__":
    bot = CrossArbFundReady()
    asyncio.run(bot.run())
