import streamlit as st
import streamlit.components.v1 as components
import yfinance as yf
import pandas as pd
import requests
from bs4 import BeautifulSoup
from datetime import datetime

st.set_page_config(
    page_title="Govind AI Terminal",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="collapsed"
)

st.markdown("""
<style>
    .metric-card {
        background-color: #151922;
        border: 1px solid #232936;
        border-radius: 10px;
        padding: 12px 14px;
        margin-bottom: 10px;
    }
    .metric-title { font-size: 0.75rem; color: #848e9c; text-transform: uppercase; font-weight: 600; }
    .metric-value { font-size: 1.35rem; font-weight: 700; color: #ffffff; }
    .trade-card {
        background-color: #151922;
        border-radius: 8px;
        padding: 12px;
        border-left: 4px solid #00c853;
        margin-bottom: 10px;
    }
    .trade-card-closed {
        background-color: #151922;
        border-radius: 8px;
        padding: 12px;
        border-left: 4px solid #848e9c;
        margin-bottom: 10px;
    }
    .news-box {
        background-color: #12151c;
        border-radius: 6px;
        padding: 10px 12px;
        margin-bottom: 8px;
        border: 1px solid #1f2430;
    }
</style>
""", unsafe_allow_html=True)

INITIAL_CAPITAL = 2000000.0  # ₹20 Lakh
LOT_SIZE = 25
LOTS = 4
TOTAL_QTY = LOT_SIZE * LOTS

TELEGRAM_BOT_TOKEN = "8620926844:AAF5mKGb820OWgUjRv9uXS4T-6amaCU5EI0"
TELEGRAM_CHAT_ID = "6018016670"

def send_telegram_notification(message: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, data=payload, timeout=5)
    except Exception as e:
        print(f"Telegram error: {e}")

if "virtual_cash" not in st.session_state:
    st.session_state.virtual_cash = INITIAL_CAPITAL
if "realized_pnl" not in st.session_state:
    st.session_state.realized_pnl = 0.0
if "active_position" not in st.session_state:
    st.session_state.active_position = None
if "trade_history" not in st.session_state:
    st.session_state.trade_history = []

@st.cache_data(ttl=60)
def fetch_market_data():
    try:
        df = yf.download(tickers="^NSEI", period="5d", interval="5m", progress=False)
        if not df.empty:
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            return df
    except Exception:
        pass
    return pd.DataFrame()

@st.cache_data(ttl=300)
def fetch_moneycontrol_news():
    news = []
    try:
        url = "https://www.moneycontrol.com/rss/marketreports.xml"
        res = requests.get(url, timeout=5)
        soup = BeautifulSoup(res.content, features="xml")
        for item in soup.findAll("item")[:5]:
            news.append({
                "title": item.title.text,
                "date": item.pubDate.text[:16] if item.pubDate else ""
            })
    except Exception:
        news = [
            {"title": "Nifty consolidates at structural resistance; institutional support intact.", "date": "Live Update"},
            {"title": "Corporate earnings drive momentum across midcaps and largecaps.", "date": "Live Update"}
        ]
    return news

market_df = fetch_market_data()
news_items = fetch_moneycontrol_news()

current_spot = 24950.00
spot_change = 65.50
spot_pct = 0.26

if not market_df.empty:
    current_spot = float(market_df['Close'].iloc[-1])
    day_open = float(market_df['Open'].iloc[0])
    spot_change = round(current_spot - day_open, 2)
    spot_pct = round((spot_change / day_open) * 100, 2)

