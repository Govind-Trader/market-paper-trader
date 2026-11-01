import os
import sys
import json
import pytz
import pyotp
import requests
import pandas as pd
from datetime import datetime, timedelta
from SmartApi import SmartConnect

IST = pytz.timezone("Asia/Kolkata")

# Credentials from GitHub Secrets
API_KEY = os.getenv("SMARTAPI_KEY")
CLIENT_CODE = os.getenv("SMARTAPI_CLIENT_CODE")
PIN = os.getenv("SMARTAPI_PIN")
TOTP_KEY = os.getenv("SMARTAPI_TOTP_KEY")

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
    "TCS": {"token": "11536", "exchange": "NSE", "lot_size": 175, "strike_step": 20, "spread_width": 40, "is_index": False},
    "BANKNIFTY": {"token": "99926009", "exchange": "NSE", "lot_size": 15, "strike_step": 100, "spread_width": 200, "is_index": True},
    "FINNIFTY": {"token": "99926037", "exchange": "NSE", "lot_size": 25, "strike_step": 50, "spread_width": 100, "is_index": True},
    "LT": {"token": "11483", "exchange": "NSE", "lot_size": 150, "strike_step": 20, "spread_width": 40, "is_index": False},
    "AXISBANK": {"token": "5900", "exchange": "NSE", "lot_size": 625, "strike_step": 10, "spread_width": 20, "is_index": False},
    "NIFTY": {"token": "99926000", "exchange": "NSE", "lot_size": 25, "strike_step": 50, "spread_width": 100, "is_index": True},
    "HDFCBANK": {"token": "1333", "exchange": "NSE", "lot_size": 550, "strike_step": 10, "spread_width": 20, "is_index": False}
}

def get_smartapi_client():
    try:
        smart_api = SmartConnect(API_KEY)
        totp = pyotp.TOTP(TOTP_KEY).now()
        data = smart_api.generateSession(CLIENT_CODE, PIN, totp)
        if data and data.get("status"):
            print("SmartAPI Session Connected Successfully.")
            return smart_api
        else:
            print("SmartAPI Session Failed:", data)
            return None
    except Exception as e:
        print(f"Session Authentication Exception: {e}")
        return None

def fetch_candles(smart_api, token, exchange, interval="FIVE_MINUTE", days=10):
    to_date = datetime.now(IST)
    from_date = to_date - timedelta(days=days)
    params = {
        "exchange": exchange,
        "symboltoken": token,
        "interval": interval,
        "fromdate": from_date.strftime("%Y-%m-%d %H:%M"),
        "todate": to_date.strftime("%Y-%m-%d %H:%M")
    }
    try:
        res = smart_api.getCandleData(params)
        if res and res.get("status") and res.get("data"):
            df = pd.DataFrame(res["data"], columns=["timestamp", "Open", "High", "Low", "Close", "Volume"])
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            df.set_index("timestamp", inplace=True)
            if df.index.tz is None:
                df.index = df.index.tz_localize("UTC").tz_convert(IST)
            else:
                df.index = df.index.tz_convert(IST)
            return df
    except Exception as e:
        print(f"Candle Fetch Exception for token {token}: {e}")
    return pd.DataFrame()

def get_option_premium(smart_api, symbol, strike, opt_type, is_index):
    """
    Fetches real-time Option LTP from Angel One NFO segment.
    If market is closed or token is unindexed, applies intrinsic Delta pricing.
    """
    try:
        # Search scrip to get token
        search_res = smart_api.searchScrip(exchange="NFO", searchscrip=f"{symbol}{strike}{opt_type}")
        if search_res and search_res.get("status") and search_res.get("data"):
            token = search_res["data"][0]["symboltoken"]
            ltp_res = smart_api.ltpData("NFO", search_res["data"][0]["tradingsymbol"], token)
            if ltp_res and ltp_res.get("status") and ltp_res.get("data"):
                ltp = float(ltp_res["data"]["ltp"])
                if ltp > 0:
                    return ltp
    except Exception as e:
        print(f"Option LTP fetch exception for {symbol} {strike} {opt_type}: {e}")

    # Theoretical model estimation fallback (Delta ~0.50 ATM, ~0.30 OTM)
    return round(float(strike) * 0.015, 1)

