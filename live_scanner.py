import os
import json
import pytz
import requests
import pandas as pd
from datetime import datetime
import yfinance as yf

IST = pytz.timezone("Asia/Kolkata")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

def send_telegram_alert(msg: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": msg,
        "parse_mode": "Markdown"
    }
    try:
        r = requests.post(url, json=payload, timeout=10)
        return r.status_code == 200
    except Exception as e:
        print(f"Telegram Alert Error: {e}")
        return False

# 7 Assets Universe
ASSETS = {
    "TCS": {"symbol": "TCS.NS", "lot_size": 175, "strike_step": 20, "spread_width": 40},
    "BANKNIFTY": {"symbol": "^NSEBANK", "lot_size": 15, "strike_step": 100, "spread_width": 200},
    "FINNIFTY": {"symbol": "NIFTY_FIN_SERVICE.NS", "lot_size": 25, "strike_step": 50, "spread_width": 100},
    "LT": {"symbol": "LT.NS", "lot_size": 150, "strike_step": 20, "spread_width": 40},
    "AXISBANK": {"symbol": "AXISBANK.NS", "lot_size": 625, "strike_step": 10, "spread_width": 20},
    "NIFTY": {"symbol": "^NSEI", "lot_size": 25, "strike_step": 50, "spread_width": 100},
    "HDFCBANK": {"symbol": "HDFCBANK.NS", "lot_size": 550, "strike_step": 10, "spread_width": 20}
}

def scan_live_market():
    now = datetime.now(IST)
    print(f"Running Market Scan at {now.strftime('%Y-%m-%d %H:%M:%S')} IST...")

    for name, conf in ASSETS.items():
        try:
            ticker = yf.Ticker(conf["symbol"])
            df_5m = ticker.history(period="15d", interval="5m")
            if df_5m.empty or len(df_5m) < 40:
                continue
            
            if df_5m.index.tz is None:
                df_5m.index = df_5m.index.tz_localize("UTC").tz_convert(IST)
            else:
                df_5m.index = df_5m.index.tz_convert(IST)

            # Daily 20 EMA calculation
            df_daily = df_5m.resample("1D").agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna()
            df_daily["EMA20"] = df_daily["Close"].ewm(span=20, adjust=False).mean()
            df_5m["Daily_EMA20"] = df_daily["EMA20"].reindex(df_5m.index, method="ffill")

            # 4-Hour Levels
            df_4h = df_5m.resample("4h").agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna()
            df_4h["HTF_Low"] = df_4h["Low"].shift(1).rolling(window=6).min()
            df_4h["HTF_High"] = df_4h["High"].shift(1).rolling(window=6).max()
            df_5m["HTF_Low"] = df_4h["HTF_Low"].reindex(df_5m.index, method="ffill")
            df_5m["HTF_High"] = df_4h["HTF_High"].reindex(df_5m.index, method="ffill")

            df_5m["Range"] = df_5m["High"] - df_5m["Low"]
            df_5m["Range_MA20"] = df_5m["Range"].rolling(window=20).mean()
            df_5m["Vol_MA20"] = df_5m["Volume"].rolling(window=20).mean()

            c1 = df_5m.iloc[-3]
            c2 = df_5m.iloc[-2]
            c3 = df_5m.iloc[-1]

            avg_vol_2 = c2["Vol_MA20"]
            avg_rng_2 = c2["Range_MA20"]
            if avg_vol_2 > 0 and c2["Volume"] > 0:
                has_vol = (c2["Volume"] / avg_vol_2) >= 1.5
                vol_str = f"{round(c2['Volume'] / avg_vol_2, 2)}x (Vol)"
            else:
                has_vol = ((c2["High"] - c2["Low"]) / avg_rng_2) >= 1.35 if avg_rng_2 > 0 else False
                vol_str = f"{round((c2['High'] - c2['Low']) / avg_rng_2, 2)}x (Rng)"

            if not has_vol:
                continue

            is_c2_green = c2["Close"] > c2["Open"]
            is_c2_red = c2["Close"] < c2["Open"]

            is_bull = (c1["Close"] >= c1["Daily_EMA20"]) and (c1["Low"] <= c1["HTF_Low"]) and is_c2_green and (c2["Close"] > c1["High"]) and (c3["High"] > c2["High"])
            is_bear = (c1["Close"] <= c1["Daily_EMA20"]) and (c1["High"] >= c1["HTF_High"]) and is_c2_red and (c2["Close"] < c1["Low"]) and (c3["Low"] < c2["Low"])

            if is_bull or is_bear:
                side = "BULLISH SWEEP (CALL SPREAD)" if is_bull else "BEARISH SWEEP (PUT SPREAD)"
                opt_type = "CE" if is_bull else "PE"
                entry_spot = c2["High"] if is_bull else c2["Low"]
                sl_buf = entry_spot * 0.0008
                sl_spot = (min(c1["Low"], c2["Low"]) - sl_buf) if is_bull else (max(c1["High"], c2["High"]) + sl_buf)
                risk = abs(entry_spot - sl_spot)
                tgt_spot = (entry_spot + (risk * 1.8)) if is_bull else (entry_spot - (risk * 1.8))

                atm = int(round(entry_spot / conf["strike_step"]) * conf["strike_step"])
                otm = (atm + conf["spread_width"]) if is_bull else (atm - conf["spread_width"])

                msg = (
                    f"🚨 *HIGH-CONVICTION TRADE ALERT*\n\n"
                    f"📌 *Asset:* `{name}` ({side})\n"
                    f"⏰ *Candle Time:* `{c3.name.strftime('%H:%M IST')}`\n"
                    f"📊 *Spread Legs:* `{atm} {opt_type} Buy` / `{otm} {opt_type} Sell`\n"
                    f"🎯 *Entry Spot:* `₹{entry_spot:.2f}`\n"
                    f"🛑 *Stop Loss:* `₹{sl_spot:.2f}`\n"
                    f"🏁 *Target (1:1.80):* `₹{tgt_spot:.2f}`\n"
                    f"⚡ *Confirmation:* `{vol_str}`\n"
                    f"💼 *Lot Size:* `{conf['lot_size']}` (1 Lot Mode)\n\n"
                    f"🛡️ *Rules:* BE Trailing at 1:1.25 | Time Exit: 3 Hours"
                )
                print(f"Triggering Telegram alert for {name}...")
                send_telegram_alert(msg)

        except Exception as e:
            print(f"Error scanning {name}: {e}")

if __name__ == "__main__":
    scan_live_market()
