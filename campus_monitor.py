# =====================================================================
# PROJECT 2: Campus Resource & Opportunity Sniper
# =====================================================================
"""
A web monitoring tool that automates browser interactions to find
high-demand campus opportunities, validates the data, tracks state
persistently, and sends push notifications for actionable changes.
"""

import os
import json
import time
import argparse
import requests
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, expect

STATE_FILE = "monitor_state.json"
NTFY_URL = "https://ntfy.sh/coe203_campus_demo"

# =====================================================================
# PART 1 - Acquire a dynamic page
# =====================================================================
def fetch_html(url: str, search_term: str, expected_status: str) -> str:
    """Open the page, run the search, and return the rendered HTML."""
    print(f"Starting browser automation for URL: {url}")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url)

        page.get_by_label("Search opportunities").fill(search_term)
        page.get_by_role("button", name="Search").click()

        # Wait for a condition, not a guess
        expect(page.get_by_role("status")).to_have_text(expected_status)

        html = page.content()
        browser.close()
        return html

# =====================================================================
# PART 2 - Parse, clean, validate, and track rejections
# =====================================================================
def parse_opportunities(html: str, base_url: str) -> tuple:
    """Return valid records by ID and a list of rejected IDs."""
    soup = BeautifulSoup(html, "html.parser")
    articles = soup.select("article.notice")

    if not articles:
        print("Warning: zero opportunity articles found in the DOM.")
        return {}, []

    by_id = {}
    rejected = []
    unidentified_count = 0

    for article in articles:
        card_id = article.get("data-id")
        h2_tag = article.select_one("h2")
        p_seats = article.select_one("p.seats")
        a_tag = article.select_one("a")

        if not card_id:
            unidentified_count += 1
            continue

        if not h2_tag or not p_seats or not a_tag:
            rejected.append(card_id)
            continue

        clean_title = " ".join(h2_tag.get_text().strip().split())
        if not clean_title:
            rejected.append(card_id)
            continue

        try:
            seats = int(p_seats.get_text().split()[0])
        except (ValueError, IndexError):
            rejected.append(card_id)
            continue

        if seats < 0:
            rejected.append(card_id)
            continue

        resolved_url = urljoin(base_url, a_tag.get("href", ""))

        if not (resolved_url.startswith("https://") or resolved_url.startswith("file://")):
            rejected.append(card_id)
            continue

        by_id[card_id] = {
            "id": card_id,
            "title": clean_title,
            "seats": seats,
            "url": resolved_url,
        }

    if unidentified_count > 0:
        print(f"Notice: {unidentified_count} unidentified card(s) dropped.")

    return by_id, rejected

# =====================================================================
# PART 3 - State and change detection
# =====================================================================
def load_state() -> dict:
    """Load saved records from disk; {} on the first run."""
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

def save_state(records: dict) -> None:
    """Save the verified records to disk."""
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2)

def detect_changes(saved: dict, current: dict) -> list:
    """Return (id, message) pairs for opportunities that just opened."""
    notifications = []
    for card_id, rec in current.items():
        old = saved.get(card_id)

        # Alert only when seats appear (0 -> >0) or a new open item shows up
        if rec["seats"] > 0 and (old is None or old["seats"] == 0):
            message = (
                f"🎯 {card_id}: '{rec['title']}' opened! "
                f"({rec['seats']} seats). Apply here: {rec['url']}"
            )
            notifications.append((card_id, message))
    return notifications

# =====================================================================
# PART 4 - Act: push notification with a retry budget
# =====================================================================
def send_push_ntfy(url: str, token: str, message: str) -> bool:
    """Send one push message; True only if the service accepted it."""
    headers = {"Title": "Campus Opportunity Sniper"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    # Retry budget: try, wait 1s, try, wait 2s, try
    wait_times = [1, 2]

    for attempt in range(len(wait_times) + 1):
        try:
            response = requests.post(url, data=message.encode("utf-8"),
                                     headers=headers, timeout=10)
            response.raise_for_status()
            print("Push notification sent successfully.")
            return True
        except requests.RequestException as e:
            # Denied access will not fix itself: stop, do not retry
            if e.response is not None and e.response.status_code in (401, 403):
                print(f"Access denied, check the token or permissions: {e}")
                return False
            if attempt < len(wait_times):
                print(f"Network issue, retrying in {wait_times[attempt]}s... ({e})")
                time.sleep(wait_times[attempt])
            else:
                print(f"Push notification failed after max retries: {e}")
    return False

# =====================================================================
# PART 5 - Job runner and main guard
# =====================================================================
def run_monitor(page_url: str, search_term: str, expected_status: str, token: str) -> None:
    """Run one full scan, alert, and save cycle."""
    print("Starting campus monitor scan cycle...")
    try:
        html = fetch_html(page_url, search_term, expected_status)
        records, rejected = parse_opportunities(html, page_url)

        if rejected:
            print(f"Inspection notice: {len(rejected)} malformed record(s) encountered.")

        saved_state = load_state()

        # Carry forward the last known good record for broken cards
        for card_id in rejected:
            if card_id in saved_state:
                records[card_id] = saved_state[card_id]

        if not records:
            print("Scan completed: no valid records. State preserved.")
            return

        actions = detect_changes(saved_state, records)

        for card_id, message in actions:
            print(f"Actionable transition detected for {card_id}. Sending alert...")
            sent = send_push_ntfy(NTFY_URL, token, message)

            if not sent:
                # Alert not delivered: restore the old record so the
                # next scan detects the same change and alerts again
                if card_id in saved_state:
                    records[card_id] = saved_state[card_id]
                else:
                    del records[card_id]

        save_state(records)
        print("Scan cycle completed and state updated.")

    except Exception as e:
        print(f"Monitor run failed: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Campus Opportunity Sniper (Web Monitor)")
    parser.add_argument("--url", help="Target URL (default is local test_page.html)", default=None)
    parser.add_argument("--search", default="AI", help="Term to search in the browser")
    parser.add_argument("--status", default="3 opportunities", help="Expected status text to wait for")
    parser.add_argument("--token", default="", help="ntfy.sh auth token (if required)")

    args = parser.parse_args()

    # Local file fallback
    target_url = args.url
    if not target_url:
        path = os.path.abspath("test_page.html").replace("\\", "/")
        if not path.startswith("/"):
            path = "/" + path
        target_url = "file://" + path

    run_monitor(target_url, args.search, args.status, args.token)