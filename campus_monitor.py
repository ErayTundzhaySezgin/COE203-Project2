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

# Rigor: Configure professional logging to track execution states 
# (INFO, WARNING, ERROR) instead of relying on basic print() statements.
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

STATE_FILE = "monitor_state.json" # File to store the results of previous scans
NTFY_URL = "https://ntfy.sh/coe203_campus_demo" # Destination URL for push notifications

# =====================================================================
# PART 1 - Acquire a dynamic page (Browser Automation)
# =====================================================================
def fetch_html(url: str, search_term: str, expected_status: str) -> str:
    """Navigates to the specified URL, performs a search, and returns the rendered HTML."""
    logging.info(f"Starting browser automation for URL: {url}")
    
    with sync_playwright() as p:
        # headless=True runs the browser in the background invisibly
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url)
        
        # Locate elements by their meaning (Locator) and take action
        page.get_by_label("Search opportunities").fill(search_term) # Fill the search box
        page.get_by_role("button", name="Search").click() # Click the search button
        
        # ROBUSTNESS: The most critical rule from the slides! "Wait for a condition, not a guess."
        # Instead of using a hardcoded time.sleep(), we wait for the expected status text to appear.
        expect(page.get_by_role("status")).to_have_text(expected_status)
        
        html = page.content() # Retrieve the final HTML content of the page
        browser.close()
        return html

# =====================================================================
# PART 2 - Parse, Clean, and Validate Data (Parsing & Validation)
# =====================================================================
def parse_opportunities(html: str, base_url: str) -> tuple:
    """Parses HTML, filters out incomplete/invalid data, and returns valid records."""
    soup = BeautifulSoup(html, "html.parser")
    articles = soup.select("article.notice") # Find opportunity cards using CSS Selectors

    if not articles:
        logging.warning("Zero opportunity articles found on the page.")
        return {}, []

    by_id = {} # Dictionary to store valid opportunities
    rejected = [] # IDs of rejected opportunities due to errors or missing data
    unidentified_count = 0 # Number of broken cards without an ID

    for article in articles:
        card_id = article.get("data-id")
        h2_tag = article.select_one("h2")
        p_seats = article.select_one("p.seats")
        a_tag = article.select_one("a")

        # Validation 1: If the opportunity lacks an ID, it cannot be tracked; skip it.
        if not card_id:
            unidentified_count += 1
            continue

        # Validation 2: Reject if title, seats, or link tags are missing.
        if not h2_tag or not p_seats or not a_tag:
            rejected.append(card_id)
            continue

        # Text Cleanup: Remove redundant spaces from the title (" AI  Talk " -> "AI Talk")
        clean_title = " ".join(h2_tag.get_text().strip().split())
        if not clean_title:
            rejected.append(card_id)
            continue

        # Error Handling with Try-Except: Attempt to extract only the integer from the seats text.
        try:
            seats = int(p_seats.get_text().split()[0])
        except (ValueError, IndexError):
            rejected.append(card_id) # Reject if it's not a number (e.g., "Full")
            continue

        # Seat count cannot be negative.
        if seats < 0:
            rejected.append(card_id)
            continue

        # Resolve relative URLs (e.g., "/events/A1") into absolute URLs.
        resolved_url = urljoin(base_url, a_tag.get("href", ""))
        
        # Security: Only accept links starting with https:// or (for demo) file://.
        if not (resolved_url.startswith("https://") or resolved_url.startswith("file://")):
            rejected.append(card_id)
            continue

        # Save the opportunity to the dictionary if it passes all tests.
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
# PART 3 - Persistence & Detecting Changes
# =====================================================================
def load_state() -> dict:
    """Loads previous scan results saved on disk. Returns an empty dict on the first run."""
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

def save_state(records: dict) -> None:
    """Saves the current state to disk for comparison in the next scan."""
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2)

