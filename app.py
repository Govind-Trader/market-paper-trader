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
OPEN_TRADES_FILE = "live_trades.json"
CLOSED_TRADES_FILE = "trades_db.json"

ASSETS = {
    "TCS": {"token": "11536", "symbol": "TCS.NS", "exch": "NSE", "market": "NSE", "lot_size": 175, "strike_step": 50, "spread_width": 100, "margin": 65000},
    "BANKNIFTY": {"token": "99926009", "symbol": "^NSEBANK", "exch": "NSE", "market": "NSE_INDEX", "lot_size": 30, "strike_step": 100, "spread_width": 200, "margin": 50000},
    "FINNIFTY": {"token": "99926037", "symbol": "NIFTY_FIN_SERVICE.NS", "exch": "NSE", "market": "NSE_INDEX", "lot_size": 25, "strike_step": 50, "spread_width": 100, "margin": 45000},
    "SBIN": {"token": "3045", "symbol": "SBIN.NS", "exch": "NSE", "market": "NSE", "lot_size": 750, "strike_step": 5, "spread_width": 10, "margin": 50000},
    "LT": {"token": "11483", "symbol": "LT.NS", "exch": "NSE", "market": "NSE", "lot_size": 150, "strike_step": 20, "spread_width": 40, "margin": 60000},
    "AXISBANK": {"token": "5900", "symbol": "AXISBANK.NS", "exch": "NSE", "market": "NSE", "lot_size": 625, "strike_step": 10, "spread_width": 20, "margin": 55000},
    "NIFTY": {"token": "99926000", "symbol": "^NSEI", "exch": "NSE", "market": "NSE_INDEX", "lot_size": 65, "strike_step": 50, "spread_width": 100, "margin": 45000},
    "HDFCBANK": {"token": "1333", "symbol": "HDFCBANK.NS", "exch": "NSE", "market": "NSE", "lot_size": 550, "strike_step": 10, "spread_width": 20, "margin": 60000}
}

TIMEFRAME_CONFIG = {
    "1 Minute": {"interval": "ONE_MINUTE", "days": 2, "yf": "1m"},
    "3 Minute": {"interval": "THREE_MINUTE", "days": 5, "yf": "5m"},
    "5 Minute": {"interval": "FIVE_MINUTE", "days": 15, "yf": "5m"},
    "15 Minute": {"interval": "FIFTEEN_MINUTE", "days": 20, "yf": "15m"},
    "1 Hour": {"interval": "ONE_HOUR", "days": 30, "yf": "1h"},
    "1 Day": {"interval": "ONE_DAY", "days": 90, "yf": "1d"}
}


# ==========================================
# PERSISTENT JSON STORAGE HELPERS
# ==========================================
def load_json_list(filepath):
    if os.path.exists(filepath):
        try:
            with open(filepath, "r") as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except Exception:
            return []
    return []


def save_json_list(filepath, data_list):
    with open(filepath, "w") as f:
        json.dump(data_list, f, indent=2)


def send_telegram_alert(msg: str):
    bot_token = (st.secrets.get("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN", "")).strip()
    chat_id = (st.secrets.get("TELEGRAM_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID", "")).strip()
    if bot_token and chat_id:
        try:
            requests.post(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                json={"chat_id": chat_id, "text": msg, "parse_mode": "HTML"},
                timeout=8
            )
        except Exception:
            pass


# ==========================================
# SMARTAPI & CANDLE FETCH
# ==========================================
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


