from datetime import datetime, time
import json
import pandas as pd
import pytz
import yfinance as yf

# 14 Assets Configuration
ASSETS = {
    "NIFTY": {
        "symbol": "^NSEI",
        "market": "NSE",
        "lot_size": 25,
        "strike_step": 50,
        "spread_width": 100,
        "margin": 45000,
    },
    "BANKNIFTY": {
        "symbol": "^NSEBANK",
        "market": "NSE",
        "lot_size": 15,
        "strike_step": 100,
        "spread_width": 200,
        "margin": 50000,
    },
    "FINNIFTY": {
        "symbol": "NIFTY_FIN_SERVICE.NS",
        "market": "NSE",
        "lot_size": 25,
        "strike_step": 50,
        "spread_width": 100,
        "margin": 45000,
    },
    "CRUDEOIL": {
        "symbol": "CL=F",
        "market": "MCX",
        "lot_size": 100,
        "strike_step": 50,
        "spread_width": 100,
        "margin": 55000,
    },
    "RELIANCE": {
        "symbol": "RELIANCE.NS",
        "market": "NSE",
        "lot_size": 250,
        "strike_step": 20,
        "spread_width": 40,
        "margin": 65000,
    },
    "HDFCBANK": {
        "symbol": "HDFCBANK.NS",
        "market": "NSE",
        "lot_size": 550,
        "strike_step": 10,
        "spread_width": 20,
        "margin": 60000,
    },
    "ICICIBANK": {
        "symbol": "ICICIBANK.NS",
        "market": "NSE",
        "lot_size": 700,
        "strike_step": 10,
        "spread_width": 20,
        "margin": 55000,
    },
    "SBIN": {
        "symbol": "SBIN.NS",
        "market": "NSE",
        "lot_size": 750,
        "strike_step": 5,
        "spread_width": 10,
        "margin": 50000,
    },
    "INFY": {
        "symbol": "INFY.NS",
        "market": "NSE",
        "lot_size": 400,
        "strike_step": 20,
        "spread_width": 40,
        "margin": 55000,
    },
    "TCS": {
        "symbol": "TCS.NS",
        "market": "NSE",
        "lot_size": 175,
        "strike_step": 50,
        "spread_width": 100,
        "margin": 65000,
    },
    "LT": {
        "symbol": "LT.NS",
        "market": "NSE",
        "lot_size": 150,
        "strike_step": 20,
        "spread_width": 40,
        "margin": 60000,
    },
    "AXISBANK": {
        "symbol": "AXISBANK.NS",
        "market": "NSE",
        "lot_size": 625,
        "strike_step": 10,
        "spread_width": 20,
        "margin": 55000,
    },
    "BHARTIARTL": {
        "symbol": "BHARTIARTL.NS",
        "market": "NSE",
        "lot_size": 475,
        "strike_step": 10,
        "spread_width": 20,
        "margin": 50000,
    },
    "TATASTEEL": {
        "symbol": "TATASTEEL.NS",
        "market": "NSE",
        "lot_size": 5500,
        "strike_step": 2.5,
        "spread_width": 5,
        "margin": 50000,
    },
}


def run_60d_simulation():
    print(
        "🚀 Fetching 60 Days 5m Data & Simulating 1:2 Bull Call Spreads across 14 Assets..."
    )
    all_trades = []

    for name, conf in ASSETS.items():
        print(f"Scanning {name}...")
        try:
            ticker = yf.Ticker(conf["symbol"])
            df = ticker.history(period="59d", interval="5m")
            if df.empty or len(df) < 50:
                continue

            df["Vol_MA20"] = df["Volume"].rolling(window=20).mean()

            # Iterate through candles
            i = 31
            while i < len(df) - 5:
                window = df.iloc[i - 30 : i]
                prev_30_low = window["Low"].min()

                curr = df.iloc[i]
                c_open, c_high, c_low, c_close, c_vol = (
                    curr["Open"],
                    curr["High"],
                    curr["Low"],
                    curr["Close"],
                    curr["Volume"],
                )
                avg_vol = curr["Vol_MA20"]

                # Dynamic Volume Multiplier
                c_time = curr.name.time()
                req_mult = (
                    2.2
                    if (
                        name == "CRUDEOIL"
                        and time(18, 0) <= c_time <= time(21, 30)
                    )
                    else 1.8
                )

                is_sweep = (c_low < prev_30_low) and (c_close > prev_30_low)
                is_green = c_close > c_open
                has_vol = (
                    c_vol >= (avg_vol * req_mult) if avg_vol > 0 else False
                )

                if is_sweep and is_green and has_vol:
                    entry_price = c_close
                    sl_price = c_low
                    risk_pts = entry_price - sl_price
                    target_pts = risk_pts * 2
                    target_price = entry_price + target_pts

                    # Strike selection
                    atm = int(
                        round(entry_price / conf["strike_step"])
                        * conf["strike_step"]
                    )
                    otm = atm + conf["spread_width"]

                    # Forward simulation for outcome (Max 24 candles = 2 hours hold)
                    outcome = "EXPIRED"
                    exit_price = entry_price
                    exit_time = curr.name
                    pnl = 0.0

                    # Realistic Spread Greeks assumption:
                    # Target hit -> ~60% of spread width captured
                    # SL hit -> ~40% debit lost
                    max_gain = conf["spread_width"] * 0.60 * conf["lot_size"]
                    max_loss = -(conf["spread_width"] * 0.35 * conf["lot_size"])

                    for forward_idx in range(i + 1, min(i + 25, len(df))):
                        future_candle = df.iloc[forward_idx]
                        if future_candle["Low"] <= sl_price:
                            outcome = "STOP_LOSS"
                            exit_price = sl_price
                            exit_time = future_candle.name
                            pnl = max_loss
                            i = forward_idx  # Skip to exit
                            break
                        elif future_candle["High"] >= target_price:
                            outcome = "TARGET_HIT"
                            exit_price = target_price
                            exit_time = future_candle.name
                            pnl = max_gain
                            i = forward_idx  # Skip to exit
                            break
                    else:
                        # Time decay / Intraday close exit
                        exit_price = df.iloc[min(i + 24, len(df) - 1)]["Close"]
                        pnl = (
                            max_gain * 0.3
                            if exit_price > entry_price
                            else max_loss * 0.5
                        )
                        i += 24

                    all_trades.append(
                        {
                            "entry_time": curr.name.strftime(
                                "%Y-%m-%d %H:%M:%S"
                            ),
                            "exit_time": exit_time.strftime(
                                "%Y-%m-%d %H:%M:%S"
                            ),
                            "date": curr.name.strftime("%Y-%m-%d"),
                            "asset": name,
                            "market": conf["market"],
                            "spread": f"{atm} CE / {otm} CE",
                            "entry_price": round(entry_price, 2),
                            "exit_price": round(exit_price, 2),
                            "stop_loss": round(sl_price, 2),
                            "target": round(target_price, 2),
                            "outcome": outcome,
                            "capital_deployed": conf["margin"],
                            "net_pnl": round(pnl, 2),
                            "vol_ratio": f"{round(c_vol / avg_vol, 2)}x",
                        }
                    )
                i += 1
        except Exception as e:
            print(f"Error processing {name}: {e}")

    # Save to JSON database
    with open("trades_db.json", "w") as f:
        json.dump(all_trades, f, indent=4)

    print(
        f"\n✅ Simulation Complete! {len(all_trades)} historical trades saved in 'trades_db.json'."
    )


if __name__ == "__main__":
    run_60d_simulation()
