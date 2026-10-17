
import os
import json
import urllib.request
import urllib.error
from datetime import datetime, timezone

BOT_TOKEN = os.environ["DISCORD_BOT_TOKEN"]
CHANNEL_ID = os.environ["DISCORD_CHANNEL_ID"]

API_ROOT = "https://discord.com/api/v10"
CHANNEL_URL = f"{API_ROOT}/channels/{CHANNEL_ID}"
TRACKING_FILE = "announced_events.json"


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


def get_bot_user_id():
    user = discord_request(f"{API_ROOT}/users/@me")
    return user["id"]


def get_recent_messages():
    return discord_request(f"{CHANNEL_URL}/messages?limit=100") or []


def load_tracking():
    try:
        with open(TRACKING_FILE, encoding="utf-8") as file:
            data = json.load(file)

        # Support the original tracking format:
        # {"announced": ["meetup URL", ...]}
        announced = set(data.get("announced", []))

        # New format stores each bot message's ID and event end time.
        messages = data.get("messages", {})

        if not isinstance(messages, dict):
            messages = {}

        return announced, messages

    except FileNotFoundError:
        return set(), {}


def save_tracking(announced, messages):
    data = {
        "announced": sorted(announced),
        "messages": messages,
    }

    with open(TRACKING_FILE, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=2, ensure_ascii=False)


def parse_end_time(value):
    if not value:
        return None

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))

        if parsed.tzinfo is None:
            print(f"WARNING: End time has no timezone: {value}")
            return None

        return parsed.astimezone(timezone.utc)

    except (TypeError, ValueError):
        print(f"WARNING: Invalid event end time: {value}")
        return None


def delete_expired_messages(messages):
    now = datetime.now(timezone.utc)
    deleted = 0

    for meetup_url, record in list(messages.items()):
        if not isinstance(record, dict):
            continue

        message_id = record.get("message_id")
        end_time = parse_end_time(record.get("end_time"))

        if not message_id or not end_time or end_time > now:
            continue

        try:
            discord_request(
                f"{CHANNEL_URL}/messages/{message_id}",
                method="DELETE",
            )

            print(
                f"Deleted expired meetup announcement: "
                f"{meetup_url}"
            )
            deleted += 1
            del messages[meetup_url]

        except urllib.error.HTTPError as error:
            if error.code == 404:
                print(
                    f"Announcement already gone: {meetup_url}"
                )
                del messages[meetup_url]
                continue

            print(
                f"Could not delete announcement {message_id}: "
                f"HTTP {error.code}"
            )
            raise

    if deleted:
        print(f"Deleted {deleted} expired announcement(s).")

    return deleted


def send_message(content):
    result = discord_request(
        f"{CHANNEL_URL}/messages",
        method="POST",
        payload={"content": content},
    )

    if not result or not result.get("id"):
        raise RuntimeError(
            "Discord did not return a message ID after posting."
        )

    print(f"Discord announcement sent. Message ID: {result['id']}")
    return result["id"]


def find_existing_message(meetup_url, existing_messages):
    for message in existing_messages:
        if meetup_url in message.get("content", ""):
            return message

    return None


def announce_event(event, announced, messages, existing_messages,
                   bot_user_id):
    meetup_url = event["url"]
    end_time = event.get("end_time")

    # If this meetup was previously announced, update its end time
    # when the scraper supplies it.
    if meetup_url in messages and end_time:
        messages[meetup_url]["end_time"] = end_time

    existing = find_existing_message(meetup_url, existing_messages)

    # If the message already exists, don't post it a second time.
    if meetup_url in announced:
        print(f"Already tracked: {event['title']}")

        # Backfill the message ID for announcements created by the
        # previous version of this bot.
        if existing and existing.get("author", {}).get("id") == bot_user_id:
            messages[meetup_url] = {
                "message_id": existing["id"],
                "end_time": end_time or messages.get(
                    meetup_url, {}
                ).get("end_time"),
            }

        return

    if existing:
        print(f"Already announced: {event['title']}")
        announced.add(meetup_url)

        # Only track messages actually authored by this bot.
        if existing.get("author", {}).get("id") == bot_user_id:
            messages[meetup_url] = {
                "message_id": existing["id"],
                "end_time": end_time,
            }

        return

    content = (
        f"🔥 **Campfire Meetup: {event['title']}**\n"
        f"📅 **Date:** {event['date']}\n"
        f"🕒 **Time:** {event['time']}\n"
        f"📍 **Location:** {event['location']}\n"
        f"👥 **Group:** {event['group']}\n"
        f"🎤 **Host:** {event['host']}\n"
        f"🔗 [View meetup]({meetup_url})\n"
        f"-------------------------------"
    )

    message_id = send_message(content)

    announced.add(meetup_url)
    messages[meetup_url] = {
        "message_id": message_id,
        "end_time": end_time,
    }

    # Keep the in-memory message list current during this run.
    existing_messages.insert(0, {
        "id": message_id,
        "content": content,
        "author": {"id": bot_user_id},
    })

    if not end_time:
        print(
            f"WARNING: No end time available for {event['title']}. "
            "Automatic deletion cannot be scheduled for it yet."
        )


if __name__ == "__main__":
    with open("campfire_events.json", encoding="utf-8") as file:
        events = json.load(file)

    announced, messages = load_tracking()
    existing_messages = get_recent_messages()
    bot_user_id = get_bot_user_id()

    # Supply the current end time to older tracking records before
    # attempting cleanup.
    for event in events:
        meetup_url = event.get("url")
        if not meetup_url:
            continue

        if meetup_url in messages and event.get("end_time"):
            messages[meetup_url]["end_time"] = event["end_time"]

    # Recover IDs for older bot announcements when they are still
    # present in the channel's most recent 100 messages.
    for event in events:
        meetup_url = event.get("url")
        if not meetup_url or meetup_url not in announced:
            continue

        existing = find_existing_message(meetup_url, existing_messages)

        if (
            existing
            and existing.get("author", {}).get("id") == bot_user_id
        ):
            previous = messages.get(meetup_url, {})

            messages[meetup_url] = {
                "message_id": existing["id"],
                "end_time": (
                    event.get("end_time")
                    or previous.get("end_time")
                ),
            }

    # Delete only messages whose IDs were recorded as bot announcements.
    delete_expired_messages(messages)
    save_tracking(announced, messages)

    for event in events:
        try:
            announce_event(
                event,
                announced,
                messages,
                existing_messages,
                bot_user_id,
            )
            save_tracking(announced, messages)

        except urllib.error.HTTPError as error:
            print(f"Discord API error: HTTP {error.code}")
            raise

    print(f"Tracking {len(announced)} announced meetup(s).")
