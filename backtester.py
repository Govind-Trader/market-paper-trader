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

def fetch_angel_index_data(smart_api, token, symbol_name):
    ist = pytz.timezone("Asia/Kolkata")
    now = datetime.now(ist)
    from_date = (now - timedelta(days=58)).strftime("%Y-%m-%d 09:15")
    to_date = now.strftime("%Y-%m-%d 15:30")
    
    historic_param = {
        "exchange": "NSE",
        "symboltoken": str(token),
        "interval": "FIVE_MINUTE",
        "fromdate": from_date,
        "todate": to_date
    }
    try:
        res = smart_api.getCandleData(historic_param)
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

ASSETS = {
    "NIFTY": {"token": "99926000", "symbol": "^NSEI", "market": "NSE_INDEX", "lot_size": 25, "strike_step": 50, "spread_width": 100, "margin": 45000},
    "BANKNIFTY": {"token": "99926009", "symbol": "^NSEBANK", "market": "NSE_INDEX", "lot_size": 15, "strike_step": 100, "spread_width": 200, "margin": 50000},
    "FINNIFTY": {"token": "99926037", "symbol": "NIFTY_FIN_SERVICE.NS", "market": "NSE_INDEX", "lot_size": 25, "strike_step": 50, "spread_width": 100, "margin": 45000},
    "CRUDEOIL": {"token": None, "symbol": "CL=F", "market": "MCX", "lot_size": 100, "strike_step": 50, "spread_width": 100, "margin": 55000},
    "RELIANCE": {"token": "2885", "symbol": "RELIANCE.NS", "market": "NSE", "lot_size": 250, "strike_step": 20, "spread_width": 40, "margin": 65000},
    "HDFCBANK": {"token": "1333", "symbol": "HDFCBANK.NS", "market": "NSE", "lot_size": 550, "strike_step": 10, "spread_width": 20, "margin": 60000},
    "ICICIBANK": {"token": "4963", "symbol": "ICICIBANK.NS", "market": "NSE", "lot_size": 700, "strike_step": 10, "spread_width": 20, "margin": 55000},
    "SBIN": {"token": "3045", "symbol": "SBIN.NS", "market": "NSE", "lot_size": 750, "strike_step": 5, "spread_width": 10, "margin": 50000},
    "INFY": {"token": "1594", "symbol": "INFY.NS", "market": "NSE", "lot_size": 400, "strike_step": 20, "spread_width": 40, "margin": 55000},
    "TCS": {"token": "11536", "symbol": "TCS.NS", "market": "NSE", "lot_size": 175, "strike_step": 50, "spread_width": 100, "margin": 65000},
    "LT": {"token": "11483", "symbol": "LT.NS", "market": "NSE", "lot_size": 150, "strike_step": 20, "spread_width": 40, "margin": 60000},
    "AXISBANK": {"token": "5900", "symbol": "AXISBANK.NS", "market": "NSE", "lot_size": 625, "strike_step": 10, "spread_width": 20, "margin": 55000},
    "BHARTIARTL": {"token": "10604", "symbol": "BHARTIARTL.NS", "market": "NSE", "lot_size": 475, "strike_step": 10, "spread_width": 20, "margin": 50000},
    "TATASTEEL": {"token": "3499", "symbol": "TATASTEEL.NS", "market": "NSE", "lot_size": 5500, "strike_step": 2.5, "spread_width": 2.5, "margin": 50000}
}

