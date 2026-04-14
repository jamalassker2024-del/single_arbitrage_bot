#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
FINAL TRAILING SCALPER – NO TIMEOUT, FIXED KEYERROR
- Market entry (0.1% fee)
- Take-profit limit order (0% fee) at 0.12% gross → 0.02% net
- Hard stop-loss (0.1% fee) at 0.06%
- Trailing stop: locks in profit after 0.02% gain, moves stop to breakeven then trails
- No timeout – trades close only via TP, SL, or trailing stop
"""

import asyncio
import json
import websockets
import aiohttp
from decimal import Decimal, getcontext
import time

getcontext().prec = 12

CONFIG = {
    "SYMBOLS": ["DOGSUSDT", "NEIROUSDT", "PEPEUSDT", "WIFUSDT", "BONKUSDT"],
    "ORDER_SIZE_USDT": Decimal("5.00"),
    "INITIAL_BALANCE": Decimal("100.00"),
    "OFI_LEVELS": 5,
    "OFI_THRESHOLD": Decimal("0.55"),
    "TAKE_PROFIT_BPS": Decimal("12"),          # 0.12% gross → net 0.02% after 0.1% entry fee
    "STOP_LOSS_BPS": Decimal("6"),             # 0.06% initial stop loss
    "TRAIL_BPS": Decimal("2"),                 # 0.02% trail distance
    "WIN_COOLDOWN_SEC": 1,
    "LOSS_COOLDOWN_SEC": 15,
    "SCAN_INTERVAL_MS": 20,
    "REFRESH_BOOK_SEC": 20,
    "BINANCE_WS": "wss://stream.binance.com:9443/ws",
}

TAKER_FEE = Decimal("0.001")   # market entry & market exit (SL)
MAKER_FEE = Decimal("0")       # limit TP exit

class TrailingScalper:
    def __init__(self):
        self.order_books = {}
        self.positions = {}
        self.balance = CONFIG["INITIAL_BALANCE"]
        self.total_trades = 0
        self.winning_trades = 0
        self.daily_profit = Decimal('0')
        self.daily_start = time.time()
        self.last_trade_time = {}
        self.last_trade_result = {}
        self.running = True

    class OrderBook:
        def __init__(self, symbol):
            self.symbol = symbol
            self.bids = {}
            self.asks = {}
            self.last_update = 0.0

        def apply_depth(self, data):
            for side, key in [('bids', 'b'), ('asks', 'a')]:
                book_side = getattr(self, side)
                for price_str, qty_str in data.get(key, []):
                    price, qty = Decimal(price_str), Decimal(qty_str)
                    if qty == 0:
                        book_side.pop(price, None)
                    else:
                        book_side[price] = qty
            self.last_update = time.time()

        def best_bid(self):
            return max(self.bids.keys()) if self.bids else Decimal('0')

        def best_ask(self):
            return min(self.asks.keys()) if self.asks else Decimal('0')

        def mid_price(self):
            bb, ba = self.best_bid(), self.best_ask()
            return (bb + ba) / 2 if bb and ba else Decimal('0')

        def get_ofi(self, depth=5):
            sorted_bids = sorted(self.bids.items(), key=lambda x: x[0], reverse=True)[:depth]
            sorted_asks = sorted(self.asks.items(), key=lambda x: x[0])[:depth]
            bid_vol = sum(q for _, q in sorted_bids)
            ask_vol = sum(q for _, q in sorted_asks)
            if bid_vol + ask_vol == 0:
                return Decimal('0')
            return (bid_vol - ask_vol) / (bid_vol + ask_vol)

        async def refresh_snapshot(self, session):
            url = f"https://api.binance.com/api/v3/depth?symbol={self.symbol}&limit=20"
            try:
                async with session.get(url) as resp:
                    data = await resp.json()
                    self.bids = {Decimal(p): Decimal(q) for p, q in data['bids']}
                    self.asks = {Decimal(p): Decimal(q) for p, q in data['asks']}
                    self.last_update = time.time()
                    return True
            except Exception:
                return False

    async def load_snapshots(self, session):
        for sym in CONFIG["SYMBOLS"]:
            self.order_books[sym] = self.OrderBook(sym)
            await self.order_books[sym].refresh_snapshot(session)
            print(f"✅ {sym} ready")

    async def subscribe_depth(self, symbol):
        stream = f"{symbol.lower()}@depth20@100ms"
        url = f"{CONFIG['BINANCE_WS']}/{stream}"
        while self.running:
            try:
                async with websockets.connect(url) as ws:
                    async for msg in ws:
                        data = json.loads(msg)
                        if 'b' in data or 'a' in data:
                            self.order_books[symbol].apply_depth(data)
            except Exception:
                await asyncio.sleep(3)

    def open_market_position(self, symbol, side):
        book = self.order_books[symbol]
        price = book.best_ask() if side == 'buy' else book.best_bid()
        if price <= 0:
            return False

        order_size = CONFIG["ORDER_SIZE_USDT"]
        if order_size > self.balance:
            order_size = self.balance * Decimal("0.95")
            if order_size < Decimal("1.00"):
                return False

        qty = order_size / price
        cost = qty * price
        fee = cost * TAKER_FEE

        if cost + fee > self.balance:
            return False

        self.balance -= (cost + fee)

        tp_bps = CONFIG["TAKE_PROFIT_BPS"]
        sl_bps = CONFIG["STOP_LOSS_BPS"]
        trail_bps = CONFIG["TRAIL_BPS"]

        if side == 'buy':
            target_price = price * (Decimal("1") + tp_bps / Decimal("10000"))
            stop_price = price * (Decimal("1") - sl_bps / Decimal("10000"))
            best_price = price
        else:
            target_price = price * (Decimal("1") - tp_bps / Decimal("10000"))
            stop_price = price * (Decimal("1") + sl_bps / Decimal("10000"))
            best_price = price

        self.positions[symbol] = {
            'side': side,
            'entry_price': price,
            'quantity': qty,
            'order_size': order_size,
            'target_price': target_price,
            'stop_price': stop_price,
            'best_price': best_price,
            'trail_bps': trail_bps,
            'entry_time': time.time(),
        }

        # Expected net profit after entry fee (limit exit has 0% fee)
        net_profit = order_size * tp_bps / Decimal("10000") - order_size * TAKER_FEE
        print(f"⚡ {symbol} MARKET {side.upper()} @ {price:.8f} | ${order_size:.2f} | Target net: +${net_profit:.4f}")
        return True

    def update_trailing_stop(self, symbol, pos, current_price):
        """Update stop price for trailing stop."""
        if pos['side'] == 'buy':
            if current_price > pos['best_price']:
                pos['best_price'] = current_price
                new_stop = current_price * (Decimal("1") - pos['trail_bps'] / Decimal("10000"))
                if new_stop > pos['stop_price']:
                    pos['stop_price'] = new_stop
                    print(f"  🔼 Trail {symbol}: stop moved to {new_stop:.8f}")
        else:
            if current_price < pos['best_price']:
                pos['best_price'] = current_price
                new_stop = current_price * (Decimal("1") + pos['trail_bps'] / Decimal("10000"))
                if new_stop < pos['stop_price']:
                    pos['stop_price'] = new_stop
                    print(f"  🔽 Trail {symbol}: stop moved to {new_stop:.8f}")

    def check_positions(self):
        for sym, pos in list(self.positions.items()):
            book = self.order_books[sym]
            mid = book.mid_price()
            if mid <= 0:
                continue

            # Update trailing stop
            self.update_trailing_stop(sym, pos, mid)

            # Take-profit (limit exit, 0% fee)
            hit_tp = (pos['side'] == 'buy' and mid >= pos['target_price']) or \
                     (pos['side'] == 'sell' and mid <= pos['target_price'])
            # Stop-loss (market exit, 0.1% fee)
            hit_sl = (pos['side'] == 'buy' and mid <= pos['stop_price']) or \
                     (pos['side'] == 'sell' and mid >= pos['stop_price'])

            if hit_tp:
                self.close_win(sym, pos['target_price'])
            elif hit_sl:
                exit_price = book.best_bid() if pos['side'] == 'buy' else book.best_ask()
                self.close_loss(sym, exit_price, "STOP LOSS")

    def close_win(self, sym, price):
        pos = self.positions.pop(sym)
        gross = pos['quantity'] * price
        fee = gross * MAKER_FEE   # 0%
        cost_basis = pos['quantity'] * pos['entry_price']
        profit = (gross - cost_basis) - fee

        self.balance += gross - fee
        self.total_trades += 1
        self.winning_trades += 1
        self.last_trade_result[sym] = 'win'
        self.daily_profit += profit

        win_rate = (self.winning_trades / self.total_trades * 100) if self.total_trades else 0
        profit_pct = (profit / pos['order_size'] * 100) if pos['order_size'] > 0 else 0
        print(f"✅ WIN {sym} (TAKE PROFIT) | Profit: ${profit:.4f} ({profit_pct:.2f}%) | Balance: ${self.balance:.2f} | WR: {win_rate:.1f}%")
        self.last_trade_time[sym] = time.time()

    def close_loss(self, sym, price, reason):
        pos = self.positions.pop(sym)
        gross = pos['quantity'] * price
        fee = gross * TAKER_FEE
        cost_basis = pos['quantity'] * pos['entry_price']
        profit = (gross - cost_basis) - fee

        self.balance += gross - fee
        self.total_trades += 1
        if profit > 0:
            self.winning_trades += 1
            self.last_trade_result[sym] = 'win'
        else:
            self.last_trade_result[sym] = 'loss'
        self.daily_profit += profit

        win_rate = (self.winning_trades / self.total_trades * 100) if self.total_trades else 0
        profit_pct = (profit / pos['order_size'] * 100) if pos['order_size'] > 0 else 0
        print(f"{'✅' if profit>0 else '❌'} {reason} {sym} | Profit: ${profit:.4f} ({profit_pct:.2f}%) | Balance: ${self.balance:.2f} | WR: {win_rate:.1f}%")
        self.last_trade_time[sym] = time.time()

    async def run(self):
        async with aiohttp.ClientSession() as session:
            await self.load_snapshots(session)

        for sym in CONFIG["SYMBOLS"]:
            asyncio.create_task(self.subscribe_depth(sym))

        print("\n🚀 TRAILING SCALPER – NO TIMEOUT, FIXED")
        print(f"   TP: 0.12% gross → 0.02% net | SL: 0.06% | Trail: 0.02% | Position: ${CONFIG['ORDER_SIZE_USDT']}")
        print(f"   OFI threshold: {CONFIG['OFI_THRESHOLD']}\n")

        last_ofi_print = 0
        last_refresh = time.time()
        async with aiohttp.ClientSession() as session:
            while self.running:
                now = time.time()

                if now - last_refresh > CONFIG["REFRESH_BOOK_SEC"]:
                    for sym in CONFIG["SYMBOLS"]:
                        await self.order_books[sym].refresh_snapshot(session)
                    last_refresh = now

                if now - last_ofi_print > 3:
                    ofi_str = []
                    for sym in CONFIG["SYMBOLS"]:
                        ofi = self.order_books[sym].get_ofi(CONFIG["OFI_LEVELS"])
                        ofi_str.append(f"{sym}:{ofi:.3f}")
                    print(f"🔍 OFI: {' | '.join(ofi_str)}")
                    last_ofi_print = now

                self.check_positions()

                for sym in CONFIG["SYMBOLS"]:
                    if sym in self.positions:
                        continue
                    cooldown = CONFIG["LOSS_COOLDOWN_SEC"] if self.last_trade_result.get(sym) == 'loss' else CONFIG["WIN_COOLDOWN_SEC"]
                    if sym in self.last_trade_time and now - self.last_trade_time[sym] < cooldown:
                        continue
                    ofi = self.order_books[sym].get_ofi(CONFIG["OFI_LEVELS"])
                    if ofi > CONFIG["OFI_THRESHOLD"]:
                        print(f"⚡ {sym} OFI: {ofi:.3f} → BUY")
                        self.open_market_position(sym, 'buy')
                    elif ofi < -CONFIG["OFI_THRESHOLD"]:
                        print(f"⚡ {sym} OFI: {ofi:.3f} → SELL")
                        self.open_market_position(sym, 'sell')

                if now - self.daily_start >= 86400:
                    print(f"\n💰 DAILY PROFIT: +${self.daily_profit:.4f} | Balance: ${self.balance:.2f}\n")
                    self.daily_profit = Decimal('0')
                    self.daily_start = now

                await asyncio.sleep(CONFIG["SCAN_INTERVAL_MS"] / 1000.0)

if __name__ == "__main__":
    try:
        asyncio.run(TrailingScalper().run())
    except KeyboardInterrupt:
        print("\nShutdown complete")
