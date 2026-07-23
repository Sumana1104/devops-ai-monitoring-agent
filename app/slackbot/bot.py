# ai-service/app/slackbot/bot.py

from slack_sdk import WebClient
import os

SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")

client = WebClient(token=SLACK_BOT_TOKEN)

def send_message(channel: str, text: str):
    client.chat_postMessage(channel=channel, text=text)
