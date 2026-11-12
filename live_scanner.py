import os
import json
import time as time_module
from datetime import datetime, time, timedelta
import pytz
import pyotp
import requests
import pandas as pd
from SmartApi import SmartConnect

# ==========================================
# 1. CONFIGURATION & CREDENTIALS
# ==========================================
API_KEY = os.environ.get("SMARTAPI_KEY", "").strip()
CLIENT_CODE = os.environ.get("CLIENT_CODE", "").strip()
PIN = os.environ.get("PIN", "").strip()
TOTP_KEY = os.environ.get("TOTP_KEY", "").strip()

TG_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

DB_FILE = "trades_db.json"
IST = pytz.timezone("Asia/Kolkata")
FORCE_EXIT_TIME = time(15, 15)  # Same-day mandatory square-off at 03:15 PM IST

# High-Accuracy Filtered Assets
ASSETS = {
    "NIFTY": {"token": "99926000", "exchange": "NSE", "lot_size": 25, "strike_step": 50, "spread_width": 100, "is_index": True},
    "AXISBANK": {"token": "5900", "exchange": "NSE", "lot_size": 625, "strike_step": 10, "spread_width": 20, "is_index": False},
    "SBIN": {"token": "3045", "exchange": "NSE", "lot_size": 750, "strike_step": 10, "spread_width": 20, "is_index": False},
    "TCS": {"token": "11536", "exchange": "NSE", "lot_size": 175, "strike_step": 20, "spread_width": 40, "is_index": False},
    "LT": {"token": "11483", "exchange": "NSE", "lot_size": 150, "strike_step": 20, "spread_width": 40, "is_index": False}
}


# ==========================================
# 2. HELPER FUNCTIONS (TELEGRAM & DB)
# ==========================================
def send_telegram_alert(message: str):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        print("⚠️ Telegram credentials missing.")
        return
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TG_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        res = requests.post(url, json=payload, timeout=10)
        if res.status_code == 200:
            print("✅ Telegram alert sent!")
        else:
            print(f"❌ Telegram failed: {res.text}")
    except Exception as e:
        print(f"❌ Telegram error: {e}")


