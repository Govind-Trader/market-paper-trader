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
    page_title="Hybrid Liquidity Sweep Options Dashboard",
    page_icon="📊",
    layout="wide"
)

IST = pytz.timezone("Asia/Kolkata")
ENTRY_START = time(9, 20)
ENTRY_CUTOFF = time(14, 15)     # 02:15 PM ke baad no new low-time entries
FORCE_EXIT_TIME = time(15, 15)  # 03:15 PM mandatory same-day square-off
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
# 2. SMARTAPI SESSION & RATE-LIMIT SAFE DATA
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
# 3. STRATEGY ENGINE (HIGH-ACCURACY + INDICES + 03:15 PM EXIT)
# ==========================================
def run_full_scan_and_backtest(obj, lookback_days: int = 45):
    scanner_rows = []
    all_trades = []
    progress = st.progress(0)
    total = len(ASSETS)

    for idx, (symbol, info) in enumerate(ASSETS.items()):
        df_5m = fetch_candles(obj, symbol, days=lookback_days)
        progress.progress((idx + 1) / total)

        if len(df_5m) < 60:
            scanner_rows.append({
                "Asset": symbol,
                "LTP": 0.0,
                "4H High": 0.0,
                "4H Low": 0.0,
                "Signal Status": "⚪ No Data"
            })
            continue

        # Authentic 4H Resample
        df_4h = df_5m.resample("4h").agg({
            "Open": "first",
            "High": "max",
            "Low": "min",
            "Close": "last"
        }).dropna()

        prev_4h_high = round(float(df_4h["High"].iloc[-2]), 2)
        prev_4h_low = round(float(df_4h["Low"].iloc[-2]), 2)

        c2_live = df_5m.iloc[-3]
        c3_live = df_5m.iloc[-2]
        ltp = round(float(df_5m["Close"].iloc[-1]), 2)

        status = "⚪ Monitoring"
        if c2_live["High"] > prev_4h_high and c2_live["Close"] < prev_4h_high and c3_live["Close"] < c2_live["Low"] and ltp < prev_4h_high:
            status = "🔴 SELL CONFIRMED (Bear Put Spread)"
        elif c2_live["Low"] < prev_4h_low and c2_live["Close"] > prev_4h_low and c3_live["Close"] > c2_live["High"] and ltp > prev_4h_low:
            status = "🟢 BUY CONFIRMED (Bull Call Spread)"
        elif ltp >= prev_4h_high:
            status = "🟠 ALERT MODE (4H High Swept)"
        elif ltp <= prev_4h_low:
            status = "🟠 ALERT MODE (4H Low Swept)"

        scanner_rows.append({
            "Asset": symbol,
            "LTP": ltp,
            "4H High": prev_4h_high,
            "4H Low": prev_4h_low,
            "Signal Status": status
        })

        # Volume & Range Expansion Metrics (Fixes Index Volume = 0 issue)
        df_5m["Vol_MA20"] = df_5m["Volume"].rolling(20).mean()
        df_5m["Range"] = df_5m["High"] - df_5m["Low"]
        df_5m["Range_MA20"] = df_5m["Range"].rolling(20).mean()

        df_4h["Prev_4H_High"] = df_4h["High"].shift(1)
        df_4h["Prev_4H_Low"] = df_4h["Low"].shift(1)
        df_bt = df_5m.join(df_4h[["Prev_4H_High", "Prev_4H_Low"]], how="left")
        df_bt[["Prev_4H_High", "Prev_4H_Low"]] = df_bt[["Prev_4H_High", "Prev_4H_Low"]].ffill()

        is_index = symbol in {"NIFTY", "BANKNIFTY", "FINNIFTY"}
        last_trade_date = None

        for i in range(25, len(df_bt) - 1):
            c2 = df_bt.iloc[i - 1]
            c3 = df_bt.iloc[i]
            entry_dt = c3.name
            entry_t = entry_dt.time()
            trade_date = entry_dt.strftime("%Y-%m-%d")

            if not (ENTRY_START <= entry_t <= ENTRY_CUTOFF) or last_trade_date == trade_date:
                continue

            h4_high = c2["Prev_4H_High"]
            h4_low = c2["Prev_4H_Low"]
            if pd.isna(h4_high) or pd.isna(h4_low):
                continue

            # Momentum / Volume Confirmation (Range expansion for Indices, Volume spike for Stocks)
            if is_index:
                rng_ratio = (c3["Range"] / c3["Range_MA20"]) if c3["Range_MA20"] > 0 else 0.0
                if rng_ratio < 1.40:
                    continue
                conf_label = f"{rng_ratio:.2f}x (Rng)"
            else:
                vol_ratio = (c3["Volume"] / c3["Vol_MA20"]) if c3["Vol_MA20"] > 0 else 0.0
                if vol_ratio < 1.50:
                    continue
                conf_label = f"{vol_ratio:.2f}x (Vol)"

            side = None
            entry_price = float(c3["Close"])
            initial_sl = 0.0

            # Bearish Sweep + Rejection + Breakout Confirmation
            if c2["High"] > h4_high and c2["Close"] < h4_high and c3["Close"] < c2["Low"]:
                side = "BEARISH_SWEEP"
                initial_sl = round(max(c2["High"], c3["High"]) + (entry_price * 0.0005), 2)
            # Bullish Sweep + Rejection + Breakout Confirmation
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

            for j in range(i + 1, len(df_bt)):
                bar = df_bt.iloc[j]
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

                # 3-Hour Max Hold Duration
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
                "vol_ratio": conf_label
            })
            last_trade_date = trade_date

    progress.empty()
    df_scanner = pd.DataFrame(scanner_rows)
    df_trades = pd.DataFrame(all_trades)

    if not df_trades.empty:
        df_trades.sort_values("entry_time", inplace=True)
        df_trades.reset_index(drop=True, inplace=True)
        df_trades["cum_pnl"] = df_trades["net_pnl"].cumsum().round(2)
        df_trades["trade_num"] = df_trades.index + 1

    return df_scanner, df_trades


