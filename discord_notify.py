
import os
import json
import urllib.request
import urllib.error

BOT_TOKEN = os.environ["DISCORD_BOT_TOKEN"]
CHANNEL_ID = os.environ["DISCORD_CHANNEL_ID"]


def send_message(content):
    url = f"https://discord.com/api/v10/channels/{CHANNEL_ID}/messages"
    payload = json.dumps({"content": content}).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Authorization": f"Bot {BOT_TOKEN}",
            "Content-Type": "application/json",
            "User-Agent": "Campfire-DiscordSync/1.0",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            print(f"Discord message sent (HTTP {response.status}).")
    except urllib.error.HTTPError as error:
        print(f"Discord returned HTTP {error.code}.")
        raise


def announce_event(event):
    message = (
        f"🔥 **Campfire Meetup: {event['title']}**\n"
        f"📅 **Date:** {event['date']}\n"
        f"🕒 **Time:** {event['time']}\n"
        f"📍 **Location:** {event['location']}\n"
        f"👥 **Group:** {event['group']}\n"
        f"🎤 **Host:** {event['host']}\n"
        f"🔗 [View meetup]({event['url']})"
    )
    send_message(message)


if __name__ == "__main__":
    with open("campfire_events.json", encoding="utf-8") as file:
        events = json.load(file)

    for event in events:
        announce_event(event)
