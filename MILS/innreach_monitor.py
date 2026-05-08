import configparser
from playwright.sync_api import sync_playwright
import re
import json
from pathlib import Path
import time
import logging
from logging.handlers import RotatingFileHandler
import requests

# ---------------------------
# CONFIG
# ---------------------------
config = configparser.ConfigParser(interpolation=None)
CONFIG_PATH = Path(__file__).parent / "config.ini"
config.read(CONFIG_PATH)

USERNAME = config["login"]["username"]
PASSWORD = config["login"]["password"]
URL = config["settings"]["url"]
HEADLESS = config.getboolean("settings", "headless", fallback=True)

DATE_RANGE = config["filters"].get("date_range", "1").strip()
MESSAGE_TYPE = config["filters"].get("message_type", "0").strip()

max_rows_config = config["filters"].get("max_rows", "50").strip().lower()
if max_rows_config == "all":
    MAX_ROWS = None
else:
    MAX_ROWS = int(max_rows_config)

# ---------------------------
# FRESHDESK CONFIG
# ---------------------------
FD_DOMAIN = config["freshdesk"]["domain"]
FD_API_KEY = config["freshdesk"]["api_key"]
FD_BASE_URL = f"https://{FD_DOMAIN}.freshdesk.com/api/v2"
FD_EMAIL = config["freshdesk"]["email"]
#FD_RESPONDER_ID = int(config["freshdesk"]["my_id"])
FD_GROUP_ID = int(config["freshdesk"]["group_id"])

# ---------------------------
# LOGGING
# ---------------------------
LOG_FILE = "innreach_monitor.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=5),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

# ---------------------------
# JSON EXTRACTION
# ---------------------------
def extract_json(html):
    matches = re.findall(r'jsonToHtml\("(.+?)"\)', html, re.DOTALL)
    records = []

    for m in matches:
        try:
            cleaned = m.encode().decode('unicode_escape')
            records.append(json.loads(cleaned))
        except:
            pass

    return records

# ---------------------------
# CREATE FRESHDESK TICKET
# ---------------------------
def create_ticket(subject, description):
    payload = {
        "subject": subject,
        "description": description,
        "status": 2,
        "priority": 1,
        "email": FD_EMAIL,
        "responder_id": FD_RESPONDER_ID,
        "group_id": FD_GROUP_ID,

    }

    response = requests.post(
        f"{FD_BASE_URL}/tickets",
        auth=(FD_API_KEY, "X"),
        json=payload,
        timeout=30
    )

    if response.status_code != 201:
        logger.error(f"Ticket failed: {response.text}")
    else:
        logger.info(f"Ticket created: {subject}")