# ==========================================
# 4. SINGLE-PAGE CLEAN DASHBOARD UI
# ==========================================
st.title("📊 Hybrid Liquidity Sweep Options Dashboard")
st.caption("Checks for: 1. Alert Mode (Liquidity Sweep) ➔ 2. Confirmation (Reversal Breakout) | Auto Same-Day Closing: 03:15 PM IST")

obj = get_smartapi_session()

if obj is None:
    st.error("SmartAPI credentials configure nahi hain. Streamlit Secrets check karein.")
    st.stop()

col_btn, col_days = st.columns([3, 1])
with col_days:
    lookback_days = st.number_input("Backtest Days", min_value=15, max_value=60, value=45, step=5)
with col_btn:
    st.write("")
    run_clicked = st.button(f"🔍 Scan {len(ASSETS)} Assets & Update Accuracy / Trades", use_container_width=True)

if run_clicked or "df_scanner" not in st.session_state:
    df_scanner, df_trades = run_full_scan_and_backtest(obj, lookback_days=lookback_days)
    st.session_state["df_scanner"] = df_scanner
    st.session_state["df_trades"] = df_trades
else:
    df_scanner = st.session_state["df_scanner"]
    df_trades = st.session_state["df_trades"]

# --- SECTION 1: LIVE SCANNER TABLE ---
st.subheader("📡 Live Market Scanner")
st.dataframe(df_scanner, use_container_width=True, hide_index=True)

# --- SECTION 2: LIVE CHART (IST WITH NO HOLIDAY / OVERNIGHT GAPS) ---
st.divider()
st.subheader("🕯️ Live Chart (IST — Continuous Without Holiday/Overnight Gaps)")

