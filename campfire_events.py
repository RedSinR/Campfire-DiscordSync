
import json
import time
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests

API_URL = "https://niantic-social-api.nianticlabs.com/graphql"
CAMPFIRE_URL = "https://campfire.scopely.com/discover/meetup/"

GROUP_ID = "e3cf00ec-13bf-48b9-a2ce-cb635564fe23"
CHANNEL_ID = "8947de81-e387-4e03-89c6-31ce2ca47c3c"
LOCAL_TZ = ZoneInfo("America/Los_Angeles")

MAP_QUERY = """
query FindMeetups(
  $realityChannelMapObjectsInLatLngBoundsInput:
    RealityChannelMapObjectsInLatLngBoundsInput!
) {
  realityChannelMapObjectsInLatLngBounds(
    input: $realityChannelMapObjectsInLatLngBoundsInput
  ) {
    mapObjectType
    event {
      id
    }
  }
}
"""

EVENT_QUERY = """
query GetMeetup($id: ID!) {
  event(id: $id) {
    id
    name
    address
    eventTime
    eventEndTime
    club {
      id
      name
    }
    creator {
      displayName
    }
  }
}
"""


def graphql(session, query, variables):
    """Send a GraphQL request and fail clearly on API errors."""
    response = session.post(
        API_URL,
        json={"query": query, "variables": variables},
        timeout=30,
    )

    print(f"GraphQL HTTP status: {response.status_code}")
    response.raise_for_status()

    try:
        result = response.json()
    except ValueError as exc:
        preview = response.text[:500]
        raise RuntimeError(
            f"API returned non-JSON content: {preview}"
        ) from exc

    if result.get("errors"):
        raise RuntimeError(
            "Campfire GraphQL error: "
            + json.dumps(result["errors"], ensure_ascii=False)
        )

    if "data" not in result:
        raise RuntimeError(
            "GraphQL response contained no data field: "
            + json.dumps(result, ensure_ascii=False)[:1000]
        )

    return result["data"] or {}


def discover_event_ids(session):
    """Discover map event IDs across the configured Kern County area."""
    found = set()
    successful_requests = 0
    failed_requests = 0

    south, north = 34.7, 35.9
    west, east = -120.1, -117.6
    tile_size = 0.25

    lat = south

    while lat < north:
        next_lat = min(lat + tile_size, north)
        lon = west

        while lon < east:
            next_lon = min(lon + tile_size, east)

            variables = {
                "realityChannelMapObjectsInLatLngBoundsInput": {
                    "zoomLevel": 16,
                    "bounds": {
                        "sw": {"lat": lat, "lng": lon},
                        "ne": {"lat": next_lat, "lng": next_lon},
                    },
                    "sources": [
                        {
                            "name": "PGO",
                            "dropTypes": ["EVENT", "CA_EVENT"],
                        }
                    ],
                    "realityChannelId": CHANNEL_ID,
                    "limitDropCount": False,
                }
            }

            try:
                data = graphql(session, MAP_QUERY, variables)

                objects = data.get(
                    "realityChannelMapObjectsInLatLngBounds"
                )

                if objects is None:
                    raise RuntimeError(
                        "Map query returned no expected result field. "
                        "Response keys: " + ", ".join(data.keys())
                    )

                if not isinstance(objects, list):
                    raise RuntimeError(
                        "Map result was not a list: "
                        + repr(objects)[:500]
                    )

                successful_requests += 1

                for item in objects:
                    event = item.get("event") or {}
                    event_id = event.get("id")

                    if event_id:
                        found.add(event_id)

                print(
                    f"Tile lat {lat:.2f}-{next_lat:.2f}, "
                    f"lon {lon:.2f}-{next_lon:.2f}: "
                    f"{len(objects)} map objects"
                )

            except Exception as exc:
                failed_requests += 1
                print(
                    f"Tile failed at ({lat:.2f}, {lon:.2f}): "
                    f"{type(exc).__name__}: {exc}"
                )

            lon = next_lon
            time.sleep(0.15)

        lat = next_lat

    print(
        f"Map scan summary: {successful_requests} successful requests, "
        f"{failed_requests} failed requests."
    )

    if successful_requests == 0:
        raise RuntimeError(
            "Every map request failed. Refusing to treat this as "
            "a valid empty event list."
        )

    if failed_requests:
        print(
            "WARNING: Some map requests failed. Results may be incomplete."
        )

    return found


def local_datetime(value):
    if not value:
        return None

    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(LOCAL_TZ)


def format_date(value):
    return value.strftime("%b %d, %Y").replace(" 0", " ")


def format_time(start, end):
    if start.date() == end.date():
        if start.strftime("%p") == end.strftime("%p"):
            return (
                f"{start.strftime('%I:%M').lstrip('0')}-"
                f"{end.strftime('%I:%M %p').lstrip('0')}"
            )

        return (
            f"{start.strftime('%I:%M %p').lstrip('0')}-"
            f"{end.strftime('%I:%M %p').lstrip('0')}"
        )

    return (
        f"{start.strftime('%b %d, %I:%M %p')} - "
        f"{end.strftime('%b %d, %I:%M %p')}"
    ).replace(" 0", " ")


def read_event(session, event_id):
    data = graphql(session, EVENT_QUERY, {"id": event_id})
    event = data.get("event")

    if not event:
        print(f"No event details returned for ID {event_id}")
        return None

    club = event.get("club") or {}

    if club.get("id") != GROUP_ID:
        return None

    title = (event.get("name") or "").strip()

    if "DUMMY-TEST" in title.upper():
        print(f"Skipping test event: {title}")
        return None

    start = local_datetime(event.get("eventTime"))
    end = local_datetime(event.get("eventEndTime"))

    if not start or not end:
        print(f"Skipping event with missing dates: {title}")
        return None

    now = datetime.now(timezone.utc).astimezone(LOCAL_TZ)

    if end <= now:
        print(f"Skipping past event: {title}")
        return None

    creator = event.get("creator") or {}

    return {
        "title": title or "Campfire Meetup",
        "date": format_date(start),
        "time": format_time(start, end),
        "location": event.get("address") or "Location not provided",
        "host": creator.get("displayName") or "Unknown",
        "group": club.get("name") or "Kern County Pokémon GO",
        "url": CAMPFIRE_URL + event_id,
    }


def main():
    session = requests.Session()
    session.headers.update({
        "Origin": "https://campfire.scopely.com",
        "Referer": "https://campfire.scopely.com/",
        "User-Agent": "Mozilla/5.0 Campfire-DiscordSync/1.0",
        "Accept": "application/json",
    })

    print("Discovering Campfire meetups across Kern County...")

    try:
        event_ids = discover_event_ids(session)
    except Exception as exc:
        print(f"FATAL: Meetup discovery failed: {exc}")
        sys.exit(1)

    print(f"Found {len(event_ids)} unique map event IDs.")

    events = []

    for event_id in sorted(event_ids):
        try:
            event = read_event(session, event_id)

            if event:
                events.append(event)
                print(f"Found group meetup: {event['title']}")

        except Exception as exc:
            print(
                f"Could not read event {event_id}: "
                f"{type(exc).__name__}: {exc}"
            )

        time.sleep(0.15)

    events.sort(key=lambda item: (item["date"], item["time"], item["title"]))

    with open("campfire_events.json", "w", encoding="utf-8") as file:
        json.dump(events, file, indent=2, ensure_ascii=False)

    print(f"Saved {len(events)} upcoming group meetup(s).")


if __name__ == "__main__":
    main()
