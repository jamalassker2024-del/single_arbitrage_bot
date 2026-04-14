#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
OCTOBOT V5: LIMIT ORDER MAKER SCALPER (0% Maker Fees)
- Limit orders for entry and TP exit
- Realistic fill simulation (best_ask <= limit price)
- Auto‑cancel pending entries after timeout
- Market SL / timeout as safety net
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
    "OFI_THRESHOLD": Decimal("0.65"),          # strong signals only
    "TAKE_PROFIT_BPS": Decimal("2"),           # 0.02% → pure profit (0% fee)
    "STOP_LOSS_BPS": Decimal("5"),             # 0.05% loss (market exit)
    "MAX_HOLD_SECONDS": 10,
    "ENTRY_TIMEOUT_SEC": 3,                    # cancel unfilled entry after 3s
    "WIN_COOLDOWN_SEC": 1,
    "LOSS_COOLDOWN_SEC": 30,                   # longer cooldown after a loss
    "SCAN_INTERVAL_MS": 20,
    "BINANCE_WS": "wss://stream.binance.com:9443/ws",
}

MAKER_FEE = Decimal("0")
TAKER_FEE = Decimal("0.001")

class LimitScalper:
    def __init__(self):
        self.order_books = {}
        self.positions = {}          # filled positions (active)
        self.pending_entries = {}    # limit orders waiting to be filled
        self.balance = CONFIG["INITIAL_BALANCE"]
        self.total_trades = 0
        self.winning_trades = 0
        self.last_trade_result = {}
        self.last_trade_time = {}
        self.running = True

    class OrderBook:
        def __init__(self):
            self.bids = {}
            self.asks = {}

        def apply_depth(self, data):
            for side, key in [('bids', 'b'), ('asks', 'a')]:
                book_side = getattr(self, side)
                for p, q in data.get(key, []):
                    price, qty = Decimal(p), Decimal(q)
                    if qty == 0:
                        book_side.pop(price, None)
                    else:
                        book_side[price] = qty

        def best_bid(self):
            return max(self.bids.keys()) if self.bids else Decimal('0')

        def best_ask(self):
            return min(self.asks.keys()) if self.asks else Decimal('0')

        def get_ofi(self, depth=5):
            sorted_bids = sorted(self.bids.items(), key=lambda x: x[0], reverse=True)[:depth]
            sorted_asks = sorted(self.asks.items(), key=lambda x: x[0])[:depth]
            bid_vol = sum(q for _, q in sorted_bids)
            ask_vol = sum(q for _, q in sorted_asks)
            if bid_vol + ask_vol == 0:
                return Decimal('0')
            return (bid_vol - ask_vol) / (bid_vol + ask_vol)

    async def subscribe_depth(self, symbol):
        url = f"{CONFIG['BINANCE_WS']}/{symbol.lower()}@depth20@100ms"
        async with websockets.connect(url) as ws:
            while self.running:
                data = json.loads(await ws.recv())
                self.order_books[symbol].apply_depth(data)

    def place_entry_limit(self, symbol, side):
        book = self.order_books[symbol]
        price = book.best_bid() if side == 'buy' else book.best_ask()
        if price <= 0:
            return

        qty = CONFIG["ORDER_SIZE_USDT"] / price
        self.pending_entries[symbol] = {
            'side': side,
            'price': price,
            'qty': qty,
            'timestamp': time.time()
        }
        print(f"📝 LIMIT {side.upper()} Entry Posted: {symbol} @ {price:.8f}")

    def check_fills_and_positions(self):
        now = time.time()

        # ---- 1. Check pending entry limit orders ----
        for sym in list(self.pending_entries.keys()):
            order = self.pending_entries[sym]
            book = self.order_books[sym]

            # Real fill condition: buy fills when best_ask ≤ limit_price
            filled = (order['side'] == 'buy' and book.best_ask() <= order['price']) or \
                     (order['side'] == 'sell' and book.best_bid() >= order['price'])

            if filled:
                # Entry filled – deduct cost, move to positions
                self.balance -= CONFIG["ORDER_SIZE_USDT"]
                tp_price = order['price'] * (1 + CONFIG["TAKE_PROFIT_BPS"]/10000) if order['side'] == 'buy' else order['price'] * (1 - CONFIG["TAKE_PROFIT_BPS"]/10000)
                sl_price = order['price'] * (1 - CONFIG["STOP_LOSS_BPS"]/10000) if order['side'] == 'buy' else order['price'] * (1 + CONFIG["STOP_LOSS_BPS"]/10000)

                self.positions[sym] = {
                    'side': order['side'],
                    'entry': order['price'],
                    'qty': order['qty'],
                    'tp': tp_price,
                    'sl': sl_price,
                    'time': now
                }
                del self.pending_entries[sym]
                print(f"✅ Entry FILLED: {sym} @ {order['price']:.8f}. TP limit @ {tp_price:.8f} (0% fee)")

            elif now - order['timestamp'] > CONFIG["ENTRY_TIMEOUT_SEC"]:
                del self.pending_entries[sym]
                print(f"⌛ Entry TIMEOUT: {sym} cancelled (price moved away)")

        # ---- 2. Check open positions (TP / SL / timeout) ----
        for sym in list(self.positions.keys()):
            pos = self.positions[sym]
            book = self.order_books[sym]

            # TP condition (limit exit – 0% fee)
            hit_tp = (pos['side'] == 'buy' and book.best_bid() >= pos['tp']) or \
                     (pos['side'] == 'sell' and book.best_ask() <= pos['tp'])

            # SL condition (market exit – 0.1% fee)
            hit_sl = (pos['side'] == 'buy' and book.best_bid() <= pos['sl']) or \
                     (pos['side'] == 'sell' and book.best_ask() >= pos['sl'])

            if hit_tp:
                self.close_trade(sym, pos['tp'], "WIN (LIMIT)")
            elif hit_sl:
                exit_price = book.best_bid() if pos['side'] == 'buy' else book.best_ask()
                self.close_trade(sym, exit_price, "LOSS (SL MARKET)")
            elif now - pos['time'] > CONFIG["MAX_HOLD_SECONDS"]:
                exit_price = book.best_bid() if pos['side'] == 'buy' else book.best_ask()
                self.close_trade(sym, exit_price, "TIMEOUT (MARKET)")

    def close_trade(self, sym, price, reason):
        pos = self.positions.pop(sym)
        is_market = "MARKET" in reason
        fee = TAKER_FEE if is_market else MAKER_FEE

        gross = pos['qty'] * price
        fee_amt = gross * fee
        cost = pos['qty'] * pos['entry']
        if pos['side'] == 'buy':
            profit = (gross - cost) - fee_amt
        else:
            profit = (cost - gross) - fee_amt

        self.balance += gross - fee_amt
        self.total_trades += 1
        if profit > 0:
            self.winning_trades += 1
        win_rate = (self.winning_trades / self.total_trades * 100) if self.total_trades else 0
        print(f"{'✅' if profit > 0 else '❌'} {reason} {sym} | PnL: ${profit:.4f} | Bal: ${self.balance:.2f} | WR: {win_rate:.1f}%")
        self.last_trade_time[sym] = time.time()
        self.last_trade_result[sym] = 'win' if profit > 0 else 'loss'

    async def run(self):
        # Initialise order books
        for sym in CONFIG["SYMBOLS"]:
            self.order_books[sym] = self.OrderBook()
            asyncio.create_task(self.subscribe_depth(sym))

        print("🚀 LIMIT ORDER MAKER SCALPER ACTIVE (0% maker fees)")
        print(f"   TP: 0.02% (pure profit) | SL: 0.05% | Max hold: {CONFIG['MAX_HOLD_SECONDS']}s")
        print(f"   Entry timeout: {CONFIG['ENTRY_TIMEOUT_SEC']}s | Position: ${CONFIG['ORDER_SIZE_USDT']}\n")

        while self.running:
            self.check_fills_and_positions()

            # Scan for new entry signals (only if no pending/active trade)
            if len(self.positions) + len(self.pending_entries) < 1:
                for sym in CONFIG["SYMBOLS"]:
                    now = time.time()
                    cooldown = CONFIG["LOSS_COOLDOWN_SEC"] if self.last_trade_result.get(sym) == 'loss' else CONFIG["WIN_COOLDOWN_SEC"]
                    if sym in self.last_trade_time and now - self.last_trade_time[sym] < cooldown:
                        continue

                    ofi = self.order_books[sym].get_ofi()
                    if ofi > CONFIG["OFI_THRESHOLD"]:
                        self.place_entry_limit(sym, 'buy')
                        break   # one trade at a time
                    elif ofi < -CONFIG["OFI_THRESHOLD"]:
                        self.place_entry_limit(sym, 'sell')
                        break

            await asyncio.sleep(CONFIG["SCAN_INTERVAL_MS"] / 1000.0)

if __name__ == "__main__":
    bot = LimitScalper()
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        bot.running = False
        print("\nShutdown complete")
