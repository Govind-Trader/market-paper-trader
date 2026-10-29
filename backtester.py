name: Run Historical Backtest

on:
  workflow_dispatch:

permissions:
  contents: write

jobs:
  run-simulation:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout Repository
        uses: actions/checkout@v4

      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.10'

      - name: Install Libraries
        run: |
          pip install yfinance pandas pytz requests

      - name: Run Backtester Script
        run: |
          python backtester.py

      - name: Commit trades_db.json
        run: |
          git config --global user.name "github-actions[bot]"
          git config --global user.email "github-actions[bot]@users.noreply.github.com"
          git add trades_db.json
          git commit -m "Auto-generated 60-day trades_db.json" || echo "No changes to commit"
          git push
