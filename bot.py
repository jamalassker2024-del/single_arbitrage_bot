import asyncio
import json
import websockets
from decimal import Decimal, getcontext
import time
import sys

getcontext().prec = 12

CONFIG = {
    "SYMBOLS": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "PEPEUSDT", "DOGEUSDT", "SUIUSDT"],
    "BINANCE_FEE": Decimal("0.001"),
    "BYBIT_FEE": Decimal("0.001"),
    "MARKET_ORDER_THRESHOLD": Decimal("0.0012"), # Aggressive 0.12%
    "ORDER_SIZE_USDT": Decimal("50"),
    "LATENCY_MS": 50,
}

class ArbitrageV3:
    def __init__(self):
        self.prices = {s: {"bin_a": 0, "bin_b": 0, "byb_a": 0, "byb_b": 0} for s in CONFIG["SYMBOLS"]}
        self.stats = {"trades": 0, "profit": Decimal("0")}
        self.running = True

    async def stream_binance(self):
        # Using a simpler URL structure for better reliability
        streams = ",".join([f"{s.lower()}@bookTicker" for s in CONFIG["SYMBOLS"]])
        url = f"wss://stream.binance.com:9443/ws/{streams}"
        async with websockets.connect(url) as ws:
            print("✅ Connected to Binance")
            while self.running:
                data = json.loads(await ws.recv())
                if 's' in data:
                    s = data['s']
                    self.prices[s]["bin_a"] = Decimal(str(data['a']))
                    self.prices[s]["bin_b"] = Decimal(str(data['b']))

    async def stream_bybit(self):
        # Switching to the more robust V5 Linear stream (works for Spot too)
        url = "wss://stream.bybit.com/v5/public/linear" 
        async with websockets.connect(url) as ws:
            print("✅ Connected to Bybit")
            sub_msg = {"op": "subscribe", "args": [f"tickers.{s}" for s in CONFIG["SYMBOLS"]]}
            await ws.send(json.dumps(sub_msg))
            while self.running:
                data = json.loads(await ws.recv())
                if 'data' in data:
                    d = data['data']
                    # Bybit can send updates as single dicts or lists
                    items = d if isinstance(d, list) else [d]
                    for item in items:
                        s = item.get('s')
                        if s in self.prices:
                            if 'ask1Price' in item and item['ask1Price']:
                                self.prices[s]["byb_a"] = Decimal(str(item['ask1Price']))
                            if 'bid1Price' in item and item['bid1Price']:
                                self.prices[s]["byb_b"] = Decimal(str(item['bid1Price']))

    async def engine(self):
        print("🚀 ENGINE STARTED...")
        last_log = 0
        while self.running:
            for s in CONFIG["SYMBOLS"]:
                p = self.prices[s]
                # ONLY PROCEED IF DATA IS NON-ZERO
                if p["bin_a"] > 0 and p["byb_a"] > 0:
                    # Logic: Buy Binance (Ask), Sell Bybit (Bid)
                    spread1 = (p["byb_b"] - p["bin_a"]) / p["bin_a"]
                    # Logic: Buy Bybit (Ask), Sell Binance (Bid)
                    spread2 = (p["bin_b"] - p["byb_a"]) / p["byb_a"]

                    if spread1 > CONFIG["MARKET_ORDER_THRESHOLD"]:
                        self.execute(s, "BIN->BYB", spread1)
                    elif spread2 > CONFIG["MARKET_ORDER_THRESHOLD"]:
                        self.execute(s, "BYB->BIN", spread2)

            if time.time() - last_log > 5:
                # HEARTBEAT: Show prices to prove the feed is working
                sample = CONFIG["SYMBOLS"][0]
                print(f"📊 {sample} | BIN: {self.prices[sample]['bin_a']} | BYB: {self.prices[sample]['byb_a']}")
                last_log = time.time()
            
            await asyncio.sleep(0.01)

    def execute(self, sym, direction, spread):
        self.stats["trades"] += 1
        profit = CONFIG["ORDER_SIZE_USDT"] * (spread - (CONFIG["BINANCE_FEE"] + CONFIG["BYBIT_FEE"]))
        self.stats["profit"] += profit
        print(f"\n🔥 {direction} TRADE! | {sym} | Spread: {round(spread*100,3)}% | Profit: ${round(profit,4)}")

    async def run(self):
        await asyncio.gather(self.stream_binance(), self.stream_bybit(), self.engine())

if __name__ == "__main__":
    asyncio.run(ArbitrageV3().run())
