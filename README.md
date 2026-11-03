# COE203 Project 2: Campus Resource & Opportunity Sniper

## Group: None Type
This project is an automated web monitoring tool designed to track campus resources and opportunities. It uses browser automation to navigate pages, extracts relevant data, tracks the state persistently, and sends real-time push notifications to users when actionable changes occur (e.g., when a closed course or event suddenly has available seats).

## Project Features (Technical Depth & Robustness)
* **Browser Automation:** Uses `Playwright` to navigate dynamic pages and wait for specific DOM states (`expect` conditions) instead of using hardcoded sleeps (Wait for a condition, not a guess).
* **Data Parsing & Validation:** Uses `BeautifulSoup` to extract HTML elements, clean whitespace, and validate numerical data (e.g., negative seats are rejected).
* **State Persistence:** Uses `json` to store the last known state (`monitor_state.json`). It carries forward temporarily broken records to prevent data loss during transient site errors.
* **Smart Notifications:** Short-circuit logic ensures we only send HTTP Push notifications via `ntfy.sh` for *actionable transitions* (0 seats -> >0 seats), preventing notification spam.
* **Retry Budget:** Includes a transient failure retry mechanism for network requests (1s and 2s backoff) to ensure robustness against connection drops.

## File Structure
* `campus_monitor.py`: The main CLI application containing all automation, parsing, persistence, and notification logic.
* `test_page.html`: A local mock HTML file with JavaScript timeouts to safely test the Playwright state machine and parsing logic.
* `requirements.txt`: Project dependencies (`playwright`, `beautifulsoup4`, `requests`).
* `contributions/`: Folder containing Markdown files detailing each team member's specific contributions to the codebase.

## Usage
You can run the monitor via CLI arguments:
```bash
python campus_monitor.py --search "Robotics" --status "3 opportunities" --token "YOUR_NTFY_TOKEN"
