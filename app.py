import os
import json
import time as time_module
from datetime import datetime, time, timedelta
import pytz
import pyotp
import pandas as pd
import streamlit as st
import plotly.graph_objects as go
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

# Streamlit Secrets Exact Mapping
API_KEY = str(st.secrets.get("SMARTAPI_KEY", os.environ.get("SMARTAPI_KEY", ""))).strip()
CLIENT_CODE = str(st.secrets.get("SMARTAPI_CLIENT_CODE", os.environ.get("CLIENT_CODE", ""))).strip()
PIN = str(st.secrets.get("SMARTAPI_PIN", os.environ.get("PIN", ""))).strip()
TOTP_KEY = str(st.secrets.get("SMARTAPI_TOTP_KEY", os.environ.get("TOTP_KEY", ""))).strip()

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
# 2. SMARTAPI SESSION & DATA FETCHING (WITH RETRY)
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


@st.cache_data(ttl=180, show_spinner=False)
def fetch_candles(_obj, symbol: str, days: int = 45) -> pd.DataFrame:
    info = ASSETS[symbol]
    to_dt = datetime.now(IST)
    from_dt = to_dt - timedelta(days=days)
    params = {
        "exchange": info["exchange"],
        "symboltoken": info["token"],
        "interval": "FIVE_MINUTE",
        "fromdate": from_dt.strftime("%Y-%m-%d %H:%M"),
        "todate": to_dt.strftime("%Y-%m-%d %H:%M")
    }

    # Auto-retry up to 3 times if Angel One rate-limits the call
    for attempt in range(3):
        try:
            time_module.sleep(0.55 + (attempt * 0.5))
            res = _obj.getCandleData(params)
            if res and res.get("data"):
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


