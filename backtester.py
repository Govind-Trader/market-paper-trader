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
    print("🚀 Running 4-Hour Liquidity Engine...")
    smart_api = get_angel_smart_api()
    all_trades = []
    ist = pytz.timezone("Asia/Kolkata")
    
    for name, conf in ASSETS.items():
        df_5m = pd.DataFrame()
        
        # 1. Fetch via SmartAPI for indices and mapped equities
        if smart_api and conf["token"]:
            df_5m = fetch_angel_index_data(smart_api, conf["token"], name)
            
        # 2. Fallback to Yahoo Finance if API data not available
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

        # 4-HOUR RESAMPLING LOGIC (Multi-Timeframe Setup)
        df_4h = df_5m.resample("4h").agg({
            "Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"
        }).dropna()

        # Rolling 6-period 4H Low (Pichhle 24 ghante ka major institutional swing low)
        df_4h["HTF_Low"] = df_4h["Low"].shift(1).rolling(window=6).min()
        df_5m["HTF_Low"] = df_4h["HTF_Low"].reindex(df_5m.index, method="ffill")

        df_5m["Vol_MA20"] = df_5m["Volume"].rolling(window=20).mean()
        df_5m["Range"] = df_5m["High"] - df_5m["Low"]
        df_5m["Range_MA20"] = df_5m["Range"].rolling(window=20).mean()

        i = 40
        while i < len(df_5m) - 5:
            curr = df_5m.iloc[i]
            c_time = curr.name.time()

            # Trade time window filter
            if conf["market"] in ["NSE", "NSE_INDEX"]:
                if not (time(9, 20) <= c_time <= time(15, 10)):
                    i += 1
                    continue
            elif conf["market"] == "MCX":
                if not (time(9, 15) <= c_time <= time(23, 15)):
                    i += 1
                    continue

            htf_liquidity_low = curr["HTF_Low"]
            if pd.isna(htf_liquidity_low):
                i += 1
                continue

            c_open, c_high, c_low, c_close, c_vol = curr["Open"], curr["High"], curr["Low"], curr["Close"], curr["Volume"]
            avg_vol = curr["Vol_MA20"]
            avg_range = curr["Range_MA20"]

            # 4-HOUR LIQUIDITY SWEEP RULE
            is_htf_sweep = (c_low < htf_liquidity_low) and (c_close > htf_liquidity_low)
            is_green = c_close > c_open

            # Volume / Range Absorption Check
            if conf["market"] == "NSE_INDEX":
                if c_vol > 0 and avg_vol > 0:
                    has_absorption = c_vol >= (avg_vol * 1.5)
                    vol_metric_str = f"{round(c_vol / avg_vol, 2)}x (Vol)"
                else:
                    has_absorption = (c_high - c_low) >= (avg_range * 1.25) if avg_range > 0 else False
                    vol_metric_str = f"{round((c_high - c_low) / avg_range, 2)}x (Rng)"
            else:
                req_mult = 2.2 if (name == "CRUDEOIL" and time(18, 0) <= c_time <= time(21, 30)) else 1.8
                has_absorption = c_vol >= (avg_vol * req_mult) if avg_vol > 0 else False
                vol_metric_str = f"{round(c_vol / avg_vol, 2)}x (Vol)"

            if is_htf_sweep and is_green and has_absorption:
                entry_price = c_close
                sl_price = c_low
                risk_pts = max(entry_price - sl_price, entry_price * 0.0015)
                target_pts = risk_pts * 2
                target_price = entry_price + target_pts

                atm = int(round(entry_price / conf["strike_step"]) * conf["strike_step"])
                otm = atm + conf["spread_width"]

                outcome = "EXPIRED"
                exit_price = entry_price
                exit_time = curr.name

                max_gain = conf["spread_width"] * 0.60 * conf["lot_size"]
                max_loss = -(conf["spread_width"] * 0.35 * conf["lot_size"])

                # Forward candle simulation (Max 36 bars = 3 hours hold)
                for forward_idx in range(i + 1, min(i + 36, len(df_5m))):
                    future_candle = df_5m.iloc[forward_idx]
                    if future_candle["Low"] <= sl_price:
                        outcome = "STOP_LOSS"
                        exit_price = sl_price
                        exit_time = future_candle.name
                        pnl = max_loss
                        i = forward_idx
                        break
                    elif future_candle["High"] >= target_price:
                        outcome = "TARGET_HIT"
                        exit_price = target_price
                        exit_time = future_candle.name
                        pnl = max_gain
                        i = forward_idx
                        break
                else:
                    exit_price = df_5m.iloc[min(i + 35, len(df_5m) - 1)]["Close"]
                    pnl = max_gain * 0.3 if exit_price > entry_price else max_loss * 0.4
                    i += 35

                all_trades.append({
                    "entry_time": curr.name.strftime("%Y-%m-%d %H:%M:%S"),
                    "exit_time": exit_time.strftime("%Y-%m-%d %H:%M:%S"),
                    "date": curr.name.strftime("%Y-%m-%d"),
                    "asset": name,
                    "market": conf["market"],
                    "spread": f"{atm} CE / {otm} CE",
                    "entry_price": round(entry_price, 2),
                    "exit_price": round(exit_price, 2),
                    "stop_loss": round(sl_price, 2),
                    "target": round(target_price, 2),
                    "outcome": outcome,
                    "capital_deployed": conf["margin"],
                    "net_pnl": round(pnl, 2),
                    "vol_ratio": vol_metric_str,
                })
            i += 1

    with open("trades_db.json", "w") as f:
        json.dump(all_trades, f, indent=4)
    print(f"Finished. Total Trades: {len(all_trades)}")

if __name__ == "__main__":
    run_simulation()