def run_automated_logic(df: pd.DataFrame, spot: float):
    if df.empty or len(df) < 30:
        return

    high_low = df['High'] - df['Low']
    high_close = abs(df['High'] - df['Close'].shift())
    low_close = abs(df['Low'] - df['Close'].shift())
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df['ATR'] = tr.rolling(14).mean()
    df['Vol_SMA'] = df['Volume'].rolling(20).mean()

    latest = df.iloc[-1]
    prev_low = df['Low'].tail(30).iloc[:-2].min()

    c_close = float(latest['Close'])
    c_low = float(latest['Low'])
    c_vol = float(latest['Volume'])
    avg_vol = float(latest['Vol_SMA'])
    atr = float(latest['ATR'])

    is_sweep = (c_low < prev_low) and (c_close > prev_low)
    is_volume = c_vol >= (avg_vol * 1.8)
    is_green = c_close > float(latest['Open'])

    if st.session_state.active_position is None:
        if is_sweep and is_volume and is_green:
            hard_sl = c_low
            risk_pts = c_close - hard_sl
            if risk_pts <= 5:
                risk_pts = 15.0
                hard_sl = c_close - risk_pts

            target = c_close + (risk_pts * 2.0)
            atm_strike = round(c_close / 50) * 50
            otm_strike = atm_strike + 200

            spread_debit_est = 55.0
            capital_required = spread_debit_est * TOTAL_QTY

            if st.session_state.virtual_cash >= capital_required:
                st.session_state.virtual_cash -= capital_required
                st.session_state.active_position = {
                    "id": f"ORD-{datetime.now().strftime('%H%M%S')}",
                    "strategy": "Bull Call Spread",
                    "buy_leg": f"{atm_strike} CE",
                    "sell_leg": f"{otm_strike} CE",
                    "entry_spot": c_close,
                    "entry_debit": spread_debit_est,
                    "qty": TOTAL_QTY,
                    "hard_sl": round(hard_sl, 2),
                    "target": round(target, 2),
                    "current_sl": round(hard_sl, 2),
                    "trailing_activated": False,
                    "entry_time": datetime.now().strftime("%I:%M %p")
                }
                
                alert_text = (
                    f"⚡ *AUTO-ORDER EXECUTED: NIFTY BULL CALL SPREAD*\n\n"
                    f"• *Spot Entry:* ₹{c_close:,.2f}\n"
                    f"• *Buy Leg:* {atm_strike} CE | *Sell Leg:* {otm_strike} CE\n"
                    f"• *Quantity:* {TOTAL_QTY} (4 Lots)\n"
                    f"• *Hard SL:* ₹{hard_sl:,.2f}\n"
                    f"• *Min 1:2 Target:* ₹{target:,.2f}\n"
                    f"• *Virtual Cash Remaining:* ₹{st.session_state.virtual_cash:,.2f}"
                )
                send_telegram_notification(alert_text)

    elif st.session_state.active_position is not None:
        pos = st.session_state.active_position
        entry = pos["entry_spot"]
        risk = entry - pos["hard_sl"]
        current_sl = pos["current_sl"]

        if not pos["trailing_activated"] and (spot >= entry + risk):
            pos["current_sl"] = entry
            pos["trailing_activated"] = True
            send_telegram_notification(f"🎯 *TRAILING SL ACTIVATED*: SL shifted to Breakeven (₹{entry:,.2f}) on Nifty position.")
        elif pos["trailing_activated"]:
            atr_sl = spot - (1.8 * atr)
            if atr_sl > pos["current_sl"]:
                pos["current_sl"] = round(atr_sl, 2)

        if spot >= pos["target"]:
            profit = (pos["target"] - entry) * 0.40 * pos["qty"]
            st.session_state.virtual_cash += (pos["entry_debit"] * pos["qty"]) + profit
            st.session_state.realized_pnl += profit
            pos["status"] = "TARGET ACHIEVED"
            pos["exit_spot"] = spot
            pos["pnl"] = profit
            st.session_state.trade_history.append(pos)
            st.session_state.active_position = None

            send_telegram_notification(
                f"✅ *TARGET HIT: ORDER CLOSED*\n\n"
                f"• *Exit Spot:* ₹{spot:,.2f}\n"
                f"• *Profit Booked:* +₹{profit:,.2f}\n"
                f"• *Updated Balance:* ₹{(st.session_state.virtual_cash + st.session_state.realized_pnl):,.2f}"
            )
        elif spot <= current_sl:
            loss = (entry - current_sl) * 0.35 * pos["qty"]
            st.session_state.virtual_cash += max(0.0, (pos["entry_debit"] * pos["qty"]) - loss)
            st.session_state.realized_pnl -= loss
            pos["status"] = "SL TRIGGERED"
            pos["exit_spot"] = spot
            pos["pnl"] = -loss
            st.session_state.trade_history.append(pos)
            st.session_state.active_position = None

            send_telegram_notification(
                f"🛑 *STOP-LOSS HIT: POSITION CLOSED*\n\n"
                f"• *Exit Spot:* ₹{spot:,.2f}\n"
                f"• *Loss Realized:* -₹{loss:,.2f}\n"
                f"• *Virtual Cash Balance:* ₹{st.session_state.virtual_cash:,.2f}"
            )

run_automated_logic(market_df, current_spot)

st.title("⚡ AI Paper Trader")

floating_pnl = 0.0
if st.session_state.active_position is not None:
    pos = st.session_state.active_position
    delta = current_spot - pos["entry_spot"]
    floating_pnl = (delta * 0.35) * pos["qty"]

total_portfolio = st.session_state.virtual_cash + st.session_state.realized_pnl + floating_pnl
total_return = total_portfolio - INITIAL_CAPITAL
return_pct = (total_return / INITIAL_CAPITAL) * 100

c1, c2, c3 = st.columns(3)

with c1:
    chg_color = "#00c853" if spot_change >= 0 else "#ff5252"
    pfx = "+" if spot_change >= 0 else ""
    st.markdown(f"""
    <div class="metric-card">
        <div class="metric-title">Nifty 50 Spot</div>
        <div class="metric-value">{current_spot:,.2f}</div>
        <div style="font-size:0.8rem; color:{chg_color}; font-weight:600;">{pfx}{spot_change} ({pfx}{spot_pct}%)</div>
    </div>
    """, unsafe_allow_html=True)

