import os
import json
import time as time_module
from datetime import datetime, time, timedelta
import pytz
import pyotp
import pandas as pd
import streamlit as st
from SmartApi import SmartConnect

# ==========================================
# 1. PAGE CONFIG & CREDENTIALS
# ==========================================
st.set_page_config(
    page_title="Liquidity Sweep Options Tracker",
    page_icon="📈",
    layout="wide"
)

IST = pytz.timezone("Asia/Kolkata")
FORCE_EXIT_TIME = time(15, 15)  # Same-day mandatory square-off at 03:15 PM IST
DB_FILE = "trades_db.json"

# Streamlit Secrets or Environment Variables
API_KEY = st.secrets.get("SMARTAPI_KEY", os.environ.get("SMARTAPI_KEY", "")).strip()
CLIENT_CODE = st.secrets.get("CLIENT_CODE", os.environ.get("CLIENT_CODE", "")).strip()
PIN = st.secrets.get("PIN", os.environ.get("PIN", "")).strip()
TOTP_KEY = st.secrets.get("TOTP_KEY", os.environ.get("TOTP_KEY", "")).strip()

ASSETS = {
    "TCS": {"token": "11536", "exchange": "NSE", "lot_size": 175, "strike_step": 20, "spread_width": 40, "cap": 65000},
    "BANKNIFTY": {"token": "99926009", "exchange": "NSE", "lot_size": 15, "strike_step": 100, "spread_width": 200, "cap": 50000},
    "FINNIFTY": {"token": "99926037", "exchange": "NSE", "lot_size": 25, "strike_step": 50, "spread_width": 100, "cap": 45000},
    "SBIN": {"token": "3045", "exchange": "NSE", "lot_size": 750, "strike_step": 10, "spread_width": 20, "cap": 50000},
    "LT": {"token": "11483", "exchange": "NSE", "lot_size": 150, "strike_step": 20, "spread_width": 40, "cap": 60000},
    "AXISBANK": {"token": "5900", "exchange": "NSE", "lot_size": 625, "strike_step": 10, "spread_width": 20, "cap": 55000},
    "NIFTY": {"token": "99926000", "exchange": "NSE", "lot_size": 25, "strike_step": 50, "spread_width": 100, "cap": 45000},
    "HDFCBANK": {"token": "1333", "exchange": "NSE", "lot_size": 550, "strike_step": 10, "spread_width": 20, "cap": 60000}
}


# ==========================================
# 2. SMARTAPI SESSION & DATA FETCHING
# ==========================================
@st.cache_resource(ttl=3600)
def get_smartapi_session():
    if not all([API_KEY, CLIENT_CODE, PIN, TOTP_KEY]):
        return None
    try:
        obj = SmartConnect(api_key=API_KEY)
        totp = pyotp.TOTP(TOTP_KEY).now()
        data = obj.generateSession(CLIENT_CODE, PIN, totp)
        if data.get("status"):
            return obj
    except Exception as e:
        st.error(f"SmartAPI Login Error: {e}")
    return None


def fetch_candles(obj, info: dict, days: int = 45) -> pd.DataFrame:
    to_dt = datetime.now(IST)
    from_dt = to_dt - timedelta(days=days)
    params = {
        "exchange": info["exchange"],
        "symboltoken": info["token"],
        "interval": "FIVE_MINUTE",
        "fromdate": from_dt.strftime("%Y-%m-%d %H:%M"),
        "todate": to_dt.strftime("%Y-%m-%d %H:%M")
    }
    try:
        res = obj.getCandleData(params)
        if not res or not res.get("data"):
            return pd.DataFrame()
        df = pd.DataFrame(res["data"], columns=["Timestamp", "Open", "High", "Low", "Close", "Volume"])
        df["Timestamp"] = pd.to_datetime(df["Timestamp"])
        if df["Timestamp"].dt.tz is None:
            df["Timestamp"] = df["Timestamp"].dt.tz_localize(IST)
        else:
            df["Timestamp"] = df["Timestamp"].dt.tz_convert(IST)
        df.set_index("Timestamp", inplace=True)
        return df
    except Exception:
        return pd.DataFrame()


