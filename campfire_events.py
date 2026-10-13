
import asyncio
import json
import re
from playwright.async_api import async_playwright

MEETUP_URLS = [
    "https://campfire.scopely.com/discover/meetup/8f3e353b-2c5f-44e7-bad7-d3bd1c67eecb",
    "https://campfire.scopely.com/discover/meetup/65a61b9e-aa54-4f76-9b8d-65b40fb8b852",
]


async def extract_event(page, url):
    await page.goto(url, wait_until="networkidle", timeout=60000)
    await page.wait_for_timeout(2000)

    text = await page.locator("body").inner_text()
    lines = [line.strip() for line in text.splitlines() if line.strip()]

    def after(label):
        for i, line in enumerate(lines):
            if line.lower() == label.lower() and i + 1 < len(lines):
                return lines[i + 1]
        return ""

    title = await page.title()
    title = re.sub(r"\s*\|\s*Campfire.*$", "", title).strip()

    return {
        "title": title,
        "date": after("Date"),
        "time": after("Time"),
        "location": after("Location"),
        "host": after("Hosted by Community Ambassador"),
        "group": after("Group"),
        "url": url,
    }


async def main():
    events = []

    async with async_playwright() as p:
        browser = await p.firefox.launch()
        page = await browser.new_page()

        for url in MEETUP_URLS:
            try:
                event = await extract_event(page, url)
                events.append(event)
                print(f"Read: {event['title']}")
            except Exception as exc:
                print(f"Could not read {url}: {exc}")

        await browser.close()

    with open("campfire_events.json", "w", encoding="utf-8") as file:
        json.dump(events, file, indent=2, ensure_ascii=False)

    print(f"Saved {len(events)} event(s).")


if __name__ == "__main__":
    asyncio.run(main())
