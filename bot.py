#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
PROFITABLE LIMIT ORDER BOT – 0% FEES ENTRY + EXIT
- Limit orders for entry (0% maker fee)
- Limit orders for exit (0% maker fee)
- TP: 0.10% net profit (pure profit, no fees)
- SL: 0.05%
- Positions fill when price reaches your levels
"""

import asyncio
import json
import websockets
import aiohttp
from decimal import Decimal, getcontext
import time

getcontext().prec = 12

CONFIG = {
    "SYMBOLS": ["BTCUSDT", "ETHUSDT", "DOGEUSDT", "SOLUSDT", "PEPEUSDT", "SUIUSDT"],
    "ORDER_SIZE_USDT": Decimal("0.50"),
    "INITIAL_BALANCE": Decimal("20.00"),
    "OFI_LEVELS": 8,
    "OFI_THRESHOLD": Decimal("0.70"),
    "TAKE_PROFIT_BPS": Decimal("10"),      # 0.10% pure profit (no fees!)
    "STOP_LOSS_BPS": Decimal("5"),         # 0.05% loss
    "MAX_HOLD_SECONDS": 60,                # Wait longer for limit fills
    "WIN_COOLDOWN_SEC": 2,
    "LOSS_COOLDOWN_SEC": 45,
    "SCAN_INTERVAL_MS": 100,
    "REFRESH_BOOK_SEC": 30,
    "BINANCE_WS": "wss://stream.binance.com:9443/ws",
}

MAKER_FEE = Decimal("0")   # Limit orders = 0% fee

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

    def get_ofi(self, depth=8):
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

class PureLimitBot:
    def __init__(self):
        self.order_books = {s: OrderBook(s) for s in CONFIG["SYMBOLS"]}
        self.positions = {}
        self.pending_exits = {}  # symbol -> exit_order info
        self.balance = CONFIG["INITIAL_BALANCE"]
        self.total_trades = 0
        self.winning_trades = 0
        self.hourly_profit = Decimal('0')
        self.last_hour_reset = time.time()
        self.last_trade_time = {}
        self.last_trade_result = {}
        self.running = True

    async def load_snapshots(self, session):
        for sym in CONFIG["SYMBOLS"]:
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

    def open_position_limit(self, symbol, side):
        """Place LIMIT order for entry (0% fee)"""
        book = self.order_books[symbol]
        
        if side == 'buy':
            price = book.best_bid()
        else:
            price = book.best_ask()
        
        if price <= 0:
            return False
        
        order_size = CONFIG["ORDER_SIZE_USDT"]
        if order_size > self.balance:
            order_size = self.balance * Decimal("0.95")
            if order_size < Decimal("0.10"):
                return False
        
        qty = order_size / price
        cost = qty * price
        fee = cost * MAKER_FEE  # 0%
        
        if cost + fee > self.balance:
            return False
        
        self.balance -= (cost + fee)
        
        # Set target and stop prices for exit LIMIT orders
        if side == 'buy':
            target_price = price * (Decimal("1") + CONFIG["TAKE_PROFIT_BPS"] / Decimal("10000"))
            stop_price = price * (Decimal("1") - CONFIG["STOP_LOSS_BPS"] / Decimal("10000"))
        else:
            target_price = price * (Decimal("1") - CONFIG["TAKE_PROFIT_BPS"] / Decimal("10000"))
            stop_price = price * (Decimal("1") + CONFIG["STOP_LOSS_BPS"] / Decimal("10000"))
        
        self.positions[symbol] = {
            'side': side,
            'entry_price': price,
            'quantity': qty,
            'entry_time': time.time(),
            'order_size': order_size,
            'target_price': target_price,
            'stop_price': stop_price,
            'exit_placed': False,
        }
        
        expected_profit = order_size * (CONFIG["TAKE_PROFIT_BPS"] / Decimal("10000"))
        print(f"📈 LIMIT ENTRY {symbol} {side.upper()} @ {price:.4f} | ${order_size:.2f} | Target: +${expected_profit:.5f} (0% fee) | Balance: ${self.balance:.2f}")
        return True

    def place_exit_limit_order(self, symbol):
        """Place a LIMIT order for exit (0% fee)"""
        pos = self.positions.get(symbol)
        if not pos or pos.get('exit_placed'):
            return
        
        book = self.order_books[symbol]
        
        if pos['side'] == 'buy':
            # Place sell limit order at target price
            exit_price = pos['target_price']
            # Check if price is already above target (shouldn't happen, but just in case)
            if book.best_bid() >= exit_price:
                self.close_position_limit(symbol, exit_price, "TP")
                return
        else:
            # Place buy limit order at target price
            exit_price = pos['target_price']
            if book.best_ask() <= exit_price:
                self.close_position_limit(symbol, exit_price, "TP")
                return
        
        pos['exit_placed'] = True
        pos['exit_price'] = exit_price
        
        print(f"📌 LIMIT EXIT {symbol} placed @ {exit_price:.4f} (0% fee) | Will fill when price reaches target")
        # Note: In a real implementation, you would submit this order to Binance API
        # For simulation, we'll check if price hits the target in check_positions

    def check_positions(self, symbol):
        """Check if exit limit order would be filled"""
        pos = self.positions.get(symbol)
        if not pos:
            return
        
        book = self.order_books[symbol]
        now = time.time()
        
        # Check if exit limit order would be filled
        if pos['side'] == 'buy':
            current_bid = book.best_bid()
            if current_bid >= pos['target_price']:
                self.close_position_limit(symbol, current_bid, "TAKE_PROFIT")
                return
            # Check stop loss (use market order for SL to limit losses)
            elif current_bid <= pos['stop_price']:
                self.close_position_market(symbol, current_bid, "STOP_LOSS")
                return
        else:
            current_ask = book.best_ask()
            if current_ask <= pos['target_price']:
                self.close_position_limit(symbol, current_ask, "TAKE_PROFIT")
                return
            elif current_ask >= pos['stop_price']:
                self.close_position_market(symbol, current_ask, "STOP_LOSS")
                return
        
        # Timeout: close with market order
        if now - pos['entry_time'] > CONFIG["MAX_HOLD_SECONDS"]:
            if pos['side'] == 'buy':
                exit_price = book.best_bid()
            else:
                exit_price = book.best_ask()
            self.close_position_market(symbol, exit_price, "TIMEOUT")

    def close_position_limit(self, symbol, exit_price, reason):
        """Close with LIMIT order (0% fee) – pure profit"""
        pos = self.positions.pop(symbol, None)
        if not pos:
            return
        
        gross = pos['quantity'] * exit_price
        fee = gross * MAKER_FEE  # 0% fee on limit exit
        cost_basis = pos['quantity'] * pos['entry_price']
        profit = (gross - cost_basis) - fee
        
        self.balance += gross - fee
        self.total_trades += 1
        
        if profit > 0:
            self.winning_trades += 1
            self.last_trade_result[symbol] = 'win'
        else:
            self.last_trade_result[symbol] = 'loss'
        
        self.hourly_profit += profit
        win_rate = (self.winning_trades / self.total_trades * 100) if self.total_trades else 0
        profit_pct = (profit / pos['order_size'] * 100) if pos['order_size'] > 0 else 0
        print(f"🎯 LIMIT CLOSE {symbol} {reason} @ {exit_price:.4f} | Profit: ${profit:.5f} ({profit_pct:.2f}%) | Balance: ${self.balance:.2f} | WR: {win_rate:.1f}%")
        self.last_trade_time[symbol] = time.time()

    def close_position_market(self, symbol, exit_price, reason):
        """Fallback: close with MARKET order (0.1% fee) – only for SL/timeout"""
        pos = self.positions.pop(symbol, None)
        if not pos:
            return
        
        gross = pos['quantity'] * exit_price
        fee = gross * Decimal("0.001")  # 0.1% taker fee (only on losses)
        cost_basis = pos['quantity'] * pos['entry_price']
        profit = (gross - cost_basis) - fee
        
        self.balance += gross - fee
        self.total_trades += 1
        
        if profit > 0:
            self.winning_trades += 1
            self.last_trade_result[symbol] = 'win'
        else:
            self.last_trade_result[symbol] = 'loss'
        
        self.hourly_profit += profit
        win_rate = (self.winning_trades / self.total_trades * 100) if self.total_trades else 0
        profit_pct = (profit / pos['order_size'] * 100) if pos['order_size'] > 0 else 0
        print(f"⚠️ MARKET CLOSE {symbol} {reason} | Profit: ${profit:.5f} ({profit_pct:.2f}%) | Balance: ${self.balance:.2f} | WR: {win_rate:.1f}%")
        self.last_trade_time[symbol] = time.time()

    async def run(self):
        async with aiohttp.ClientSession() as session:
            await self.load_snapshots(session)
        
        for sym in CONFIG["SYMBOLS"]:
            asyncio.create_task(self.subscribe_depth(sym))
        
        print("\n🚀 PURE LIMIT ORDER BOT – 0% FEES ENTRY + EXIT")
        print(f"   Order size: ${CONFIG['ORDER_SIZE_USDT']} | TP: {float(CONFIG['TAKE_PROFIT_BPS'])/100:.2f}% (pure profit)")
        print(f"   Stop loss: {float(CONFIG['STOP_LOSS_BPS'])/100:.2f}% | Loss cooldown: {CONFIG['LOSS_COOLDOWN_SEC']}s\n")
        
        last_hb = 0
        last_ofi_debug = 0
        last_refresh = time.time()
        
        async with aiohttp.ClientSession() as session:
            while self.running:
                now = time.time()
                
                if now - last_refresh > CONFIG["REFRESH_BOOK_SEC"]:
                    for sym in CONFIG["SYMBOLS"]:
                        await self.order_books[sym].refresh_snapshot(session)
                    last_refresh = now
                
                # Debug OFI
                if now - last_ofi_debug > 5:
                    strong = []
                    for sym in CONFIG["SYMBOLS"]:
                        ofi = self.order_books[sym].get_ofi(CONFIG["OFI_LEVELS"])
                        if abs(ofi) > 0.4:
                            strong.append(f"{sym}:{ofi:.3f}")
                    if strong:
                        print(f"🔍 Strong OFI: {' | '.join(strong)}")
                    last_ofi_debug = now
                
                # Check positions and place exit limit orders
                for sym in list(self.positions.keys()):
                    self.check_positions(sym)
                    # Place exit limit order if not already placed and position is open
                    if sym in self.positions and not self.positions[sym].get('exit_placed'):
                        self.place_exit_limit_order(sym)
                
                # Open new positions
                if self.balance >= Decimal("0.20"):
                    for sym in CONFIG["SYMBOLS"]:
                        if sym in self.positions:
                            continue
                        
                        cooldown = CONFIG["LOSS_COOLDOWN_SEC"] if self.last_trade_result.get(sym) == 'loss' else CONFIG["WIN_COOLDOWN_SEC"]
                        if sym in self.last_trade_time and now - self.last_trade_time[sym] < cooldown:
                            continue
                        
                        ofi = self.order_books[sym].get_ofi(CONFIG["OFI_LEVELS"])
                        
                        if ofi > CONFIG["OFI_THRESHOLD"]:
                            print(f"⚡ {sym} OFI: {ofi:.3f} → LIMIT BUY")
                            self.open_position_limit(sym, 'buy')
                        elif ofi < -CONFIG["OFI_THRESHOLD"]:
                            print(f"⚡ {sym} OFI: {ofi:.3f} → LIMIT SELL")
                            self.open_position_limit(sym, 'sell')
                
                # Hourly summary
                if now - self.last_hour_reset >= 3600:
                    print(f"\n⏰ HOURLY PROFIT: +${self.hourly_profit:.5f} | Balance: ${self.balance:.2f}\n")
                    self.hourly_profit = Decimal('0')
                    self.last_hour_reset = now
                
                # Heartbeat
                if now - last_hb > 30:
                    wr = (self.winning_trades/self.total_trades*100) if self.total_trades else 0
                    print(f"📡 Balance: ${self.balance:.2f} | Open: {len(self.positions)} | WR: {wr:.1f}% | Trades: {self.total_trades}")
                    last_hb = now
                
                await asyncio.sleep(CONFIG["SCAN_INTERVAL_MS"] / 1000.0)

if __name__ == "__main__":
    bot = PureLimitBot()
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        bot.running = False
        print("\nShutdown")
