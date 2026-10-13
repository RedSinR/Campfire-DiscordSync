
import os
import json
import urllib.request
import urllib.error
import urllib.parse

BOT_TOKEN = os.environ["DISCORD_BOT_TOKEN"]
CHANNEL_ID = os.environ["DISCORD_CHANNEL_ID"]

BASE_URL = f"https://discord.com/api/v10/channels/{CHANNEL_ID}"


def discord_request(url, method="GET", payload=None):
    data = None
    headers = {
        "Authorization": f"Bot {BOT_TOKEN}",
        "User-Agent": "Campfire-DiscordSync/1.0",
    }

    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(
        url,
        data=data,
        headers=headers,
        method=method,
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read()
        return json.loads(body) if body else None


def get_recent_messages():
    url = f"{BASE_URL}/messages?limit=100"
    return discord_request(url) or []


def send_message(content):
    discord_request(
        f"{BASE_URL}/messages",
        method="POST",
        payload={"content": content},
    )
    print("Discord announcement sent.")


def announce_event(event, existing_messages):
    meetup_url = event["url"]

    for message in existing_messages:
        if meetup_url in message.get("content", ""):
            print(f"Already announced: {event['title']}")
            return False

    content = (
        f"🔥 **Campfire Meetup: {event['title']}**\n"
        f"📅 **Date:** {event['date']}\n"
        f"🕒 **Time:** {event['time']}\n"
        f"📍 **Location:** {event['location']}\n"
        f"👥 **Group:** {event['group']}\n"
        f"🎤 **Host:** {event['host']}\n"
        f"🔗 [View meetup]({meetup_url})"
    )

    send_message(content)
    existing_messages.insert(0, {"content": content})
    return True


if __name__ == "__main__":
    with open("campfire_events.json", encoding="utf-8") as file:
        events = json.load(file)

    existing_messages = get_recent_messages()

    for event in events:
        try:
            announce_event(event, existing_messages)
        except urllib.error.HTTPError as error:
            print(f"Discord API error: HTTP {error.code}")
            raise
