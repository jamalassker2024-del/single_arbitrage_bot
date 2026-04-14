#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
OCTOBOT V6: UNIFIED LIMIT-MAKER SCALPER
- High-frequency limit order entry/exit (Maker)
- Best-bid/Best-ask fill simulation logic
- Adaptive cooldowns (longer wait after a loss)
- Strict 10s hold time with Market emergency exit
"""

import asyncio
import json
import websockets
from decimal import Decimal, getcontext
import time

getcontext().prec = 12

CONFIG = {
    "SYMBOLS": ["NEIROUSDT", "DOGSUSDT", "PEPEUSDT", "1000FLOKIUSDT", "WIFUSDT"],
    "ORDER_SIZE_USDT": Decimal("5.00"),
    "INITIAL_BALANCE": Decimal("100.00"),
    "OFI_THRESHOLD": Decimal("0.65"),      # Signal sensitivity
    "TAKE_PROFIT_BPS": Decimal("2"),       # 0.02% Target
    "STOP_LOSS_BPS": Decimal("5"),         # 0.05% Stop
    "MAX_HOLD_SECONDS": 10,                # Emergency exit time
    "ENTRY_TIMEOUT_SEC": 3,                # Cancel unfilled limits
    "WIN_COOLDOWN_SEC": 1,                 # Rapid fire on wins
    "LOSS_COOLDOWN_SEC": 20,               # Chill out after a loss
    "SCAN_INTERVAL_MS": 20,
    "BINANCE_WS": "wss://stream.binance.com:9443/ws",
}

MAKER_FEE = Decimal("0")        # Assuming Maker rebate or 0% tier
TAKER_FEE = Decimal("0.001")    # 0.1% for emergency Market exits

class OctoBotV6:
    def __init__(self):
        self.order_books = {}
        self.positions = {}       
        self.pending_entries = {} 
        self.balance = CONFIG["INITIAL_BALANCE"]
        self.total_trades = 0
        self.winning_trades = 0
        self.last_trade_result = {}
        self.last_trade_time = {}
        self.running = True

    class OrderBook:
        def __init__(self, symbol):
            self.symbol = symbol
            self.bids = {}
            self.asks = {}

        def apply_depth(self, data):
            for side, key in [('bids', 'b'), ('asks', 'a')]:
                book_side = getattr(self, side)
                for price_str, qty_str in data.get(key, []):
                    p, q = Decimal(price_str), Decimal(qty_str)
                    if q == 0: book_side.pop(p, None)
                    else: book_side[p] = q

        def best_bid(self): return max(self.bids.keys()) if self.bids else Decimal('0')
        def best_ask(self): return min(self.asks.keys()) if self.asks else Decimal('0')
        
        def get_ofi(self, depth=5):
            sorted_bids = sorted(self.bids.items(), key=lambda x: x[0], reverse=True)[:depth]
            sorted_asks = sorted(self.asks.items(), key=lambda x: x[0])[:depth]
            b_vol = sum(q for _, q in sorted_bids)
            a_vol = sum(q for _, q in sorted_asks)
            return (b_vol - a_vol) / (b_vol + a_vol) if (b_vol + a_vol) > 0 else 0

    async def subscribe_depth(self, symbol):
        url = f"{CONFIG['BINANCE_WS']}/{symbol.lower()}@depth20@100ms"
        async with websockets.connect(url) as ws:
            while self.running:
                data = json.loads(await ws.recv())
                self.order_books[symbol].apply_depth(data)

    def place_entry_limit(self, symbol, side):
        book = self.order_books[symbol]
        price = book.best_bid() if side == 'buy' else book.best_ask()
        if price <= 0: return

        self.pending_entries[symbol] = {
            'side': side,
            'price': price,
            'qty': CONFIG["ORDER_SIZE_USDT"] / price,
            'timestamp': time.time()
        }
        print(f"📝 LIMIT {side.upper()} Posted: {symbol} @ {price:.8f}")

    def check_fills_and_positions(self):
        now = time.time()
        
        # 1. Check Pending Limits
        for sym in list(self.pending_entries.keys()):
            order = self.pending_entries[sym]
            book = self.order_books[sym]
            
            # Simulated Fill: Buy fills if someone sells to us at our bid (Best Ask <= Our Price)
            is_filled = (order['side'] == 'buy' and book.best_ask() <= order['price']) or \
                        (order['side'] == 'sell' and book.best_bid() >= order['price'])
            
            if is_filled:
                self.balance -= CONFIG["ORDER_SIZE_USDT"]
                tp = order['price'] * (1 + CONFIG["TAKE_PROFIT_BPS"]/10000) if order['side'] == 'buy' else order['price'] * (1 - CONFIG["TAKE_PROFIT_BPS"]/10000)
                sl = order['price'] * (1 - CONFIG["STOP_LOSS_BPS"]/10000) if order['side'] == 'buy' else order['price'] * (1 + CONFIG["STOP_LOSS_BPS"]/10000)
                
                self.positions[sym] = {
                    'side': order['side'], 'entry': order['price'], 'qty': order['qty'],
                    'tp': tp, 'sl': sl, 'time': now
                }
                del self.pending_entries[sym]
                print(f"✅ FILLED: {sym} @ {order['price']:.8f}. TP Target: {tp:.8f}")
            
            elif now - order['timestamp'] > CONFIG["ENTRY_TIMEOUT_SEC"]:
                del self.pending_entries[sym]
                print(f"⌛ TIMEOUT: {sym} entry cancelled.")

        # 2. Check Active Positions
        for sym in list(self.positions.keys()):
            pos = self.positions[sym]
            book = self.order_books[sym]
            
            # TP (Limit/Maker) | SL (Market/Taker)
            hit_tp = (pos['side'] == 'buy' and book.best_bid() >= pos['tp']) or \
                     (pos['side'] == 'sell' and book.best_ask() <= pos['tp'])
            
            hit_sl = (pos['side'] == 'buy' and book.best_bid() <= pos['sl']) or \
                     (pos['side'] == 'sell' and book.best_ask() >= pos['sl'])
            
            if hit_tp:
                self.close_trade(sym, pos['tp'], "WIN (LIMIT)")
            elif hit_sl:
                exit_p = book.best_bid() if pos['side'] == 'buy' else book.best_ask()
                self.close_trade(sym, exit_p, "LOSS (SL MARKET)")
            elif now - pos['time'] > CONFIG["MAX_HOLD_SECONDS"]:
                exit_p = book.best_bid() if pos['side'] == 'buy' else book.best_ask()
                self.close_trade(sym, exit_p, "TIMEOUT (MARKET)")

    def close_trade(self, sym, price, reason):
        pos = self.positions.pop(sym)
        is_market = "MARKET" in reason
        fee_rate = TAKER_FEE if is_market else MAKER_FEE
        
        gross = pos['qty'] * price
        fee_amt = gross * fee_rate
        cost = pos['qty'] * pos['entry']
        
        profit = (gross - cost) - fee_amt if pos['side'] == 'buy' else (cost - gross) - fee_amt
        
        self.balance += (gross - fee_amt)
        self.total_trades += 1
        if profit > 0: self.winning_trades += 1
        
        wr = (self.winning_trades / self.total_trades * 100)
        self.last_trade_time[sym] = time.time()
        self.last_trade_result[sym] = 'win' if profit > 0 else 'loss'
        
        print(f"{'✅' if profit > 0 else '❌'} {reason} {sym} | PnL: ${profit:.4f} | Bal: ${self.balance:.2f} | WR: {wr:.1f}%")

    async def run(self):
        for sym in CONFIG["SYMBOLS"]:
            self.order_books[sym] = self.OrderBook(sym)
            asyncio.create_task(self.subscribe_depth(sym))

        print(f"🚀 OctoBot V6 Active | TP: {CONFIG['TAKE_PROFIT_BPS']} bps | SL: {CONFIG['STOP_LOSS_BPS']} bps")

        while self.running:
            self.check_fills_and_positions()
            
            if len(self.positions) + len(self.pending_entries) < 1:
                for sym in CONFIG["SYMBOLS"]:
                    now = time.time()
                    cd = CONFIG["LOSS_COOLDOWN_SEC"] if self.last_trade_result.get(sym) == 'loss' else CONFIG["WIN_COOLDOWN_SEC"]
                    if sym in self.last_trade_time and now - self.last_trade_time[sym] < cd: continue
                    
                    ofi = self.order_books[sym].get_ofi()
                    if ofi > CONFIG["OFI_THRESHOLD"]: 
                        self.place_entry_limit(sym, 'buy')
                        break
                    elif ofi < -CONFIG["OFI_THRESHOLD"]: 
                        self.place_entry_limit(sym, 'sell')
                        break

            await asyncio.sleep(CONFIG["SCAN_INTERVAL_MS"] / 1000.0)

if __name__ == "__main__":
    try: asyncio.run(OctoBotV6().run())
    except KeyboardInterrupt: print("\nStopped.")