# ==========================================
# 3. LIVE SCANNER ENGINE (DASHBOARD VIEW)
# ==========================================
def run_live_dashboard_scan(obj):
    rows = []
    progress = st.progress(0)
    total = len(ASSETS)

    for idx, (symbol, info) in enumerate(ASSETS.items()):
        time_module.sleep(0.35)
        df_5m = fetch_candles(obj, info, days=7)
        progress.progress((idx + 1) / total)

        if len(df_5m) < 40:
            rows.append({
                "Asset": symbol,
                "LTP": 0.0,
                "4H High": 0.0,
                "4H Low": 0.0,
                "Signal Status": "⚪ No Data"
            })
            continue

        df_4h = df_5m.resample("4h").agg({
            "Open": "first",
            "High": "max",
            "Low": "min",
            "Close": "last"
        }).dropna()

        prev_4h_high = round(float(df_4h["High"].iloc[-2]), 2)
        prev_4h_low = round(float(df_4h["Low"].iloc[-2]), 2)

        c2 = df_5m.iloc[-3]
        c3 = df_5m.iloc[-2]
        ltp = round(float(df_5m["Close"].iloc[-1]), 2)

        status = "⚪ Monitoring"

        # Step 2: Confirmation (Reversal Breakout)
        if c2["High"] > prev_4h_high and c2["Close"] < prev_4h_high and c3["Close"] < c2["Low"]:
            status = "🔴 SELL CONFIRMED (Bear Put Spread)"
        elif c2["Low"] < prev_4h_low and c2["Close"] > prev_4h_low and c3["Close"] > c2["High"]:
            status = "🟢 BUY CONFIRMED (Bull Call Spread)"
        # Step 1: Alert Mode (Liquidity Sweep)
        elif ltp >= prev_4h_high or c3["High"] >= prev_4h_high:
            status = "🟠 ALERT MODE (4H High Touched)"
        elif ltp <= prev_4h_low or c3["Low"] <= prev_4h_low:
            status = "🟠 ALERT MODE (4H Low Touched)"

        rows.append({
            "Asset": symbol,
            "LTP": ltp,
            "4H High": prev_4h_high,
            "4H Low": prev_4h_low,
            "Signal Status": status
        })

    progress.empty()
    return pd.DataFrame(rows)


