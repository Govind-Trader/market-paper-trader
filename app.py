import streamlit as st
import pandas as pd
import numpy as np
import json
import os
import pytz
import pyotp
import requests
from datetime import datetime, timedelta, time
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import yfinance as yf
from SmartApi import SmartConnect

# Page Setup
st.set_page_config(
    page_title="Institutional Liquidity Terminal",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom Dark Theme Styling
st.markdown("""
<style>
    .metric-card {
        background-color: #11141c;
        border: 1px solid #1f2937;
        padding: 15px;
        border-radius: 8px;
        text-align: center;
    }
    .metric-val {
        font-size: 24px;
        font-weight: bold;
    }
    .metric-lbl {
        color: #9ca3af;
        font-size: 13px;
    }
</style>
""", unsafe_allow_html=True)

IST = pytz.timezone("Asia/Kolkata")

# Asset Universe Mapping (Angel One Tokens & Yahoo Symbols)
ASSETS = {
    "NIFTY": {"token": "99926000", "symbol": "^NSEI", "exch": "NSE", "market": "NSE_INDEX", "lot_size": 25, "strike_step": 50, "spread_width": 100, "margin": 45000},
    "BANKNIFTY": {"token": "99926009", "symbol": "^NSEBANK", "exch": "NSE", "market": "NSE_INDEX", "lot_size": 15, "strike_step": 100, "spread_width": 200, "margin": 50000},
    "FINNIFTY": {"token": "99926037", "symbol": "NIFTY_FIN_SERVICE.NS", "exch": "NSE", "market": "NSE_INDEX", "lot_size": 25, "strike_step": 50, "spread_width": 100, "margin": 45000},
    "CRUDEOIL": {"token": None, "symbol": "CL=F", "exch": "MCX", "market": "MCX", "lot_size": 100, "strike_step": 50, "spread_width": 100, "margin": 55000},
    "RELIANCE": {"token": "2885", "symbol": "RELIANCE.NS", "exch": "NSE", "market": "NSE", "lot_size": 250, "strike_step": 20, "spread_width": 40, "margin": 65000},
    "HDFCBANK": {"token": "1333", "symbol": "HDFCBANK.NS", "exch": "NSE", "market": "NSE", "lot_size": 550, "strike_step": 10, "spread_width": 20, "margin": 60000},
    "ICICIBANK": {"token": "4963", "symbol": "ICICIBANK.NS", "exch": "NSE", "market": "NSE", "lot_size": 700, "strike_step": 10, "spread_width": 20, "margin": 55000},
    "SBIN": {"token": "3045", "symbol": "SBIN.NS", "exch": "NSE", "market": "NSE", "lot_size": 750, "strike_step": 5, "spread_width": 10, "margin": 50000},
    "INFY": {"token": "1594", "symbol": "INFY.NS", "exch": "NSE", "market": "NSE", "lot_size": 400, "strike_step": 20, "spread_width": 40, "margin": 55000},
    "TCS": {"token": "11536", "symbol": "TCS.NS", "exch": "NSE", "market": "NSE", "lot_size": 175, "strike_step": 50, "spread_width": 100, "margin": 65000},
    "LT": {"token": "11483", "symbol": "LT.NS", "exch": "NSE", "market": "NSE", "lot_size": 150, "strike_step": 20, "spread_width": 40, "margin": 60000},
    "AXISBANK": {"token": "5900", "symbol": "AXISBANK.NS", "exch": "NSE", "market": "NSE", "lot_size": 625, "strike_step": 10, "spread_width": 20, "margin": 55000},
    "BHARTIARTL": {"token": "10604", "symbol": "BHARTIARTL.NS", "exch": "NSE", "market": "NSE", "lot_size": 475, "strike_step": 10, "spread_width": 20, "margin": 50000},
    "TATASTEEL": {"token": "3499", "symbol": "TATASTEEL.NS", "exch": "NSE", "market": "NSE", "lot_size": 5500, "strike_step": 2.5, "spread_width": 2.5, "margin": 50000}
}

# 1. Angel One SmartAPI Client Session
@st.cache_resource(ttl=3600)
def get_smart_api_client():
    try:
        api_key = st.secrets.get("SMARTAPI_KEY") or os.getenv("SMARTAPI_KEY")
        client_code = st.secrets.get("SMARTAPI_CLIENT_CODE") or os.getenv("SMARTAPI_CLIENT_CODE")
        pin = st.secrets.get("SMARTAPI_PIN") or os.getenv("SMARTAPI_PIN")
        totp_secret = st.secrets.get("SMARTAPI_TOTP_KEY") or os.getenv("SMARTAPI_TOTP_KEY")

        if not all([api_key, client_code, pin, totp_secret]):
            return None

        smart_api = SmartConnect(api_key=api_key)
        totp = pyotp.TOTP(totp_secret).now()
        data = smart_api.generateSession(client_code, pin, totp)
        if data and data.get("status"):
            return smart_api
    except Exception as e:
        print(f"SmartAPI auth error: {e}")
    return None

# 2. Historical & Live Candle Fetcher
def fetch_candles(asset_name, days=5):
    conf = ASSETS[asset_name]
    smart_api = get_smart_api_client()
    df = pd.DataFrame()

    if smart_api and conf["token"]:
        try:
            now = datetime.now(IST)
            from_date = (now - timedelta(days=days)).strftime("%Y-%m-%d 09:15")
            to_date = now.strftime("%Y-%m-%d 15:30")

            params = {
                "exchange": conf["exch"],
                "symboltoken": str(conf["token"]),
                "interval": "FIVE_MINUTE",
                "fromdate": from_date,
                "todate": to_date
            }
            res = smart_api.getCandleData(params)
            if res and res.get("status") and res.get("data"):
                df = pd.DataFrame(res["data"], columns=["Timestamp", "Open", "High", "Low", "Close", "Volume"])
                df["Timestamp"] = pd.to_datetime(df["Timestamp"])
                df.set_index("Timestamp", inplace=True)
                if df.index.tz is None:
                    df.index = df.index.tz_localize("UTC").tz_convert(IST)
                else:
                    df.index = df.index.tz_convert(IST)
                return df
        except Exception as e:
            print(f"SmartAPI fetch error for {asset_name}: {e}")

    # Fallback to Yahoo Finance
    try:
        ticker = yf.Ticker(conf["symbol"])
        df = ticker.history(period=f"{days}d", interval="5m")
        if not df.empty:
            if df.index.tz is None:
                df.index = df.index.tz_localize("UTC").tz_convert(IST)
            else:
                df.index = df.index.tz_convert(IST)
            df = df[["Open", "High", "Low", "Close", "Volume"]]
    except Exception as e:
        print(f"Yahoo fetch error: {e}")

    return df

# Header
st.title("⚡ Institutional Liquidity Sweep & Absorption Terminal")
st.caption("Angel One SmartAPI Real-Time Feeds | 4-Hour Major Liquidity Sweeps with 5-Minute Volume Absorption")

tab1, tab2, tab3 = st.tabs([
    "📊 Live Interactive Chart (Angel One Feed)",
    "🎯 Live Scanner & Sweep Signals",
    "📈 60-Day P&L & Capital Audit"
])

# ==========================================
# TAB 1: LIVE INTERACTIVE CHARTS
# ==========================================
with tab1:
    col_sel, col_stat = st.columns([2, 3])
    with col_sel:
        selected_asset = st.selectbox("Asset Select Karein:", list(ASSETS.keys()), index=0)
    
    with col_stat:
        smart_status = "🟢 SmartAPI Live Feed Active" if get_smart_api_client() else "🟠 Yahoo Finance Fallback Mode"
        st.markdown(f"**Feed Status:** `{smart_status}`")

    with st.spinner(f"Fetching real-time candles for {selected_asset}..."):
        df_chart = fetch_candles(selected_asset, days=5)

    if not df_chart.empty:
        # Calculate HTF 4-Hour Swing Low & 5M Vol MA
        df_4h = df_chart.resample("4h").agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna()
        df_4h["HTF_Low"] = df_4h["Low"].shift(1).rolling(window=6).min()
        df_chart["HTF_Low"] = df_4h["HTF_Low"].reindex(df_chart.index, method="ffill")
        df_chart["Vol_MA20"] = df_chart["Volume"].rolling(window=20).mean()

        latest_price = df_chart["Close"].iloc[-1]
        latest_htf = df_chart["HTF_Low"].iloc[-1]
        latest_vol = df_chart["Volume"].iloc[-1]
        avg_vol = df_chart["Vol_MA20"].iloc[-1]

        kpi1, kpi2, kpi3, kpi4 = st.columns(4)
        kpi1.metric("LTP", f"₹{latest_price:,.2f}")
        kpi2.metric("4-Hour Institutional Low", f"₹{latest_htf:,.2f}" if pd.notna(latest_htf) else "Calculating")
        kpi3.metric("Latest 5M Volume", f"{int(latest_vol):,}")
        kpi4.metric("Volume Expansion", f"{round(latest_vol/avg_vol, 2)}x" if avg_vol > 0 else "N/A")

        # Plotly Subplots (Top: Candles, Bottom: Volume)
        fig = make_subplots(
            rows=2, cols=1,
            shared_xaxes=True,
            vertical_spacing=0.03,
            row_heights=[0.75, 0.25]
        )

        # Candlestick
        fig.add_trace(go.Candlestick(
            x=df_chart.index,
            open=df_chart['Open'],
            high=df_chart['High'],
            low=df_chart['Low'],
            close=df_chart['Close'],
            name="5M Price",
            increasing_line_color="#00FF7F",
            decreasing_line_color="#FF4500"
        ), row=1, col=1)

        # 4H Institutional Liquidity Line
        if "HTF_Low" in df_chart.columns and not df_chart["HTF_Low"].dropna().empty:
            fig.add_trace(go.Scatter(
                x=df_chart.index,
                y=df_chart["HTF_Low"],
                name="4H Liquidity Reference",
                line=dict(color="#FFD700", width=2, dash="dash")
            ), row=1, col=1)

        # Volume Bars
        vol_colors = ['#00FF7F' if c >= o else '#FF4500' for o, c in zip(df_chart['Open'], df_chart['Close'])]
        fig.add_trace(go.Bar(
            x=df_chart.index,
            y=df_chart['Volume'],
            name="Volume",
            marker_color=vol_colors
        ), row=2, col=1)

        fig.update_layout(
            template="plotly_dark",
            xaxis_rangeslider_visible=False,
            height=600,
            margin=dict(l=10, r=10, t=10, b=10),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.error(f"Candle data load nahi hua for {selected_asset}. Refresh karein.")

# ==========================================
# TAB 2: LIVE SCANNER & SWEEP SIGNALS
# ==========================================
with tab2:
    st.subheader("🎯 Real-Time 4-Hour Sweep Scanner")
    st.write("Scan button dabate hi sabhi 14 assets live check honge:")

    if st.button("🔍 Scan 14 Assets Now", use_container_width=True):
        signals = []
        scan_progress = st.progress(0)
        
        for idx, (name, conf) in enumerate(ASSETS.items()):
            df_asset = fetch_candles(name, days=5)
            if not df_asset.empty and len(df_asset) > 30:
                df_4h = df_asset.resample("4h").agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna()
                df_4h["HTF_Low"] = df_4h["Low"].shift(1).rolling(window=6).min()
                df_asset["HTF_Low"] = df_4h["HTF_Low"].reindex(df_asset.index, method="ffill")
                df_asset["Vol_MA20"] = df_asset["Volume"].rolling(window=20).mean()

                curr = df_asset.iloc[-1]
                htf_low = curr["HTF_Low"]

                if pd.notna(htf_low):
                    is_sweep = (curr["Low"] < htf_low) and (curr["Close"] > htf_low)
                    is_green = curr["Close"] > curr["Open"]
                    avg_v = curr["Vol_MA20"]
                    vol_mult = round(curr["Volume"] / avg_v, 2) if avg_v > 0 else 0
                    has_absorption = vol_mult >= 1.5

                    status_str = "🟢 BUY TRIGGER (SWEEP + ABSORPTION)" if (is_sweep and is_green and has_absorption) else "⚪ Monitoring"

                    signals.append({
                        "Asset": name,
                        "LTP": round(curr["Close"], 2),
                        "4H Low Ref": round(htf_low, 2),
                        "Candle": "Green" if is_green else "Red",
                        "Volume Ratio": f"{vol_mult}x",
                        "Signal Status": status_str
                    })
            scan_progress.progress((idx + 1) / len(ASSETS))

        st.dataframe(pd.DataFrame(signals), use_container_width=True)

# ==========================================
# TAB 3: 60-DAY P&L & CAPITAL AUDIT
# ==========================================
with tab3:
    st.subheader("📈 Institutional 4-Hour Engine Performance Audit")

    if os.path.exists("trades_db.json"):
        with open("trades_db.json", "r") as f:
            trades_data = json.load(f)

        if trades_data:
            df_trades = pd.DataFrame(trades_data)
            
            # Key Calculations
            total_trades = len(df_trades)
            winners = len(df_trades[df_trades["outcome"] == "TARGET_HIT"])
            win_rate = (winners / total_trades) * 100 if total_trades > 0 else 0
            net_pnl = df_trades["net_pnl"].sum()
            avg_capital = df_trades["capital_deployed"].mean()

            # Top KPI Metrics
            m1, m2, m3, m4 = st.columns(4)
            pnl_color = "#00FF7F" if net_pnl >= 0 else "#FF4500"
            m1.markdown(f"<div class='metric-card'><div class='metric-val' style='color:{pnl_color}'>₹{net_pnl:,.2f}</div><div class='metric-lbl'>Cumulative Net P&L</div></div>", unsafe_allow_html=True)
            m2.markdown(f"<div class='metric-card'><div class='metric-val' style='color:#00FF7F'>{win_rate:.1f}%</div><div class='metric-lbl'>Win Rate ({winners}/{total_trades})</div></div>", unsafe_allow_html=True)
            m3.markdown(f"<div class='metric-card'><div class='metric-val'>{total_trades}</div><div class='metric-lbl'>Total High-Conviction Trades</div></div>", unsafe_allow_html=True)
            m4.markdown(f"<div class='metric-card'><div class='metric-val'>₹{avg_capital:,.2f}</div><div class='metric-lbl'>Avg Capital Deployed / Trade</div></div>", unsafe_allow_html=True)

            st.write("---")

            # Equity Curve
            df_trades["cum_pnl"] = df_trades["net_pnl"].cumsum()
            df_trades["trade_num"] = range(1, len(df_trades) + 1)

            fig_eq = go.Figure()
            fig_eq.add_trace(go.Scatter(
                x=df_trades["trade_num"],
                y=df_trades["cum_pnl"],
                mode="lines+markers",
                name="Equity Curve",
                line=dict(color="#00FF7F" if net_pnl >= 0 else "#FF4500", width=2.5)
            ))
            fig_eq.update_layout(
                title="Cumulative Equity Growth Curve (₹)",
                template="plotly_dark",
                height=400,
                xaxis_title="Trade Number",
                yaxis_title="Net P&L (₹)"
            )
            st.plotly_chart(fig_eq, use_container_width=True)

            # Trades Table & CSV Download
            st.write("### 📜 Complete Trade Ledger")
            st.dataframe(df_trades[["entry_time", "asset", "spread", "outcome", "net_pnl", "vol_ratio"]], use_container_width=True)

            csv_data = df_trades.to_csv(index=False).encode('utf-8')
            st.download_button(
                label="📥 Download Trades CSV",
                data=csv_data,
                file_name="4H_institutional_trades_audit.csv",
                mime="text/csv"
            )
        else:
            st.info("trades_db.json empty hai. Actions workflow run karein.")
    else:
        st.warning("trades_db.json file nahi mili. Pehle backtester script run karein.")
