import os
import json
import pytz
import pyotp
import numpy as np
import pandas as pd
from datetime import datetime, time, timedelta
import yfinance as yf
from SmartApi import SmartConnect

def get_angel_smart_api():
    api_key = (os.getenv("SMARTAPI_KEY") or "").strip()
    client_code = (os.getenv("SMARTAPI_CLIENT_CODE") or "").strip()
    pin = (os.getenv("SMARTAPI_PIN") or "").strip()
    totp_secret = (os.getenv("SMARTAPI_TOTP_KEY") or "").strip()
    
    if not all([api_key, client_code, pin, totp_secret]):
        return None
        
    try:
        smart_api = SmartConnect(api_key=api_key)
        totp = pyotp.TOTP(totp_secret).now()
        data = smart_api.generateSession(client_code, pin, totp)
        if data and data.get("status"):
            return smart_api
    except Exception as e:
        print(f"SmartAPI Auth Error: {e}")
    return None

def fetch_angel_data(smart_api, token, symbol_name, interval="FIVE_MINUTE", days=58):
    ist = pytz.timezone("Asia/Kolkata")
    now = datetime.now(ist)
    from_date = (now - timedelta(days=days)).strftime("%Y-%m-%d 09:15")
    to_date = now.strftime("%Y-%m-%d 15:30")
    
    params = {
        "exchange": "NSE",
        "symboltoken": str(token),
        "interval": interval,
        "fromdate": from_date,
        "todate": to_date
    }
    try:
        res = smart_api.getCandleData(params)
        if res and res.get("status") and res.get("data"):
            df = pd.DataFrame(res["data"], columns=["Timestamp", "Open", "High", "Low", "Close", "Volume"])
            df["Timestamp"] = pd.to_datetime(df["Timestamp"])
            df.set_index("Timestamp", inplace=True)
            if df.index.tz is None:
                df.index = df.index.tz_localize("UTC").tz_convert(ist)
            else:
                df.index = df.index.tz_convert(ist)
            return df
    except Exception as e:
        print(f"Error fetching {symbol_name}: {e}")
    return pd.DataFrame()

# High-conviction consistent universe (60%+ Win Rate Potential)
ASSETS = {
    "TCS": {"token": "11536", "symbol": "TCS.NS", "market": "NSE", "lot_size": 175, "strike_step": 50, "spread_width": 100, "margin": 65000},
    "BANKNIFTY": {"token": "99926009", "symbol": "^NSEBANK", "market": "NSE_INDEX", "lot_size": 15, "strike_step": 100, "spread_width": 200, "margin": 50000},
    "FINNIFTY": {"token": "99926037", "symbol": "NIFTY_FIN_SERVICE.NS", "market": "NSE_INDEX", "lot_size": 25, "strike_step": 50, "spread_width": 100, "margin": 45000},
    "SBIN": {"token": "3045", "symbol": "SBIN.NS", "market": "NSE", "lot_size": 750, "strike_step": 5, "spread_width": 10, "margin": 50000},
    "LT": {"token": "11483", "symbol": "LT.NS", "market": "NSE", "lot_size": 150, "strike_step": 20, "spread_width": 40, "margin": 60000},
    "AXISBANK": {"token": "5900", "symbol": "AXISBANK.NS", "market": "NSE", "lot_size": 625, "strike_step": 10, "spread_width": 20, "margin": 55000},
    "NIFTY": {"token": "99926000", "symbol": "^NSEI", "market": "NSE_INDEX", "lot_size": 25, "strike_step": 50, "spread_width": 100, "margin": 45000},
    "HDFCBANK": {"token": "1333", "symbol": "HDFCBANK.NS", "market": "NSE", "lot_size": 550, "strike_step": 10, "spread_width": 20, "margin": 60000}
}

