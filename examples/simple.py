from distbot.spider import Spider
from pprint import pformat
import asyncio


async def main():
    launch_options = {
        "headless": True,
        "ignoreHTTPSErrors": True,
        "defaultViewport": {},
        # "executablePath": "/usr/bin/google-chrome-stable",
        "defaultNavigationTimeout": 45_000,
        "args": [
            "--disable-web-security",
            "--no-sandbox",
            "--start-maximized",
            "--blink-settings=imagesEnabled=false",
        ]
    }
    spider = Spider()
    await spider.add_browser(launch_options=launch_options)
    page = await spider.get('https://www.amazon.com/')
    urls = set([await page.evaluate("(ele) => ele.getAttribute('href')", ele)
                for ele in await page.xpath("//a[@href]")])
    await spider.set_idle(page)
    print(f"Extracted {len(urls)} urls from {page.url}: {pformat(urls)}")
    await spider.shutdown()

asyncio.run(main())
