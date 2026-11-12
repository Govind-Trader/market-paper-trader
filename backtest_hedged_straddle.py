import os
import math
import time as time_module
from datetime import datetime, time, timedelta
import pytz
import pyotp
import pandas as pd
import numpy as np
from SmartApi import SmartConnect

# ==========================================
# 1. CREDENTIALS (GitHub Secrets Mapping)
# ==========================================
API_KEY = os.environ.get("SMARTAPI_KEY", "").strip()
CLIENT_CODE = (os.environ.get("SMARTAPI_CLIENT_CODE") or os.environ.get("CLIENT_CODE") or "").strip()
PIN = (os.environ.get("SMARTAPI_PIN") or os.environ.get("PIN") or "").strip()
TOTP_KEY = (os.environ.get("SMARTAPI_TOTP_KEY") or os.environ.get("TOTP_KEY") or "").strip()

IST = pytz.timezone("Asia/Kolkata")
ENTRY_TIME = time(9, 20)
FORCE_EXIT_TIME = time(15, 15)  # Mandatory 03:15 PM IST Same-Day Square-Off

NIFTY_TOKEN = "99926000"
LOT_SIZE = 75         # 1 Lot NIFTY = 75 Qty
STRIKE_STEP = 50
HEDGE_DISTANCE = 500  # 500 pts OTM CE & PE Buy Hedge
LEG_SL_PCT = 0.25     # 25% Stop-Loss per sold leg (with Cost Trail)
BROKERAGE_TAX = 120.0 # 4 Legs (2 Sell + 2 Buy) flat charges per day


# ==========================================
# 2. SMARTAPI DATA FETCHER (NIFTY 5-MIN)
# ==========================================
def init_smartapi():
    obj = SmartConnect(api_key=API_KEY)
    totp = pyotp.TOTP(TOTP_KEY).now()
    data = obj.generateSession(CLIENT_CODE, PIN, totp)
    if not data or not data.get("status"):
        raise ConnectionError(f"SmartAPI Login Failed: {data}")
    return obj


