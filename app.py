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

st.set_page_config(
    page_title="Institutional Liquidity Terminal",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded"
)

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

ASSETS = {
    "TCS": {"token": "11536", "symbol": "TCS.NS", "exch": "NSE", "market": "NSE", "lot_size": 175, "strike_step": 50, "spread_width": 100, "margin": 65000},
    "BANKNIFTY": {"token": "99926009", "symbol": "^NSEBANK", "exch": "NSE", "market": "NSE_INDEX", "lot_size": 15, "strike_step": 100, "spread_width": 200, "margin": 50000},
    "FINNIFTY": {"token": "99926037", "symbol": "NIFTY_FIN_SERVICE.NS", "exch": "NSE", "market": "NSE_INDEX", "lot_size": 25, "strike_step": 50, "spread_width": 100, "margin": 45000},
    "SBIN": {"token": "3045", "symbol": "SBIN.NS", "exch": "NSE", "market": "NSE", "lot_size": 750, "strike_step": 5, "spread_width": 10, "margin": 50000},
    "LT": {"token": "11483", "symbol": "LT.NS", "exch": "NSE", "market": "NSE", "lot_size": 150, "strike_step": 20, "spread_width": 40, "margin": 60000},
    "AXISBANK": {"token": "5900", "symbol": "AXISBANK.NS", "exch": "NSE", "market": "NSE", "lot_size": 625, "strike_step": 10, "spread_width": 20, "margin": 55000},
    "NIFTY": {"token": "99926000", "symbol": "^NSEI", "exch": "NSE", "market": "NSE_INDEX", "lot_size": 25, "strike_step": 50, "spread_width": 100, "margin": 45000},
    "HDFCBANK": {"token": "1333", "symbol": "HDFCBANK.NS", "exch": "NSE", "market": "NSE", "lot_size": 550, "strike_step": 10, "spread_width": 20, "margin": 60000}
}
TIMEFRAME_CONFIG = {
    "1 Minute": {"interval": "ONE_MINUTE", "days": 2, "yf": "1m"},
    "3 Minute": {"interval": "THREE_MINUTE", "days": 5, "yf": "5m"},
    "5 Minute": {"interval": "FIVE_MINUTE", "days": 5, "yf": "5m"},
    "15 Minute": {"interval": "FIFTEEN_MINUTE", "days": 15, "yf": "15m"},
    "1 Hour": {"interval": "ONE_HOUR", "days": 30, "yf": "1h"},
    "1 Day": {"interval": "ONE_DAY", "days": 90, "yf": "1d"}
}

@st.cache_resource(ttl=600)
def get_smart_api_client():
    try:
        api_key = (st.secrets.get("SMARTAPI_KEY") or os.getenv("SMARTAPI_KEY", "")).strip()
        client_code = (st.secrets.get("SMARTAPI_CLIENT_CODE") or os.getenv("SMARTAPI_CLIENT_CODE", "")).strip()
        pin = (st.secrets.get("SMARTAPI_PIN") or os.getenv("SMARTAPI_PIN", "")).strip()
        totp_secret = (st.secrets.get("SMARTAPI_TOTP_KEY") or os.getenv("SMARTAPI_TOTP_KEY", "")).strip()

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

