import os
import datetime
import requests
import pandas as pd
import yfinance as yf

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

def send_alert(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"}
    try:
        requests.post(url, data=payload, timeout=10)
    except Exception as e:
        print(f"Alert error: {e}")

def run_trading_engine():
    symbol = "^NSEI"
    # Fetch 5-minute data
    df = yf.download(tickers=symbol, period="5d", interval="5m", progress=False)
    if df.empty:
        print("Data currently not available.")
        send_alert("⚠️ *Bot Ran:* Data currently unavailable (Market closed/Weekend). Bot is working properly!")
        return
        
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    # Indicator Calculations
    high_low = df['High'] - df['Low']
    high_close = abs(df['High'] - df['Close'].shift())
    low_close = abs(df['Low'] - df['Close'].shift())
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df['ATR'] = tr.rolling(14).mean()
    df['Vol_SMA'] = df['Volume'].rolling(20).mean()

    latest = df.iloc[-1]
    prev_low = df['Low'].tail(30).iloc[:-2].min()
    
    current_close = float(latest['Close'])
    current_low = float(latest['Low'])
    current_vol = float(latest['Volume'])
    avg_vol = float(latest['Vol_SMA'])
    atr = float(latest['ATR'])

    is_sweep = (current_low < prev_low) and (current_close > prev_low)
    is_volume = current_vol >= (avg_vol * 1.8)
    is_green = current_close > float(latest['Open'])

    if is_sweep and is_volume and is_green:
        sl = current_low
        risk = current_close - sl
        target = current_close + (risk * 2.0)
        
        atm_strike = round(current_close / 50) * 50
        otm_strike = atm_strike + 200

        msg = (
            f"⚡ *LIVE SIGNAL: NIFTY 50*\n\n"
            f"*Spot Price:* {round(current_close, 2)}\n"
            f"• *Setup:* Sweep + 1.8x Volume Absorption\n"
            f"• *Bull Call Spread:* Buy {atm_strike} CE / Sell {otm_strike} CE\n"
            f"• *Hard SL:* {round(sl, 2)} (Risk: {round(risk, 2)} pts)\n"
            f"• *Min 1:2 Target:* {round(target, 2)} (+{round(risk * 2, 2)} pts)\n"
            f"• *Trailing:* 1:1 Breakeven, then 1.8x ATR trail"
        )
        send_alert(msg)
    else:
        print("Engine ran: Conditions not met on this 5m candle.")

if __name__ == "__main__":
    run_trading_engine()