# ==========================================
# 4. BACKTEST SIMULATOR (WITH 03:15 PM SAME-DAY EXIT)
# ==========================================
def run_backtest_audit(obj, lookback_days: int = 45) -> pd.DataFrame:
    all_trades = []

    for symbol, info in ASSETS.items():
        time_module.sleep(0.35)
        df_5m = fetch_candles(obj, info, days=lookback_days)
        if len(df_5m) < 100:
            continue

        df_5m["Vol_MA20"] = df_5m["Volume"].rolling(20).mean()
        df_4h = df_5m.resample("4h").agg({
            "High": "max",
            "Low": "min"
        }).dropna()

        # Map previous completed 4H High/Low onto 5m bars
        df_4h["Prev_4H_High"] = df_4h["High"].shift(1)
        df_4h["Prev_4H_Low"] = df_4h["Low"].shift(1)
        df_5m = df_5m.join(df_4h[["Prev_4H_High", "Prev_4H_Low"]], how="left")
        df_5m[["Prev_4H_High", "Prev_4H_Low"]] = df_5m[["Prev_4H_High", "Prev_4H_Low"]].ffill()

        in_trade = False
        last_trade_date = None

        for i in range(25, len(df_5m) - 1):
            if in_trade:
                continue

            c2 = df_5m.iloc[i - 1]
            c3 = df_5m.iloc[i]
            entry_dt = c3.name
            entry_t = entry_dt.time()
            trade_date = entry_dt.strftime("%Y-%m-%d")

            # Do not open new trade at or after 03:15 PM force-exit candle
            if entry_t >= FORCE_EXIT_TIME or last_trade_date == trade_date:
                continue

            h4_high = c2["Prev_4H_High"]
            h4_low = c2["Prev_4H_Low"]
            if pd.isna(h4_high) or pd.isna(h4_low):
                continue

            vol_ratio = (c3["Volume"] / c3["Vol_MA20"]) if c3["Vol_MA20"] > 0 else 1.0
            if vol_ratio < 1.5:
                continue

            side = None
            entry_price = float(c3["Close"])
            initial_sl = 0.0

            # Bearish Sweep
            if c2["High"] > h4_high and c2["Close"] < h4_high and c3["Close"] < c2["Low"]:
                side = "BEARISH_SWEEP"
                initial_sl = round(max(c2["High"], c3["High"]) + (entry_price * 0.0005), 2)
            # Bullish Sweep
            elif c2["Low"] < h4_low and c2["Close"] > h4_low and c3["Close"] > c2["High"]:
                side = "BULLISH_SWEEP"
                initial_sl = round(min(c2["Low"], c3["Low"]) - (entry_price * 0.0005), 2)

            if not side:
                continue

            risk_pts = abs(entry_price - initial_sl)
            if risk_pts <= 0:
                continue

            target = round(entry_price + (1.80 * risk_pts), 2) if side == "BULLISH_SWEEP" else round(entry_price - (1.80 * risk_pts), 2)
            current_sl = initial_sl

            # Simulate Trade Forward (Same-Day Mandatory Closing at 03:15 PM IST)
            exit_price = entry_price
            exit_dt = entry_dt
            outcome = "EOD_CLOSED"

            for j in range(i + 1, len(df_5m)):
                bar = df_5m.iloc[j]
                bar_dt = bar.name
                bar_t = bar_dt.time()
                hours_held = (bar_dt - entry_dt).total_seconds() / 3600.0

                if side == "BULLISH_SWEEP":
                    # Breakeven trailing SL at 1:1.25 move
                    if bar["High"] >= entry_price + (1.25 * risk_pts):
                        current_sl = max(current_sl, entry_price)

                    if bar["Low"] <= current_sl:
                        exit_price = current_sl
                        exit_dt = bar_dt
                        outcome = "TRAILING_SL" if current_sl == entry_price else "STOP_LOSS"
                        break
                    if bar["High"] >= target:
                        exit_price = target
                        exit_dt = bar_dt
                        outcome = "TARGET_HIT"
                        break
                else:
                    if bar["Low"] <= entry_price - (1.25 * risk_pts):
                        current_sl = min(current_sl, entry_price)

                    if bar["High"] >= current_sl:
                        exit_price = current_sl
                        exit_dt = bar_dt
                        outcome = "TRAILING_SL" if current_sl == entry_price else "STOP_LOSS"
                        break
                    if bar["Low"] <= target:
                        exit_price = target
                        exit_dt = bar_dt
                        outcome = "TARGET_HIT"
                        break

                # 3-Hour Max Hold Rule
                if hours_held >= 3.0:
                    exit_price = float(bar["Close"])
                    exit_dt = bar_dt
                    outcome = "EXPIRED"
                    break

                # MANDATORY SAME-DAY CLOSING RULE (03:15 PM IST)
                if bar_t >= FORCE_EXIT_TIME:
                    exit_price = float(bar["Close"])
                    exit_dt = bar_dt
                    outcome = "EOD_CLOSED"
                    break

            # Spread & Estimated Option PnL Modeling
            step = info["strike_step"]
            width = info["spread_width"]
            atm = round(entry_price / step) * step
            spread_str = f"{atm} CE / {atm + width} CE" if side == "BULLISH_SWEEP" else f"{atm} PE / {atm - width} PE"

            spot_move = (exit_price - entry_price) if side == "BULLISH_SWEEP" else (entry_price - exit_price)
            delta_approx = 0.35
            net_pnl = round((spot_move * delta_approx * info["lot_size"]) - 60.0, 2)  # After brokerage/charges

            all_trades.append({
                "entry_time": entry_dt.strftime("%Y-%m-%d %H:%M:%S"),
                "exit_time": exit_dt.strftime("%Y-%m-%d %H:%M:%S"),
                "date": trade_date,
                "asset": symbol,
                "side": side,
                "spread": spread_str,
                "entry_price": round(entry_price, 2),
                "exit_price": round(exit_price, 2),
                "initial_sl": initial_sl,
                "target": target,
                "outcome": outcome,
                "capital_deployed": info["cap"],
                "net_pnl": net_pnl,
                "vol_ratio": f"{vol_ratio:.2f}x (Vol)"
            })
            last_trade_date = trade_date

    if not all_trades:
        return pd.DataFrame()

    df_res = pd.DataFrame(all_trades)
    # Sort chronologically by entry_time so cumulative PnL is accurate
    df_res.sort_values("entry_time", inplace=True)
    df_res.reset_index(drop=True, inplace=True)
    df_res["cum_pnl"] = df_res["net_pnl"].cumsum().round(2)
    df_res["trade_num"] = df_res.index + 1
    return df_res