selected_asset = st.selectbox("Select Asset for Live IST Chart:", options=list(ASSETS.keys()), index=0)
df_chart = fetch_candles(obj, selected_asset, days=7)

if not df_chart.empty and len(df_chart) >= 30:
    df_4h = df_chart.resample("4h").agg({
        "Open": "first",
        "High": "max",
        "Low": "min",
        "Close": "last"
    }).dropna()

    prev_4h_high = float(df_4h["High"].iloc[-2])
    prev_4h_low = float(df_4h["Low"].iloc[-2])
    current_ltp = float(df_chart["Close"].iloc[-1])

    df_plot = df_chart.tail(100).copy()
    ist_labels = df_plot.index.strftime("%d %b %H:%M")

    fig = go.Figure(data=[go.Candlestick(
        x=ist_labels,
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
        title=f"{selected_asset} — 5-Min IST Chart (No Holiday Gaps) | LTP: ₹{current_ltp:,.2f}",
        template="plotly_dark",
        xaxis_type="category",
        xaxis_rangeslider_visible=False,
        xaxis=dict(nticks=12),
        height=500,
        margin=dict(l=20, r=20, t=50, b=20),
        yaxis_title="Spot Price (₹)"
    )

    st.plotly_chart(fig, use_container_width=True)

# --- SECTION 3: ACCURACY PERCENTAGE & ASSET-WISE PERFORMANCE ---
st.divider()
st.subheader("🎯 Strategy Accuracy & Asset-Wise Performance")

if not df_trades.empty:
    total_trades = len(df_trades)
    wins = len(df_trades[df_trades["net_pnl"] > 0])
    losses = len(df_trades[df_trades["net_pnl"] <= 0])
    accuracy_pct = (wins / total_trades) * 100
    net_pnl_total = df_trades["net_pnl"].sum()

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Overall Accuracy %", f"{accuracy_pct:.1f}%")
    m2.metric("Total Trades", f"{total_trades} ({wins}W / {losses}L)")
    m3.metric("Net Cumulative P&L", f"₹{net_pnl_total:,.2f}")
    m4.metric("Closing Rule", "03:15 PM IST (Same-Day)")

    asset_stats = []
    for asset_name, grp in df_trades.groupby("asset"):
        t_cnt = len(grp)
        w_cnt = len(grp[grp["net_pnl"] > 0])
        l_cnt = t_cnt - w_cnt
        acc = (w_cnt / t_cnt) * 100
        pnl_sum = grp["net_pnl"].sum()
        asset_stats.append({
            "Asset": asset_name,
            "Trades": t_cnt,
            "Wins": w_cnt,
            "Losses": l_cnt,
            "Accuracy %": f"{acc:.1f}%",
            "Net P&L (₹)": round(pnl_sum, 2)
        })
    st.dataframe(pd.DataFrame(asset_stats), use_container_width=True, hide_index=True)

    # --- SECTION 4: COMPLETE TRADE LIST (WITH SAME-DAY CLOSING) ---
    st.divider()
    st.subheader("📋 Complete Trade List (Chronological & Same-Day Closed)")
    st.dataframe(df_trades, use_container_width=True, hide_index=True)

    csv_bytes = df_trades.to_csv(index=False).encode("utf-8")
    st.download_button(
        label="📥 Download Trade List (CSV)",
        data=csv_bytes,
        file_name="verified_trades_audit.csv",
        mime="text/csv"
    )
else:
    st.info("Selected lookback window mein abhi koi qualified trade nahi mili.")

# --- SECTION 5: LIVE PAPER TRADES LOG (trades_db.json) ---
if os.path.exists(DB_FILE):
    with open(DB_FILE, "r") as f:
        live_records = json.load(f)
    if live_records:
        st.divider()
        st.subheader("⚡ Live Paper Trades Log (trades_db.json)")
        st.dataframe(pd.DataFrame(live_records), use_container_width=True, hide_index=True)
