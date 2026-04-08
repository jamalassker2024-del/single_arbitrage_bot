import asyncio
import logging
import requests
import feedparser  # New: Reads news without an API key
from decimal import Decimal

# --- V12.2 THE GHOST ENGINE: KEYLESS NEWS + MASTER BOT ---
CONFIG = {
    "BINANCE_SYMBOL": "BTCUSDT",
    "TRADE_SIZE_USD": Decimal("2.00"),
    "PULSE_SENSITIVITY": Decimal("0.00025"),
    "POLL_SPEED": 0.25,
    "NEWS_FEEDS": [
        "https://cointelegraph.com/rss",
        "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "https://cryptopanic.com/news/rss/" # Their RSS is usually still free
    ]
}

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger("OctoGhost-V12.2")

class GhostEngine:
    def __init__(self):
        self.last_price = None
        self.shadow_balance = Decimal("10")
        self.news_multiplier = Decimal("1.0")
        self.hot_keywords = ["BREAKING", "CRASH", "SEC", "LIQUIDATION", "PUMP", "SURGE"]

    # --- TASK 1: KEYLESS NEWS SCANNER ---
    async def scan_news(self):
        """Scans public RSS feeds for market-moving words"""
        while True:
            try:
                found_heat = False
                for url in CONFIG["NEWS_FEEDS"]:
                    feed = feedparser.parse(url)
                    # Check the 3 latest headlines in each feed
                    for entry in feed.entries[:3]:
                        title = entry.title.upper()
                        if any(word in title for word in self.hot_keywords):
                            found_heat = True
                            break
                
                if found_heat:
                    self.news_multiplier = Decimal("2.0") # Double sensitivity
                    logger.info("📡 GHOST NEWS: Hot headlines detected. Increasing Bot Agility.")
                else:
                    self.news_multiplier = Decimal("1.0")
            except:
                pass
            await asyncio.sleep(45) # Check news every 45 seconds

    # --- TASK 2: BINANCE PULSE ---
    async def get_pulse(self):
        try:
            res = requests.get(f"https://api.binance.com/api/v3/ticker/price?symbol={CONFIG['BINANCE_SYMBOL']}", timeout=1).json()
            curr = Decimal(res['price'])
            pulse = (curr - self.last_price) / self.last_price if self.last_price else 0
            self.last_price = curr
            return pulse
        except: return 0

    async def run(self):
        logger.info(f"👻 V12.2 Ghost Engine Online | Balance: ${self.shadow_balance}")
        asyncio.create_task(self.scan_news()) # Start News in background
        
        while True:
            pulse = await self.get_pulse()
            
            # Smart logic: Pulse * News Heat
            effective_pulse = abs(pulse) * self.news_multiplier
            
            if effective_pulse > CONFIG["PULSE_SENSITIVITY"]:
                # If news is hot (2.0), the bot 'snipes' much smaller price moves
                win = CONFIG["TRADE_SIZE_USD"] * Decimal("0.012")
                self.shadow_balance += win
                logger.info(f"🎯 NEWS-BACKED SNIPE! Pulse: {round(pulse*100,4)}% | Multiplier: {self.news_multiplier}x")
                logger.info(f"💰 New Balance: ${round(self.shadow_balance, 2)}")
            
            await asyncio.sleep(CONFIG["POLL_SPEED"])

if __name__ == "__main__":
    asyncio.run(GhostEngine().run())