# ==========================================
# 5. STREAMLIT UI LAYOUT
# ==========================================
st.title("📊 Hybrid Liquidity Sweep Options Dashboard")
st.caption("Checks for: 1. Alert Mode (Liquidity Sweep) ➔ 2. Confirmation (Reversal Breakout) | Auto Same-Day Closing: 03:15 PM IST")

obj = get_smartapi_session()

tab1, tab2, tab3 = st.tabs(["🔍 Live Market Scanner", "🧪 Backtest & Audit Report", "📁 Live Paper Trades (trades_db.json)"])

# --- TAB 1: LIVE SCANNER ---
with tab1:
    if st.button(f"🔍 Scan {len(ASSETS)} Assets Now", use_container_width=True):
        if obj is None:
            st.error("SmartAPI credentials configure nahi hain. Streamlit Secrets check karein.")
        else:
            df_scan = run_live_dashboard_scan(obj)
            st.dataframe(df_scan, use_container_width=True, hide_index=True)

# --- TAB 2: BACKTEST AUDIT ---
with tab2:
    lookback = st.slider("Historical Lookback Window (Days)", min_value=15, max_value=60, value=45)
    if st.button("⚙️ Run Verified Strategy Backtest"):
        if obj is None:
            st.error("SmartAPI session active nahi hai.")
        else:
            with st.spinner("Historical candles scan ho rahi hain (with 03:15 PM Same-Day Closing)..."):
                df_audit = run_backtest_audit(obj, lookback_days=lookback)
                if df_audit.empty:
                    st.warning("Selected window mein koi rule-qualified trade nahi mili.")
                else:
                    wins = len(df_audit[df_audit["net_pnl"] > 0])
                    total_t = len(df_audit)
                    win_rate = (wins / total_t) * 100
                    total_pnl = df_audit["net_pnl"].sum()

                    c1, c2, c3 = st.columns(3)
                    c1.metric("Total Verified Trades", total_t)
                    c2.metric("Win Rate", f"{win_rate:.1f}%")
                    c3.metric("Net Cumulative P&L", f"₹{total_pnl:,.2f}")

                    st.dataframe(df_audit, use_container_width=True, hide_index=True)

                    csv_data = df_audit.to_csv(index=False).encode("utf-8")
                    st.download_button(
                        label="📥 Download verified_trades_audit.csv",
                        data=csv_data,
                        file_name="verified_trades_audit.csv",
                        mime="text/csv"
                    )

# --- TAB 3: LIVE PAPER TRADES DB ---
with tab3:
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r") as f:
            live_data = json.load(f)
        if live_data:
            df_live = pd.DataFrame(live_data)
            st.dataframe(df_live, use_container_width=True, hide_index=True)
        else:
            st.info("Abhi tak trades_db.json mein koi live paper trade record nahi hui hai.")
    else:
        st.info("trades_db.json file abhi create nahi hui hai.")
