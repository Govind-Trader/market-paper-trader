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
CLIENT_CODE = (os.environ.get("SMARTAPI_CLIENT_CODE") or os.environ.get("CLIENT_CODE") or "").strip()
PIN = (os.environ.get("SMARTAPI_PIN") or os.environ.get("PIN") or "").strip()
TOTP_KEY = (os.environ.get("SMARTAPI_TOTP_KEY") or os.environ.get("TOTP_KEY") or "").strip()

TG_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

OPEN_DB_FILE = "live_trades.json"
CLOSED_DB_FILE = "trades_db.json"
IST = pytz.timezone("Asia/Kolkata")
FORCE_EXIT_TIME = time(15, 15)  # Same-day mandatory square-off at 03:15 PM IST

ASSETS = {
    "NIFTY": {"token": "99926000", "exchange": "NSE", "lot_size": 65, "strike_step": 50, "spread_width": 100, "margin": 45000, "is_index": True},
    "BANKNIFTY": {"token": "99926009", "exchange": "NSE", "lot_size": 30, "strike_step": 100, "spread_width": 200, "margin": 50000, "is_index": True},
    "FINNIFTY": {"token": "99926037", "exchange": "NSE", "lot_size": 25, "strike_step": 50, "spread_width": 100, "margin": 45000, "is_index": True},
    "AXISBANK": {"token": "5900", "exchange": "NSE", "lot_size": 625, "strike_step": 10, "spread_width": 20, "margin": 55000, "is_index": False},
    "SBIN": {"token": "3045", "exchange": "NSE", "lot_size": 750, "strike_step": 10, "spread_width": 20, "margin": 50000, "is_index": False},
    "TCS": {"token": "11536", "exchange": "NSE", "lot_size": 175, "strike_step": 20, "spread_width": 40, "margin": 65000, "is_index": False},
    "LT": {"token": "11483", "exchange": "NSE", "lot_size": 150, "strike_step": 20, "spread_width": 40, "margin": 60000, "is_index": False},
    "HDFCBANK": {"token": "1333", "exchange": "NSE", "lot_size": 550, "strike_step": 10, "spread_width": 20, "margin": 60000, "is_index": False}
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


def load_json_db(filepath: str) -> list:
    if not os.path.exists(filepath):
        return []
    try:
        with open(filepath, "r") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []


def save_json_db(filepath: str, data: list):
    with open(filepath, "w") as f:
        json.dump(data, f, indent=4)


# ==========================================
# 3. ANGEL ONE SMARTAPI CONNECTION & DATA
# ==========================================
def init_smartapi():
    obj = SmartConnect(api_key=API_KEY)
    totp = pyotp.TOTP(TOTP_KEY).now()
    data = obj.generateSession(CLIENT_CODE, PIN, totp)
    if not data or not data.get("status"):
        raise ConnectionError(f"SmartAPI Login Failed: {data}")
    print("✅ Angel One SmartAPI Session Connected.")
    return obj


def get_historical_candles(obj, info: dict, interval: str = "FIVE_MINUTE", days: int = 12) -> pd.DataFrame:
    to_date = datetime.now(IST)
    from_date = to_date - timedelta(days=days)
    params = {
        "exchange": info["exchange"],
        "symboltoken": info["token"],
        "interval": interval,
        "fromdate": from_date.strftime("%Y-%m-%d 09:15"),
        "todate": to_date.strftime("%Y-%m-%d 15:30")
    }
    for attempt in range(3):
        try:
            time_module.sleep(0.55 + (attempt * 0.5))
            res = obj.getCandleData(params)
            if res and res.get("status") and res.get("data"):
                df = pd.DataFrame(res["data"], columns=["Timestamp", "Open", "High", "Low", "Close", "Volume"])
                df["Timestamp"] = pd.to_datetime(df["Timestamp"])
                if df["Timestamp"].dt.tz is None:
                    df["Timestamp"] = df["Timestamp"].dt.tz_localize(IST)
                else:
                    df["Timestamp"] = df["Timestamp"].dt.tz_convert(IST)
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
        "spread_str": f"Buy {buy_strike} {opt_type} / Sell {sell_strike} {opt_type}",
        "buy_ltp": buy_ltp,
        "sell_ltp": sell_ltp,
        "net_debit": net_debit
    }