def load_trades() -> list:
    if not os.path.exists(DB_FILE):
        return []
    try:
        with open(DB_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return []


def save_trades(trades: list):
    with open(DB_FILE, "w") as f:
        json.dump(trades, f, indent=4)


# ==========================================
# 3. ANGEL ONE SMARTAPI CONNECTION & DATA
# ==========================================
def init_smartapi():
    obj = SmartConnect(api_key=API_KEY)
    totp = pyotp.TOTP(TOTP_KEY).now()
    data = obj.generateSession(CLIENT_CODE, PIN, totp)
    if not data.get("status"):
        raise ConnectionError(f"SmartAPI Login Failed: {data.get('message')}")
    print("✅ Angel One SmartAPI Session Connected.")
    return obj


def get_historical_candles(obj, symbol: str, info: dict, interval: str = "FIVE_MINUTE", days: int = 12) -> pd.DataFrame:
    to_date = datetime.now(IST)
    from_date = to_date - timedelta(days=days)
    params = {
        "exchange": info["exchange"],
        "symboltoken": info["token"],
        "interval": interval,
        "fromdate": from_date.strftime("%Y-%m-%d %H:%M"),
        "todate": to_date.strftime("%Y-%m-%d %H:%M")
    }
    for attempt in range(3):
        try:
            time_module.sleep(0.55 + (attempt * 0.5))
            res = obj.getCandleData(params)
            if res and res.get("data"):
                df = pd.DataFrame(res["data"], columns=["Timestamp", "Open", "High", "Low", "Close", "Volume"])
                df["Timestamp"] = pd.to_datetime(df["Timestamp"])
                df.set_index("Timestamp", inplace=True)
                return df
        except Exception:
            pass
    return pd.DataFrame()


def fetch_live_ltp(obj, exchange: str, trading_symbol: str, token: str) -> float:
    try:
        data = obj.ltpData(exchange, trading_symbol, token)
        if data and data.get("status"):
            return float(data["data"]["ltp"])
    except Exception as e:
        print(f"⚠️ LTP fetch error for {trading_symbol}: {e}")
    return 0.0


def get_option_spread_ltp(obj, symbol: str, spot_price: float, side: str, info: dict):
    step = info["strike_step"]
    width = info["spread_width"]
    atm_strike = round(spot_price / step) * step

    if side == "BULLISH_SWEEP":
        opt_type = "CE"
        buy_strike = atm_strike
        sell_strike = atm_strike + width
    else:
        opt_type = "PE"
        buy_strike = atm_strike
        sell_strike = atm_strike - width

    buy_ltp, sell_ltp = 0.0, 0.0
    buy_sym, sell_sym = f"{symbol}{buy_strike}{opt_type}", f"{symbol}{sell_strike}{opt_type}"

    try:
        res_buy = obj.searchScrip("NFO", f"{symbol} {buy_strike} {opt_type}")
        if res_buy and res_buy.get("data"):
            contract_buy = sorted(res_buy["data"], key=lambda x: x["tradingsymbol"])[0]
            buy_sym = contract_buy["tradingsymbol"]
            buy_ltp = fetch_live_ltp(obj, "NFO", buy_sym, contract_buy["symboltoken"])

        time_module.sleep(0.35)

        res_sell = obj.searchScrip("NFO", f"{symbol} {sell_strike} {opt_type}")
        if res_sell and res_sell.get("data"):
            contract_sell = sorted(res_sell["data"], key=lambda x: x["tradingsymbol"])[0]
            sell_sym = contract_sell["tradingsymbol"]
            sell_ltp = fetch_live_ltp(obj, "NFO", sell_sym, contract_sell["symboltoken"])
    except Exception as e:
        print(f"⚠️ Option chain lookup error ({symbol}): {e}")

    net_debit = round(max(buy_ltp - sell_ltp, 0.0), 2)
    return {
        "buy_leg": buy_sym,
        "sell_leg": sell_sym,
        "buy_strike": buy_strike,
        "sell_strike": sell_strike,
        "opt_type": opt_type,
        "buy_ltp": buy_ltp,
        "sell_ltp": sell_ltp,
        "net_debit": net_debit
    }


# ==========================================
# 4. TRADE MANAGEMENT (1:1.50 TARGET, TRAILING SL & 03:15 PM EOD EXIT)
# ==========================================
def manage_open_trades(obj, trades: list, now_dt: datetime) -> bool:
    updated = False
    now_time = now_dt.time()

    for trade in trades:
        if trade.get("status") != "OPEN":
            continue

        symbol = trade["asset"]
        info = ASSETS.get(symbol)
        if not info:
            continue

        spot_ltp = fetch_live_ltp(obj, info["exchange"], symbol, info["token"])
        if spot_ltp <= 0:
            continue

        entry_price = trade["entry_price"]
        sl_price = trade["current_sl"]
        target_price = trade["target"]
        side = trade["side"]
        entry_dt = IST.localize(datetime.strptime(trade["entry_time"], "%Y-%m-%d %H:%M:%S"))
        hours_held = (now_dt - entry_dt).total_seconds() / 3600.0

        exit_reason = None

        if side == "BULLISH_SWEEP":
            risk_pts = entry_price - trade["initial_sl"]
            if spot_ltp >= entry_price + (1.10 * risk_pts) and sl_price < entry_price:
                trade["current_sl"] = entry_price
                sl_price = entry_price
                updated = True

            if spot_ltp >= target_price:
                exit_reason = "TARGET_HIT"
            elif spot_ltp <= sl_price:
                exit_reason = "TRAILING_SL" if sl_price == entry_price else "STOP_LOSS"
        else:
            risk_pts = trade["initial_sl"] - entry_price
            if spot_ltp <= entry_price - (1.10 * risk_pts) and sl_price > entry_price:
                trade["current_sl"] = entry_price
                sl_price = entry_price
                updated = True

            if spot_ltp <= target_price:
                exit_reason = "TARGET_HIT"
            elif spot_ltp >= sl_price:
                exit_reason = "TRAILING_SL" if sl_price == entry_price else "STOP_LOSS"

        if not exit_reason and hours_held >= 3.0:
            exit_reason = "3HR_TIME_EXIT"

        # MANDATORY SAME-DAY CLOSING RULE (03:15 PM IST)
        if not exit_reason and now_time >= FORCE_EXIT_TIME:
            exit_reason = "EOD_SAME_DAY_EXIT (03:15 PM)"

        if exit_reason:
            trade["status"] = "CLOSED"
            trade["exit_price"] = spot_ltp
            trade["exit_time"] = now_dt.strftime("%Y-%m-%d %H:%M:%S")
            trade["exit_reason"] = exit_reason
            updated = True

            msg = (
                f"🔔 *TRADE CLOSED ({exit_reason})*\n"
                f"📌 *Asset:* `{symbol}` ({side})\n"
                f"📊 *Spread:* `{trade['buy_leg']} / {trade['sell_leg']}`\n"
                f"💰 *Spot Entry:* `{entry_price}` ➔ *Exit:* `{spot_ltp}`\n"
                f"🕒 *Closed At:* `{trade['exit_time']}`"
            )
            send_telegram_alert(msg)

    return updated


# ==========================================
# 5. MAIN SCANNER EXECUTION
# ==========================================
def run_scanner():
    now_dt = datetime.now(IST)
    now_time = now_dt.time()
    print(f"🚀 Running Live Scanner at {now_dt.strftime('%Y-%m-%d %H:%M:%S IST')}")

    if not (time(9, 15) <= now_time <= time(15, 30)):
        print("⏸️ Outside NSE Market Hours (09:15 - 15:30 IST). Exiting.")
        return

    obj = init_smartapi()
    trades = load_trades()

    db_changed = manage_open_trades(obj, trades, now_dt)

    if now_time >= FORCE_EXIT_TIME:
        if db_changed:
            save_trades(trades)
        print("🛑 03:15 PM Same-Day Closing complete. No new entries post square-off.")
        return

    today_str = now_dt.strftime("%Y-%m-%d")
    active_assets = {t["asset"] for t in trades if t.get("status") == "OPEN" or t.get("date") == today_str}

    for symbol, info in ASSETS.items():
        if symbol in active_assets:
            print(f"⏭️ {symbol}: Already traded/open today. Skipping duplicate.")
            continue

        df_5m = get_historical_candles(obj, symbol, info, interval="FIVE_MINUTE", days=12)
        if len(df_5m) < 80:
            continue

        df_5m["Vol_MA20"] = df_5m["Volume"].rolling(20).mean()
        df_5m["Range"] = df_5m["High"] - df_5m["Low"]
        df_5m["Range_MA20"] = df_5m["Range"].rolling(20).mean()

        df_4h = df_5m.resample("4h").agg({
            "Open": "first",
            "High": "max",
            "Low": "min",
            "Close": "last",
            "Volume": "sum"
        }).dropna()

        if len(df_4h) < 20:
            continue

        df_4h["EMA20"] = df_4h["Close"].ewm(span=20, adjust=False).mean()

        prev_4h_high = float(df_4h["High"].iloc[-2])
        prev_4h_low = float(df_4h["Low"].iloc[-2])
        prev_4h_ema = float(df_4h["EMA20"].iloc[-2])

        c2 = df_5m.iloc[-3]
        c3 = df_5m.iloc[-2]
        spot_ltp = float(df_5m["Close"].iloc[-1])

        # Momentum / Volume Confirmation
        if info["is_index"]:
            rng_ratio = (c3["Range"] / c3["Range_MA20"]) if c3["Range_MA20"] > 0 else 0.0
            if rng_ratio < 1.40:
                print(f"⚪ {symbol}: Low range expansion ({rng_ratio:.2f}x). Waiting.")
                continue
        else:
            vol_ratio = (c3["Volume"] / c3["Vol_MA20"]) if c3["Vol_MA20"] > 0 else 0.0
            if vol_ratio < 1.50:
                print(f"⚪ {symbol}: Low volume spike ({vol_ratio:.2f}x). Waiting.")
                continue

        signal_side = None
        initial_sl = 0.0

        # Bearish Liquidity Sweep ONLY in 4H Downtrend
        if spot_ltp < prev_4h_ema and c2["High"] > prev_4h_high and c2["Close"] < prev_4h_high and c3["Close"] < c2["Low"]:
            signal_side = "BEARISH_SWEEP"
            initial_sl = round(max(c2["High"], c3["High"]) + (spot_ltp * 0.0005), 2)

        # Bullish Liquidity Sweep ONLY in 4H Uptrend
        elif spot_ltp > prev_4h_ema and c2["Low"] < prev_4h_low and c2["Close"] > prev_4h_low and c3["Close"] > c2["High"]:
            signal_side = "BULLISH_SWEEP"
            initial_sl = round(min(c2["Low"], c3["Low"]) - (spot_ltp * 0.0005), 2)

        if not signal_side:
            print(f"⚪ {symbol}: Monitoring (LTP: {spot_ltp} | 4H EMA20: {prev_4h_ema:.2f})")
            continue

        risk_pts = abs(spot_ltp - initial_sl)
        if risk_pts <= 0:
            continue

        target_price = round(spot_ltp + (1.50 * risk_pts), 2) if signal_side == "BULLISH_SWEEP" else round(spot_ltp - (1.50 * risk_pts), 2)

        opt_data = get_option_spread_ltp(obj, symbol, spot_ltp, signal_side, info)
        capital_at_risk = round(opt_data["net_debit"] * info["lot_size"], 2)

        new_trade = {
            "date": today_str,
            "entry_time": now_dt.strftime("%Y-%m-%d %H:%M:%S"),
            "exit_time": None,
            "asset": symbol,
            "side": signal_side,
            "status": "OPEN",
            "entry_price": spot_ltp,
            "initial_sl": initial_sl,
            "current_sl": initial_sl,
            "target": target_price,
            "buy_leg": opt_data["buy_leg"],
            "sell_leg": opt_data["sell_leg"],
            "buy_ltp": opt_data["buy_ltp"],
            "sell_ltp": opt_data["sell_ltp"],
            "net_debit": opt_data["net_debit"],
            "lot_size": info["lot_size"],
            "capital_at_risk": capital_at_risk,
            "exit_price": None,
            "exit_reason": None
        }

        trades.append(new_trade)
        db_changed = True

        spread_label = "Bull Call Spread" if signal_side == "BULLISH_SWEEP" else "Bear Put Spread"
        alert_msg = (
            f"🚨 *NEW PAPER TRADE TRIGGERED*\n"
            f"📌 *Asset:* `{symbol}` | *{spread_label}*\n"
            f"💰 *Spot Entry:* `{spot_ltp}` (4H EMA20: `{prev_4h_ema:.2f}`)\n"
            f"🛡️ *Stop Loss:* `{initial_sl}` | 🎯 *Target (1:1.5):* `{target_price}`\n\n"
            f"📊 *Option Spread (1 Lot = {info['lot_size']}):*\n"
            f"• *BUY:* `{opt_data['buy_leg']}` @ ₹{opt_data['buy_ltp']}\n"
            f"• *SELL:* `{opt_data['sell_leg']}` @ ₹{opt_data['sell_ltp']}\n"
            f"• *Net Debit:* ₹{opt_data['net_debit']} | *Max Risk:* ₹{capital_at_risk:,.2f}\n\n"
            f"⏰ *Rule:* Auto square-off at 03:15 PM IST (Same-Day)"
        )
        send_telegram_alert(alert_msg)
        print(f"🔥 {symbol}: {signal_side} logged & alerted!")

    if db_changed:
        save_trades(trades)
        print("💾 trades_db.json updated successfully.")


if __name__ == "__main__":
    run_scanner()
