import os
import math
import calendar
import time as time_module
from datetime import datetime, time, timedelta
import pytz
import pyotp
import pandas as pd
import numpy as np
from SmartApi import SmartConnect

# ==========================================
# 1. CREDENTIALS & CONFIG
# ==========================================
API_KEY = os.environ.get("SMARTAPI_KEY", "").strip()
CLIENT_CODE = (os.environ.get("SMARTAPI_CLIENT_CODE") or os.environ.get("CLIENT_CODE") or "").strip()
PIN = (os.environ.get("SMARTAPI_PIN") or os.environ.get("PIN") or "").strip()
TOTP_KEY = (os.environ.get("SMARTAPI_TOTP_KEY") or os.environ.get("TOTP_KEY") or "").strip()

IST = pytz.timezone("Asia/Kolkata")
ENTRY_TIME = time(9, 20)
FORCE_EXIT_TIME = time(15, 15)  # Mandatory 03:15 PM IST Same-Day Square-Off
LEG_SL_PCT = 0.25               # 25% SL per sold leg + Cost Trail
BROKERAGE_TAX = 120.0           # 4 Legs flat brokerage + STT/GST

# Monthly Expiry Assets (Index + Liquid Stock Options)
MONTHLY_ASSETS = {
    "BANKNIFTY": {"token": "99926009", "lot_size": 30, "step": 100, "hedge_pts": 1000, "margin": 55000},
    "HDFCBANK":  {"token": "1333",     "lot_size": 550, "step": 10,  "hedge_pts": 80,   "margin": 52000},
    "RELIANCE":  {"token": "2885",     "lot_size": 250, "step": 20,  "hedge_pts": 140,  "margin": 50000},
    "SBIN":      {"token": "3045",     "lot_size": 750, "step": 10,  "hedge_pts": 60,   "margin": 48000}
}


# ==========================================
# 2. SMARTAPI SESSION & CANDLE FETCHER
# ==========================================
def init_smartapi():
    obj = SmartConnect(api_key=API_KEY)
    totp = pyotp.TOTP(TOTP_KEY).now()
    data = obj.generateSession(CLIENT_CODE, PIN, totp)
    if not data or not data.get("status"):
        raise ConnectionError(f"SmartAPI Login Failed: {data}")
    return obj