def detect_changes(saved: dict, current: dict) -> list:
    """Compares old and new states, returning a list of actionable transitions."""
    notifications = []
    for card_id, rec in current.items():
        old = saved.get(card_id) # Get old state (Returns None if it doesn't exist)
        
        # Short-circuit logic (Business Rule): 
        # Only notify if current seats > 0 AND (it's newly added OR previously had 0 seats)!
        # This prevents spamming notifications for opportunities that are already open.
        if rec["seats"] > 0 and (old is None or old["seats"] == 0):
            notifications.append(
                f"🎯 {card_id}: '{rec['title']}' opened! "
                f"({rec['seats']} seats). Apply here: {rec['url']}"
            )
    return notifications

# =====================================================================
# PART 4 - Act / Fault-Tolerant Push Notifications (Retry Budget)
# =====================================================================
def send_push_ntfy(url: str, token: str, message: str) -> None:
    """Sends an instant push notification via ntfy.sh. Retries upon failure."""
    headers = {"Title": "Campus Opportunity Sniper"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    # ROBUSTNESS: "Retry Budget" against temporary network drops.
    # We don't give up on errors. We wait 1 sec, then 2 secs, and try again.
    wait_times = [1, 2] 
    
    for attempt in range(len(wait_times) + 1):
        try:
            # Prevent infinite hanging with timeout=10
            response = requests.post(url, data=message.encode("utf-8"), headers=headers, timeout=10)
            response.raise_for_status() # Raise an exception for non-200 HTTP status codes
            logging.info("Push notification sent successfully.")
            return # Exit function on success
        except requests.RequestException as e:
            if attempt < len(wait_times):
                logging.warning(f"Network issue, retrying in {wait_times[attempt]} seconds... ({e})")
                time.sleep(wait_times[attempt])
            else:
                logging.error(f"Push notification failed after maximum retries: {e}")

# =====================================================================
# PART 5 - Job Runner
# =====================================================================
def run_monitor(page_url: str, search_term: str, expected_status: str, token: str) -> None:
    """Orchestrates the entire scan, parse, compare, and notify pipeline."""
    logging.info("Starting campus monitor scan cycle...")
    try:
        # 1. Fetch HTML
        html = fetch_html(page_url, search_term, expected_status)
        # 2. Parse and validate opportunities within the HTML
        records, rejected = parse_opportunities(html, page_url)

        if rejected:
            logging.warning(f"Inspection notice: {len(rejected)} malformed record(s) dropped.")

        # 3. Load previous state from disk
        saved_state = load_state()

        # Critical Rule: If the site temporarily loads a broken card during a scan,
        # tolerate the error by carrying forward the last known good state instead of deleting it.
        for card_id in rejected:
            if card_id in saved_state:
                records[card_id] = saved_state[card_id]

        if not records:
            logging.info("Scan completed: No valid records found. State preserved.")
            return

        # 4. Detect changes
        actions = detect_changes(saved_state, records)

        # 5. Send notifications only for actionable changes
        for message in actions:
            logging.info(f"Actionable transition detected! Sending alert: {message}")
            send_push_ntfy(NTFY_URL, token, message)

        # 6. Save current state to disk
        save_state(records)
        logging.info("Scan cycle completed and state updated successfully.")

    except Exception as e:
        logging.error(f"A critical error occurred during the monitor run: {e}")

# Entry point when executed from the terminal (Command Line)
if __name__ == "__main__":
    # Rigor: Made the program flexible with Command Line Arguments (CLI) instead of hardcoding.
    parser = argparse.ArgumentParser(description="Campus Opportunity Sniper (Web Monitor)")
    parser.add_argument("--url", help="Target URL to scan", default=None)
    parser.add_argument("--search", default="AI", help="Term to search in the browser")
    parser.add_argument("--status", default="3 opportunities", help="Expected status text to wait for")
    parser.add_argument("--token", default="", help="Security token for ntfy.sh (if required)")
    
    args = parser.parse_args()

    # If no URL is provided externally, default to the local test_page.html file.
    target_url = args.url
    if not target_url:
        path = os.path.abspath("test_page.html").replace("\\", "/")
        if not path.startswith("/"):
            path = "/" + path
        target_url = "file://" + path

    # Run the entire system
    run_monitor(target_url, args.search, args.status, args.token)