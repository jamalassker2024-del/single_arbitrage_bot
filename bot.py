import asyncio
import json
import websockets
from decimal import Decimal, getcontext
import time
import sys

getcontext().prec = 12

# ========== CONFIGURATION ==========
CONFIG = {
    "SYMBOLS": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "PEPEUSDT", "DOGEUSDT", "SUIUSDT"],
    "BINANCE_FEE": Decimal("0.001"),          
    "BYBIT_FEE": Decimal("0.001"),
    "MARKET_ORDER_THRESHOLD": Decimal("0.0015"),   # Dropped slightly to be more aggressive
    "LIMIT_ORDER_THRESHOLD": Decimal("0.0010"),    
    "ORDER_SIZE_USDT": Decimal("50"),              
    "SLIPPAGE_MARKET": Decimal("0.0003"),          
    "MAX_LIMIT_WAIT_SEC": 5,
    "LATENCY_MS": 100,
}

class RealArbitrageExecutor:
    def __init__(self):
        self.prices = {s: {"binance_ask": Decimal("0"), "binance_bid": Decimal("0"),
                           "bybit_ask": Decimal("0"), "bybit_bid": Decimal("0")}
                       for s in CONFIG["SYMBOLS"]}
        self.updates = {s: {"binance": 0, "bybit": 0} for s in CONFIG["SYMBOLS"]}
        self.open_limit_orders = []
        self.stats = {
            "total_trades": 0,
            "winning_trades": 0,
            "total_profit_usdt": Decimal("0"),
            "total_fees_paid": Decimal("0"),
        }
        self.last_log = 0
        self.running = True

    async def stream_binance(self):
        while self.running:
            try:
                streams = "/".join([f"{s.lower()}@bookTicker" for s in CONFIG["SYMBOLS"]])
                url = f"wss://stream.binance.com:9443/stream?streams={streams}"
                async with websockets.connect(url) as ws:
                    while self.running:
                        msg = await ws.recv()
                        data = json.loads(msg)
                        if 'data' in data:
                            d = data['data']
                            s = d['s']
                            self.prices[s]["binance_ask"] = Decimal(str(d['a']))
                            self.prices[s]["binance_bid"] = Decimal(str(d['b']))
                            self.updates[s]["binance"] = time.time()
            except Exception as e:
                await asyncio.sleep(2)

    async def stream_bybit(self):
        while self.running:
            try:
                url = "wss://stream.bybit.com/v5/public/spot"
                async with websockets.connect(url) as ws:
                    await ws.send(json.dumps({"op": "subscribe", "args": [f"tickers.{s}" for s in CONFIG["SYMBOLS"]]}))
                    while self.running:
                        msg = await ws.recv()
                        data = json.loads(msg)
                        if 'data' in data:
                            # Bybit V5 can send a dict or a list in 'data'
                            d = data['data']
                            if isinstance(d, list): d = d[0] # Handle list wrapper
                            
                            symbol = d.get('s') or d.get('symbol')
                            if symbol in self.prices:
                                if 'ask1Price' in d and d['ask1Price']:
                                    self.prices[symbol]["bybit_ask"] = Decimal(str(d['ask1Price']))
                                if 'bid1Price' in d and d['bid1Price']:
                                    self.prices[symbol]["bybit_bid"] = Decimal(str(d['bid1Price']))
                                self.updates[symbol]["bybit"] = time.time()
            except Exception as e:
                await asyncio.sleep(2)

    async def execute_market_arbitrage(self, symbol, buy_exch, sell_exch, buy_price, sell_price):
        await asyncio.sleep(CONFIG["LATENCY_MS"] / 1000.0)
        
        # Freshness Check
        if time.time() - self.updates[symbol]["binance"] > 5 or time.time() - self.updates[symbol]["bybit"] > 5:
            return

        buy_price_exec = buy_price * (1 + CONFIG["SLIPPAGE_MARKET"])
        sell_price_exec = sell_price * (1 - CONFIG["SLIPPAGE_MARKET"])
        
        qty = CONFIG["ORDER_SIZE_USDT"] / buy_price_exec
        net_profit = (qty * sell_price_exec) - (qty * buy_price_exec) - (CONFIG["ORDER_SIZE_USDT"] * (CONFIG["BINANCE_FEE"] + CONFIG["BYBIT_FEE"]))

        self.stats["total_trades"] += 1
        if net_profit > 0: self.stats["winning_trades"] += 1
        self.stats["total_profit_usdt"] += net_profit
        print(f"\n🔥 {symbol} MARKET ARB | Profit: ${net_profit:.4f} | Buy {buy_exch} Sell {sell_exch}")

    async def scan_opportunities(self):
        for sym in CONFIG["SYMBOLS"]:
            p = self.prices[sym]
            if p["binance_ask"] == 0 or p["bybit_ask"] == 0: continue

            # Direction A: Buy Binance, Sell Bybit
            spread_a = (p["bybit_bid"] - p["binance_ask"]) / p["binance_ask"]
            # Direction B: Buy Bybit, Sell Binance
            spread_b = (p["binance_bid"] - p["bybit_ask"]) / p["bybit_ask"]

            if spread_a >= CONFIG["MARKET_ORDER_THRESHOLD"]:
                await self.execute_market_arbitrage(sym, "binance", "bybit", p["binance_ask"], p["bybit_bid"])
            elif spread_b >= CONFIG["MARKET_ORDER_THRESHOLD"]:
                await self.execute_market_arbitrage(sym, "bybit", "binance", p["bybit_ask"], p["binance_bid"])

    async def run(self):
        print("🚀 ARBITRAGE ENGINE STARTING...")
        asyncio.create_task(self.stream_binance())
        asyncio.create_task(self.stream_bybit())

        while self.running:
            await self.scan_opportunities()
            
            now = time.time()
            if now - self.last_log > 10:
                # Debug output to verify we are actually getting data
                active_symbols = [s for s in CONFIG["SYMBOLS"] if self.prices[s]["binance_ask"] > 0 and self.prices[s]["bybit_ask"] > 0]
                print(f"📡 Monitoring: {len(active_symbols)}/{len(CONFIG['SYMBOLS'])} pairs | Balance: ${2000 + self.stats['total_profit_usdt']:.2f}")
                self.last_log = now
            await asyncio.sleep(0.01)

if __name__ == "__main__":
    try:
        asyncio.run(RealArbitrageExecutor().run())
    except KeyboardInterrupt:
        print("\nStopping...")