# ==========================================
# TIME-GAP & LIFECYCLE ENGINE FOR OPEN TRADES
# ==========================================
def evaluate_open_trades():
    """
    Checks all OPEN trades in live_trades.json against historical/live candles
    after entry_time. Properly handles:
    1. Overnight/Weekend Opening Time-Gaps (09:15 AM Gap Open past SL/Target)
    2. 1:1 Breakeven Trail + 1.8x Trail (Zero fake profit bug fixed)
    3. Max 2 Trading Days (48H) Holding Time Expiry
    """
    open_trades = load_json_list(OPEN_TRADES_FILE)
    if not open_trades:
        return [], []

    closed_trades = load_json_list(CLOSED_TRADES_FILE)
    still_open = []
    newly_closed = []

    for tr in open_trades:
        asset = tr["asset"]
        if asset not in ASSETS:
            still_open.append(tr)
            continue

        df = fetch_candles(asset, tf_label="5 Minute")
        if df.empty:
            still_open.append(tr)
            continue

        entry_dt = IST.localize(datetime.strptime(tr["entry_time"], "%Y-%m-%d %H:%M:%S"))
        post_df = df[df.index > entry_dt]

        entry_price = float(tr["entry_price"])
        curr_sl = float(tr.get("current_sl", tr["initial_sl"]))
        initial_sl = float(tr["initial_sl"])
        target = float(tr["target"])
        side = tr["side"]
        risk_pts = abs(entry_price - initial_sl)
        lot_size = ASSETS[asset]["lot_size"]

        closed_flag = False
        exit_price = entry_price
        exit_dt = datetime.now(IST)
        outcome = "OPEN"
        prev_date = entry_dt.date()

        for bar_dt, bar in post_df.iterrows():
            bar_open = float(bar["Open"])
            bar_high = float(bar["High"])
            bar_low = float(bar["Low"])
            bar_close = float(bar["Close"])
            curr_date = bar_dt.date()

            # 1. OVERNIGHT / TIME-GAP CHECK (If new day opens across SL or Target)
            if curr_date > prev_date:
                if side == "BULLISH_SWEEP":
                    if bar_open <= curr_sl:
                        exit_price = bar_open
                        exit_dt = bar_dt
                        outcome = "GAP_SL_HIT" if curr_sl < entry_price else "GAP_TRAIL_EXIT"
                        closed_flag = True
                        break
                    elif bar_open >= target:
                        exit_price = bar_open
                        exit_dt = bar_dt
                        outcome = "GAP_TARGET_HIT"
                        closed_flag = True
                        break
                else:
                    if bar_open >= curr_sl:
                        exit_price = bar_open
                        exit_dt = bar_dt
                        outcome = "GAP_SL_HIT" if curr_sl > entry_price else "GAP_TRAIL_EXIT"
                        closed_flag = True
                        break
                    elif bar_open <= target:
                        exit_price = bar_open
                        exit_dt = bar_dt
                        outcome = "GAP_TARGET_HIT"
                        closed_flag = True
                        break
                prev_date = curr_date

            # 2. INTRADAY SL, TRAILING & TARGET CHECK
            if side == "BULLISH_SWEEP":
                # Breakeven trail after +1R move
                if bar_high >= entry_price + risk_pts and curr_sl < entry_price:
                    curr_sl = entry_price
                if bar_low <= curr_sl:
                    exit_price = curr_sl
                    exit_dt = bar_dt
                    outcome = "BREAKEVEN_SL" if abs(curr_sl - entry_price) < 0.05 else "STOP_LOSS"
                    closed_flag = True
                    break
                if bar_high >= target:
                    exit_price = target
                    exit_dt = bar_dt
                    outcome = "TARGET_HIT"
                    closed_flag = True
                    break
            else:
                if bar_low <= entry_price - risk_pts and curr_sl > entry_price:
                    curr_sl = entry_price
                if bar_high >= curr_sl:
                    exit_price = curr_sl
                    exit_dt = bar_dt
                    outcome = "BREAKEVEN_SL" if abs(curr_sl - entry_price) < 0.05 else "STOP_LOSS"
                    closed_flag = True
                    break
                if bar_low <= target:
                    exit_price = target
                    exit_dt = bar_dt
                    outcome = "TARGET_HIT"
                    closed_flag = True
                    break

            # 3. MAX HOLDING TIME EXPIRY (2 Trading Days / 48 Hours)
            trading_days_held = len(set(post_df[post_df.index <= bar_dt].index.date))
            if trading_days_held >= 2 and bar_dt.time() >= time(15, 15):
                exit_price = bar_close
                exit_dt = bar_dt
                outcome = "TIME_EXPIRED"
                closed_flag = True
                break

        if closed_flag:
            spot_move = (exit_price - entry_price) if side == "BULLISH_SWEEP" else (entry_price - exit_price)
            # Overnight time-decay penalty if held > 1 day
            days_held = max((exit_dt.date() - entry_dt.date()).days, 0)
            theta_penalty = days_held * 2.5 * lot_size
            # Accurate Net P&L (0 point breakeven exit = -₹60 brokerage, no fake profit bug)
            net_pnl = round((spot_move * 0.40 * lot_size) - 60.0 - theta_penalty, 2)

            closed_record = {
                "entry_time": tr["entry_time"],
                "exit_time": exit_dt.strftime("%Y-%m-%d %H:%M:%S"),
                "date": tr["entry_time"][:10],
                "asset": asset,
                "side": side,
                "spread": tr["spread"],
                "entry_price": round(entry_price, 2),
                "exit_price": round(exit_price, 2),
                "initial_sl": round(initial_sl, 2),
                "target": round(target, 2),
                "outcome": outcome,
                "capital_deployed": ASSETS[asset]["margin"],
                "net_pnl": net_pnl,
                "vol_ratio": tr.get("vol_ratio", "1.8x (Live)")
            }
            closed_trades.append(closed_record)
            newly_closed.append(closed_record)
            send_telegram_alert(
                f"🔔 <b>TRADE CLOSED: {asset}</b>\n"
                f"• Outcome: <b>{outcome}</b>\n"
                f"• Entry: {entry_price} → Exit: {round(exit_price, 2)}\n"
                f"• Net P&L: <b>₹{net_pnl:,.2f}</b>"
            )
        else:
            tr["current_sl"] = curr_sl
            if not post_df.empty:
                ltp = float(post_df["Close"].iloc[-1])
                tr["ltp"] = round(ltp, 2)
                live_move = (ltp - entry_price) if side == "BULLISH_SWEEP" else (entry_price - ltp)
                tr["unrealized_pnl"] = round((live_move * 0.40 * lot_size) - 60.0, 2)
            still_open.append(tr)

    save_json_list(OPEN_TRADES_FILE, still_open)
    if newly_closed:
        save_json_list(CLOSED_TRADES_FILE, closed_trades)

    return still_open, newly_closed