# ---------------------------
# MAIN
# ---------------------------
def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context()
        page = context.new_page()

        logger.info("Loading page...")
        page.goto(URL)

        # LOGIN
        if page.locator('input[type="password"]').count() > 0:
            logger.info("Logging in...")
            page.fill('input[type="text"]', USERNAME)
            page.fill('input[type="password"]', PASSWORD)
            page.click('button[type="submit"]')
            page.wait_for_load_state("networkidle")

        # SET FILTERS
        logger.info("Setting filters...")

        try:
            page.select_option('#dateRange', DATE_RANGE)
        except Exception as e:
            logger.error(f"Date range failed: {e}")

        try:
            page.select_option('#messageFilter', MESSAGE_TYPE)
        except Exception as e:
            logger.error(f"Message filter failed: {e}")

        page.click('button[type="submit"]')
        page.wait_for_load_state("networkidle")

        # COLLECT ROWS
        rows = page.locator("table tbody tr")
        row_count = rows.count()

        limit = row_count if MAX_ROWS is None else min(row_count, MAX_ROWS)

        tracking_urls = []

        for i in range(limit):
            row = rows.nth(i)
            link = row.locator('a[href*="ApiDiagnostics"]')

            if link.count() > 0:
                href = link.get_attribute("href")
                tracking_urls.append("https://mils.polarislibrary.com" + href)

        logger.info(f"Processing {len(tracking_urls)} tracking IDs")

        failure_details = []
        new_alerts = []

        for idx, url in enumerate(tracking_urls, start=1):
            logger.info(f"[{idx}/{len(tracking_urls)}] {url}")
            time.sleep(1)

            detail_page = context.new_page()

            try:
                detail_page.goto(url)
                detail_page.wait_for_load_state("networkidle")

                html = detail_page.content()
                records = extract_json(html)

                # Extract shared data
                circ_id = None
                state = None
                title = None
                borrower = None
                lender = None
                patron_agency = None
                patron_id = None
                item_agency = None
                item_barcode = None

                for r in records:
                    if isinstance(r, dict):
                        circ_id = circ_id or r.get("circId") or r.get("id")
                        state = state or r.get("lastCircState")
                        title = title or r.get("title")
                        borrower = borrower or r.get("borrowerCode")
                        lender = lender or r.get("lenderCode")
                        patron_agency = patron_agency or r.get("patronAgencyCode")
                        patron_id = patron_id or r.get("patronId")
                        item_agency = item_agency or r.get("itemAgencyCode")
                        item_barcode = item_barcode or r.get("itemBarcode")

                # FAILURE DETECTION
                for r in records:
                    if not isinstance(r, dict):
                        continue

                    status = (r.get("status") or "").lower()
                    error = r.get("error")

                    if status == "failure":
                        reason = r.get("reason") or "Unknown failure"
                    elif error:
                        reason = error
                    else:
                        continue

                    if not circ_id:
                        circ_id = url.split("/")[-1].split("?")[0]


                    new_alerts.append(circ_id)

                    logger.warning(f"FAILURE DETECTED | {circ_id} | {reason}")

                    clean_reason = "\n".join(
                        line.strip() for line in reason.splitlines()
                       )
                       
                    failure_block = f"""
                    <hr>
                    <b>circID:</b> {circ_id}<br>
                    <b>lastCircState:</b> {state or ""}<br>
                    <b>title:</b> {title or ""}<br>
                    <b>borrowerCode:</b> {borrower or ""}<br>
                    <b>patronAgencyCode:</b> {patron_agency or ""}<br>
                    <b>patronID:</b> {patron_id or ""}<br>
                    <b>lenderCode:</b> {lender or ""}<br>
                    <b>itemAgencyCode:</b> {item_agency or ""}<br>
                    <b>itemBarcode:</b> {item_barcode or ""}<br>
                    <b>reason:</b> {clean_reason}
                    </div>
                    """
                    failure_details.append(failure_block)

            except Exception:
                logger.exception(f"Error processing {url}")

            finally:
                detail_page.close()


        # CREATE SINGLE TICKET
        if failure_details:
            from datetime import datetime, timedelta

            today = datetime.today()

            if DATE_RANGE == "0":
                date_label = today.strftime("%Y-%m-%d")

            elif DATE_RANGE == "1":
                yesterday = today - timedelta(days=1)
                date_label = yesterday.strftime("%Y-%m-%d")

            elif DATE_RANGE in ["7", "30"]:
                days = int(DATE_RANGE)

                start_date = (today - timedelta(days=days)).strftime("%Y-%m-%d")
                end_date = today.strftime("%Y-%m-%d")

                date_label = f"{start_date} to {end_date}"

            else:
                date_label = f"Last {DATE_RANGE} Days"

            subject = f"MILS INN-Reach Failures - {date_label}"

            description = f"""
            <h3>MILS INN-Reach failure summary</h3>

            <p><b>Total failures:</b> {len(failure_details)}</p>

            {''.join(failure_details)}
            """

            create_ticket(subject, description)

        else:
            logger.info("No new failures found.")

        logger.info("Run complete.")
        browser.close()

# ---------------------------
# RUN
# ---------------------------
if __name__ == "__main__":
    main()