def run_simulation():
    print("🚀 Running 60%+ Trend-Aligned 4H Sweep Engine...")
    smart_api = get_angel_smart_api()
    all_trades = []
    ist = pytz.timezone("Asia/Kolkata")
    
    for name, conf in ASSETS.items():
        df_5m = pd.DataFrame()
        
        if smart_api and conf["token"]:
            df_5m = fetch_angel_data(smart_api, conf["token"], name, interval="FIVE_MINUTE", days=58)
            
        if df_5m.empty:
            try:
                ticker = yf.Ticker(conf["symbol"])
                df_5m = ticker.history(period="59d", interval="5m")
                if df_5m.empty and name == "FINNIFTY":
                    ticker = yf.Ticker("NIFTY_FIN_SERVICE.NS")
                    df_5m = ticker.history(period="59d", interval="5m")
                if not df_5m.empty:
                    if df_5m.index.tz is None:
                        df_5m.index = df_5m.index.tz_localize("UTC").tz_convert(ist)
                    else:
                        df_5m.index = df_5m.index.tz_convert(ist)
            except Exception as e:
                pass

        if df_5m.empty or len(df_5m) < 100:
            continue

        # 1. DAILY 20 EMA TREND FILTER (Higher Timeframe Direction)
        df_daily = df_5m.resample("1D").agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna()
        df_daily["EMA20"] = df_daily["Close"].ewm(span=20, adjust=False).mean()
        df_5m["Daily_EMA20"] = df_daily["EMA20"].reindex(df_5m.index, method="ffill")

        # 2. 4-HOUR LIQUIDITY LEVELS (High & Low of last 24h)
        df_4h = df_5m.resample("4h").agg({"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}).dropna()
        df_4h["HTF_Low"] = df_4h["Low"].shift(1).rolling(window=6).min()
        df_4h["HTF_High"] = df_4h["High"].shift(1).rolling(window=6).max()
        df_5m["HTF_Low"] = df_4h["HTF_Low"].reindex(df_5m.index, method="ffill")
        df_5m["HTF_High"] = df_4h["HTF_High"].reindex(df_5m.index, method="ffill")

        df_5m["Vol_MA20"] = df_5m["Volume"].rolling(window=20).mean()
        df_5m["Range"] = df_5m["High"] - df_5m["Low"]
        df_5m["Range_MA20"] = df_5m["Range"].rolling(window=20).mean()

        last_trade_date = None
        i = 40
        while i < len(df_5m) - 6:
            c1 = df_5m.iloc[i]     # Candle 1 (4H Sweep Alert)
            c2 = df_5m.iloc[i + 1] # Candle 2 (Volume Breakout / Absorption)
            c3 = df_5m.iloc[i + 2] # Candle 3 (Trigger on High/Low Breakout)
            c_time = c1.name.time()
            trade_date = c1.name.date()

            # Trade Window: Avoid early 9:15-9:25 noise & late session chop
            if not (time(9, 30) <= c_time <= time(14, 45)):
                i += 1
                continue

            # Cap: Max 1 high-conviction trade per day per asset
            if last_trade_date == trade_date:
                i += 1
                continue

            htf_low = c1["HTF_Low"]
            htf_high = c1["HTF_High"]
            daily_ema = c1["Daily_EMA20"]

            if pd.isna(htf_low) or pd.isna(htf_high) or pd.isna(daily_ema):
                i += 1
                continue

            # Volume Absorption Calculation
            avg_vol_2 = c2["Vol_MA20"]
            avg_rng_2 = c2["Range_MA20"]
            if avg_vol_2 > 0 and c2["Volume"] > 0:
                has_vol_absorption = (c2["Volume"] / avg_vol_2) >= 1.5
                vol_str = f"{round(c2['Volume'] / avg_vol_2, 2)}x (Vol)"
            else:
                has_vol_absorption = ((c2["High"] - c2["Low"]) / avg_rng_2) >= 1.35 if avg_rng_2 > 0 else False
                vol_str = f"{round((c2['High'] - c2['Low']) / avg_rng_2, 2)}x (Rng)"

            # ==========================================
            # SETUP 1: TREND-ALIGNED BULLISH SWEEP (CE SPREAD)
            # ==========================================
            # Daily Trend Bullish (Close > Daily EMA20)
            is_bullish_trend = c1["Close"] >= daily_ema
            is_c1_sweep_low = c1["Low"] <= htf_low
            is_c2_breakout_up = (c2["Close"] > c2["Open"]) and (c2["Close"] > c1["High"]) and has_vol_absorption
            is_c3_continuation_up = c3["High"] > c2["High"]

            if is_bullish_trend and is_c1_sweep_low and is_c2_breakout_up and is_c3_continuation_up:
                entry_price = c2["High"]
                sl_buffer = entry_price * 0.0008
                initial_sl = min(c1["Low"], c2["Low"]) - sl_buffer
                risk_pts = entry_price - initial_sl

                if risk_pts > 0:
                    # Optimized Target: 1:1.8 (Realistic Intraday Reach)
                    target_price = entry_price + (risk_pts * 1.8)
                    atm = int(round(entry_price / conf["strike_step"]) * conf["strike_step"])
                    otm = atm + conf["spread_width"]

                    max_gain = conf["spread_width"] * 0.55 * conf["lot_size"]
                    max_loss = -(conf["spread_width"] * 0.35 * conf["lot_size"])

                    current_sl = initial_sl
                    outcome = "EXPIRED"
                    exit_price = entry_price
                    exit_time = c3.name
                    pnl = 0

                    for forward_idx in range(i + 3, min(i + 36, len(df_5m))):
                        bar = df_5m.iloc[forward_idx]

                        # Target Hit
                        if bar["High"] >= target_price:
                            outcome = "TARGET_HIT"
                            exit_price = target_price
                            exit_time = bar.name
                            pnl = max_gain
                            i = forward_idx
                            break

                        # Faster Breakeven: At 1:1.25 move, shift SL to Entry
                        if bar["High"] >= (entry_price + (risk_pts * 1.25)):
                            current_sl = max(current_sl, entry_price)

                        # SL Check
                        if bar["Low"] <= current_sl:
                            outcome = "TRAILING_SL" if current_sl >= entry_price else "STOP_LOSS"
                            exit_price = current_sl
                            exit_time = bar.name
                            pnl = max_gain * 0.20 if current_sl >= entry_price else max_loss
                            i = forward_idx
                            break
                    else:
                        exit_price = df_5m.iloc[min(i + 35, len(df_5m) - 1)]["Close"]
                        pnl = max_gain * 0.35 if exit_price > entry_price else max_loss * 0.3
                        i += 35

                    all_trades.append({
                        "entry_time": c3.name.strftime("%Y-%m-%d %H:%M:%S"),
                        "exit_time": exit_time.strftime("%Y-%m-%d %H:%M:%S"),
                        "date": c3.name.strftime("%Y-%m-%d"),
                        "asset": name,
                        "side": "BULLISH_SWEEP",
                        "spread": f"{atm} CE / {otm} CE",
                        "entry_price": round(entry_price, 2),
                        "exit_price": round(exit_price, 2),
                        "initial_sl": round(initial_sl, 2),
                        "target": round(target_price, 2),
                        "outcome": outcome,
                        "capital_deployed": conf["margin"],
                        "net_pnl": round(pnl, 2),
                        "vol_ratio": vol_str
                    })
                    last_trade_date = trade_date
                    continue

            # ==========================================
            # SETUP 2: TREND-ALIGNED BEARISH SWEEP (PE SPREAD)
            # ==========================================
            # Daily Trend Bearish (Close < Daily EMA20)
            is_bearish_trend = c1["Close"] <= daily_ema
            is_c1_sweep_high = c1["High"] >= htf_high
            is_c2_breakdown_dn = (c2["Close"] < c2["Open"]) and (c2["Close"] < c1["Low"]) and has_vol_absorption
            is_c3_continuation_dn = c3["Low"] < c2["Low"]

            if is_bearish_trend and is_c1_sweep_high and is_c2_breakdown_dn and is_c3_continuation_dn:
                entry_price = c2["Low"]
                sl_buffer = entry_price * 0.0008
                initial_sl = max(c1["High"], c2["High"]) + sl_buffer
                risk_pts = initial_sl - entry_price

                if risk_pts > 0:
                    target_price = entry_price - (risk_pts * 1.8)
                    atm = int(round(entry_price / conf["strike_step"]) * conf["strike_step"])
                    otm = atm - conf["spread_width"]

                    max_gain = conf["spread_width"] * 0.55 * conf["lot_size"]
                    max_loss = -(conf["spread_width"] * 0.35 * conf["lot_size"])

                    current_sl = initial_sl
                    outcome = "EXPIRED"
                    exit_price = entry_price
                    exit_time = c3.name
                    pnl = 0

                    for forward_idx in range(i + 3, min(i + 36, len(df_5m))):
                        bar = df_5m.iloc[forward_idx]

                        # Target Hit
                        if bar["Low"] <= target_price:
                            outcome = "TARGET_HIT"
                            exit_price = target_price
                            exit_time = bar.name
                            pnl = max_gain
                            i = forward_idx
                            break

                        # Faster Breakeven: At 1:1.25 move, shift SL to Entry
                        if bar["Low"] <= (entry_price - (risk_pts * 1.25)):
                            current_sl = min(current_sl, entry_price)

                        # SL Check
                        if bar["High"] >= current_sl:
                            outcome = "TRAILING_SL" if current_sl <= entry_price else "STOP_LOSS"
                            exit_price = current_sl
                            exit_time = bar.name
                            pnl = max_gain * 0.20 if current_sl <= entry_price else max_loss
                            i = forward_idx
                            break
                    else:
                        exit_price = df_5m.iloc[min(i + 35, len(df_5m) - 1)]["Close"]
                        pnl = max_gain * 0.35 if exit_price < entry_price else max_loss * 0.3
                        i += 35

                    all_trades.append({
                        "entry_time": c3.name.strftime("%Y-%m-%d %H:%M:%S"),
                        "exit_time": exit_time.strftime("%Y-%m-%d %H:%M:%S"),
                        "date": c3.name.strftime("%Y-%m-%d"),
                        "asset": name,
                        "side": "BEARISH_SWEEP",
                        "spread": f"{atm} PE / {otm} PE",
                        "entry_price": round(entry_price, 2),
                        "exit_price": round(exit_price, 2),
                        "initial_sl": round(initial_sl, 2),
                        "target": round(target_price, 2),
                        "outcome": outcome,
                        "capital_deployed": conf["margin"],
                        "net_pnl": round(pnl, 2),
                        "vol_ratio": vol_str
                    })
                    last_trade_date = trade_date
                    continue

            i += 1

    with open("trades_db.json", "w") as f:
        json.dump(all_trades, f, indent=4)
    print(f"Finished. Total High-Conviction Trades: {len(all_trades)}")

if __name__ == "__main__":
    run_simulation()
