
import os
import json
import urllib.request
import urllib.error
from datetime import datetime, timedelta

BOT_TOKEN = os.environ["DISCORD_BOT_TOKEN"]
CHANNEL_ID = os.environ["DISCORD_CHANNEL_ID"]

BASE_URL = f"https://discord.com/api/v10/channels/{CHANNEL_ID}"
TRACKING_FILE = "announced_events.json"
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
        url, data=data, headers=headers, method=method
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read()
        return json.loads(body) if body else None


def load_tracking():
    try:
        with open(TRACKING_FILE, encoding="utf-8") as file:
            data = json.load(file)
    except FileNotFoundError:
        data = {}

    return {
        "announced": set(data.get("announced", [])),
        "messages": data.get("messages", {}),
    }


def save_tracking(tracking):
    data = {
        "announced": sorted(tracking["announced"]),
        "messages": tracking["messages"],
    }

    with open(TRACKING_FILE, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=2, ensure_ascii=False)


def get_recent_messages():
    return discord_request(f"{BASE_URL}/messages?limit=100") or []


def get_bot_id():
    bot = discord_request("https://discord.com/api/v10/users/@me")
    return bot["id"]


def recover_message_ids(tracking, events, recent_messages, bot_id):
    end_times = {event["url"]: event.get("end_time") for event in events}

    for message in recent_messages:
        if message.get("author", {}).get("id") != bot_id:
            continue

        content = message.get("content", "")

        for url in tracking["announced"]:
            if url in content:
                record = tracking["messages"].setdefault(url, {})
                record["message_id"] = message["id"]

                if end_times.get(url):
                    record["end_time"] = end_times[url]


def delete_expired_messages(tracking):
    now = datetime.now().astimezone()
    deleted = 0
    already_gone = 0
    pending = 0

    for url, record in list(tracking["messages"].items()):
        message_id = record.get("message_id")
        end_time = record.get("end_time")

        if not message_id or not end_time:
            continue

        try:
            end = datetime.fromisoformat(end_time)
            if end.tzinfo is None:
                print(f"Cannot safely determine timezone for {url}")
                pending += 1
                continue

            delete_after = end + GRACE_PERIOD

            if now < delete_after:
                remaining = int((delete_after - now).total_seconds() / 60)
                print(f"Not ready to delete {url}; {remaining} minute(s) remain.")
                continue

            try:
                discord_request(
                    f"{BASE_URL}/messages/{message_id}",
                    method="DELETE",
                )
                print(f"Deleted expired announcement: {url}")
                deleted += 1

            except urllib.error.HTTPError as error:
                body = error.read().decode("utf-8", errors="replace")
                try:
                    details = json.loads(body)
                except ValueError:
                    details = {}

                if error.code == 404 and details.get("code") == 10008:
                    print(f"Announcement already gone: {url}")
                    already_gone += 1
                else:
                    print(
                        f"Deletion failed for {url}: HTTP {error.code} "
                        f"{body}. Will retry."
                    )
                    pending += 1
                    continue

            except Exception as error:
                print(f"Deletion failed for {url}: {error}. Will retry.")
                pending += 1
                continue

            tracking["messages"].pop(url, None)
            tracking["announced"].discard(url)

        except (ValueError, TypeError) as error:
            print(f"Invalid tracking data for {url}: {error}")
            pending += 1

    print(
        f"Cleanup summary: {deleted} deleted, "
        f"{already_gone} already gone, {pending} pending."
    )


def send_message(content):
    return discord_request(
        f"{BASE_URL}/messages",
        method="POST",
        payload={"content": content},
    )


def announce_event(event, tracking, recent_messages, bot_id):
    url = event["url"]

    if url in tracking["announced"]:
        print(f"Already tracked: {event['title']}")
        return

    for message in recent_messages:
        if (
            message.get("author", {}).get("id") == bot_id
            and url in message.get("content", "")
        ):
            tracking["announced"].add(url)
            tracking["messages"][url] = {
                "message_id": message["id"],
                "end_time": event.get("end_time"),
            }
            print(f"Recovered existing announcement: {event['title']}")
            return

    content = (
        f"🔥 **Campfire Meetup: {event['title']}**\n"
        f"📅 **Date:** {event['date']}\n"
        f"🕒 **Time:** {event['time']}\n"
        f"📍 **Location:** {event['location']}\n"
        f"👥 **Group:** {event['group']}\n"
        f"🎤 **Host:** {event['host']}\n"
        f"🔗 [View meetup]({url})\n"
        f"-------------------------------"
    )

    message = send_message(content)

    tracking["announced"].add(url)
    tracking["messages"][url] = {
        "message_id": message["id"],
        "end_time": event.get("end_time"),
    }

    recent_messages.insert(0, {
        "id": message["id"],
        "author": {"id": bot_id},
        "content": content,
    })

    save_tracking(tracking)
    print(f"Discord announcement sent: {event['title']}")


def main():
    with open("campfire_events.json", encoding="utf-8") as file:
        events = json.load(file)

    tracking = load_tracking()
    recent_messages = get_recent_messages()
    bot_id = get_bot_id()

    recover_message_ids(tracking, events, recent_messages, bot_id)

    delete_expired_messages(tracking)
    save_tracking(tracking)

    for event in events:
        try:
            announce_event(event, tracking, recent_messages, bot_id)
        except urllib.error.HTTPError as error:
            print(f"Discord API error: HTTP {error.code}")
            raise

    save_tracking(tracking)
    print(f"Tracking {len(tracking['announced'])} announced meetup(s).")


if __name__ == "__main__":
    main()
