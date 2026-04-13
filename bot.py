import asyncio
import json
import websockets
from decimal import Decimal
import time
import sys

# --- INSTITUTIONAL MULTI-PAIR CONFIG ---
CONFIG = {
    # Top 10 Volatile Pairs for 2026 Lead-Lag
    "SYMBOLS": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "PEPEUSDT", "SUIUSDT", 
                "AVAXUSDT", "APTUSDT", "ARBUSDT", "DOGEUSDT", "NEARUSDT"],
    "BINANCE_FEE": Decimal("0.001"), 
    "BYBIT_FEE": Decimal("0.001"),   
    "MIN_SPREAD_THRESHOLD": Decimal("0.0028"), # Lowered to 0.28% for aggressive entry
    "SLIPPAGE_PROTECTION": Decimal("0.0004"),  
    "LOG_INTERVAL": 5
}

class SovereignScalper:
    def __init__(self):
        # Nested dict: self.prices['BTCUSDT']['binance']
        self.prices = {s: {"binance": Decimal("0"), "bybit": Decimal("0")} for s in CONFIG["SYMBOLS"]}
        self.last_update = {s: {"binance": 0, "bybit": 0} for s in CONFIG["SYMBOLS"]}
        self.balance = Decimal("2000.00")
        self.last_log = 0

    async def stream_binance(self):
        # Combined stream: stream?streams=btcusdt@bookTicker/ethusdt@bookTicker...
        streams = "/".join([f"{s.lower()}@bookTicker" for s in CONFIG["SYMBOLS"]])
        url = f"wss://stream.binance.com:9443/stream?streams={streams}"
        
        async with websockets.connect(url) as ws:
            while True:
                raw = await ws.recv()
                data = json.loads(raw)
                symbol = data['data']['s'] # e.g. BTCUSDT
                self.prices[symbol]["binance"] = Decimal(data['data']['a']) # Best Ask
                self.last_update[symbol]["binance"] = time.time()

    async def stream_bybit(self):
        url = "wss://stream.bybit.com/v5/public/spot"
        async with websockets.connect(url) as ws:
            # Subscribe to all symbols in one go
            args = [f"tickers.{s}" for s in CONFIG["SYMBOLS"]]
            await ws.send(json.dumps({"op": "subscribe", "args": args}))
            
            while True:
                raw = await ws.recv()
                data = json.loads(raw)
                if 'data' in data:
                    d = data['data']
                    symbol = d.get('symbol')
                    # Bybit Spot V5 uses ask1Price
                    if symbol in self.prices and 'ask1Price' in d:
                        self.prices[symbol]["bybit"] = Decimal(d['ask1Price'])
                        self.last_update[symbol]["bybit"] = time.time()

    async def engine(self):
        print(f"⚔️ SOVEREIGN SCALPER ONLINE | Monitoring {len(CONFIG['SYMBOLS'])} Pairs")
        while True:
            now = time.time()
            for s in CONFIG["SYMBOLS"]:
                b_p = self.prices[s]["binance"]
                by_p = self.prices[s]["bybit"]

                # Ensure data is fresh (< 2s) and both prices exist
                if (now - self.last_update[s]["binance"] < 2) and (now - self.last_update[s]["bybit"] < 2):
                    if b_p > 0 and by_p > 0:
                        spread = (b_p - by_p).copy_abs() / min(b_p, by_p)
                        costs = CONFIG["BINANCE_FEE"] + CONFIG["BYBIT_FEE"] + CONFIG["SLIPPAGE_PROTECTION"]

                        if spread > CONFIG["MIN_SPREAD_THRESHOLD"]:
                            profit = (Decimal("100.0") * (spread - costs))
                            self.balance += profit
                            print(f"\n🚀 ARB: {s} | Spread: {round(spread*100,3)}% | Net: +${round(profit,3)}")
                            print(f"🏦 BALANCE: ${round(self.balance, 2)}")
                            # Cooldown per symbol to prevent double-dipping same move
                            self.last_update[s]["binance"] = 0 

            # Heartbeat Log
            if now - self.last_log > CONFIG["LOG_INTERVAL"]:
                sys.stdout.write(f"\r📡 SCANNING {len(CONFIG['SYMBOLS'])} PAIRS | BAL: ${round(self.balance,2)}   ")
                sys.stdout.flush()
                self.last_log = now
            
            await asyncio.sleep(0.01)

    async def run(self):
        await asyncio.gather(self.stream_binance(), self.stream_bybit(), self.engine())

if __name__ == "__main__":
    asyncio.run(SovereignScalper().run())
