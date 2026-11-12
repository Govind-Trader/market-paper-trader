import os
import time as time_module
from datetime import datetime, time, timedelta
import pytz
import pyotp
import pandas as pd
from SmartApi import SmartConnect

# ==========================================
# 1. CREDENTIALS (Environment ya direct paste)
# ==========================================
API_KEY = os.environ.get("SMARTAPI_KEY", "").strip()
CLIENT_CODE = os.environ.get("SMARTAPI_CLIENT_CODE") or os.environ.get("CLIENT_CODE", "").strip()
PIN = os.environ.get("SMARTAPI_PIN") or os.environ.get("PIN", "").strip()
TOTP_KEY = os.environ.get("SMARTAPI_TOTP_KEY") or os.environ.get("TOTP_KEY", "").strip()

IST = pytz.timezone("Asia/Kolkata")
ENTRY_CUTOFF = time(14, 0)      # 02:00 PM IST ke baad no new entry (15m move ke liye time chahiye)
FORCE_EXIT_TIME = time(15, 15)  # 03:15 PM IST mandatory same-day square-off

ASSETS = {
    "TCS": {"token": "11536", "exch": "NSE", "lot_size": 175, "strike_step": 50, "spread_width": 100, "margin": 65000, "is_index": False},
    "BANKNIFTY": {"token": "99926009", "exch": "NSE", "lot_size": 15, "strike_step": 100, "spread_width": 200, "margin": 50000, "is_index": True},
    "FINNIFTY": {"token": "99926037", "exch": "NSE", "lot_size": 25, "strike_step": 50, "spread_width": 100, "margin": 45000, "is_index": True},
    "SBIN": {"token": "3045", "exch": "NSE", "lot_size": 750, "strike_step": 5, "spread_width": 10, "margin": 50000, "is_index": False},
    "LT": {"token": "11483", "exch": "NSE", "lot_size": 150, "strike_step": 20, "spread_width": 40, "margin": 60000, "is_index": False},
    "AXISBANK": {"token": "5900", "exch": "NSE", "lot_size": 625, "strike_step": 10, "spread_width": 20, "margin": 55000, "is_index": False},
    "NIFTY": {"token": "99926000", "exch": "NSE", "lot_size": 25, "strike_step": 50, "spread_width": 100, "margin": 45000, "is_index": True},
    "HDFCBANK": {"token": "1333", "exch": "NSE", "lot_size": 550, "strike_step": 10, "spread_width": 20, "margin": 60000, "is_index": False}
}


# ==========================================
# 2. SMARTAPI DATA FETCHER (15-MINUTE CANDLES)
# ==========================================
def init_smartapi():
    obj = SmartConnect(api_key=API_KEY)
    totp = pyotp.TOTP(TOTP_KEY).now()
    data = obj.generateSession(CLIENT_CODE, PIN, totp)
    if not data or not data.get("status"):
        raise ConnectionError(f"SmartAPI Login Failed: {data}")
    return obj


