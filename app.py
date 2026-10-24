import streamlit as st
import yfinance as yf
import pandas as pd
import requests
from bs4 import BeautifulSoup
from datetime import datetime
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# Page configuration
st.set_page_config(
    page_title="Govind Multi-Asset AI Terminal",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# Custom Financial Dark Theme
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

INITIAL_CAPITAL = 2000000.0  # ₹20 Lakh Virtual Capital
TELEGRAM_BOT_TOKEN = "8620926844:AAF5mKGb820OWgUjRv9uXS4T-6amaCU5EI0"
TELEGRAM_CHAT_ID = "6018016670"

# 2 Indices + 10 Top Liquidity F&O Stocks
WATCHLIST = {
    "^NSEI": {"name": "NIFTY 50", "type": "INDEX", "lot": 100, "step": 50},
    "^NSEBANK": {"name": "BANK NIFTY", "type": "INDEX", "lot": 60, "step": 100},
    "RELIANCE.NS": {"name": "RELIANCE", "type": "STOCK", "lot": 100, "step": 20},
    "HDFCBANK.NS": {"name": "HDFC BANK", "type": "STOCK", "lot": 100, "step": 10},
    "ICICIBANK.NS": {"name": "ICICI BANK", "type": "STOCK", "lot": 100, "step": 10},
    "SBIN.NS": {"name": "SBI", "type": "STOCK", "lot": 100, "step": 5},
    "TCS.NS": {"name": "TCS", "type": "STOCK", "lot": 50, "step": 20},
    "INFY.NS": {"name": "INFOSYS", "type": "STOCK", "lot": 100, "step": 10},
    "TATAMOTORS.NS": {"name": "TATA MOTORS", "type": "STOCK", "lot": 100, "step": 5},
    "ITC.NS": {"name": "ITC", "type": "STOCK", "lot": 200, "step": 5},
    "BHARTIARTL.NS": {"name": "BHARTI AIRTEL", "type": "STOCK", "lot": 100, "step": 10},
    "LT.NS": {"name": "L&T", "type": "STOCK", "lot": 50, "step": 20}
}

def send_telegram_notification(message: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, data=payload, timeout=5)
    except Exception as e:
        print(f"Telegram error: {e}")

# Session state initialization
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
                "title
