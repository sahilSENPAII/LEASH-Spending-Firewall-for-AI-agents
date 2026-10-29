"""Spike 3: can a headless browser read a page the way our demo agent will,
including the hidden prompt-injection text?

Run:  python spikes/03_playwright.py
"""
from pathlib import Path

from playwright.sync_api import sync_playwright

page_url = (Path(__file__).resolve().parent / "fixtures" / "megadeals.html").as_uri()

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto(page_url)
    title = page.locator("h2").first.inner_text()
    price = page.locator(".price").first.inner_text()
    full_text = page.locator("body").text_content()
    browser.close()

print(f"Product : {title}")
print(f"Price   : {price}")
print(f"Hidden injection visible to agent: {'gift cards' in full_text}")