# ==========================================
# 4. TIME-GAP & CANDLE-BY-CANDLE OPEN TRADE RESOLVER
# ==========================================
def manage_open_trades(obj, now_dt: datetime) -> bool:
    open_trades = load_json_db(OPEN_DB_FILE)
    if not open_trades:
        return False

    closed_trades = load_json_db(CLOSED_DB_FILE)
    still_open = []
    changed = False

    for trade in open_trades:
        symbol = trade["asset"]
        info = ASSETS.get(symbol)
        if not info:
            still_open.append(trade)
            continue

        df_5m = get_historical_candles(obj, info, interval="FIVE_MINUTE", days=12)
        if df_5m.empty:
            still_open.append(trade)
            continue

        entry_dt = IST.localize(datetime.strptime(trade["entry_time"], "%Y-%m-%d %H:%M:%S"))
        post_df = df_5m[df_5m.index > entry_dt]

        entry_price = float(trade["entry_price"])
        sl_price = float(trade["current_sl"])
        initial_sl = float(trade["initial_sl"])
        target_price = float(trade["target"])
        side = trade["side"]
        risk_pts = abs(entry_price - initial_sl)
        lot_size = info["lot_size"]

        exit_reason = None
        exit_price = entry_price
        exit_dt = now_dt
        prev_date = entry_dt.date()

        # Walk every 5m bar after entry so GitHub Actions cron delays NEVER miss SL/Target/Gap/EOD!
        for bar_dt, bar in post_df.iterrows():
            b_open, b_high, b_low, b_close = float(bar["Open"]), float(bar["High"]), float(bar["Low"]), float(bar["Close"])
            curr_date = bar_dt.date()

            # 1. OVERNIGHT TIME-GAP CHECK (Next-Day 09:15 AM Open across SL/Target)
            if curr_date > prev_date:
                if side == "BULLISH_SWEEP":
                    if b_open <= sl_price:
                        exit_price = b_open
                        exit_dt = bar_dt
                        exit_reason = "GAP_TRAILING_SL" if abs(sl_price - entry_price) < 0.05 else "GAP_STOP_LOSS"
                        break
                    elif b_open >= target_price:
                        exit_price = b_open
                        exit_dt = bar_dt
                        exit_reason = "GAP_TARGET_HIT"
                        break
                else:
                    if b_open >= sl_price:
                        exit_price = b_open
                        exit_dt = bar_dt
                        exit_reason = "GAP_TRAILING_SL" if abs(sl_price - entry_price) < 0.05 else "GAP_STOP_LOSS"
                        break
                    elif b_open <= target_price:
                        exit_price = b_open
                        exit_dt = bar_dt
                        exit_reason = "GAP_TARGET_HIT"
                        break
                prev_date = curr_date

            # 2. INTRADAY SL, BREAKEVEN TRAIL & TARGET CHECK
            if side == "BULLISH_SWEEP":
                if b_high >= entry_price + (1.10 * risk_pts) and sl_price < entry_price:
                    sl_price = entry_price
                    trade["current_sl"] = entry_price
                    changed = True

                if b_low <= sl_price:
                    exit_price = sl_price
                    exit_dt = bar_dt
                    exit_reason = "BREAKEVEN_SL" if abs(sl_price - entry_price) < 0.05 else "STOP_LOSS"
                    break
                elif b_high >= target_price:
                    exit_price = target_price
                    exit_dt = bar_dt
                    exit_reason = "TARGET_HIT"
                    break
            else:
                if b_low <= entry_price - (1.10 * risk_pts) and sl_price > entry_price:
                    sl_price = entry_price
                    trade["current_sl"] = entry_price
                    changed = True

                if b_high >= sl_price:
                    exit_price = sl_price
                    exit_dt = bar_dt
                    exit_reason = "BREAKEVEN_SL" if abs(sl_price - entry_price) < 0.05 else "STOP_LOSS"
                    break
                elif b_low <= target_price:
                    exit_price = target_price
                    exit_dt = bar_dt
                    exit_reason = "TARGET_HIT"
                    break

            # 3. SAME-DAY MANDATORY SQUARE-OFF AT 03:15 PM IST (Even if cron runs late!)
            if bar_dt.time() >= FORCE_EXIT_TIME:
                exit_price = b_close
                exit_dt = bar_dt
                exit_reason = "EOD_SAME_DAY_EXIT"
                break

        if exit_reason:
            spot_move = (exit_price - entry_price) if side == "BULLISH_SWEEP" else (entry_price - exit_price)
            days_held = max((exit_dt.date() - entry_dt.date()).days, 0)
            theta_penalty = days_held * 2.5 * lot_size
            # Exact P&L (Breakeven 0 move = -₹60 brokerage, compatible with app.py!)
            net_pnl = round((spot_move * 0.40 * lot_size) - 60.0 - theta_penalty, 2)

            closed_record = {
                "entry_time": trade["entry_time"],
                "exit_time": exit_dt.strftime("%Y-%m-%d %H:%M:%S"),
                "date": trade["date"],
                "asset": symbol,
                "side": side,
                "spread": trade.get("spread", f"{trade.get('buy_leg', '')} / {trade.get('sell_leg', '')}"),
                "entry_price": round(entry_price, 2),
                "exit_price": round(exit_price, 2),
                "initial_sl": round(initial_sl, 2),
                "target": round(target_price, 2),
                "entry_premium": trade.get("net_debit", 25.0),
                "outcome": exit_reason,
                "capital_deployed": info["margin"],
                "net_pnl": net_pnl,
                "vol_ratio": trade.get("vol_ratio", "1.8x (Live)")
            }
            closed_trades.append(closed_record)
            changed = True

            msg = (
                f"🔔 *TRADE CLOSED ({exit_reason})*\n"
                f"📌 *Asset:* `{symbol}` ({side})\n"
                f"📊 *Spread:* `{closed_record['spread']}`\n"
                f"💰 *Spot Entry:* `{entry_price}` ➔ *Exit:* `{round(exit_price, 2)}`\n"
                f"💵 *Net P&L:* `₹{net_pnl:,.2f}`\n"
                f"🕒 *Closed At:* `{closed_record['exit_time']}`"
            )
            send_telegram_alert(msg)
        else:
            still_open.append(trade)

    if changed:
        save_json_db(OPEN_DB_FILE, still_open)
        save_json_db(CLOSED_DB_FILE, closed_trades)

    return changed


