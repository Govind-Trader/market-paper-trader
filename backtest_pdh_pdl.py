import os
import time as time_module
from datetime import datetime, time, timedelta
import pytz
import pyotp
import pandas as pd
from SmartApi import SmartConnect

API_KEY = os.environ.get("SMARTAPI_KEY", "").strip()
CLIENT_CODE = (os.environ.get("SMARTAPI_CLIENT_CODE") or os.environ.get("CLIENT_CODE") or "").strip()
PIN = (os.environ.get("SMARTAPI_PIN") or os.environ.get("PIN") or "").strip()
TOTP_KEY = (os.environ.get("SMARTAPI_TOTP_KEY") or os.environ.get("TOTP_KEY") or "").strip()

IST = pytz.timezone("Asia/Kolkata")
ENTRY_CUTOFF = time(14, 0)
FORCE_EXIT_TIME = time(15, 15)

ASSETS = {
    "TCS": {"token": "11536", "exch": "NSE", "lot_size": 175, "strike_step": 50, "spread_width": 100},
    "BANKNIFTY": {"token": "99926009", "exch": "NSE", "lot_size": 15, "strike_step": 100, "spread_width": 200},
    "FINNIFTY": {"token": "99926037", "exch": "NSE", "lot_size": 25, "strike_step": 50, "spread_width": 100},
    "SBIN": {"token": "3045", "exch": "NSE", "lot_size": 750, "strike_step": 5, "spread_width": 10},
    "LT": {"token": "11483", "exch": "NSE", "lot_size": 150, "strike_step": 20, "spread_width": 40},
    "AXISBANK": {"token": "5900", "exch": "NSE", "lot_size": 625, "strike_step": 10, "spread_width": 20},
    "NIFTY": {"token": "99926000", "exch": "NSE", "lot_size": 25, "strike_step": 50, "spread_width": 100},
    "HDFCBANK": {"token": "1333", "exch": "NSE", "lot_size": 550, "strike_step": 10, "spread_width": 20}
}


def init_smartapi():
    obj = SmartConnect(api_key=API_KEY)
    totp = pyotp.TOTP(TOTP_KEY).now()
    data = obj.generateSession(CLIENT_CODE, PIN, totp)
    if not data or not data.get("status"):
        raise ConnectionError(f"SmartAPI Login Failed: {data}")
    return obj


def fetch_15m_candles(obj, info: dict, days: int = 60) -> pd.DataFrame:
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