def fetch_15m_candles(obj, symbol: str, info: dict, days: int = 60) -> pd.DataFrame:
    to_dt = datetime.now(IST)
    from_dt = to_dt - timedelta(days=days)
    params = {
        "exchange": info["exch"],
        "symboltoken": info["token"],
        "interval": "FIFTEEN_MINUTE",
        "fromdate": from_dt.strftime("%Y-%m-%d 09:15"),
        "todate": to_dt.strftime("%Y-%m-%d 15:30")
    }
    for attempt in range(3):
        try:
            time_module.sleep(0.55 + (attempt * 0.5))
            res = obj.getCandleData(params)
            if res and res.get("status") and res.get("data"):
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
# 3. GAUTAM JHA PDH/PDL + 15M SWEEP BACKTEST
# ==========================================
def run_pdh_pdl_backtest(days: int = 60):
    obj = init_smartapi()
    all_trades = []

    for symbol, info in ASSETS.items():
        print(f"Scanning {symbol} (15-Min + PDH/PDL)...")
        df = fetch_15m_candles(obj, symbol, info, days=days)
        if len(df) < 50:
            print(f"⚠️ {symbol}: Insufficient data.")
            continue

        # Calculate Static Previous Day High (PDH) & Previous Day Low (PDL)
        df["Trade_Date"] = df.index.date
        daily_hl = df.groupby("Trade_Date").agg(Day_High=("High", "max"), Day_Low=("Low", "min"))
        daily_hl["PDH"] = daily_hl["Day_High"].shift(1)
        daily_hl["PDL"] = daily_hl["Day_Low"].shift(1)

        df = df.join(daily_hl[["PDH", "PDL"]], on="Trade_Date")
        df["Vol_MA20"] = df["Volume"].rolling(20).mean()

        traded_dates = set()

        for i in range(2, len(df) - 1):
            c1 = df.iloc[i - 1]  # Sweep Candle (15-min)
            c2 = df.iloc[i]      # Confirmation Candle (15-min)
            entry_dt = c2.name
            entry_t = entry_dt.time()
            t_date = entry_dt.date()

            # Max 1 high-conviction trade per asset per day, before 02:00 PM IST
            if t_date in traded_dates or not (time(9, 30) <= entry_t <= ENTRY_CUTOFF):
                continue

            pdh = c1["PDH"]
            pdl = c1["PDL"]
            if pd.isna(pdh) or pd.isna(pdl):
                continue

            # Ensure c1 and c2 belong to the same trading day
            if c1.name.date() != t_date:
                continue

            side = None
            entry_price = float(c2["Close"])
            initial_sl = 0.0

            # 1. BULLISH PDL SWEEP (Gautam Jha Exact Setup):
            # Candle 1 sweeps below PDL but closes ABOVE PDL (Green), Candle 2 breaks Candle 1 High
            if c1["Low"] < pdl and c1["Close"] > pdl and c1["Close"] > c1["Open"]:
                if c2["Close"] > c2["Open"] and c2["High"] > c1["High"] and c2["Close"] > c1["High"]:
                    side = "BULLISH_SWEEP"
                    initial_sl = round(min(c1["Low"], c2["Low"]) - (entry_price * 0.0005), 2)

            # 2. BEARISH PDH SWEEP (Gautam Jha Exact Setup):
            # Candle 1 sweeps above PDH but closes BELOW PDH (Red), Candle 2 breaks Candle 1 Low
            elif c1["High"] > pdh and c1["Close"] < pdh and c1["Close"] < c1["Open"]:
                if c2["Close"] < c2["Open"] and c2["Low"] < c1["Low"] and c2["Close"] < c1["Low"]:
                    side = "BEARISH_SWEEP"
                    initial_sl = round(max(c1["High"], c2["High"]) + (entry_price * 0.0005), 2)

            if not side:
                continue

            risk_pts = abs(entry_price - initial_sl)
            if risk_pts <= 0:
                continue

            # 1:1.80 R:R Target + 1:1.20 Breakeven Trailing SL
            target = round(entry_price + (1.80 * risk_pts), 2) if side == "BULLISH_SWEEP" else round(entry_price - (1.80 * risk_pts), 2)
            current_sl = initial_sl

            exit_price = entry_price
            exit_dt = entry_dt
            outcome = "EOD_CLOSED"

            for j in range(i + 1, len(df)):
                bar = df.iloc[j]
                bar_dt = bar.name
                bar_t = bar_dt.time()

                # Safety: never cross into next day
                if bar_dt.date() != t_date:
                    break

                if side == "BULLISH_SWEEP":
                    if bar["High"] >= entry_price + (1.20 * risk_pts):
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
                    if bar["Low"] <= entry_price - (1.20 * risk_pts):
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
                "date": str(t_date),
                "entry_time": entry_dt.strftime("%Y-%m-%d %H:%M"),
                "exit_time": exit_dt.strftime("%Y-%m-%d %H:%M"),
                "asset": symbol,
                "side": side,
                "spread": spread_str,
                "pdh": round(pdh, 2),
                "pdl": round(pdl, 2),
                "entry_price": round(entry_price, 2),
                "exit_price": round(exit_price, 2),
                "outcome": outcome,
                "net_pnl": net_pnl
            })
            traded_dates.add(t_date)

    if not all_trades:
        print("No trades found.")
        return

    df_res = pd.DataFrame(all_trades).sort_values("entry_time").reset_index(drop=True)
    df_res["cum_pnl"] = df_res["net_pnl"].cumsum().round(2)

    wins = len(df_res[df_res["net_pnl"] > 0])
    losses = len(df_res[df_res["net_pnl"] <= 0])
    win_rate = (wins / len(df_res)) * 100

    print("\n" + "=" * 55)
    print(f"📊 PDH/PDL + 15-MIN SWEEP BACKTEST REPORT ({days} DAYS)")
    print("=" * 55)
    print(f"Total Trades : {len(df_res)} ({wins} Wins / {losses} Losses)")
    print(f"Win Rate     : {win_rate:.1f}%")
    print(f"Net P&L      : ₹{df_res['net_pnl'].sum():,.2f}")
    print("=" * 55)
    print("\nAsset-Wise Summary:")
    print(df_res.groupby("asset").agg(
        Trades=("net_pnl", "count"),
        Wins=("net_pnl", lambda x: (x > 0).sum()),
        Net_PnL=("net_pnl", "sum")
    ))
    print("\nTrade List:")
    print(df_res[["entry_time", "exit_time", "asset", "side", "outcome", "net_pnl", "cum_pnl"]].to_string(index=False))

    df_res.to_csv("pdh_pdl_15m_backtest.csv", index=False)
    print("\n✅ Saved full report to pdh_pdl_15m_backtest.csv")


if __name__ == "__main__":
    run_pdh_pdl_backtest(days=60)
