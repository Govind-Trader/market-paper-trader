import os
import requests

# GitHub Secrets se automatically credentials load honge
bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "8620926844:AAF5mKGb8200WgUjRv9uXS4T-6amaCU5EI0")
chat_id = os.getenv("TELEGRAM_CHAT_ID", "6018016670")

msg = (
    "✅ *Trading System Connected Successfully!*\n\n"
    "Aapka Telegram Bot (@GovindMarketAiBot) live paper-trading alerts ke liye ready hai.\n"
    "Market setups banne par instant notifications yahan deliver honge."
)

url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
payload = {
    "chat_id": chat_id,
    "text": msg,
    "parse_mode": "Markdown"
}

try:
    resp = requests.post(url, json=payload, timeout=10)
    print("Response Status:", resp.status_code)
    print("Response JSON:", resp.json())
except Exception as e:
    print("Error:", e)
