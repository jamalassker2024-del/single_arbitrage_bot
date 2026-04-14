#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
ULTIMATE HIGH-VOLATILITY BOT – $5 POSITIONS, FASTEST MOVING PAIRS
- Most volatile pairs on Binance (meme coins + high-beta alts)
- $5 position size for meaningful profit
- Ultra-fast TP: 0.04% (hits in seconds on volatile pairs)
- Aggressive entry: no confirmation needed
- 20 second max hold – cut losses fast
"""

import asyncio
import json
import websockets
import aiohttp
from decimal import Decimal, getcontext
import time
from collections import deque

getcontext().prec = 12

# ========== MOST VOLATILE PAIRS ON BINANCE ==========
CONFIG = {
    "SYMBOLS": [
        "PEPEUSDT",    # #1 meme coin – moves constantly
        "WIFUSDT",     # Dogwifhat – extreme volatility
        "BONKUSDT",    # Bonk – huge swings
        "FLOKIUSDT",   # Floki – strong momentum
        "DOGSUSDT",    # Dogs – new high volatility
        "NEIROUSDT",   # Neiro – meme coin
        "SUIUSDT",     # Sui – volatile L1
        "SEIUSDT",     # Sei – high beta
        "WUSDT",       # Wormhole – volatile
        "1000PEPEUSDT" # PEPE 1000x leverage pair
    ],
    "ORDER_SIZE_USDT": Decimal("5.00"),        # $5 per trade
    "INITIAL_BALANCE": Decimal("100.00"),      # $100 starting balance
    "OFI_LEVELS": 5,                           # Fast response
    "OFI_THRESHOLD": Decimal("0.50"),          # Strong signals only
    "TAKE_PROFIT_BPS": Decimal("4"),           # 0.04% – super fast hits
    "STOP_LOSS_BPS": Decimal("6"),             # 0.06% – tight but fair
    "MAX_HOLD_SECONDS": 20,                    # Fast exit
    "WIN_COOLDOWN_SEC": 1,
    "LOSS_COOLDOWN_SEC": 15,                   # Quick recovery
    "SCAN_INTERVAL_MS": 30,                   # Ultra-fast scanning
    "REFRESH_BOOK_SEC": 15,
    "BINANCE_WS": "wss://stream.binance.com:9443/ws",
}

MAKER_FEE = Decimal("0")
TAKER_FEE = Decimal("0.001")

class OrderBook:
    def __init__(self, symbol):
        self.symbol = symbol
        self.bids = {}
        self.asks = {}
        self.last_update = 0.0
        self.ofi_history = deque(maxlen=3)

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
        ofi = (bid_vol - ask_vol) / (bid_vol + ask_vol)
        self.ofi_history.append(ofi)
        return ofi

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

class UltimateVolatilityBot:
    def __init__(self):
        self.order_books = {s: OrderBook(s) for s in CONFIG["SYMBOLS"]}
        self.positions = {}
        self.balance = CONFIG["INITIAL_BALANCE"]
        self.total_trades = 0
        self.winning_trades = 0
        self.daily_profit = Decimal('0')
        self.daily_start = time.time()
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

    def open_position(self, symbol, side):
        book = self.order_books[symbol]
        price = book.best_bid() if side == 'buy' else book.best_ask()
        if price <= 0:
            return False
        
        order_size = CONFIG["ORDER_SIZE_USDT"]
        if order_size > self.balance:
            order_size = self.balance * Decimal("0.95")
            if order_size < Decimal("0.50"):
                return False
        
        qty = order_size / price
        cost = qty * price
        fee = cost * MAKER_FEE
        
        if cost + fee > self.balance:
            return False
        
        self.balance -= (cost + fee)
        
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
        }
        
        expected_profit = order_size * (CONFIG["TAKE_PROFIT_BPS"] / Decimal("10000"))
        print(f"📈 {symbol} {side.upper()} @ {price:.8f} | ${order_size:.2f} | Target: +${expected_profit:.4f}")
        return True

    def check_positions(self, symbol):
        pos = self.positions.get(symbol)
        if not pos:
            return
        
        book = self.order_books[symbol]
        now = time.time()
        
        if pos['side'] == 'buy':
            current = book.best_bid()
            if current >= pos['target_price']:
                self.close_win(symbol, current)
                return
            elif current <= pos['stop_price']:
                self.close_loss(symbol, current, "SL")
                return
        else:
            current = book.best_ask()
            if current <= pos['target_price']:
                self.close_win(symbol, current)
                return
            elif current >= pos['stop_price']:
                self.close_loss(symbol, current, "SL")
                return
        
        if now - pos['entry_time'] > CONFIG["MAX_HOLD_SECONDS"]:
            self.close_loss(symbol, current, "TIMEOUT")

    def close_win(self, symbol, exit_price):
        pos = self.positions.pop(symbol, None)
        if not pos:
            return
        
        gross = pos['quantity'] * exit_price
        fee = gross * MAKER_FEE
        cost_basis = pos['quantity'] * pos['entry_price']
        profit = (gross - cost_basis) - fee
        
        self.balance += gross - fee
        self.total_trades += 1
        self.winning_trades += 1
        self.last_trade_result[symbol] = 'win'
        self.daily_profit += profit
        
        profit_pct = (profit / pos['order_size'] * 100)
        win_rate = (self.winning_trades / self.total_trades * 100)
        print(f"✅ WIN {symbol} | Profit: ${profit:.4f} ({profit_pct:.2f}%) | Balance: ${self.balance:.2f} | WR: {win_rate:.1f}%")
        self.last_trade_time[symbol] = time.time()

    def close_loss(self, symbol, exit_price, reason):
        pos = self.positions.pop(symbol, None)
        if not pos:
            return
        
        gross = pos['quantity'] * exit_price
        fee = gross * TAKER_FEE
        cost_basis = pos['quantity'] * pos['entry_price']
        profit = (gross - cost_basis) - fee
        
        self.balance += gross - fee
        self.total_trades += 1
        self.last_trade_result[symbol] = 'loss'
        self.daily_profit += profit
        
        profit_pct = (profit / pos['order_size'] * 100)
        win_rate = (self.winning_trades / self.total_trades * 100)
        print(f"❌ LOSS {symbol} {reason} | Profit: ${profit:.4f} ({profit_pct:.2f}%) | Balance: ${self.balance:.2f} | WR: {win_rate:.1f}%")
        self.last_trade_time[symbol] = time.time()

    async def run(self):
        async with aiohttp.ClientSession() as session:
            await self.load_snapshots(session)
        
        for sym in CONFIG["SYMBOLS"]:
            asyncio.create_task(self.subscribe_depth(sym))
        
        print("\n🚀 ULTIMATE HIGH-VOLATILITY BOT")
        print(f"   Pairs: {len(CONFIG['SYMBOLS'])} volatile meme coins & alts")
        print(f"   Position size: ${CONFIG['ORDER_SIZE_USDT']} | TP: 0.04% | SL: 0.06%")
        print(f"   Max hold: {CONFIG['MAX_HOLD_SECONDS']}s | Loss cooldown: {CONFIG['LOSS_COOLDOWN_SEC']}s\n")
        
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
                
                # Debug OFI (only strong signals)
                if now - last_ofi_debug > 5:
                    strong = []
                    for sym in CONFIG["SYMBOLS"]:
                        ofi = self.order_books[sym].get_ofi(CONFIG["OFI_LEVELS"])
                        if abs(ofi) > 0.35:
                            strong.append(f"{sym}:{ofi:.3f}")
                    if strong:
                        print(f"🔍 OFI: {' | '.join(strong)}")
                    last_ofi_debug = now
                
                # Check positions
                for sym in list(self.positions.keys()):
                    self.check_positions(sym)
                
                # Open new positions
                if self.balance >= Decimal("1.00"):
                    for sym in CONFIG["SYMBOLS"]:
                        if sym in self.positions:
                            continue
                        
                        cooldown = CONFIG["LOSS_COOLDOWN_SEC"] if self.last_trade_result.get(sym) == 'loss' else CONFIG["WIN_COOLDOWN_SEC"]
                        if sym in self.last_trade_time and now - self.last_trade_time[sym] < cooldown:
                            continue
                        
                        ofi = self.order_books[sym].get_ofi(CONFIG["OFI_LEVELS"])
                        
                        if ofi > CONFIG["OFI_THRESHOLD"]:
                            print(f"⚡ {sym} OFI: {ofi:.3f} → BUY")
                            self.open_position(sym, 'buy')
                        elif ofi < -CONFIG["OFI_THRESHOLD"]:
                            print(f"⚡ {sym} OFI: {ofi:.3f} → SELL")
                            self.open_position(sym, 'sell')
                
                # Daily summary
                if now - self.daily_start >= 86400:
                    print(f"\n💰 DAILY PROFIT: +${self.daily_profit:.4f} | Balance: ${self.balance:.2f}\n")
                    self.daily_profit = Decimal('0')
                    self.daily_start = now
                
                # Heartbeat
                if now - last_hb > 30:
                    win_rate = (self.winning_trades / self.total_trades * 100) if self.total_trades else 0
                    print(f"📡 Balance: ${self.balance:.2f} | Open: {len(self.positions)} | WR: {win_rate:.1f}% | Trades: {self.total_trades}")
                    last_hb = now
                
                await asyncio.sleep(CONFIG["SCAN_INTERVAL_MS"] / 1000.0)

if __name__ == "__main__":
    bot = UltimateVolatilityBot()
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        bot.running = False
        print("\nShutdown")