# ==========================================
# MAIN UI
# ==========================================
st.title("⚡ Institutional Liquidity Sweep & Absorption Terminal")
st.caption("Persistent Open/Closed Ledger | Overnight Time-Gap Protection | Angel One SmartAPI")

tab1, tab2, tab3 = st.tabs([
    "📊 Live Interactive Chart (Angel One Feed)",
    "🎯 Live Scanner & Open Positions",
    "📈 Closed Trade Audit (Permanent Ledger)"
])

# TAB 1: CHARTS
with tab1:
    col_sel, col_tf, col_stat = st.columns([2, 2, 2])
    with col_sel:
        selected_asset = st.selectbox("Asset Select Karein:", list(ASSETS.keys()), index=6)
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
        fig.add_trace(go.Candlestick(
            x=df_chart.index, open=df_chart['Open'], high=df_chart['High'], low=df_chart['Low'], close=df_chart['Close'],
            name=f"{selected_tf} Price", increasing_line_color="#089981", decreasing_line_color="#F23645",
            increasing_fillcolor="#089981", decreasing_fillcolor="#F23645"
        ), row=1, col=1)

        if "HTF_High" in df_chart.columns and not df_chart["HTF_High"].dropna().empty:
            fig.add_trace(go.Scatter(x=df_chart.index, y=df_chart["HTF_High"], name="4H Liquidity High", line=dict(color="#FF4500", width=2, dash="dash")), row=1, col=1)
        if "HTF_Low" in df_chart.columns and not df_chart["HTF_Low"].dropna().empty:
            fig.add_trace(go.Scatter(x=df_chart.index, y=df_chart["HTF_Low"], name="4H Liquidity Low", line=dict(color="#2962FF", width=2, dash="dash")), row=1, col=1)

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