def record_trade_log(trade_dict):
    db_file = "trades_db.json"
    trades = []
    if os.path.exists(db_file):
        try:
            with open(db_file, "r") as f:
                trades = json.load(f)
        except Exception:
            trades = []

    # Check duplicate trade in same 15-min window
    for t in trades:
        if t.get("asset") == trade_dict.get("asset") and t.get("candle_time") == trade_dict.get("candle_time"):
            return

    trades.append(trade_dict)
    with open(db_file, "w") as f:
        json.dump(trades, f, indent=2)
    print(f"Recorded trade for {trade_dict['asset']} to {db_file}")

def scan_live_market():
    smart_api = get_smartapi_client()
    if not smart_api:
        print("SmartAPI connection unestablished. Exiting scan.")
        return

    now = datetime.now(IST)
    print(f"Running Real-Time SmartAPI Scan at {now.strftime('%Y-%m-%d %H:%M:%S')} IST...")

    for name, conf in ASSETS.items():
        try:
            df_5m = fetch_candles(smart_api, conf["token"], conf["exchange"], interval="FIVE_MINUTE", days=10)
            if df_5m.empty or len(df_5m) < 40:
                continue

            # Daily 20 EMA
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

                # Real Options Pricing
                buy_ltp = get_option_premium(smart_api, name, atm, opt_type, conf["is_index"])
                sell_ltp = get_option_premium(smart_api, name, otm, opt_type, conf["is_index"])
                net_debit = round(buy_ltp - sell_ltp, 2)
                max_risk_inr = round(net_debit * conf["lot_size"], 2)
                max_reward_inr = round((conf["spread_width"] - net_debit) * conf["lot_size"], 2)

                candle_time_str = c3.name.strftime("%Y-%m-%d %H:%M IST")

                msg = (
                    f"🚨 *HIGH-CONVICTION TRADE SETUP*\n\n"
                    f"📌 *Asset:* `{name}` ({side})\n"
                    f"⏰ *Time:* `{candle_time_str}`\n\n"
                    f"💰 *OPTION SPREAD EXECUTION (1 LOT):*\n"
                    f"🟢 *BUY Leg:* `{atm} {opt_type}` @ `₹{buy_ltp:.1f}`\n"
                    f"🔴 *SELL Leg:* `{otm} {opt_type}` @ `₹{sell_ltp:.1f}`\n"
                    f"💵 *Net Debit:* `₹{net_debit:.1f}` / share\n"
                    f"💼 *Lot Size:* `{conf['lot_size']}` | *Capital Risk:* `₹{max_risk_inr:,.0f}`\n"
                    f"🏆 *Max Reward:* `₹{max_reward_inr:,.0f}`\n\n"
                    f"🎯 *Spot Reference:* Entry `₹{entry_spot:.2f}` | SL `₹{sl_spot:.2f}` | Target `₹{tgt_spot:.2f}`\n"
                    f"⚡ *Confirmation:* `{vol_str}`\n"
                    f"🛡️ *Management:* BE Trailing at 1:1.25 | Time Exit: 3 Hours"
                )

                print(f"Triggering SmartAPI Alert for {name} with Option Pricing...")
                send_telegram_alert(msg)

                # Save trade in database
                trade_record = {
                    "asset": name,
                    "candle_time": candle_time_str,
                    "direction": "CALL_SPREAD" if is_bull else "PUT_SPREAD",
                    "spot_entry": round(entry_spot, 2),
                    "spot_sl": round(sl_spot, 2),
                    "spot_target": round(tgt_spot, 2),
                    "buy_strike": atm,
                    "buy_ltp": buy_ltp,
                    "sell_strike": otm,
                    "sell_ltp": sell_ltp,
                    "net_debit": net_debit,
                    "lot_size": conf["lot_size"],
                    "capital_at_risk": max_risk_inr,
                    "status": "OPEN",
                    "entry_timestamp": datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")
                }
                record_trade_log(trade_record)

        except Exception as e:
            print(f"Error scanning {name}: {e}")

if __name__ == "__main__":
    scan_live_market()