def fetch_candles(asset_name, tf_label="5 Minute"):
    conf = ASSETS[asset_name]
    tf_info = TIMEFRAME_CONFIG[tf_label]
    smart_api = get_smart_api_client()
    df = pd.DataFrame()

    if smart_api and conf["token"]:
        try:
            now = datetime.now(IST)
            from_date = (now - timedelta(days=tf_info["days"])).strftime("%Y-%m-%d 09:15")
            to_date = now.strftime("%Y-%m-%d 15:30")

            params = {
                "exchange": conf["exch"],
                "symboltoken": str(conf["token"]),
                "interval": tf_info["interval"],
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

    try:
        ticker = yf.Ticker(conf["symbol"])
        df = ticker.history(period=f"{tf_info['days']}d", interval=tf_info["yf"])
        if not df.empty:
            if df.index.tz is None:
                df.index = df.index.tz_localize("UTC").tz_convert(IST)
            else:
                df.index = df.index.tz_convert(IST)
            df = df[["Open", "High", "Low", "Close", "Volume"]]
    except Exception as e:
        print(f"Yahoo fetch error: {e}")

    return df

st.title("⚡ Institutional Liquidity Sweep & Absorption Terminal")
st.caption("Angel One SmartAPI Feeds | 2-Candle Reversal Confirmation + Trailing Stop Loss Engine")

tab1, tab2, tab3 = st.tabs([
    "📊 Live Interactive Chart (Angel One Feed)",
    "🎯 Live Scanner & Sweep Alerts",
    "📈 60-Day P&L & Capital Audit"
])

# TAB 1: CHARTS
with tab1:
    col_sel, col_tf, col_stat = st.columns([2, 2, 2])
    with col_sel:
        selected_asset = st.selectbox("Asset Select Karein:", list(ASSETS.keys()), index=0)
    with col_tf:
        selected_tf = st.selectbox("Timeframe Switch:", list(TIMEFRAME_CONFIG.keys()), index=2)
    with col_stat:
        smart_status = "🟢 SmartAPI Live Feed Active" if get_smart_api_client() else "🟠 Yahoo Finance Fallback Mode"
        st.markdown(f"**Feed Status:** `{smart_status}`")

    with st.spinner(f"Fetching real-time {selected_tf} candles for {selected_asset}..."):
        df_chart = fetch_candles(selected_asset, tf_label=selected_tf)

    if not df_chart.empty:
        df_4h = df_chart.resample("4h").agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna()
        if len(df_4h) > 6:
            df_4h["HTF_Low"] = df_4h["Low"].shift(1).rolling(window=6).min()
            df_4h["HTF_High"] = df_4h["High"].shift(1).rolling(window=6).max()
            df_chart["HTF_Low"] = df_4h["HTF_Low"].reindex(df_chart.index, method="ffill")
            df_chart["HTF_High"] = df_4h["HTF_High"].reindex(df_chart.index, method="ffill")
        else:
            df_chart["HTF_Low"] = np.nan
            df_chart["HTF_High"] = np.nan

        latest_price = df_chart["Close"].iloc[-1]
        latest_htf_low = df_chart["HTF_Low"].iloc[-1]
        latest_htf_high = df_chart["HTF_High"].iloc[-1]
        latest_vol = df_chart["Volume"].iloc[-1]

        kpi1, kpi2, kpi3, kpi4 = st.columns(4)
        kpi1.metric("LTP", f"₹{latest_price:,.2f}")
        kpi2.metric("4H High Liquidity", f"₹{latest_htf_high:,.2f}" if pd.notna(latest_htf_high) else "N/A")
        kpi3.metric("4H Low Liquidity", f"₹{latest_htf_low:,.2f}" if pd.notna(latest_htf_low) else "N/A")
        kpi4.metric(f"Latest {selected_tf} Volume", f"{int(latest_vol):,}")

        fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.03, row_heights=[0.75, 0.25])

        # Candlestick
        fig.add_trace(go.Candlestick(
            x=df_chart.index, open=df_chart['Open'], high=df_chart['High'], low=df_chart['Low'], close=df_chart['Close'],
            name=f"{selected_tf} Price", increasing_line_color="#089981", decreasing_line_color="#F23645",
            increasing_fillcolor="#089981", decreasing_fillcolor="#F23645"
        ), row=1, col=1)

        # 4H High & Low Lines
        if "HTF_High" in df_chart.columns and not df_chart["HTF_High"].dropna().empty:
            fig.add_trace(go.Scatter(x=df_chart.index, y=df_chart["HTF_High"], name="4H Liquidity High", line=dict(color="#FF4500", width=2, dash="dash")), row=1, col=1)
        if "HTF_Low" in df_chart.columns and not df_chart["HTF_Low"].dropna().empty:
            fig.add_trace(go.Scatter(x=df_chart.index, y=df_chart["HTF_Low"], name="4H Liquidity Low", line=dict(color="#2962FF", width=2, dash="dash")), row=1, col=1)

        # Volume
        vol_colors = ['#089981' if c >= o else '#F23645' for o, c in zip(df_chart['Open'], df_chart['Close'])]
        fig.add_trace(go.Bar(x=df_chart.index, y=df_chart['Volume'], name="Volume", marker_color=vol_colors), row=2, col=1)

        if selected_tf != "1 Day":
            fig.update_xaxes(rangebreaks=[dict(bounds=["sat", "mon"]), dict(bounds=[15.5, 9.25], pattern="hour")])
        else:
            fig.update_xaxes(rangebreaks=[dict(bounds=["sat", "mon"])])

        fig.update_layout(
            template="plotly_dark", xaxis_rangeslider_visible=False, height=650,
            margin=dict(l=20, r=40, t=20, b=20), paper_bgcolor="#131722", plot_bgcolor="#131722",
            hovermode="x unified", legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.error(f"Data load nahi hua for {selected_asset}.")

# TAB 2: SCANNER & ALERT MODE
with tab2:
    st.subheader("🎯 Real-Time 2-Candle Sweep Scanner")
    st.caption("Checks for: 1. Alert Mode (Liquidity Sweep) -> 2. Confirmation (Reversal Breakout)")

    if st.button("🔍 Scan 14 Assets Now", use_container_width=True):
        signals = []
        scan_progress = st.progress(0)
        
        for idx, (name, conf) in enumerate(ASSETS.items()):
            df_asset = fetch_candles(name, tf_label="5 Minute")
            if not df_asset.empty and len(df_asset) > 30:
                df_4h = df_asset.resample("4h").agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna()
                df_4h["HTF_Low"] = df_4h["Low"].shift(1).rolling(window=6).min()
                df_4h["HTF_High"] = df_4h["High"].shift(1).rolling(window=6).max()
                df_asset["HTF_Low"] = df_4h["HTF_Low"].reindex(df_asset.index, method="ffill")
                df_asset["HTF_High"] = df_4h["HTF_High"].reindex(df_asset.index, method="ffill")

                c1 = df_asset.iloc[-2] # Previous Candle
                c2 = df_asset.iloc[-1] # Current Candle
                htf_low = c1["HTF_Low"]
                htf_high = c1["HTF_High"]

                status_str = "⚪ Monitoring"
                if pd.notna(htf_low) and pd.notna(htf_high):
                    # Bullish check
                    if c2["Low"] <= htf_low:
                        status_str = "🟡 ALERT MODE (4H Low Touched)"
                    if (c1["Low"] <= htf_low) and (c2["Close"] > c2["Open"]) and (c2["High"] > c1["High"]):
                        status_str = "🟢 BUY CONFIRMED (Bull Call Spread)"
                    
                    # Bearish check
                    if c2["High"] >= htf_high:
                        status_str = "🟡 ALERT MODE (4H High Touched)"
                    if (c1["High"] >= htf_high) and (c2["Close"] < c2["Open"]) and (c2["Low"] < c1["Low"]):
                        status_str = "🔴 SELL CONFIRMED (Bear Put Spread)"

                    signals.append({
                        "Asset": name,
                        "LTP": round(c2["Close"], 2),
                        "4H High": round(htf_high, 2),
                        "4H Low": round(htf_low, 2),
                        "Signal Status": status_str
                    })
            scan_progress.progress((idx + 1) / len(ASSETS))

        st.dataframe(pd.DataFrame(signals), use_container_width=True)

# TAB 3: AUDIT
with tab3:
    st.subheader("📈 Institutional Strategy Performance & Capital Audit")

    db_path = "trades_db.json"
    if os.path.exists(db_path):
        # Fresh read without stale memory caching
        with open(db_path, "r") as f:
            trades_data = json.load(f)

        if trades_data:
            df_trades = pd.DataFrame(trades_data)
            
            total_trades = len(df_trades)
            # EXACT WIN CALCULATION: Any trade ending with positive net P&L
            profitable_trades = df_trades[df_trades["net_pnl"] > 0]
            losing_trades = df_trades[df_trades["net_pnl"] < 0]
            
            winners = len(profitable_trades)
            losers = len(losing_trades)
            win_rate = (winners / total_trades) * 100 if total_trades > 0 else 0
            
            net_pnl = df_trades["net_pnl"].sum()
            avg_win = profitable_trades["net_pnl"].mean() if winners > 0 else 0
            avg_loss = abs(losing_trades["net_pnl"].mean()) if losers > 0 else 0
            profit_factor = (profitable_trades["net_pnl"].sum() / abs(losing_trades["net_pnl"].sum())) if losers > 0 else 0
            avg_capital = df_trades["capital_deployed"].mean() if "capital_deployed" in df_trades.columns else 50000

            # Metric Cards
            m1, m2, m3, m4 = st.columns(4)
            pnl_color = "#089981" if net_pnl >= 0 else "#F23645"
            m1.markdown(f"<div class='metric-card'><div class='metric-val' style='color:{pnl_color}'>₹{net_pnl:,.2f}</div><div class='metric-lbl'>Cumulative Net P&L</div></div>", unsafe_allow_html=True)
            m2.markdown(f"<div class='metric-card'><div class='metric-val' style='color:#089981'>{win_rate:.1f}%</div><div class='metric-lbl'>Win Rate ({winners} Wins / {total_trades} Trades)</div></div>", unsafe_allow_html=True)
            m3.markdown(f"<div class='metric-card'><div class='metric-val'>{profit_factor:.2f}</div><div class='metric-lbl'>Profit Factor (Gross Win / Gross Loss)</div></div>", unsafe_allow_html=True)
            m4.markdown(f"<div class='metric-card'><div class='metric-val'>₹{avg_capital:,.2f}</div><div class='metric-lbl'>Avg Margin / Trade</div></div>", unsafe_allow_html=True)

            st.write("---")

            # Risk-Reward & Trade Breakdown Details
            sub1, sub2, sub3, sub4 = st.columns(4)
            sub1.metric("Average Profit / Win", f"₹{avg_win:,.2f}")
            sub2.metric("Average Loss / Trade", f"-₹{avg_loss:,.2f}")
            sub3.metric("Realized Risk-Reward", f"1 : {(avg_win / avg_loss):.2f}" if avg_loss > 0 else "N/A")
            sub4.metric("Total Losses Recorded", f"{losers} Trades")

            # Cumulative Equity Curve
            df_trades["cum_pnl"] = df_trades["net_pnl"].cumsum()
            df_trades["trade_num"] = range(1, len(df_trades) + 1)

            fig_eq = go.Figure()
            fig_eq.add_trace(go.Scatter(
                x=df_trades["trade_num"], y=df_trades["cum_pnl"],
                mode="lines+markers", name="Equity Curve",
                line=dict(color="#089981" if net_pnl >= 0 else "#F23645", width=2.5)
            ))
            fig_eq.update_layout(
                title="Cumulative Equity Growth Curve (₹)", template="plotly_dark",
                height=400, xaxis_title="Trade Number", yaxis_title="Net P&L (₹)",
                paper_bgcolor="#131722", plot_bgcolor="#131722"
            )
            st.plotly_chart(fig_eq, use_container_width=True)

            # Detailed Asset Table
            st.write("### 📊 Performance By Asset")
            asset_grp = df_trades.groupby("asset").agg(
                Trades=("net_pnl", "count"),
                Wins=("net_pnl", lambda x: (x > 0).sum()),
                Losses=("net_pnl", lambda x: (x < 0).sum()),
                Net_PnL=("net_pnl", "sum")
            ).reset_index()
            asset_grp["Win_Rate"] = (asset_grp["Wins"] / asset_grp["Trades"]) * 100
            asset_grp["Win_Rate"] = asset_grp["Win_Rate"].map("{:.1f}%".format)
            asset_grp["Net_PnL_Formatted"] = asset_grp["Net_PnL"].map("₹{:,.2f}".format)
            st.dataframe(asset_grp[["asset", "Trades", "Wins", "Losses", "Win_Rate", "Net_PnL_Formatted"]], use_container_width=True)

            st.write("### 📜 Complete Trade Ledger")
            disp_cols = [c for c in ["entry_time", "asset", "side", "spread", "outcome", "net_pnl", "vol_ratio"] if c in df_trades.columns]
            st.dataframe(df_trades[disp_cols], use_container_width=True)

            csv_data = df_trades.to_csv(index=False).encode('utf-8')
            st.download_button(
                label="📥 Download Trades CSV",
                data=csv_data,
                file_name="verified_trades_audit.csv",
                mime="text/csv"
            )
        else:
            st.info("trades_db.json empty hai.")
    else:
        st.warning("trades_db.json file nahi mili. Actions workflow run karein.")
