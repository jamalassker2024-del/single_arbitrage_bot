import asyncio
import json
import websockets
from decimal import Decimal
import time

# --- INSTITUTIONAL CONFIG ---
CONFIG = {
    "SYMBOL": "BTCUSDT",
    "BINANCE_FEE": Decimal("0.001"), # 0.1% Taker
    "BYBIT_FEE": Decimal("0.001"),   # 0.1% Taker
    "MIN_SPREAD_THRESHOLD": Decimal("0.0035"), # 0.35% (Covers fees + slippage)
    "SLIPPAGE_PROTECTION": Decimal("0.0005"),  
}

class LeadLagArb:
    def __init__(self):
        # We store prices and the last time they were updated
        self.prices = {"binance": Decimal("0"), "bybit": Decimal("0")}
        self.last_update = {"binance": 0, "bybit": 0}
        self.balance = Decimal("2000.00")

    async def stream_binance(self):
        url = f"wss://stream.binance.com:9443/ws/{CONFIG['SYMBOL'].lower()}@ticker"
        async with websockets.connect(url) as ws:
            while True:
                data = json.loads(await ws.recv())
                self.prices["binance"] = Decimal(data['a']) # Best Ask
                self.last_update["binance"] = time.time()

    async def stream_bybit(self):
        url = "wss://stream.bybit.com/v5/public/spot"
        async with websockets.connect(url) as ws:
            # Subscribe to tickers for Spot
            await ws.send(json.dumps({"op": "subscribe", "args": [f"tickers.{CONFIG['SYMBOL'].upper()}"]}))
            while True:
                raw_data = await ws.recv()
                data = json.loads(raw_data)
                
                # Bybit Spot V5 returns data inside a 'data' object
                if 'data' in data:
                    ticker_data = data['data']
                    # Spot ticker can be a list or object; handle ask1Price correctly
                    if isinstance(ticker_data, dict) and 'ask1Price' in ticker_data:
                        self.prices["bybit"] = Decimal(ticker_data['ask1Price'])
                        self.last_update["bybit"] = time.time()
                    elif isinstance(ticker_data, list) and 'ask1Price' in ticker_data[0]:
                        self.prices["bybit"] = Decimal(ticker_data[0]['ask1Price'])
                        self.last_update["bybit"] = time.time()

    async def trade_engine(self):
        print("⚔️ ARBITRAGE ENGINE ONLINE | Monitoring Lead-Lag...")
        while True:
            b_p = self.prices["binance"]
            by_p = self.prices["bybit"]
            now = time.time()

            # INSTITUTIONAL RULE: Data must be fresh (less than 1 second old)
            if (now - self.last_update["binance"] < 1) and (now - self.last_update["bybit"] < 1):
                if b_p > 0 and by_p > 0:
                    # Binance is the Leader, Bybit is the Laggard
                    spread = (b_p - by_p) / by_p
                    total_costs = CONFIG["BINANCE_FEE"] + CONFIG["BYBIT_FEE"] + CONFIG["SLIPPAGE_PROTECTION"]

                    if spread > CONFIG["MIN_SPREAD_THRESHOLD"]:
                        net_profit = spread - total_costs
                        profit_usd = (Decimal("100.0") * net_profit)
                        
                        print(f"\n🚀 ARB FOUND! | LDR: {b_p} | LAG: {by_p} | Spread: {round(spread*100, 3)}%")
                        print(f"💰 ESTIMATED NET PROFIT: ${round(profit_usd, 3)}")
                        
                        self.balance += profit_usd
                        print(f"🏦 NEW TOTAL BALANCE: ${round(self.balance, 2)}")
                        await asyncio.sleep(1) # Small cooldown for price to settle

            await asyncio.sleep(0.01)

    async def run(self):
        await asyncio.gather(
            self.stream_binance(),
            self.stream_bybit(),
            self.trade_engine()
        )

if __name__ == "__main__":
    asyncio.run(LeadLagArb().run())