def run_simulation():
    print("🚀 Running 2-Candle Confirmation & Trailing Stop Engine...")
    smart_api = get_angel_smart_api()
    all_trades = []
    ist = pytz.timezone("Asia/Kolkata")
    
    for name, conf in ASSETS.items():
        df_5m = pd.DataFrame()
        
        if smart_api and conf["token"]:
            df_5m = fetch_angel_index_data(smart_api, conf["token"], name)
            
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

        # 4-HOUR RESAMPLING LOGIC (Both High and Low)
        df_4h = df_5m.resample("4h").agg({
            "Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"
        }).dropna()

        # Rolling 6-period 4H Low & High (Pichhle 24 ghante ka major swing)
        df_4h["HTF_Low"] = df_4h["Low"].shift(1).rolling(window=6).min()
        df_4h["HTF_High"] = df_4h["High"].shift(1).rolling(window=6).max()

        df_5m["HTF_Low"] = df_4h["HTF_Low"].reindex(df_5m.index, method="ffill")
        df_5m["HTF_High"] = df_4h["HTF_High"].reindex(df_5m.index, method="ffill")
        df_5m["Vol_MA20"] = df_5m["Volume"].rolling(window=20).mean()

        i = 40
        while i < len(df_5m) - 6:
            c1 = df_5m.iloc[i]     # Candle 1 (Alert Candle)
            c2 = df_5m.iloc[i + 1] # Candle 2 (Confirmation Candle)
            c_time = c1.name.time()

            # Market Timing Check
            if conf["market"] in ["NSE", "NSE_INDEX"]:
                if not (time(9, 20) <= c_time <= time(15, 0)):
                    i += 1
                    continue
            elif conf["market"] == "MCX":
                if not (time(9, 15) <= c_time <= time(23, 0)):
                    i += 1
                    continue

            htf_low = c1["HTF_Low"]
            htf_high = c1["HTF_High"]
            if pd.isna(htf_low) or pd.isna(htf_high):
                i += 1
                continue

            # ==========================================
            # SETUP 1: BULLISH SWEEP REVERSAL (CALL SPREAD)
            # ==========================================
            # 1. Alert Mode: Candle 1 sweeps/touches 4H Low
            is_c1_sweep_low = c1["Low"] <= htf_low
            
            # 2. Trigger Mode: Candle 2 reverses and breaks Candle 1 High
            is_c2_reversal_up = (c2["Close"] > c2["Open"]) and (c2["High"] > c1["High"])

            if is_c1_sweep_low and is_c2_reversal_up:
                entry_price = max(c2["Open"], c1["High"])
                sl_buffer = entry_price * 0.0010
                initial_sl = min(c1["Low"], c2["Low"]) - sl_buffer
                risk_pts = entry_price - initial_sl

                if risk_pts > 0:
                    target_price = entry_price + (risk_pts * 2.5)
                    atm = int(round(entry_price / conf["strike_step"]) * conf["strike_step"])
                    otm = atm + conf["spread_width"]

                    max_gain = conf["spread_width"] * 0.60 * conf["lot_size"]
                    max_loss = -(conf["spread_width"] * 0.35 * conf["lot_size"])

                    # Trailing SL Loop
                    current_sl = initial_sl
                    outcome = "EXPIRED"
                    exit_price = entry_price
                    exit_time = c2.name
                    pnl = 0

                    for forward_idx in range(i + 2, min(i + 36, len(df_5m))):
                        bar = df_5m.iloc[forward_idx]
                        
                        # Target Hit Check
                        if bar["High"] >= target_price:
                            outcome = "TARGET_HIT"
                            exit_price = target_price
                            exit_time = bar.name
                            pnl = max_gain
                            i = forward_idx
                            break

                        # Trailing Rule: 1:1 Move par Cost (Breakeven) par shift
                        if bar["High"] >= (entry_price + risk_pts):
                            current_sl = max(current_sl, entry_price)

                        # New Higher Lows trail karna
                        if bar["Close"] > entry_price:
                            current_sl = max(current_sl, bar["Low"] - sl_buffer)

                        # SL Hit Check
                        if bar["Low"] <= current_sl:
                            outcome = "TRAILING_SL" if current_sl >= entry_price else "STOP_LOSS"
                            exit_price = current_sl
                            exit_time = bar.name
                            pnl = max_gain * 0.15 if current_sl >= entry_price else max_loss
                            i = forward_idx
                            break
                    else:
                        exit_price = df_5m.iloc[min(i + 35, len(df_5m) - 1)]["Close"]
                        pnl = max_gain * 0.3 if exit_price > entry_price else max_loss * 0.4
                        i += 35

                    all_trades.append({
                        "entry_time": c2.name.strftime("%Y-%m-%d %H:%M:%S"),
                        "exit_time": exit_time.strftime("%Y-%m-%d %H:%M:%S"),
                        "date": c2.name.strftime("%Y-%m-%d"),
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
                        "vol_ratio": "2-Candle Confirmed"
                    })
                    continue

            # ==========================================
            # SETUP 2: BEARISH SWEEP REVERSAL (PUT SPREAD)
            # ==========================================
            # 1. Alert Mode: Candle 1 sweeps/touches 4H High
            is_c1_sweep_high = c1["High"] >= htf_high

            # 2. Trigger Mode: Candle 2 reverses down and breaks Candle 1 Low
            is_c2_reversal_down = (c2["Close"] < c2["Open"]) and (c2["Low"] < c1["Low"])

            if is_c1_sweep_high and is_c2_reversal_down:
                entry_price = min(c2["Open"], c1["Low"])
                sl_buffer = entry_price * 0.0010
                initial_sl = max(c1["High"], c2["High"]) + sl_buffer
                risk_pts = initial_sl - entry_price

                if risk_pts > 0:
                    target_price = entry_price - (risk_pts * 2.5)
                    atm = int(round(entry_price / conf["strike_step"]) * conf["strike_step"])
                    otm = atm - conf["spread_width"]

                    max_gain = conf["spread_width"] * 0.60 * conf["lot_size"]
                    max_loss = -(conf["spread_width"] * 0.35 * conf["lot_size"])

                    current_sl = initial_sl
                    outcome = "EXPIRED"
                    exit_price = entry_price
                    exit_time = c2.name
                    pnl = 0

                    for forward_idx in range(i + 2, min(i + 36, len(df_5m))):
                        bar = df_5m.iloc[forward_idx]

                        # Target Hit
                        if bar["Low"] <= target_price:
                            outcome = "TARGET_HIT"
                            exit_price = target_price
                            exit_time = bar.name
                            pnl = max_gain
                            i = forward_idx
                            break

                        # Trailing to Breakeven
                        if bar["Low"] <= (entry_price - risk_pts):
                            current_sl = min(current_sl, entry_price)

                        # Trail with lower highs
                        if bar["Close"] < entry_price:
                            current_sl = min(current_sl, bar["High"] + sl_buffer)

                        # SL Hit
                        if bar["High"] >= current_sl:
                            outcome = "TRAILING_SL" if current_sl <= entry_price else "STOP_LOSS"
                            exit_price = current_sl
                            exit_time = bar.name
                            pnl = max_gain * 0.15 if current_sl <= entry_price else max_loss
                            i = forward_idx
                            break
                    else:
                        exit_price = df_5m.iloc[min(i + 35, len(df_5m) - 1)]["Close"]
                        pnl = max_gain * 0.3 if exit_price < entry_price else max_loss * 0.4
                        i += 35

                    all_trades.append({
                        "entry_time": c2.name.strftime("%Y-%m-%d %H:%M:%S"),
                        "exit_time": exit_time.strftime("%Y-%m-%d %H:%M:%S"),
                        "date": c2.name.strftime("%Y-%m-%d"),
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
                        "vol_ratio": "2-Candle Confirmed"
                    })
                    continue

            i += 1

    with open("trades_db.json", "w") as f:
        json.dump(all_trades, f, indent=4)
    print(f"Finished. Total Trades: {len(all_trades)}")

if __name__ == "__main__":
    run_simulation()