with c2:
    pnl_c = "#00c853" if (floating_pnl + st.session_state.realized_pnl) >= 0 else "#ff5252"
    pnl_pfx = "+" if (floating_pnl + st.session_state.realized_pnl) >= 0 else ""
    st.markdown(f"""
    <div class="metric-card">
        <div class="metric-title">Net P&L (Realized + Open)</div>
        <div class="metric-value" style="color:{pnl_c};">{pnl_pfx}₹{(floating_pnl + st.session_state.realized_pnl):,.2f}</div>
        <div style="font-size:0.8rem; color:#848e9c;">Open: ₹{floating_pnl:,.2f}</div>
    </div>
    """, unsafe_allow_html=True)

with c3:
    st.markdown(f"""
    <div class="metric-card">
        <div class="metric-title">Virtual Capital</div>
        <div class="metric-value">₹{total_portfolio:,.2f}</div>
        <div style="font-size:0.8rem; color:#848e9c;">Seed: ₹20,00,000 ({pfx}{return_pct:.2f}%)</div>
    </div>
    """, unsafe_allow_html=True)

tab1, tab2, tab3 = st.tabs(["📊 Live Chart", "📋 Positions & Execution", "📰 News & Events"])

with tab1:
    st.caption("TradingView Live 5-Minute Candlestick Chart (Nifty 50)")
    tv_code = """
    <div class="tradingview-widget-container">
      <div id="tv_chart" style="height: 480px; width: 100%;"></div>
      <script type="text/javascript" src="https://s3.tradingview.com/tv.js"></script>
      <script type="text/javascript">
      new TradingView.widget({
        "autosize": true,
        "symbol": "INDEX:NIFTY",
        "interval": "5",
        "timezone": "Asia/Kolkata",
        "theme": "dark",
        "style": "1",
        "locale": "in",
        "enable_publishing": false,
        "allow_symbol_change": true,
        "container_id": "tv_chart"
      });
      </script>
    </div>
    """
    components.html(tv_code, height=500)

with tab2:
    st.write("##### Active Virtual Execution")
    pos = st.session_state.active_position
    if pos is not None:
        st.markdown(f"""
        <div class="trade-card">
            <div style="display:flex; justify-content:space-between; font-weight:700;">
                <span style="color:#00c853;">{pos['strategy']} ({pos['qty']} Qty / 4 Lots)</span>
                <span style="color:#00c853; font-size:0.8rem; background-color:#163820; padding:2px 8px; border-radius:4px;">RUNNING</span>
            </div>
            <div style="font-size:0.88rem; color:#d1d4dc; margin: 6px 0;">
                Buy: <b>{pos['buy_leg']}</b> | Sell: <b>{pos['sell_leg']}</b>
            </div>
            <div style="font-size:0.82rem; color:#848e9c;">
                Entry: <b>₹{pos['entry_spot']}</b> | Trailing SL: <b style="color:#ff5252;">₹{pos['current_sl']}</b> | Target: <b style="color:#00c853;">₹{pos['target']}</b>
            </div>
        </div>
        """, unsafe_allow_html=True)
    else:
        st.info("Market scanner is active. Waiting for 5m Liquidity Sweep + 1.8x Volume setup.")

    if st.button("🔄 Check & Re-evaluate Strategy Now", use_container_width=True):
        st.rerun()

    st.write("##### Closed Trade History")
    if st.session_state.trade_history:
        for t in reversed(st.session_state.trade_history):
            pnl_badge = f"+₹{t['pnl']:,.2f}" if t['pnl'] >= 0 else f"-₹{abs(t['pnl']):,.2f}"
            pnl_col = "#00c853" if t['pnl'] >= 0 else "#ff5252"
            st.markdown(f"""
            <div class="trade-card-closed">
                <div style="display:flex; justify-content:space-between; font-size:0.85rem;">
                    <b>{t['strategy']} ({t['buy_leg']})</b>
                    <b style="color:{pnl_col};">{pnl_badge}</b>
                </div>
                <div style="font-size:0.75rem; color:#848e9c; margin-top:3px;">
                    Result: {t['status']} | Exit: ₹{t.get('exit_spot', 0.0)}
                </div>
            </div>
            """, unsafe_allow_html=True)
    else:
        st.caption("No closed trades yet.")

with tab3:
    st.write("##### Moneycontrol High-Impact News")
    for news in news_items:
        st.markdown(f"""
        <div class="news-box">
            <div style="font-size:0.88rem; font-weight:600; color:#e0e3eb;">{news['title']}</div>
            <div style="font-size:0.75rem; color:#848e9c; margin-top:3px;">{news['date']}</div>
        </div>
        """, unsafe_allow_html=True)
