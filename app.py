from datetime import datetime, time
import json
import math
import os
import time as tm
import pandas as pd
import pytz
import requests
import streamlit as st
import streamlit.components.v1 as components
import yfinance as yf

# ==============================================================================
# 1. PAGE SETUP & CONFIGURATION
# ==============================================================================
st.set_page_config(
    page_title="Institutional Liquidity Terminal (14 Assets)",
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ==============================================================================
# 2. TELEGRAM ALERT CREDENTIALS
# ==============================================================================
TELEGRAM_BOT_TOKEN = st.secrets.get("TELEGRAM_BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
TELEGRAM_CHAT_ID = st.secrets.get("TELEGRAM_CHAT_ID", "YOUR_CHAT_ID_HERE")


def send_telegram_alert(message: str):
    """Telegram channel/group par instant alert send karta hai."""
    if (
        TELEGRAM_BOT_TOKEN == "YOUR_BOT_TOKEN_HERE"
        or TELEGRAM_CHAT_ID == "YOUR_CHAT_ID_HERE"
    ):
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
    }
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print(f"Telegram notification error: {e}")


# ==============================================================================
# 3. 14 ASSETS UNIVERSE (INDICES + MCX CRUDE + 10 HEAVYWEIGHT F&O STOCKS)
# ==============================================================================
ASSETS = {
    # --- INDICES ---
    "NIFTY": {
        "symbol": "^NSEI",
        "market": "NSE",
        "lot_size": 25,
        "strike_step": 50,
        "spread_width": 100,
        "margin_per_lot": 45000,
    },
    "BANKNIFTY": {
        "symbol": "^NSEBANK",
        "market": "NSE",
        "lot_size": 15,
        "strike_step": 100,
        "spread_width": 200,
        "margin_per_lot": 50000,
    },
    "FINNIFTY": {
        "symbol": "NIFTY_FIN_SERVICE.NS",
        "market": "NSE",
        "lot_size": 25,
        "strike_step": 50,
        "spread_width": 100,
        "margin_per_lot": 45000,
    },
    # --- COMMODITY ---
    "CRUDEOIL": {
        "symbol": "CL=F",
        "market": "MCX",
        "lot_size": 100,
        "strike_step": 50,
        "spread_width": 100,
        "margin_per_lot": 55000,
    },
    # --- 10 HIGH-BETA STOCKS ---
    "RELIANCE": {
        "symbol": "RELIANCE.NS",
        "market": "NSE",
        "lot_size": 250,
        "strike_step": 20,
        "spread_width": 40,
        "margin_per_lot": 65000,
    },
    "HDFCBANK": {
        "symbol": "HDFCBANK.NS",
        "market": "NSE",
        "lot_size": 550,
        "strike_step": 10,
        "spread_width": 20,
        "margin_per_lot": 60000,
    },
    "ICICIBANK": {
        "symbol": "ICICIBANK.NS",
        "market": "NSE",
        "lot_size": 700,
        "strike_step": 10,
        "spread_width": 20,
        "margin_per_lot": 55000,
    },
    "SBIN": {
        "symbol": "SBIN.NS",
        "market": "NSE",
        "lot_size": 750,
        "strike_step": 5,
        "spread_width": 10,
        "margin_per_lot": 50000,
    },
    "INFY": {
        "symbol": "INFY.NS",
        "market": "NSE",
        "lot_size": 400,
        "strike_step": 20,
        "spread_width": 40,
        "margin_per_lot": 55000,
    },
    "TCS": {
        "symbol": "TCS.NS",
        "market": "NSE",
        "lot_size": 175,
        "strike_step": 50,
        "spread_width": 100,
        "margin_per_lot": 65000,
    },
    "LT": {
        "symbol": "LT.NS",
        "market": "NSE",
        "lot_size": 150,
        "strike_step": 20,
        "spread_width": 40,
        "margin_per_lot": 60000,
    },
    "AXISBANK": {
        "symbol": "AXISBANK.NS",
        "market": "NSE",
        "lot_size": 625,
        "strike_step": 10,
        "spread_width": 20,
        "margin_per_lot": 55000,
    },
    "BHARTIARTL": {
        "symbol": "BHARTIARTL.NS",
        "market": "NSE",
        "lot_size": 475,
        "strike_step": 10,
        "spread_width": 20,
        "margin_per_lot": 50000,
    },
    "TATASTEEL": {
        "symbol": "TATASTEEL.NS",
        "market": "NSE",
        "lot_size": 5500,
        "strike_step": 2.5,
        "spread_width": 5,
        "margin_per_lot": 50000,
    },
}

# ==============================================================================
# 4. SESSION STATE INITIALIZATION
# ==============================================================================
if "virtual_capital" not in st.session_state:
    st.session_state.virtual_capital = 2000000.0  # ₹20 Lakhs virtual seed
if "trade_history" not in st.session_state:
    st.session_state.trade_history = []
if "open_positions" not in st.session_state:
    st.session_state.open_positions = []

# ==============================================================================
# 5. CORE ALGORITHMIC ENGINE
# ==============================================================================


def is_market_trading_hours(market_type: str) -> bool:
    """IST trading hours check."""
    ist = pytz.timezone("Asia/Kolkata")
    now_time = datetime.now(ist).time()

    if market_type == "NSE":
        return time(9, 15) <= now_time <= time(15, 30)
    elif market_type == "MCX":
        return time(9, 0) <= now_time <= time(23, 30)
    return False


def get_volume_multiplier(asset_name: str) -> float:
    """MCX Crude Oil US opening overlap (06:00 PM - 09:30 PM IST) ke dauran 2.2x threshold."""
    ist = pytz.timezone("Asia/Kolkata")
    now_time = datetime.now(ist).time()

    if asset_name == "CRUDEOIL" and (time(18, 0) <= now_time <= time(21, 30)):
        return 2.2
    return 1.8


def scan_asset_for_setup(name: str, config: dict):
    """5-Minute Liquidity Sweep + Dynamic Volume Absorption rule verify karta hai."""
    try:
        ticker = yf.Ticker(config["symbol"])
        df = ticker.history(period="5d", interval="5m")

        if df.empty or len(df) < 35:
            return None

        df["Vol_MA20"] = df["Volume"].rolling(window=20).mean()
        prev_30_low = df["Low"].iloc[-31:-1].min()

        curr = df.iloc[-1]
        c_open, c_high, c_low, c_close, c_vol = (
            curr["Open"],
            curr["High"],
            curr["Low"],
            curr["Close"],
            curr["Volume"],
        )
        avg_vol = curr["Vol_MA20"]

        required_mult = get_volume_multiplier(name)

        is_sweep = (c_low < prev_30_low) and (c_close > prev_30_low)
        is_green = c_close > c_open
        has_volume = (
            c_vol >= (avg_vol * required_mult) if avg_vol > 0 else False
        )

        if is_sweep and is_green and has_volume:
            atm_strike = int(
                round(c_close / config["strike_step"]) * config["strike_step"]
            )
            otm_strike = atm_strike + config["spread_width"]

            risk_points = round(c_close - c_low, 2)
            target_points = round(risk_points * 2, 2)

            return {
                "asset": name,
                "market": config["market"],
                "trigger_time": datetime.now(pytz.timezone("Asia/Kolkata")),
                "spot_price": round(c_close, 2),
                "sweep_low": round(c_low, 2),
                "vol_ratio": round(c_vol / avg_vol, 2),
                "required_mult": required_mult,
                "atm_buy": f"{atm_strike} CE",
                "otm_sell": f"{otm_strike} CE",
                "spread_width": config["spread_width"],
                "lot_size": config["lot_size"],
                "capital_used": config["margin_per_lot"],
                "stop_loss": round(c_low, 2),
                "target": round(c_close + target_points, 2),
                "status": "ACTIVE",
            }
    except Exception as e:
        print(f"Scanning exception on {name}: {e}")

    return None


# ==============================================================================
# 6. USER INTERFACE (STREAMLIT DASHBOARD)
# ==============================================================================
st.title("🎯 Institutional Liquidity Terminal")
st.caption(
    "Automated 5m 30-Candle Swing Low Sweep + 1.8x/2.2x Volume Absorption Bull Call Spreads"
)

# Header Metrics Bar
used_capital = sum(
    pos["capital_used"] for pos in st.session_state.open_positions
)
available_capital = st.session_state.virtual_capital - used_capital

m1, m2, m3, m4 = st.columns(4)
m1.metric("Virtual Total Capital", f"₹{st.session_state.virtual_capital:,.2f}")
m2.metric(
    "Active Capital Deployed",
    f"₹{used_capital:,.2f}",
    delta=f"{len(st.session_state.open_positions)} Open Spreads",
)
m3.metric("Available Free Margin", f"₹{available_capital:,.2f}")
m4.metric("Active Assets", "14 (NSE + MCX)")

st.divider()

# 3 Complete Tabs
tab1, tab2, tab3 = st.tabs(
    [
        "📊 Interactive Live Charts",
        "⚙️ Scanner & Trade Log",
        "📈 60-Day P&L & Capital Audit",
    ]
)

# ------------------------------------------------------------------------------
# TAB 1: TRADINGVIEW ADVANCED PRO WIDGET
# ------------------------------------------------------------------------------
with tab1:
    col_chart_select, _ = st.columns([2, 3])
    with col_chart_select:
        selected_instrument = st.selectbox(
            "Select Asset to Load Live TradingView Chart:",
            [
                "MCX:CRUDEOIL1! (MCX Crude Continuous)",
                "NSE:NIFTY (Nifty 50)",
                "NSE:BANKNIFTY (Bank Nifty)",
                "NSE:FINNIFTY (Fin Nifty)",
                "NSE:RELIANCE (Reliance Industries)",
                "NSE:HDFCBANK (HDFC Bank)",
                "NSE:ICICIBANK (ICICI Bank)",
                "NSE:SBIN (State Bank of India)",
            ],
            index=0,
        )

    chart_symbol = selected_instrument.split(" ")[0]

    tradingview_html = f"""
    <div class="tradingview-widget-container" style="height:620px;width:100%">
      <div id="tv_chart_main" style="height:600px;width:100%"></div>
      <script type="text/javascript" src="https://s3.tradingview.com/tv.js"></script>
      <script type="text/javascript">
      new TradingView.widget({{
        "autosize": true,
        "symbol": "{chart_symbol}",
        "interval": "5",
        "timezone": "Asia/Kolkata",
        "theme": "dark",
        "style": "1",
        "locale": "in",
        "enable_publishing": false,
        "container_id": "tv_chart_main",
        "studies": [
          "Volume@tv-basicstudies",
          "MASimple@tv-basicstudies"
        ]
      }});
      </script>
    </div>
    """
    components.html(tradingview_html, height=620, scrolling=False)

# ------------------------------------------------------------------------------
# TAB 2: LIVE SCANNER, ACTIVE POSITIONS & TRADE AUDIT
# ------------------------------------------------------------------------------
with tab2:
    scan_col, info_col = st.columns([2, 3])
    with scan_col:
        trigger_scan = st.button(
            "🔄 Scan & Re-evaluate All 14 Assets Now", use_container_width=True
        )

    if trigger_scan:
        with st.spinner("Scanning 14 assets across NSE & MCX order-flow..."):
            found_trades = 0
            for asset_name, config in ASSETS.items():
                signal = scan_asset_for_setup(asset_name, config)
                if signal:
                    found_trades += 1
                    if available_capital >= signal["capital_used"]:
                        st.session_state.open_positions.append(signal)
                        st.session_state.trade_history.append(signal)

                        alert_text = (
                            f"🚨 *NEW TRADE TRIGGERED* 🚨\n\n"
                            f"*Asset:* `{signal['asset']}` ({signal['market']})\n"
                            f"*Spot Price:* ₹{signal['spot_price']}\n"
                            f"*Volume Surge:* `{signal['vol_ratio']}x` (Req: {signal['required_mult']}x)\n\n"
                            f"*Leg 1 (Buy ATM):* `{signal['atm_buy']}`\n"
                            f"*Leg 2 (Sell OTM):* `{signal['otm_sell']}`\n"
                            f"*Capital Deployed:* `₹{signal['capital_used']:,}`\n"
                            f"*Stop-Loss:* ₹{signal['stop_loss']}\n"
                            f"*Target (1:2):* ₹{signal['target']}"
                        )
                        send_telegram_alert(alert_text)
                        st.success(
                            f"✅ Trigger on {signal['asset']}! Capital Deployed: ₹{signal['capital_used']:,}"
                        )
                    else:
                        st.warning(
                            f"⚠️ Signal on {signal['asset']}, but insufficient margin (Required: ₹{signal['capital_used']:,})."
                        )

            if found_trades == 0:
                st.info(
                    "Scanner Complete: 14 assets mein kisi par bhi 1.8x/2.2x volume absorption qualify nahi hua."
                )

    # Active Open Positions
    st.subheader("📌 Open Positions & Live Capital Deployed")
    if st.session_state.open_positions:
        pos_rows = []
        for p in st.session_state.open_positions:
            pos_rows.append(
                {
                    "Asset": p["asset"],
                    "Market": p["market"],
                    "Spot Entry": f"₹{p['spot_price']}",
                    "ATM Buy": p["atm_buy"],
                    "OTM Sell": p["otm_sell"],
                    "Capital Deployed": f"₹{p['capital_used']:,}",
                    "Stop Loss": f"₹{p['stop_loss']}",
                    "Target (1:2)": f"₹{p['target']}",
                    "Volume Ratio": f"{p['vol_ratio']}x",
                }
            )
        st.dataframe(pd.DataFrame(pos_rows), use_container_width=True)
    else:
        st.caption("Filhal koi open position active nahi hai.")

    # Trade History Log
    st.subheader("📜 Paper Trading Live History")
    if st.session_state.trade_history:
        hist_rows = []
        for t in st.session_state.trade_history:
            hist_rows.append(
                {
                    "Trigger Time (IST)": t["trigger_time"].strftime(
                        "%Y-%m-%d %H:%M:%S"
                    ),
                    "Asset": t["asset"],
                    "Market": t["market"],
                    "Hedged Spread": f"{t['atm_buy']} / {t['otm_sell']}",
                    "Capital Deployed": f"₹{t['capital_used']:,}",
                    "Status": t["status"],
                }
            )
        st.dataframe(pd.DataFrame(hist_rows), use_container_width=True)
    else:
        st.caption("Aaj koi live trade record nahi hua hai.")

# ------------------------------------------------------------------------------
# TAB 3: 60-DAY HISTORICAL PERFORMANCE & CAPITAL AUDIT
# ------------------------------------------------------------------------------
with tab3:
    if os.path.exists("trades_db.json"):
        with open("trades_db.json", "r") as f:
            db_data = json.load(f)

        if db_data:
            df_hist = pd.DataFrame(db_data)

            total_net_pnl = df_hist["net_pnl"].sum()
            target_hits = df_hist[df_hist["outcome"] == "TARGET_HIT"]
            win_ratio = (
                (len(target_hits) / len(df_hist)) * 100
                if len(df_hist) > 0
                else 0
            )

            # Peak Margin: Single day maximum capital block
            peak_margin = (
                df_hist.groupby("date")["capital_deployed"].sum().max()
            )

            st.subheader("💰 60-Day Capital & Performance Summary")
            k1, k2, k3, k4 = st.columns(4)
            k1.metric(
                "Cumulative Net P&L",
                f"₹{total_net_pnl:,.2f}",
                delta=f"{win_ratio:.1f}% Win Rate",
            )
            k2.metric(
                "Peak Capital Required",
                f"₹{peak_margin:,.2f}",
                help="Pichhle 60 dino mein kisi ek date par laga maximum total margin",
            )
            k3.metric("Total Trades Triggered", f"{len(df_hist)} Trades")
            k4.metric(
                "Avg Capital / Trade",
                f"₹{df_hist['capital_deployed'].mean():,.2f}",
            )

            # Cumulative Equity Curve
            st.subheader("📈 Capital Growth Curve")
            df_hist["Equity_Curve"] = df_hist["net_pnl"].cumsum()
            st.line_chart(df_hist.set_index("entry_time")["Equity_Curve"])

            # Full Ledger Table
            st.subheader("📜 Complete Trade Ledger")
            st.dataframe(
                df_hist[
                    [
                        "entry_time",
                        "asset",
                        "spread",
                        "capital_deployed",
                        "outcome",
                        "net_pnl",
                        "vol_ratio",
                    ]
                ],
                use_container_width=True,
            )
        else:
            st.info("trades_db.json file khali hai.")
    else:
        st.warning("trades_db.json file load nahi ho saki.")