def run_pdh_pdl_trailing_backtest(days: int = 60):
    obj = init_smartapi()
    all_trades = []

    for symbol, info in ASSETS.items():
        print(f"Scanning {symbol} (PDH/PDL + Uncapped Trailing Ladder)...")
        df = fetch_15m_candles(obj, info, days=days)
        if len(df) < 50:
            continue

        df["Trade_Date"] = df.index.date
        daily_hl = df.groupby("Trade_Date").agg(Day_High=("High", "max"), Day_Low=("Low", "min"))
        daily_hl["PDH"] = daily_hl["Day_High"].shift(1)
        daily_hl["PDL"] = daily_hl["Day_Low"].shift(1)
        df = df.join(daily_hl[["PDH", "PDL"]], on="Trade_Date")

        traded_dates = set()

        for i in range(2, len(df) - 1):
            c1 = df.iloc[i - 1]
            c2 = df.iloc[i]
            entry_dt = c2.name
            entry_t = entry_dt.time()
            t_date = entry_dt.date()

            if t_date in traded_dates or not (time(9, 45) <= entry_t <= ENTRY_CUTOFF):
                continue

            pdh, pdl = c1["PDH"], c1["PDL"]
            if pd.isna(pdh) or pd.isna(pdl) or c1.name.date() != t_date:
                continue

            side = None
            entry_price = float(c2["Close"])
            initial_sl = 0.0

            if c1["Low"] < pdl and c1["Close"] > pdl and c1["Close"] > c1["Open"]:
                if c2["Close"] > c2["Open"] and c2["High"] > c1["High"] and c2["Close"] > c1["High"]:
                    side = "BULLISH_SWEEP"
                    initial_sl = round(min(c1["Low"], c2["Low"]) - (entry_price * 0.0005), 2)

            elif c1["High"] > pdh and c1["Close"] < pdh and c1["Close"] < c1["Open"]:
                if c2["Close"] < c2["Open"] and c2["Low"] < c1["Low"] and c2["Close"] < c1["Low"]:
                    side = "BEARISH_SWEEP"
                    initial_sl = round(max(c1["High"], c2["High"]) + (entry_price * 0.0005), 2)

            if not side:
                continue

            risk_pts = abs(entry_price - initial_sl)
            if risk_pts <= 0:
                continue

            current_sl = initial_sl
            trail_stage = "INITIAL_SL"
            exit_price = entry_price
            exit_dt = entry_dt
            outcome = "EOD_TREND_EXIT"

            for j in range(i + 1, len(df)):
                bar = df.iloc[j]
                bar_dt = bar.name
                bar_t = bar_dt.time()

                if bar_dt.date() != t_date:
                    break

                if side == "BULLISH_SWEEP":
                    # Trailing Ladder (No fixed target cap)
                    if bar["High"] >= entry_price + (3.0 * risk_pts):
                        current_sl = max(current_sl, round(entry_price + (2.0 * risk_pts), 2))
                        trail_stage = "TRAIL_2R_LOCKED"
                    elif bar["High"] >= entry_price + (2.0 * risk_pts):
                        current_sl = max(current_sl, round(entry_price + (1.0 * risk_pts), 2))
                        trail_stage = "TRAIL_1R_LOCKED"
                    elif bar["High"] >= entry_price + (1.2 * risk_pts):
                        current_sl = max(current_sl, entry_price)
                        trail_stage = "BREAKEVEN_SL"

                    if bar["Low"] <= current_sl:
                        exit_price = current_sl
                        exit_dt = bar_dt
                        outcome = trail_stage
                        break
                else:
                    if bar["Low"] <= entry_price - (3.0 * risk_pts):
                        current_sl = min(current_sl, round(entry_price - (2.0 * risk_pts), 2))
                        trail_stage = "TRAIL_2R_LOCKED"
                    elif bar["Low"] <= entry_price - (2.0 * risk_pts):
                        current_sl = min(current_sl, round(entry_price - (1.0 * risk_pts), 2))
                        trail_stage = "TRAIL_1R_LOCKED"
                    elif bar["Low"] <= entry_price - (1.2 * risk_pts):
                        current_sl = min(current_sl, entry_price)
                        trail_stage = "BREAKEVEN_SL"

                    if bar["High"] >= current_sl:
                        exit_price = current_sl
                        exit_dt = bar_dt
                        outcome = trail_stage
                        break

                if bar_t >= FORCE_EXIT_TIME:
                    exit_price = float(bar["Close"])
                    exit_dt = bar_dt
                    outcome = "EOD_TREND_EXIT"
                    break

            spot_move = (exit_price - entry_price) if side == "BULLISH_SWEEP" else (entry_price - exit_price)
            rr_achieved = round(spot_move / risk_pts, 2)
            net_pnl = round((spot_move * 0.35 * info["lot_size"]) - 60.0, 2)

            all_trades.append({
                "entry_time": entry_dt.strftime("%Y-%m-%d %H:%M"),
                "exit_time": exit_dt.strftime("%Y-%m-%d %H:%M"),
                "asset": symbol,
                "side": side,
                "rr": rr_achieved,
                "outcome": outcome,
                "net_pnl": net_pnl
            })
            traded_dates.add(t_date)

    df_res = pd.DataFrame(all_trades).sort_values("entry_time").reset_index(drop=True)
    df_res["cum_pnl"] = df_res["net_pnl"].cumsum().round(2)

    wins = len(df_res[df_res["net_pnl"] > 0])
    losses = len(df_res[df_res["net_pnl"] <= 0])
    win_rate = (wins / len(df_res)) * 100
    avg_win = df_res[df_res["net_pnl"] > 0]["net_pnl"].mean() if wins > 0 else 0
    avg_loss = abs(df_res[df_res["net_pnl"] <= 0]["net_pnl"].mean()) if losses > 0 else 0

    print("\n" + "=" * 65)
    print(f"🚀 PDH/PDL + UNCAPPED TRAILING LADDER BACKTEST ({days} DAYS)")
    print("=" * 65)
    print(f"Total Trades       : {len(df_res)} ({wins} Wins / {losses} Losses)")
    print(f"Win Rate           : {win_rate:.1f}%")
    print(f"Net P&L            : ₹{df_res['net_pnl'].sum():,.2f}")
    print(f"Avg Win / Avg Loss : +₹{avg_win:,.2f} / -₹{avg_loss:,.2f}")
    print("=" * 65)
    print("\nAsset-Wise Summary:")
    print(df_res.groupby("asset").agg(
        Trades=("net_pnl", "count"),
        Wins=("net_pnl", lambda x: (x > 0).sum()),
        Avg_RR=("rr", "mean"),
        Net_PnL=("net_pnl", "sum")
    ).round(2))
    print("\nTop Winning Trades (R:R > 1.8):")
    print(df_res[df_res["rr"] > 1.8][["entry_time", "asset", "side", "rr", "outcome", "net_pnl"]].to_string(index=False))


if __name__ == "__main__":
    run_pdh_pdl_trailing_backtest(days=60)
