# =====================================================================
# PROJECT 2: Campus Resource & Opportunity Sniper
# =====================================================================
"""
A robust web monitoring tool that automates browser interactions to find 
high-demand campus opportunities, validates the data, tracks state persistently, 
and sends real-time push notifications for actionable changes.
"""

import os
import json
import time
import logging
import argparse
import requests
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, expect

# Configure professional logging (Rigor)
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

STATE_FILE = "monitor_state.json"
NTFY_URL = "https://ntfy.sh/coe203_campus_demo"

# =====================================================================
# PART 1 - Acquire a dynamic page (State Machine Control)
# =====================================================================
def fetch_html(url: str, search_term: str, expected_status: str) -> str:
    """Returns: str - the rendered HTML after browser interaction."""
    logging.info(f"Starting browser automation for URL: {url}")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url)
        
        # Locate by meaning, act
        page.get_by_label("Search opportunities").fill(search_term)
        page.get_by_role("button", name="Search").click()
        
        # Robustness: Wait for a condition, not a guess (Expected to use in project!)
        expect(page.get_by_role("status")).to_have_text(expected_status)
        
        html = page.content()
        browser.close()
        return html

# =====================================================================
# PART 2 - Parse, clean, validate, and track rejections
# =====================================================================
def parse_opportunities(html: str, base_url: str) -> tuple:
    """Returns: tuple (dict, list) - valid records by ID and list of rejected IDs."""
    soup = BeautifulSoup(html, "html.parser")
    articles = soup.select("article.notice")

    if not articles:
        logging.warning("Zero opportunity articles found in the DOM.")
        return {}, []

    by_id = {}
    rejected = []
    unidentified_count = 0

    for article in articles:
        card_id = article.get("data-id")
        h2_tag = article.select_one("h2")
        p_seats = article.select_one("p.seats")
        a_tag = article.select_one("a")

        # Validation: Identify missing critical data
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
        
        # Enforce authorized schemes
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
        logging.info(f"Notice: {unidentified_count} unidentified card(s) dropped.")

    return by_id, rejected

# =====================================================================
# PART 3 - Persistence & Short-Circuit Logic
# =====================================================================
def load_state() -> dict:
    """Returns: dict - saved records from disk, {} on first run."""
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

def save_state(records: dict) -> None:
    """Returns: None - persists verified state to disk."""
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2)

def detect_changes(saved: dict, current: dict) -> list:
    """Returns: list - notification messages for actionable transitions."""
    notifications = []
    for card_id, rec in current.items():
        old = saved.get(card_id)
        
        # Short-circuit logic: Only notify if seats became available (0 -> >0) or it's a new open opportunity
        if rec["seats"] > 0 and (old is None or old["seats"] == 0):
            notifications.append(
                f"🎯 {card_id}: '{rec['title']}' opened! "
                f"({rec['seats']} seats). Apply here: {rec['url']}"
            )
    return notifications

# =====================================================================
# PART 4 - Act / HTTP Push Notification with Retry Budget
# =====================================================================
def send_push_ntfy(url: str, token: str, message: str) -> None:
    """Returns: None - publishes event with a Transient Failure Retry Budget."""
    headers = {"Title": "Campus Opportunity Sniper"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    # Robustness: Retry budget (try, wait 1s, try, wait 2s, try)
    wait_times = [1, 2] 
    
    for attempt in range(len(wait_times) + 1):
        try:
            response = requests.post(url, data=message.encode("utf-8"), headers=headers, timeout=10)
            response.raise_for_status()
            logging.info("Push notification sent successfully.")
            return
        except requests.RequestException as e:
            if attempt < len(wait_times):
                logging.warning(f"Network issue, retrying in {wait_times[attempt]}s... ({e})")
                time.sleep(wait_times[attempt])
            else:
                logging.error(f"Push notification failed after max retries: {e}")

# =====================================================================
# PART 5 - Job Runner & Main Guard
# =====================================================================
def run_monitor(page_url: str, search_term: str, expected_status: str, token: str) -> None:
    """Returns: None - executes full monitoring and alerting cycle."""
    logging.info("Starting campus monitor scan cycle...")
    try:
        html = fetch_html(page_url, search_term, expected_status)
        records, rejected = parse_opportunities(html, page_url)

        if rejected:
            logging.warning(f"Inspection notice: {len(rejected)} malformed record(s) encountered.")

        saved_state = load_state()

        # Carry forward last known good record for temporarily broken cards
        for card_id in rejected:
            if card_id in saved_state:
                records[card_id] = saved_state[card_id]

        if not records:
            logging.info("Scan completed: No valid records. State preserved.")
            return

        actions = detect_changes(saved_state, records)

        for message in actions:
            logging.info(f"Actionable transition detected! Sending alert: {message}")
            send_push_ntfy(NTFY_URL, token, message)

        save_state(records)
        logging.info("Scan cycle completed and state updated successfully.")

    except Exception as e:
        logging.error(f"Monitor run failed: {e}")

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