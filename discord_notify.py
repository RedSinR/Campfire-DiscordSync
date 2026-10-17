
import os
import json
import re
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

BOT_TOKEN = os.environ["DISCORD_BOT_TOKEN"]
CHANNEL_ID = os.environ["DISCORD_CHANNEL_ID"]

API_ROOT = "https://discord.com/api/v10"
BASE_URL = f"{API_ROOT}/channels/{CHANNEL_ID}"
TRACKING_FILE = "announced_events.json"
LOCAL_TZ = ZoneInfo("America/Los_Angeles")
DELETE_DELAY_MINUTES = 30


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
    return discord_request(f"{BASE_URL}/messages?limit=100") or []


def load_tracking():
    try:
        with open(TRACKING_FILE, encoding="utf-8") as file:
            data = json.load(file)
    except FileNotFoundError:
        data = {}

    announced = set(data.get("announced", []))
    messages = data.get("messages", {})

    if not isinstance(messages, dict):
        messages = {}

    return announced, messages


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
            return None

        return parsed.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def recover_end_time_from_message(content):
    """Recover a legacy announcement's end time from its displayed text."""
    date_match = re.search(r"\*\*Date:\*\*\s*(.+)", content)
    time_match = re.search(r"\*\*Time:\*\*\s*(.+)", content)

    if not date_match or not time_match:
        return None

    try:
        date_text = date_match.group(1).strip()
        time_text = time_match.group(1).strip()

        event_date = datetime.strptime(
            date_text, "%b %d, %Y"
        ).date()

        # Cross-day announcements include a date on the ending time.
        cross_day = re.search(
            r"-\s*([A-Za-z]{3}\s+\d{1,2}),\s*"
            r"(\d{1,2}:\d{2}\s*[AP]M)\s*$",
            time_text,
            re.IGNORECASE,
        )

        if cross_day:
            end_date_text = f"{cross_day.group(1)}, {event_date.year}"
            end_date = datetime.strptime(
                end_date_text, "%b %d, %Y"
            ).date()
            end_clock = datetime.strptime(
                cross_day.group(2).upper(), "%I:%M %p"
            ).time()
        else:
            # The end time is always the part after the final hyphen.
            end_clock_text = time_text.rsplit("-", 1)[-1].strip()
            end_clock = datetime.strptime(
                end_clock_text.upper(), "%I:%M %p"
            ).time()
            end_date = event_date

        local_end = datetime.combine(
            end_date, end_clock, tzinfo=LOCAL_TZ
        )
        return local_end.astimezone(timezone.utc).isoformat()

    except (ValueError, TypeError):
        return None


def find_meetup_url(content):
    match = re.search(
        r"https://campfire\.scopely\.com/discover/meetup/[^\s)>]+",
        content,
    )
    return match.group(0).rstrip(".,") if match else None


def send_message(content):
    response = discord_request(
        f"{BASE_URL}/messages",
        method="POST",
        payload={"content": content},
    )

    if not response or not response.get("id"):
        raise RuntimeError("Discord did not return the new message ID.")

    print(f"Discord announcement sent. Message ID: {response['id']}")
    return response["id"]


def announce_event(event, announced, messages, existing_messages, bot_id):
    meetup_url = event["url"]

    if meetup_url in announced:
        print(f"Already tracked: {event['title']}")
        return

    # Avoid duplicates if the message exists but tracking was lost.
    for message in existing_messages:
        if (
            (message.get("author") or {}).get("id") == bot_id
            and meetup_url in message.get("content", "")
        ):
            print(f"Found existing announcement: {event['title']}")
            announced.add(meetup_url)
            messages[meetup_url] = {
                "message_id": message["id"],
                "end_time": event.get("end_time"),
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
        "end_time": event.get("end_time"),
    }

    existing_messages.insert(0, {
        "id": message_id,
        "content": content,
        "author": {"id": bot_id},
    })


def recover_legacy_tracking(
    announced, messages, events_by_url, existing_messages, bot_id
):
    """Attach IDs and end times to announcements made by the older script."""
    recovered = 0

    for message in existing_messages:
        if (message.get("author") or {}).get("id") != bot_id:
            continue

        url = find_meetup_url(message.get("content", ""))
        if not url:
            continue

        if url not in announced and url not in events_by_url:
            continue

        record = messages.get(url, {})
        if not isinstance(record, dict):
            record = {}

        if record.get("message_id") and record.get("end_time"):
            continue

        record["message_id"] = message["id"]

        event = events_by_url.get(url)
        if event and event.get("end_time"):
            record["end_time"] = event["end_time"]
        elif not record.get("end_time"):
            record["end_time"] = recover_end_time_from_message(
                message.get("content", "")
            )

        messages[url] = record
        announced.add(url)
        recovered += 1

    if recovered:
        print(f"Recovered tracking for {recovered} existing announcement(s).")


def delete_expired_messages(announced, messages):
    now = datetime.now(timezone.utc)
    delay = timedelta(minutes=DELETE_DELAY_MINUTES)
    deleted = 0

    for url, record in list(messages.items()):
        if not isinstance(record, dict):
            continue

        message_id = record.get("message_id")
        end_time = parse_end_time(record.get("end_time"))

        if not message_id:
            print(f"Cannot delete {url}: message ID is not tracked.")
            continue

        if not end_time:
            print(f"Cannot determine end time for {url}; leaving message alone.")
            continue

        delete_after = end_time + delay

        if now < delete_after:
            remaining = int((delete_after - now).total_seconds() // 60)
            print(f"Not ready to delete {url}; approximately {remaining} minute(s) remain.")
            continue

        try:
            discord_request(
                f"{BASE_URL}/messages/{message_id}",
                method="DELETE",
            )
            print(f"Deleted expired meetup announcement: {url}")
            messages.pop(url, None)
            deleted += 1

        except urllib.error.HTTPError as error:
            if error.code == 404:
                print(f"Announcement already gone: {url}")
                messages.pop(url, None)
            else:
                print(
                    f"Failed to delete {url}: HTTP {error.code}"
                )
                raise

    if deleted:
        print(f"Deleted {deleted} expired announcement(s).")


if __name__ == "__main__":
    with open("campfire_events.json", encoding="utf-8") as file:
        events = json.load(file)

    announced, messages = load_tracking()
    existing_messages = get_recent_messages()
    bot_id = get_bot_user_id()

    events_by_url = {
        event["url"]: event
        for event in events
        if event.get("url")
    }

    recover_legacy_tracking(
        announced,
        messages,
        events_by_url,
        existing_messages,
        bot_id,
    )

    for event in events:
        try:
            announce_event(
                event,
                announced,
                messages,
                existing_messages,
                bot_id,
            )
        except urllib.error.HTTPError as error:
            print(f"Discord API error: HTTP {error.code}")
            raise

    # Delete only after the meetup has ended and the extra 30 minutes passed.
    delete_expired_messages(announced, messages)

    save_tracking(announced, messages)
    print(f"Tracking {len(announced)} announced meetup(s).")
