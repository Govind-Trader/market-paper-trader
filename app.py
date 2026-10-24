import streamlit as st
import streamlit.components.v1 as components
import yfinance as yf
import pandas as pd
import requests
from bs4 import BeautifulSoup
from datetime import datetime

st.set_page_config(
    page_title="Govind Multi-Asset AI Terminal",
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
    .metric-title { font-size: 0.72rem; color: #848e9c; text-transform: uppercase; font-weight: 600; }
    .metric-value { font-size: 1.25rem; font-weight: 700; color: #ffffff; }
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
TELEGRAM_BOT_TOKEN = "8620926844:AAF5mKGb820OWgUjRv9uXS4T-6amaCU5EI0"
TELEGRAM_CHAT_ID = "6018016670"

# 2 Indices + Top 10 Stocks
WATCHLIST = {
    "^NSEI": {"name": "NIFTY 50", "type": "INDEX", "tv": "INDEX:NIFTY", "lot": 100, "step": 50},
    "^NSEBANK": {"name": "BANK NIFTY", "type": "INDEX", "tv": "INDEX:NIFTYBANK", "lot": 60, "step": 100},
    "RELIANCE.NS": {"name": "RELIANCE", "type": "STOCK", "tv": "NSE:RELIANCE", "lot": 100, "step": 20},
    "HDFCBANK.NS": {"name": "HDFC BANK", "type": "STOCK", "tv": "NSE:HDFCBANK", "lot": 100, "step": 10},
    "ICICIBANK.NS": {"name": "ICICI BANK", "type": "STOCK", "tv": "NSE:ICICIBANK", "lot": 100, "step": 10},
    "SBIN.NS": {"name": "SBI", "type": "STOCK", "tv": "NSE:SBIN", "lot": 100, "step": 5},
    "TCS.NS": {"name": "TCS", "type": "STOCK", "tv": "NSE:TCS", "lot": 50, "step": 20},
    "INFY.NS": {"name": "INFOSYS", "type": "STOCK", "tv": "NSE:INFY", "lot": 100, "step": 10},
    "TATAMOTORS.NS": {"name": "TATA MOTORS", "type": "STOCK", "tv": "NSE:TATAMOTORS", "lot": 100, "step": 5},
    "ITC.NS": {"name": "ITC", "type": "STOCK", "tv": "NSE:ITC", "lot": 200, "step": 5},
    "BHARTIARTL.NS": {"name": "BHARTI AIRTEL", "type": "STOCK", "tv": "NSE:BHARTIARTL", "lot": 100, "step": 10},
    "LT.NS": {"name": "L&T", "type": "STOCK", "tv": "NSE:LT", "lot": 50, "step": 20}
}

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
if "active_positions" not in st.session_state:
    st.session_state.active_positions = {}
if "trade_history" not in st.session_state:
    st.session_state.trade_history = []

@st.cache_data(ttl=60)
def fetch_all_tickers():
    tickers = " ".join(WATCHLIST.keys())
    data_dict = {}
    try:
        raw_df = yf.download(tickers=tickers, period="5d", interval="5m", group_by='ticker', progress=False)
        for ticker in WATCHLIST.keys():
            if ticker in raw_df:
                sub = raw_df[ticker].dropna()
                if not sub.empty:
                    data_dict[ticker] = sub
    except Exception:
        pass
    return data_dict

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
        news = [{"title": "Markets consolidate at structural swing zones.", "date": "Live Update"}]
    return news

data_map = fetch_all_tickers()
news_items = fetch_moneycontrol_news()

# Evaluation Loop
for ticker, meta in WATCHLIST.items():
    if ticker not in data_map or len(data_map[ticker]) < 30:
        continue
    
    df = data_map[ticker].copy()
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

    # Entry Check
    if ticker not in st.session_state.active_positions:
        if is_sweep and is_volume and is_green:
            hard_sl = c_low
            risk_pts = c_close - hard_sl
            if risk_pts <= 2:
                risk_pts = max(5.0, c_close * 0.005)
                hard_sl = c_close - risk_pts

            target = c_close + (risk_pts * 2.0)
            step = meta["step"]
            atm_strike = round(c_close / step) * step
            otm_strike = atm_strike + (step * 2)

            spread_debit_est = max(15.0, risk_pts * 0.4)
            capital_required = spread_debit_est * meta["lot"]

            if st.session_state.virtual_cash >= capital_required:
                st.session_state.virtual_cash -= capital_required
                st.session_state.active_positions[ticker] = {
                    "symbol": meta["name"],
                    "ticker": ticker,
                    "type": meta["type"],
                    "strategy": "Bull Call Spread",
                    "buy_leg": f"{atm_strike} CE",
                    "sell_leg": f"{otm_strike} CE",
                    "entry_spot": c_close,
                    "entry_debit": spread_debit_est,
                    "qty": meta["lot"],
                    "hard_sl": round(hard_sl, 2),
                    "target": round(target, 2),
                    "current_sl": round(hard_sl, 2),
                    "trailing_activated": False,
                    "entry_time": datetime.now().strftime("%I:%M %p")
                }
                
                alert_text = (
                    f"⚡ *AUTO-ORDER EXECUTED: {meta['name']}*\n\n"
                    f"• *Spot Entry:* ₹{c_close:,.2f}\n"
                    f"• *Structure:* {atm_strike} CE / {otm_strike} CE\n"
                    f"• *Quantity:* {meta['lot']}\n"
                    f"• *Hard SL:* ₹{hard_sl:,.2f}\n"
                    f"• *1:2 Target:* ₹{target:,.2f}"
                )
                send_telegram_notification(alert_text)

    # Exit Check
    else:
        pos = st.session_state.active_positions[ticker]
        entry = pos["entry_spot"]
        risk = entry - pos["hard_sl"]

        if not pos["trailing_activated"] and (c_close >= entry + risk):
            pos["current_sl"] = entry
            pos["trailing_activated"] = True
            send_telegram_notification(f"🎯 *TRAILING SL ACTIVATED*: {pos['symbol']} SL moved to breakeven (₹{entry:,.2f}).")
        elif pos["trailing_activated"]:
            atr_sl = c_close - (1.8 * atr)
            if atr_sl > pos["current_sl"]:
                pos["current_sl"] = round(atr_sl, 2)

        if c_close >= pos["target"]:
            profit = (pos["target"] - entry) * 0.40 * pos["qty"]
            st.session_state.virtual_cash += (pos["entry_debit"] * pos["qty"]) + profit
            st.session_state.realized_pnl += profit
            pos["status"] = "TARGET ACHIEVED"
            pos["exit_spot"] = c_close
            pos["pnl"] = profit
            st.session_state.trade_history.append(pos)
            del st.session_state.active_positions[ticker]

            send_telegram_notification(f"✅ *TARGET HIT: {pos['symbol']}* | Profit: +₹{profit:,.2f}")
        elif c_close <= pos["current_sl"]:
            loss = (entry - pos["current_sl"]) * 0.35 * pos["qty"]
            st.session_state.virtual_cash += max(0.0, (pos["entry_debit"] * pos["qty"]) - loss)
            st.session_state.realized_pnl -= loss
            pos["status"] = "SL TRIGGERED"
            pos["exit_spot"] = c_close
            pos["pnl"] = -loss
            st.session_state.trade_history.append(pos)
            del st.session_state.active_positions[ticker]

            send_telegram_notification(f"🛑 *STOP-LOSS HIT: {pos['symbol']}* | Loss: -₹{loss:,.2f}")

st.title("⚡ Multi-Asset Paper Trader")

# Floating P&L calculation
floating_pnl = 0.0
for ticker, pos in st.session_state.active_positions.items():
    if ticker in data_map and not data_map[ticker].empty:
        curr_price = float(data_map[ticker]['Close'].iloc[-1])
        floating_pnl += (curr_price - pos["entry_spot"]) * 0.35 * pos["qty"]

total_portfolio = st.session_state.virtual_cash + st.session_state.realized_pnl + floating_pnl
total_return = total_portfolio - INITIAL_CAPITAL
return_pct = (total_return / INITIAL_CAPITAL) * 100

c1, c2, c3 = st.columns(3)
with c1:
    st.markdown(f"""
    <div class="metric-card">
        <div class="metric-title">Active Universe</div>
        <div class="metric-value">12 Assets</div>
        <div style="font-size:0.8rem; color:#848e9c;">2 Indices + 10 Liquid Stocks</div>
    </div>
    """, unsafe_allow_html=True)

with c2:
    pnl_c = "#00c853" if (floating_pnl + st.session_state.realized_pnl) >= 0 else "#ff5252"
    pnl_pfx = "+" if (floating_pnl + st.session_state.realized_pnl) >= 0 else ""
    st.markdown(f"""
    <div class="metric-card">
        <div class="metric-title">Total P&L (Net)</div>
        <div class="metric-value" style="color:{pnl_c};">{pnl_pfx}₹{(floating_pnl + st.session_state.realized_pnl):,.2f}</div>
        <div style="font-size:0.8rem; color:#848e9c;">Open Pos: {len(st.session_state.active_positions)}</div>
    </div>
    """, unsafe_allow_html=True)

with c3:
    st.markdown(f"""
    <div class="metric-card">
        <div class="metric-title">Portfolio Capital</div>
        <div class="metric-value">₹{total_portfolio:,.2f}</div>
        <div style="font-size:0.8rem; color:#848e9c;">Seed: ₹20 Lakh ({pnl_pfx}{return_pct:.2f}%)</div>
    </div>
    """, unsafe_allow_html=True)

tab1, tab2, tab3 = st.tabs(["📊 Interactive Charts", "📋 Open & Closed Trades", "📰 News"])

with tab1:
    selected_asset = st.selectbox("Chart Asset Chuniye:", list(WATCHLIST.keys()), format_func=lambda x: WATCHLIST[x]["name"])
    tv_symbol = WATCHLIST[selected_asset]["tv"]
    
    tv_code = f"""
    <div class="tradingview-widget-container">
      <div id="tv_chart" style="height: 480px; width: 100%;"></div>
      <script type="text/javascript" src="https://s3.tradingview.com/tv.js"></script>
      <script type="text/javascript">
      new TradingView.widget({{
        "autosize": true,
        "symbol": "{tv_symbol}",
        "interval": "5",
        "timezone": "Asia/Kolkata",
        "theme": "dark",
        "style": "1",
        "locale": "in",
        "enable_publishing": false,
        "allow_symbol_change": true,
        "container_id": "tv_chart"
      }});
      </script>
    </div>
    """
    components.html(tv_code, height=500)

with tab2:
    st.write("##### Active Virtual Positions")
    if st.session_state.active_positions:
        for tkr, pos in st.session_state.active_positions.items():
            st.markdown(f"""
            <div class="trade-card">
                <div style="display:flex; justify-content:space-between; font-weight:700;">
                    <span style="color:#00c853;">{pos['symbol']} — {pos['strategy']} ({pos['qty']} Qty)</span>
                    <span style="color:#00c853; font-size:0.8rem; background-color:#163820; padding:2px 8px; border-radius:4px;">RUNNING</span>
                </div>
                <div style="font-size:0.88rem; color:#d1d4dc; margin: 6px 0;">
                    Spread: <b>{pos['buy_leg']}</b> / <b>{pos['sell_leg']}</b>
                </div>
                <div style="font-size:0.82rem; color:#848e9c;">
                    Entry: <b>₹{pos['entry_spot']}</b> | Trailing SL: <b style="color:#ff5252;">₹{pos['current_sl']}</b> | Target: <b style="color:#00c853;">₹{pos['target']}</b>
                </div>
            </div>
            """, unsafe_allow_html=True)
    else:
        st.info("Scanner active hai (Scanning 12 assets: Nifty, BankNifty, Reliance, HDFCBANK, ICICIBANK, SBIN, TCS, INFY, TATAMOTORS, ITC, BHARTIARTL, LT).")

    if st.button("🔄 Scan & Re-evaluate All 12 Assets Now", use_container_width=True):
        st.rerun()

    st.write("##### Closed Trade History")
    if st.session_state.trade_history:
        for t in reversed(st.session_state.trade_history):
            pnl_badge = f"+₹{t['pnl']:,.2f}" if t['pnl'] >= 0 else f"-₹{abs(t['pnl']):,.2f}"
            pnl_col = "#00c853" if t['pnl'] >= 0 else "#ff5252"
            st.markdown(f"""
            <div class="trade-card-closed">
                <div style="display:flex; justify-content:space-between; font-size:0.85rem;">
                    <b>{t['symbol']} ({t['buy_leg']})</b>
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
    st.write("##### Market News Feed")
    for news in news_items:
        st.markdown(f"""
        <div class="news-box">
            <div style="font-size:0.88rem; font-weight:600; color:#e0e3eb;">{news['title']}</div>
            <div style="font-size:0.75rem; color:#848e9c; margin-top:3px;">{news['date']}</div>
        </div>
        """, unsafe_allow_html=True)
