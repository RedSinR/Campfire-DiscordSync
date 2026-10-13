
import asyncio
from playwright.async_api import async_playwright

URLS = [
    "https://campfire.scopely.com/discover/meetup/8f3e353b-2c5f-44e7-bad7-d3bd1c67eecb",
    "https://campfire.scopely.com/discover/meetup/914a8c68-e97c-4de3-ad3b-6f7709162f8a",
]

async def main():
    async with async_playwright() as p:
        browser = await p.firefox.launch()
        page = await browser.new_page()

        def log_request(request):
            url = request.url
            if "graphql" in url.lower() or "campfire" in url.lower():
                print(f"REQUEST {request.method} {url}")

        def log_response(response):
            url = response.url
            if "graphql" in url.lower():
                print(f"RESPONSE {response.status} {url}")

        page.on("request", log_request)
        page.on("response", log_response)

        for url in URLS:
            print(f"\n--- OPENING {url} ---")
            try:
                await page.goto(
                    url,
                    wait_until="networkidle",
                    timeout=60000,
                )
                await page.wait_for_timeout(3000)
                print("PAGE TITLE:", await page.title())
            except Exception as exc:
                print("PAGE ERROR:", str(exc))

        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