# ==========================================
# 3. LIVE SCANNER ENGINE (FIXED ALERT LOGIC)
# ==========================================
def run_live_dashboard_scan(obj):
    rows = []
    progress = st.progress(0)
    total = len(ASSETS)

    for idx, symbol in enumerate(ASSETS.keys()):
        df_5m = fetch_candles(obj, symbol, days=7)
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

        # Build clean 4H levels from completed 4H blocks
        df_4h = df_5m.resample("4h", origin="start_day").agg({
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

        # Step 2: Reversal Confirmation (Strict Sweep + Rejection + Breakout)
        if c2["High"] > prev_4h_high and c2["Close"] < prev_4h_high and c3["Close"] < c2["Low"] and ltp < prev_4h_high:
            status = "🔴 SELL CONFIRMED (Bear Put Spread)"
        elif c2["Low"] < prev_4h_low and c2["Close"] > prev_4h_low and c3["Close"] > c2["High"] and ltp > prev_4h_low:
            status = "🟢 BUY CONFIRMED (Bull Call Spread)"
        # Step 1: Alert Mode (Only when current LTP actually pierces 4H High/Low)
        elif ltp >= prev_4h_high:
            status = "🟠 ALERT MODE (4H High Swept)"
        elif ltp <= prev_4h_low:
            status = "🟠 ALERT MODE (4H Low Swept)"

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
        df_5m = fetch_candles(obj, symbol, days=lookback_days)
        if len(df_5m) < 100:
            continue

        df_5m["Vol_MA20"] = df_5m["Volume"].rolling(20).mean()
        df_4h = df_5m.resample("4h", origin="start_day").agg({
            "High": "max",
            "Low": "min"
        }).dropna()

        df_4h["Prev_4H_High"] = df_4h["High"].shift(1)
        df_4h["Prev_4H_Low"] = df_4h["Low"].shift(1)
        df_5m = df_5m.join(df_4h[["Prev_4H_High", "Prev_4H_Low"]], how="left")
        df_5m[["Prev_4H_High", "Prev_4H_Low"]] = df_5m[["Prev_4H_High", "Prev_4H_Low"]].ffill()

        last_trade_date = None

        for i in range(25, len(df_5m) - 1):
            c2 = df_5m.iloc[i - 1]
            c3 = df_5m.iloc[i]
            entry_dt = c3.name
            entry_t = entry_dt.time()
            trade_date = entry_dt.strftime("%Y-%m-%d")

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

            if c2["High"] > h4_high and c2["Close"] < h4_high and c3["Close"] < c2["Low"]:
                side = "BEARISH_SWEEP"
                initial_sl = round(max(c2["High"], c3["High"]) + (entry_price * 0.0005), 2)
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

            exit_price = entry_price
            exit_dt = entry_dt
            outcome = "EOD_CLOSED"

            for j in range(i + 1, len(df_5m)):
                bar = df_5m.iloc[j]
                bar_dt = bar.name
                bar_t = bar_dt.time()
                hours_held = (bar_dt - entry_dt).total_seconds() / 3600.0

                if side == "BULLISH_SWEEP":
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

            step = info["strike_step"]
            width = info["spread_width"]
            atm = round(entry_price / step) * step
            spread_str = f"{atm} CE / {atm + width} CE" if side == "BULLISH_SWEEP" else f"{atm} PE / {atm - width} PE"

            spot_move = (exit_price - entry_price) if side == "BULLISH_SWEEP" else (entry_price - exit_price)
            net_pnl = round((spot_move * 0.35 * info["lot_size"]) - 60.0, 2)

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

tab1, tab2, tab3 = st.tabs(["🔍 Live Market Scanner & Charts", "🧪 Backtest & Audit Report", "📁 Live Paper Trades (trades_db.json)"])

# --- TAB 1: LIVE SCANNER & INTERACTIVE CANDLESTICK CHARTS ---
with tab1:
    if st.button(f"🔍 Scan {len(ASSETS)} Assets Now", use_container_width=True):
        if obj is None:
            st.error("SmartAPI credentials configure nahi hain. Streamlit Secrets check karein.")
        else:
            df_scan = run_live_dashboard_scan(obj)
            st.dataframe(df_scan, use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("🕯️ Live Interactive Candlestick Chart (with 4H Liquidity Levels)")

    selected_asset = st.selectbox(
        "Chart dekhne ke liye Stock / Index select karein:",
        options=list(ASSETS.keys()),
        index=0
    )

    if obj is not None and selected_asset:
        df_chart = fetch_candles(obj, selected_asset, days=5)
        if not df_chart.empty and len(df_chart) >= 30:
            df_4h = df_chart.resample("4h", origin="start_day").agg({
                "Open": "first",
                "High": "max",
                "Low": "min",
                "Close": "last"
            }).dropna()

            prev_4h_high = float(df_4h["High"].iloc[-2])
            prev_4h_low = float(df_4h["Low"].iloc[-2])
            current_ltp = float(df_chart["Close"].iloc[-1])

            df_plot = df_chart.tail(85)

            fig = go.Figure(data=[go.Candlestick(
                x=df_plot.index,
                open=df_plot["Open"],
                high=df_plot["High"],
                low=df_plot["Low"],
                close=df_plot["Close"],
                name=selected_asset,
                increasing_line_color="#00d26a",
                decreasing_line_color="#ff4b4b"
            )])

            fig.add_hline(
                y=prev_4h_high,
                line_dash="dash",
                line_color="#ff9f1c",
                line_width=1.8,
                annotation_text=f"Prev 4H High: {prev_4h_high:.2f}",
                annotation_position="top left"
            )

            fig.add_hline(
                y=prev_4h_low,
                line_dash="dash",
                line_color="#00b4d8",
                line_width=1.8,
                annotation_text=f"Prev 4H Low: {prev_4h_low:.2f}",
                annotation_position="bottom left"
            )

            fig.update_layout(
                title=f"{selected_asset} — 5-Min Candlestick Chart | LTP: ₹{current_ltp:,.2f}",
                template="plotly_dark",
                xaxis_rangeslider_visible=False,
                height=520,
                margin=dict(l=20, r=20, t=50, b=20),
                yaxis_title="Price (₹)"
            )

            st.plotly_chart(fig, use_container_width=True)
        else:
            st.warning(f"{selected_asset} ke liye candle data abhi fetch nahi ho paya. Thodi der baad dubara try karein.")

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

                    st.subheader("📈 Cumulative Equity Curve & Asset Performance")
                    col_c1, col_c2 = st.columns(2)
                    with col_c1:
                        st.caption("Cumulative P&L Growth (₹)")
                        st.line_chart(df_audit.set_index("entry_time")["cum_pnl"], color="#00d26a")
                    with col_c2:
                        st.caption("Asset-Wise Net P&L (₹)")
                        st.bar_chart(df_audit.groupby("asset")["net_pnl"].sum())

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