# TAB 2: LIVE SCANNER & PERSISTENT OPEN POSITIONS
with tab2:
    st.subheader("🟢 Active / Open Paper Positions (Time-Gap & Auto-Exit Protected)")
    st.caption("Jo bhi live Telegram signal aata hai woh pehle yahan save hota hai — SL, Target, Gap-Open ya 48H Expiry hit hone ke baad hi Closed Ledger mein jata hai.")

    col_sync1, col_sync2 = st.columns([2, 2])
    with col_sync1:
        if st.button("🔄 Sync & Check Open Trades (SL / Target / Time-Gap)", use_container_width=True):
            with st.spinner("Checking open positions against live/historical candles..."):
                open_list, closed_now = evaluate_open_trades()
                if closed_now:
                    st.success(f"{len(closed_now)} trade(s) close hokar Closed Ledger mein move ho gayi hain!")
                else:
                    st.info("Open positions updated.")

    with col_sync2:
        if st.button("📥 Log Nov 4 NIFTY Telegram Signal & Resolve Now", use_container_width=True):
            existing_open = load_json_list(OPEN_TRADES_FILE)
            existing_closed = load_json_list(CLOSED_TRADES_FILE)
            already_logged = any(t.get("entry_time") == "2026-11-04 11:50:00" and t.get("asset") == "NIFTY" for t in (existing_open + existing_closed))
            if not already_logged:
                nov4_trade = {
                    "entry_time": "2026-11-04 11:50:00",
                    "asset": "NIFTY",
                    "side": "BULLISH_SWEEP",
                    "spread": "Buy 23800 CE / Sell 24000 CE",
                    "entry_price": 23794.0,
                    "initial_sl": 23777.15,
                    "current_sl": 23777.15,
                    "target": 23827.7,
                    "vol_ratio": "1.8x (Telegram Live)"
                }
                existing_open.append(nov4_trade)
                save_json_list(OPEN_TRADES_FILE, existing_open)
                evaluate_open_trades()
                st.success("Nov 4 NIFTY trade log aur resolve ho gayi hai! Tab 3 mein check karein.")
            else:
                st.warning("Nov 4 NIFTY trade pehle se logged hai.")

    current_open_trades = load_json_list(OPEN_TRADES_FILE)
    if current_open_trades:
        st.dataframe(pd.DataFrame(current_open_trades), use_container_width=True)
    else:
        st.info("Abhi koi OPEN trade active nahi hai. Sabhi logged trades SL/Target/Expiry par close ho chuki hain.")

    st.write("---")
    st.subheader("🎯 Real-Time 2-Candle Sweep Scanner (Auto-Logs Confirmed Signals)")

    if st.button("🔍 Scan 8 Assets & Auto-Log Live Signals", use_container_width=True):
        signals = []
        scan_progress = st.progress(0)
        open_trades = load_json_list(OPEN_TRADES_FILE)
        closed_trades = load_json_list(CLOSED_TRADES_FILE)

        for idx, (name, conf) in enumerate(ASSETS.items()):
            df_asset = fetch_candles(name, tf_label="5 Minute")
            if not df_asset.empty and len(df_asset) > 30:
                df_4h = df_asset.resample("4h").agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna()
                df_4h["HTF_Low"] = df_4h["Low"].shift(1).rolling(window=6).min()
                df_4h["HTF_High"] = df_4h["High"].shift(1).rolling(window=6).max()
                df_asset["HTF_Low"] = df_4h["HTF_Low"].reindex(df_asset.index, method="ffill")
                df_asset["HTF_High"] = df_4h["HTF_High"].reindex(df_asset.index, method="ffill")

                c1 = df_asset.iloc[-2]
                c2 = df_asset.iloc[-1]
                htf_low = c1["HTF_Low"]
                htf_high = c1["HTF_High"]

                status_str = "⚪ Monitoring"
                new_signal_obj = None

                if pd.notna(htf_low) and pd.notna(htf_high):
                    entry_px = round(float(c2["Close"]), 2)
                    atm = round(entry_px / conf["strike_step"]) * conf["strike_step"]
                    entry_ts = c2.name.strftime("%Y-%m-%d %H:%M:%S")

                    if c2["Low"] <= htf_low:
                        status_str = "🟡 ALERT MODE (4H Low Touched)"
                    if (c1["Low"] <= htf_low) and (c2["Close"] > c2["Open"]) and (c2["High"] > c1["High"]):
                        status_str = "🟢 BUY CONFIRMED (Bull Call Spread)"
                        sl_px = round(min(float(c1["Low"]), float(c2["Low"])) - (entry_px * 0.0005), 2)
                        risk = max(entry_px - sl_px, entry_px * 0.001)
                        tgt_px = round(entry_px + (2.0 * risk), 2)
                        new_signal_obj = {
                            "entry_time": entry_ts,
                            "asset": name,
                            "side": "BULLISH_SWEEP",
                            "spread": f"Buy {atm} CE / Sell {atm + conf['spread_width']} CE",
                            "entry_price": entry_px,
                            "initial_sl": sl_px,
                            "current_sl": sl_px,
                            "target": tgt_px,
                            "vol_ratio": "Live Confirmed"
                        }

                    if c2["High"] >= htf_high:
                        status_str = "🟡 ALERT MODE (4H High Touched)"
                    if (c1["High"] >= htf_high) and (c2["Close"] < c2["Open"]) and (c2["Low"] < c1["Low"]):
                        status_str = "🔴 SELL CONFIRMED (Bear Put Spread)"
                        sl_px = round(max(float(c1["High"]), float(c2["High"])) + (entry_px * 0.0005), 2)
                        risk = max(sl_px - entry_px, entry_px * 0.001)
                        tgt_px = round(entry_px - (2.0 * risk), 2)
                        new_signal_obj = {
                            "entry_time": entry_ts,
                            "asset": name,
                            "side": "BEARISH_SWEEP",
                            "spread": f"Buy {atm} PE / Sell {atm - conf['spread_width']} PE",
                            "entry_price": entry_px,
                            "initial_sl": sl_px,
                            "current_sl": sl_px,
                            "target": tgt_px,
                            "vol_ratio": "Live Confirmed"
                        }

                    if new_signal_obj:
                        dup = any(
                            t["asset"] == name and t["entry_time"][:10] == entry_ts[:10]
                            for t in (open_trades + closed_trades)
                        )
                        if not dup:
                            open_trades.append(new_signal_obj)
                            save_json_list(OPEN_TRADES_FILE, open_trades)
                            send_telegram_alert(
                                f"⚡ <b>LIVE SIGNAL: {name}</b>\n"
                                f"Spot Price: {entry_px}\n"
                                f"• Spread: {new_signal_obj['spread']}\n"
                                f"• Hard SL: {new_signal_obj['initial_sl']}\n"
                                f"• Target (1:2): {new_signal_obj['target']}"
                            )

                    signals.append({
                        "Asset": name,
                        "LTP": entry_px,
                        "4H High": round(htf_high, 2),
                        "4H Low": round(htf_low, 2),
                        "Signal Status": status_str
                    })
            scan_progress.progress((idx + 1) / len(ASSETS))

        st.dataframe(pd.DataFrame(signals), use_container_width=True)

