from datetime import datetime, time
import math
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
# Puraane bot ka Token aur Chat ID yahan daalein
# Agar Streamlit Secrets use kar rahe hain toh yeh automatically detect ho jayega
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
    """Checks trading hours in Indian Standard Time (IST)."""
    ist = pytz.timezone("Asia/Kolkata")
    now_time = datetime.now(ist).time()

    if market_type == "NSE":
        return time(9, 15) <= now_time <= time(15, 30)
    elif market_type == "MCX":
        # MCX trades from 09:00 AM to 11:30 PM IST
        return time(9, 0) <= now_time <= time(23, 30)
    return False


def get_volume_multiplier(asset_name: str) -> float:
    """MCX Crude Oil US opening overlap (06:00 PM - 09:30 PM IST) ke dauran 2.2x volume threshold require karta hai."""
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

        # 20-period Volume Moving Average
        df["Vol_MA20"] = df["Volume"].rolling(window=20).mean()

        # Pichhle 30 candles ka lowest swing point (excluding trigger candle)
        prev_30_low = df["Low"].iloc[-31:-1].min()

        # Trigger candle parameters
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

        # 1. Sweep Rule: Low undercut previous 30-low, but closed back above it
        is_sweep = (c_low < prev_30_low) and (c_close > prev_30_low)
        # 2. Bullish Rejection Candle
        is_green = c_close > c_open
        # 3. Institutional Volume Absorption Spike
        has_volume = c_vol >= (avg_vol * required_mult) if avg_vol > 0 else False

        if is_sweep and is_green and has_volume:
            # Bull Call Spread strike computation
            atm_strike = int(
                round(c_close / config["strike_step"]) * config["strike_step"]
            )
            otm_strike = atm_strike + config["spread_width"]

            # Risk-Reward 1:2 Setup
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
                "capital_used": config["margin_per_lot"],  # Capital Tracking
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

# Header Metric Bar (Capital Deployed & Free Margin tracking)
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

# Tab Navigation
tab1, tab2 = st.tabs(["📊 Interactive Live Charts", "⚙️ Scanner & Trade Log"])

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
                    # Capital Margin Check
                    if available_capital >= signal["capital_used"]:
                        st.session_state.open_positions.append(signal)
                        st.session_state.trade_history.append(signal)

                        # Telegram Instant Notification Payload
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

    # 1. ACTIVE OPEN POSITIONS
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

    # 2. COMPLETE TRADE AUDIT LOG
    st.subheader("📜 Paper Trading Complete Audit History")
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
        st.caption("Pichhla koi trade record nahi hai.")
