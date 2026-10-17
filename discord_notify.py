
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

BOT_TOKEN = os.environ["DISCORD_BOT_TOKEN"]
CHANNEL_ID = os.environ["DISCORD_CHANNEL_ID"]

API_ROOT = "https://discord.com/api/v10"
CHANNEL_URL = f"{API_ROOT}/channels/{CHANNEL_ID}"
TRACKING_FILE = "announced_events.json"

# Keep announcements visible for 30 minutes after the meetup ends.
GRACE_PERIOD = timedelta(minutes=30)


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
    return discord_request(
        f"{CHANNEL_URL}/messages?limit=100"
    ) or []


def load_tracking():
    try:
        with open(TRACKING_FILE, encoding="utf-8") as file:
            data = json.load(file)

        announced = set(data.get("announced", []))
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


def discord_error_details(error):
    """Read Discord's error code and message when available."""
    try:
        body = error.read().decode("utf-8")
        data = json.loads(body)
        return data.get("code"), data.get("message", "")
    except (ValueError, UnicodeDecodeError, AttributeError):
        return None, ""


def delete_expired_messages(messages):
    """Retry due deletions on every run until they are confirmed."""
    now = datetime.now(timezone.utc)
    deleted = 0
    already_gone = 0
    pending = 0

    for meetup_url, record in list(messages.items()):
        if not isinstance(record, dict):
            print(f"WARNING: Invalid tracking record: {meetup_url}")
            continue

        message_id = record.get("message_id")
        end_time = parse_end_time(record.get("end_time"))

        if not message_id:
            print(f"Cleanup pending: no Discord message ID for {meetup_url}")
            pending += 1
            continue

        if not end_time:
            print(f"Cleanup pending: no valid end time for {meetup_url}")
            pending += 1
            continue

        delete_after = end_time + GRACE_PERIOD

        if delete_after > now:
            minutes_left = int(
                (delete_after - now).total_seconds() / 60
            )
            print(
                f"Not ready to delete {meetup_url}; "
                f"approximately {minutes_left} minute(s) remain."
            )
            continue

        try:
            discord_request(
                f"{CHANNEL_URL}/messages/{message_id}",
                method="DELETE",
            )

            print(
                f"Deleted expired meetup announcement: {meetup_url}"
            )
            del messages[meetup_url]
            deleted += 1

        except urllib.error.HTTPError as error:
            code, message = discord_error_details(error)

            # Discord error 10008 means the message no longer exists.
            # Treat that as completed cleanup.
            if error.code == 404 and code == 10008:
                print(
                    f"Announcement already gone; clearing tracking: "
                    f"{meetup_url}"
                )
                del messages[meetup_url]
                already_gone += 1
                continue

            # Keep the record so a future workflow run can retry.
            print(
                f"Deletion still pending for {meetup_url} "
                f"(message {message_id}): HTTP {error.code}"
                + (f", Discord code {code}" if code is not None else "")
                + (f" — {message}" if message else "")
            )
            pending += 1

        except (urllib.error.URLError, TimeoutError, OSError) as error:
            # Network failures are temporary; keep the ID for retry.
            print(
                f"Deletion still pending for {meetup_url} "
                f"(message {message_id}): "
                f"{type(error).__name__}: {error}"
            )
            pending += 1

    print(
        "Cleanup summary: "
        f"{deleted} deleted, "
        f"{already_gone} already gone, "
        f"{pending} pending."
    )

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


def announce_event(
    event,
    announced,
    messages,
    existing_messages,
    bot_user_id,
):
    meetup_url = event["url"]
    end_time = event.get("end_time")

    if meetup_url in messages and end_time:
        messages[meetup_url]["end_time"] = end_time

    existing = find_existing_message(meetup_url, existing_messages)

    if meetup_url in announced:
        print(f"Already tracked: {event['title']}")

        if (
            existing
            and existing.get("author", {}).get("id") == bot_user_id
        ):
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

    # Refresh end times when the scraper has current event data.
    for event in events:
        meetup_url = event.get("url")
        if not meetup_url:
            continue

        if meetup_url in messages and event.get("end_time"):
            messages[meetup_url]["end_time"] = event["end_time"]

    # Recover older bot message IDs when those messages are among
    # the channel's latest 100 messages.
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

    # Attempt due deletions. Failed attempts remain tracked for the
    # next scheduled run; they do not stop new announcements.
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
            print(f"Discord API error while announcing: HTTP {error.code}")
            raise

    print(f"Tracking {len(announced)} announced meetup(s).")