# TAB 3: CLOSED TRADE AUDIT (PERMANENT LEDGER)
with tab3:
    st.subheader("📈 Institutional Strategy Performance & Capital Audit (Permanent Closed Ledger)")

    trades_data = load_json_list(CLOSED_TRADES_FILE)
    if trades_data:
        df_trades = pd.DataFrame(trades_data)
        # Deduplicate by asset + entry_time so rolling scripts cannot corrupt history
        df_trades = df_trades.drop_duplicates(subset=["asset", "entry_time"]).sort_values("entry_time").reset_index(drop=True)

        total_trades = len(df_trades)
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

        m1, m2, m3, m4 = st.columns(4)
        pnl_color = "#089981" if net_pnl >= 0 else "#F23645"
        m1.markdown(f"<div class='metric-card'><div class='metric-val' style='color:{pnl_color}'>₹{net_pnl:,.2f}</div><div class='metric-lbl'>Cumulative Net P&L</div></div>", unsafe_allow_html=True)
        m2.markdown(f"<div class='metric-card'><div class='metric-val' style='color:#089981'>{win_rate:.1f}%</div><div class='metric-lbl'>Win Rate ({winners} Wins / {total_trades} Trades)</div></div>", unsafe_allow_html=True)
        m3.markdown(f"<div class='metric-card'><div class='metric-val'>{profit_factor:.2f}</div><div class='metric-lbl'>Profit Factor (Gross Win / Gross Loss)</div></div>", unsafe_allow_html=True)
        m4.markdown(f"<div class='metric-card'><div class='metric-val'>₹{avg_capital:,.2f}</div><div class='metric-lbl'>Avg Margin / Trade</div></div>", unsafe_allow_html=True)

        st.write("---")

        sub1, sub2, sub3, sub4 = st.columns(4)
        sub1.metric("Average Profit / Win", f"₹{avg_win:,.2f}")
        sub2.metric("Average Loss / Trade", f"-₹{avg_loss:,.2f}")
        sub3.metric("Realized Risk-Reward", f"1 : {(avg_win / avg_loss):.2f}" if avg_loss > 0 else "N/A")
        sub4.metric("Total Losses Recorded", f"{losers} Trades")

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

        st.write("### 📜 Complete Closed Trade Ledger")
        disp_cols = [c for c in ["entry_time", "exit_time", "asset", "side", "spread", "entry_price", "exit_price", "initial_sl", "target", "outcome", "net_pnl", "vol_ratio"] if c in df_trades.columns]
        st.dataframe(df_trades[disp_cols], use_container_width=True)

        csv_data = df_trades.to_csv(index=False).encode('utf-8')
        st.download_button(
            label="📥 Download Trades CSV",
            data=csv_data,
            file_name="verified_trades_audit.csv",
            mime="text/csv"
        )
    else:
        st.warning("trades_db.json empty hai ya nahi mili.")