def fetch_5m_candles(obj, token: str, days: int = 60) -> pd.DataFrame:
    to_dt = datetime.now(IST)
    from_dt = to_dt - timedelta(days=days)
    params = {
        "exchange": "NSE",
        "symboltoken": token,
        "interval": "FIVE_MINUTE",
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
# 3. MONTHLY EXPIRY DTE & BLACK-SCHOLES MODEL
# ==========================================
def get_last_thursday(year: int, month: int):
    last_day = calendar.monthrange(year, month)[1]
    dt = datetime(year, month, last_day).date()
    while dt.weekday() != 3:  # 3 = Thursday
        dt -= timedelta(days=1)
    return dt


def get_monthly_dte(curr_date) -> float:
    exp = get_last_thursday(curr_date.year, curr_date.month)
    if curr_date > exp:
        next_m = curr_date.month + 1 if curr_date.month < 12 else 1
        next_y = curr_date.year if curr_date.month < 12 else curr_date.year + 1
        exp = get_last_thursday(next_y, next_m)
    days_left = (exp - curr_date).days
    return 0.35 if days_left <= 0 else float(days_left) + 0.35


def estimate_option_price(spot: float, strike: float, opt_type: str, iv: float, dte: float) -> float:
    T = max(dte / 365.0, 0.0002)
    sigma = max(iv, 0.10)
    r = 0.065

    d1 = (math.log(spot / strike) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)

    def norm_cdf(x):
        return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

    if opt_type == "CE":
        price = spot * norm_cdf(d1) - strike * math.exp(-r * T) * norm_cdf(d2)
    else:
        price = strike * math.exp(-r * T) * norm_cdf(-d2) - spot * norm_cdf(-d1)

    return round(max(price, 1.0), 2)


# ==========================================
# 4. RUN MONTHLY EXPIRY STRADDLE BACKTEST
# ==========================================
def run_monthly_backtest(days: int = 60):
    obj = init_smartapi()
    summary_rows = []
    banknifty_ledger = []

    for symbol, info in MONTHLY_ASSETS.items():
        print(f"Running Monthly Expiry Hedged Straddle on {symbol}...")
        df = fetch_5m_candles(obj, info["token"], days=days)
        if df.empty:
            continue

        df["Trade_Date"] = df.index.date
        daily_stats = df.groupby("Trade_Date").agg(Day_High=("High", "max"), Day_Low=("Low", "min"), Day_Close=("Close", "last"))
        daily_stats["Prev_Range_Pct"] = ((daily_stats["Day_High"] - daily_stats["Day_Low"]) / daily_stats["Day_Close"]).shift(1)
        daily_stats["Rolling_IV"] = (daily_stats["Prev_Range_Pct"].rolling(5).mean() * math.sqrt(252) * 0.85).fillna(0.15).clip(0.11, 0.28)

        lot = info["lot_size"]
        step = info["step"]
        hedge_pts = info["hedge_pts"]
        asset_trades = []

        for t_date, day_df in df.groupby("Trade_Date"):
            if t_date not in daily_stats.index:
                continue

            iv = float(daily_stats.loc[t_date, "Rolling_IV"])
            dte_start = get_monthly_dte(t_date)

            entry_bars = day_df[day_df.index.time == ENTRY_TIME]
            if entry_bars.empty:
                continue

            entry_spot = float(entry_bars.iloc[0]["Close"])
            atm = round(entry_spot / step) * step
            h_ce = atm + hedge_pts
            h_pe = atm - hedge_pts

            sell_ce_in = estimate_option_price(entry_spot, atm, "CE", iv, dte_start)
            sell_pe_in = estimate_option_price(entry_spot, atm, "PE", iv, dte_start)
            buy_ce_in = estimate_option_price(entry_spot, h_ce, "CE", iv, dte_start)
            buy_pe_in = estimate_option_price(entry_spot, h_pe, "PE", iv, dte_start)

            ce_sl = round(sell_ce_in * (1.0 + LEG_SL_PCT), 2)
            pe_sl = round(sell_pe_in * (1.0 + LEG_SL_PCT), 2)

            ce_active, pe_active = True, True
            ce_out, pe_out = sell_ce_in, sell_pe_in
            ce_stat, pe_stat = "EOD", "EOD"

            intraday_bars = day_df[(day_df.index.time > ENTRY_TIME) & (day_df.index.time <= FORCE_EXIT_TIME)]
            if intraday_bars.empty:
                continue

            total_bars = max(len(intraday_bars), 1)
            last_spot = entry_spot
            final_dte = max(dte_start - 0.25, 0.05)

            for idx_b, (_, bar) in enumerate(intraday_bars.iterrows()):
                curr_dte = max(dte_start - (0.25 * ((idx_b + 1) / total_bars)), 0.05)
                final_dte = curr_dte
                s_high, s_low, last_spot = float(bar["High"]), float(bar["Low"]), float(bar["Close"])

                if ce_active and estimate_option_price(s_high, atm, "CE", iv, curr_dte) >= ce_sl:
                    ce_active = False
                    ce_out = ce_sl
                    ce_stat = "SL_HIT" if ce_sl > sell_ce_in else "COST_SL"
                    if pe_active:
                        pe_sl = sell_pe_in

                if pe_active and estimate_option_price(s_low, atm, "PE", iv, curr_dte) >= pe_sl:
                    pe_active = False
                    pe_out = pe_sl
                    pe_stat = "SL_HIT" if pe_sl > sell_pe_in else "COST_SL"
                    if ce_active:
                        ce_sl = sell_ce_in

            if ce_active:
                ce_out = estimate_option_price(last_spot, atm, "CE", iv, final_dte)
            if pe_active:
                pe_out = estimate_option_price(last_spot, atm, "PE", iv, final_dte)

            buy_ce_out = estimate_option_price(last_spot, h_ce, "CE", iv, final_dte)
            buy_pe_out = estimate_option_price(last_spot, h_pe, "PE", iv, final_dte)

            net_pnl = round(
                ((sell_ce_in - ce_out) + (sell_pe_in - pe_out) + (buy_ce_out - buy_ce_in) + (buy_pe_out - buy_pe_in)) * lot
                - BROKERAGE_TAX, 2
            )

            row = {
                "date": str(t_date),
                "day": t_date.strftime("%a"),
                "dte": round(dte_start, 1),
                "atm": atm,
                "spot_move": round(last_spot - entry_spot, 1),
                "capital": info["margin"],
                "outcome": f"CE:{ce_stat} | PE:{pe_stat}",
                "net_pnl": net_pnl
            }
            asset_trades.append(row)

        df_a = pd.DataFrame(asset_trades)
        wins = len(df_a[df_a["net_pnl"] > 0])
        losses = len(df_a[df_a["net_pnl"] <= 0])
        win_rate = (wins / len(df_a)) * 100 if len(df_a) > 0 else 0
        total_pnl = df_a["net_pnl"].sum()

        summary_rows.append({
            "Asset": symbol,
            "Lot_Size": lot,
            "Capital_Used": info["margin"],
            "Days": len(df_a),
            "Wins": wins,
            "Losses": losses,
            "Win_Ratio_%": round(win_rate, 1),
            "Net_PnL_INR": round(total_pnl, 2),
            "ROI_%": round((total_pnl / info["margin"]) * 100, 1)
        })

        if symbol == "BANKNIFTY":
            df_a["cum_pnl"] = df_a["net_pnl"].cumsum().round(2)
            banknifty_ledger = df_a

    print("\n" + "=" * 75)
    print(f"🛡️ MONTHLY EXPIRY HEDGED 09:20 STRADDLE SUMMARY ({days} DAYS)")
    print("=" * 75)
    print(pd.DataFrame(summary_rows).to_string(index=False))

    if len(banknifty_ledger) > 0:
        banknifty_ledger["Month"] = pd.to_datetime(banknifty_ledger["date"]).dt.strftime("%Y-%m")
        print("\n📅 BANKNIFTY MONTHLY P&L BREAKDOWN:")
        m_grp = banknifty_ledger.groupby("Month").agg(
            Days=("net_pnl", "count"),
            Wins=("net_pnl", lambda x: (x > 0).sum()),
            Capital=("capital", "first"),
            Net_PnL=("net_pnl", "sum")
        )
        m_grp["Win_%"] = (m_grp["Wins"] / m_grp["Days"] * 100).round(1)
        print(m_grp.to_string())

        print("\n📜 BANKNIFTY DATE-WISE LEDGER:")
        print(banknifty_ledger[["date", "day", "dte", "atm", "spot_move", "outcome", "net_pnl", "cum_pnl"]].to_string(index=False))


if __name__ == "__main__":
    run_monthly_backtest(days=60)