# ==========================================
# 5. MAIN SCANNER EXECUTION
# ==========================================
def run_scanner():
    now_dt = datetime.now(IST)
    now_time = now_dt.time()
    print(f"🚀 Running Live Scanner at {now_dt.strftime('%Y-%m-%d %H:%M:%S IST')}")

    obj = init_smartapi()

    # Always resolve any lingering open trades first (even outside market hours!)
    manage_open_trades(obj, now_dt)

    if not (time(9, 15) <= now_time <= time(15, 30)):
        print("⏸️ Outside NSE Market Hours (09:15 - 15:30 IST). Exiting.")
        return

    if now_time >= FORCE_EXIT_TIME:
        print("🛑 03:15 PM Same-Day Closing complete. No new entries post square-off.")
        return

    open_trades = load_json_db(OPEN_DB_FILE)
    closed_trades = load_json_db(CLOSED_DB_FILE)
    today_str = now_dt.strftime("%Y-%m-%d")

    active_assets = {
        t["asset"] for t in (open_trades + closed_trades)
        if t.get("date") == today_str
    }

    new_entries = False

    for symbol, info in ASSETS.items():
        if symbol in active_assets:
            print(f"⏭️ {symbol}: Already traded/open today. Skipping duplicate.")
            continue

        df_5m = get_historical_candles(obj, info, interval="FIVE_MINUTE", days=12)
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

        if info["is_index"]:
            rng_ratio = (c3["Range"] / c3["Range_MA20"]) if c3["Range_MA20"] > 0 else 0.0
            if rng_ratio < 1.40:
                continue
            ratio_str = f"{rng_ratio:.2f}x (Rng)"
        else:
            vol_ratio = (c3["Volume"] / c3["Vol_MA20"]) if c3["Vol_MA20"] > 0 else 0.0
            if vol_ratio < 1.50:
                continue
            ratio_str = f"{vol_ratio:.2f}x (Vol)"

        signal_side = None
        initial_sl = 0.0

        if spot_ltp < prev_4h_ema and c2["High"] > prev_4h_high and c2["Close"] < prev_4h_high and c3["Close"] < c2["Low"]:
            signal_side = "BEARISH_SWEEP"
            initial_sl = round(max(c2["High"], c3["High"]) + (spot_ltp * 0.0005), 2)

        elif spot_ltp > prev_4h_ema and c2["Low"] < prev_4h_low and c2["Close"] > prev_4h_low and c3["Close"] > c2["High"]:
            signal_side = "BULLISH_SWEEP"
            initial_sl = round(min(c2["Low"], c3["Low"]) - (spot_ltp * 0.0005), 2)

        if not signal_side:
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
            "asset": symbol,
            "side": signal_side,
            "status": "OPEN",
            "spread": opt_data["spread_str"],
            "entry_price": spot_ltp,
            "initial_sl": initial_sl,
            "current_sl": initial_sl,
            "target": target_price,
            "buy_leg": opt_data["buy_leg"],
            "sell_leg": opt_data["sell_leg"],
            "net_debit": opt_data["net_debit"],
            "lot_size": info["lot_size"],
            "vol_ratio": ratio_str
        }

        open_trades.append(new_trade)
        new_entries = True

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
        print(f"🔥 {symbol}: {signal_side} logged to live_trades.json & alerted!")

    if new_entries:
        save_json_db(OPEN_DB_FILE, open_trades)
        print("💾 live_trades.json updated successfully.")


if __name__ == "__main__":
    run_scanner()
