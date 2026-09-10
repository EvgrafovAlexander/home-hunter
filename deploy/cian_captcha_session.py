"""Temporary noVNC browser for manually completing a CIAN challenge.

Run only while CIAN timers are stopped: it opens the production persistent
profile so cookies accepted by the user remain available to the collector.
"""

import asyncio
import os
import subprocess
import time
from pathlib import Path

os.environ["DISPLAY"] = ":99"
from playwright.async_api import async_playwright

url = os.environ["CIAN_CAPTCHA_URL"]

subprocess.Popen(["Xvfb", ":99", "-screen", "0", "1440x1000x24", "-nolisten", "tcp"])
for _ in range(100):
    if Path("/tmp/.X11-unix/X99").exists():
        break
    time.sleep(0.1)
else:
    raise SystemExit("Xvfb did not start")

subprocess.Popen(["x11vnc", "-display", ":99", "-listen", "127.0.0.1", "-rfbport", "5900", "-forever", "-shared", "-nopw", "-quiet"])
subprocess.Popen(["websockify", "--web=/usr/share/novnc", "6080", "127.0.0.1:5900"])


async def main():
    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            "/profile/chromium",
            headless=False,
            viewport={"width": 1440, "height": 900},
            locale="ru-RU",
            proxy={"server": os.environ["CIAN_PROXY_URL"]},
            args=["--no-sandbox"],
        )
        page = context.pages[0] if context.pages else await context.new_page()
        try:
            response = await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            print(f"INITIAL_HTTP {response.status if response else None}", flush=True)
        except Exception as exc:
            print(f"INITIAL_ERROR {exc}", flush=True)
        print("READY_FOR_MANUAL_CAPTCHA", flush=True)
        while True:
            await asyncio.sleep(5)


asyncio.run(main())