def fetch_nifty_5m(obj, days: int = 60) -> pd.DataFrame:
    to_dt = datetime.now(IST)
    from_dt = to_dt - timedelta(days=days)
    params = {
        "exchange": "NSE",
        "symboltoken": NIFTY_TOKEN,
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
# 3. REALISTIC OPTION PRICING & DECAY MODEL
# ==========================================
def estimate_option_price(spot: float, strike: float, opt_type: str, iv: float, days_to_expiry: float) -> float:
    """Black-Scholes approximation for NIFTY weekly options."""
    T = max(days_to_expiry / 365.0, 0.0002)
    sigma = max(iv, 0.09)
    r = 0.065

    d1 = (math.log(spot / strike) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)

    def norm_cdf(x):
        return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

    if opt_type == "CE":
        price = spot * norm_cdf(d1) - strike * math.exp(-r * T) * norm_cdf(d2)
    else:
        price = strike * math.exp(-r * T) * norm_cdf(-d2) - spot * norm_cdf(-d1)

    return round(max(price, 1.5), 2)


# ==========================================
# 4. HEDGED 09:20 SHORT STRADDLE BACKTEST
# ==========================================
def run_hedged_straddle_backtest(days: int = 60):
    obj = init_smartapi()
    print(f"Fetching NIFTY 5-Min data for last {days} days...")
    df = fetch_nifty_5m(obj, days=days)
    if df.empty:
        print("❌ Failed to fetch NIFTY candles.")
        return

    df["Trade_Date"] = df.index.date
    daily_stats = df.groupby("Trade_Date").agg(Day_High=("High", "max"), Day_Low=("Low", "min"), Day_Close=("Close", "last"))
    daily_stats["Prev_Range_Pct"] = ((daily_stats["Day_High"] - daily_stats["Day_Low"]) / daily_stats["Day_Close"]).shift(1)
    daily_stats["Rolling_IV"] = (daily_stats["Prev_Range_Pct"].rolling(5).mean() * math.sqrt(252) * 0.85).fillna(0.13).clip(0.10, 0.24)

    # NIFTY Weekly Expiry is Thursday (weekday == 3)
    def get_dte(dt_date):
        wd = dt_date.weekday()
        dte = (3 - wd) % 7
        return 0.35 if dte == 0 else float(dte) + 0.35

    results = []

    for t_date, day_df in df.groupby("Trade_Date"):
        if t_date not in daily_stats.index:
            continue

        iv = float(daily_stats.loc[t_date, "Rolling_IV"])
        dte_start = get_dte(t_date)

        # Find 09:20 AM candle
        entry_bars = day_df[day_df.index.time == ENTRY_TIME]
        if entry_bars.empty:
            continue

        entry_bar = entry_bars.iloc[0]
        entry_spot = float(entry_bar["Close"])
        atm_strike = round(entry_spot / STRIKE_STEP) * STRIKE_STEP
        hedge_ce_strike = atm_strike + HEDGE_DISTANCE
        hedge_pe_strike = atm_strike - HEDGE_DISTANCE

        # Entry Premiums at 09:20 AM
        sell_ce_entry = estimate_option_price(entry_spot, atm_strike, "CE", iv, dte_start)
        sell_pe_entry = estimate_option_price(entry_spot, atm_strike, "PE", iv, dte_start)
        buy_ce_entry = estimate_option_price(entry_spot, hedge_ce_strike, "CE", iv, dte_start)
        buy_pe_entry = estimate_option_price(entry_spot, hedge_pe_strike, "PE", iv, dte_start)

        ce_sl = round(sell_ce_entry * (1.0 + LEG_SL_PCT), 2)
        pe_sl = round(sell_pe_entry * (1.0 + LEG_SL_PCT), 2)

        ce_active = True
        pe_active = True
        ce_exit = sell_ce_entry
        pe_exit = sell_pe_entry
        ce_status = "EOD"
        pe_status = "EOD"

        # Iterate intraday bars from 09:25 AM to 03:15 PM
        intraday_bars = day_df[(day_df.index.time > ENTRY_TIME) & (day_df.index.time <= FORCE_EXIT_TIME)]
        if intraday_bars.empty:
            continue

        total_bars = max(len(intraday_bars), 1)
        last_spot = entry_spot
        final_dte = max(dte_start - 0.25, 0.05)

        for step_idx, (bar_dt, bar) in enumerate(intraday_bars.iterrows()):
            elapsed_frac = (step_idx + 1) / total_bars
            curr_dte = max(dte_start - (0.25 * elapsed_frac), 0.05)
            final_dte = curr_dte

            # Check worst-case intrabar High for CE and intrabar Low for PE
            spot_high = float(bar["High"])
            spot_low = float(bar["Low"])
            last_spot = float(bar["Close"])

            if ce_active:
                curr_ce_high = estimate_option_price(spot_high, atm_strike, "CE", iv, curr_dte)
                if curr_ce_high >= ce_sl:
                    ce_active = False
                    ce_exit = ce_sl
                    ce_status = "SL_HIT" if ce_sl > sell_ce_entry else "COST_SL"
                    # Trail PE SL to Cost (Breakeven)
                    if pe_active:
                        pe_sl = sell_pe_entry

            if pe_active:
                curr_pe_high = estimate_option_price(spot_low, atm_strike, "PE", iv, curr_dte)
                if curr_pe_high >= pe_sl:
                    pe_active = False
                    pe_exit = pe_sl
                    pe_status = "SL_HIT" if pe_sl > sell_pe_entry else "COST_SL"
                    # Trail CE SL to Cost (Breakeven)
                    if ce_active:
                        ce_sl = sell_ce_entry

        # Square-off remaining open legs at 03:15 PM IST
        if ce_active:
            ce_exit = estimate_option_price(last_spot, atm_strike, "CE", iv, final_dte)
        if pe_active:
            pe_exit = estimate_option_price(last_spot, atm_strike, "PE", iv, final_dte)

        buy_ce_exit = estimate_option_price(last_spot, hedge_ce_strike, "CE", iv, final_dte)
        buy_pe_exit = estimate_option_price(last_spot, hedge_pe_strike, "PE", iv, final_dte)

        # Net P&L Calculation (1 Lot = 75 Qty)
        short_ce_pnl = (sell_ce_entry - ce_exit) * LOT_SIZE
        short_pe_pnl = (sell_pe_entry - pe_exit) * LOT_SIZE
        hedge_ce_pnl = (buy_ce_exit - buy_ce_entry) * LOT_SIZE
        hedge_pe_pnl = (buy_pe_exit - buy_pe_entry) * LOT_SIZE

        net_pnl = round(short_ce_pnl + short_pe_pnl + hedge_ce_pnl + hedge_pe_pnl - BROKERAGE_TAX, 2)
        outcome_label = f"CE:{ce_status} | PE:{pe_status}"

        results.append({
            "date": str(t_date),
            "day": t_date.strftime("%a"),
            "atm": atm_strike,
            "entry_spot": round(entry_spot, 1),
            "exit_spot": round(last_spot, 1),
            "spot_move": round(last_spot - entry_spot, 1),
            "ce_entry": sell_ce_entry,
            "pe_entry": sell_pe_entry,
            "outcome": outcome_label,
            "net_pnl": net_pnl
        })

    if not results:
        print("No trading days processed.")
        return

    df_res = pd.DataFrame(results)
    df_res["cum_pnl"] = df_res["net_pnl"].cumsum().round(2)

    wins = len(df_res[df_res["net_pnl"] > 0])
    losses = len(df_res[df_res["net_pnl"] <= 0])
    win_rate = (wins / len(df_res)) * 100
    avg_win = df_res[df_res["net_pnl"] > 0]["net_pnl"].mean() if wins > 0 else 0
    avg_loss = abs(df_res[df_res["net_pnl"] <= 0]["net_pnl"].mean()) if losses > 0 else 0

    print("\n" + "=" * 65)
    print(f"🛡️ NIFTY HEDGED 09:20 SHORT STRADDLE BACKTEST ({days} DAYS)")
    print("   Rule: 500-pt OTM Hedge Wings + 25% Leg SL + Cost Trail + 03:15 PM Exit")
    print("=" * 65)
    print(f"Total Trading Days : {len(df_res)} ({wins} Wins / {losses} Losses)")
    print(f"Win Ratio          : {win_rate:.1f}%")
    print(f"Net Cumulative P&L : ₹{df_res['net_pnl'].sum():,.2f} (1 Lot = 75 Qty | Margin ~₹48,000)")
    print(f"Avg Win / Avg Loss : +₹{avg_win:,.2f} / -₹{avg_loss:,.2f}")
    print("=" * 65)
    print("\nDay-Wise Breakdown (Mon - Fri):")
    print(df_res.groupby("day").agg(
        Days=("net_pnl", "count"),
        Wins=("net_pnl", lambda x: (x > 0).sum()),
        Net_PnL=("net_pnl", "sum")
    ))
    print("\nComplete Daily Trade Ledger:")
    print(df_res[["date", "day", "atm", "spot_move", "outcome", "net_pnl", "cum_pnl"]].to_string(index=False))


if __name__ == "__main__":
    run_hedged_straddle_backtest(days=60)
