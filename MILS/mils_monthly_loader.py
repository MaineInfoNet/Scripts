#!/usr/bin/env python3
# Run in sic

# ---------------------------
# MILS Monthly Export & Upload to PRM
# Lynn Uhlman - Maine InfoNet
# ---------------------------

import os
import sys
import base64
import configparser
import pandas as pd
import shutil
import requests
from requests_ntlm import HttpNtlmAuth
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

config = configparser.ConfigParser()
config.read(os.path.join(BASE_DIR, "config.ini"))

BASE_URL = config["ssrs"]["base_url"]
USERNAME = config["ssrs"]["username"]
PASSWORD = config["ssrs"]["password"]

# Local directory where monthly reports are stored and the archive for after they are uploaded
REPORTS_DIR = r"C:\Scripts\Polaris\mils_monthly\Reports"
ARCHIVE_DIR = os.path.join(REPORTS_DIR, "old")

UPLOAD_MAP = {
    "Item_Count": "/Polaris/Custom/MILS TOP HITS/Item Counts",
    "Patron_Count": "/Polaris/Custom/MILS TOP HITS/Patron Counts",
    "Bib_Count": "/Polaris/Custom/MILS TOP HITS/Bib Counts",
    "ShelfLocation_Count": "/Polaris/Custom/MILS TOP HITS/ITEM -- Shelf Location Statistics Reports"
}

DRY_RUN = "--dry-run" in sys.argv


def get_previous_month_string():
    today = datetime.today()
    first_of_this_month = today.replace(day=1)
    previous_month_last_day = first_of_this_month - timedelta(days=1)
    return previous_month_last_day.strftime("%Y%m")


def convert_csv_to_xlsx(csv_path):
    base, _ = os.path.splitext(csv_path)
    xlsx_path = base + ".xlsx"

    if os.path.exists(xlsx_path):
        print(f"XLSX already exists — skipping conversion: {os.path.basename(xlsx_path)}")
        return os.path.basename(xlsx_path)

    # If you have leading zeros you need to preserve, keep dtype=str
    df = pd.read_csv(csv_path, dtype=str, encoding="utf-8-sig")
    df.to_excel(xlsx_path, index=False)

    print(f"Converted to XLSX: {os.path.basename(xlsx_path)}")
    return os.path.basename(xlsx_path)


def find_file(prefix, month_suffix):
    csv_name = f"{prefix}_{month_suffix}.csv"
    csv_path = os.path.join(REPORTS_DIR, csv_name)

    if os.path.exists(csv_path):
        print(f"Found CSV: {csv_name} — converting to XLSX")
        return convert_csv_to_xlsx(csv_path), csv_name

    return None

# move uploaded files to archive folder function
def archive_file(file_name):
    os.makedirs(ARCHIVE_DIR, exist_ok=True)

    source_path = os.path.join(REPORTS_DIR, file_name)
    destination_path = os.path.join(ARCHIVE_DIR, file_name)

    # Overwrite if already exists
    if os.path.exists(destination_path):
        os.remove(destination_path)

    shutil.move(source_path, destination_path)
    print(f"Moved to archive: {file_name}")

def upload_file(session, file_name, folder_path):
    print(f"\nProcessing: {file_name}")
    print(f"Destination: {folder_path}")

    if DRY_RUN:
        print("DRY RUN: Upload skipped.")
        return

    file_path = os.path.join(REPORTS_DIR, file_name)

    with open(file_path, "rb") as f:
        encoded_content = base64.b64encode(f.read()).decode("utf-8")

    payload = {
        "Name": file_name,
        "Path": f"{folder_path}/{file_name}",
        "Content": encoded_content,
        "ContentType": "application/octet-stream",
    }

    url = f"{BASE_URL}/api/v2.0/ExcelWorkbooks"
    response = session.post(url, json=payload)

    if response.status_code in (200, 201):
        print(f"Uploaded: {file_name}")
        archive_file(file_name)
        
    elif response.status_code == 409:
        print(f"Skipped (already exists): {file_name}")
    else:
        print(f"Upload failed: {file_name}")
        print(response.status_code)
        print(response.text)


def main():
    print("Starting Monthly Upload Process")

    month_suffix = get_previous_month_string()
    print(f"Target Month: {month_suffix}")

    session = requests.Session()
    session.auth = HttpNtlmAuth(USERNAME, PASSWORD)
    session.headers.update({"Content-Type": "application/json"})

    for prefix, folder_path in UPLOAD_MAP.items():
        result = find_file(prefix, month_suffix)

        if result:
            xlsx_name, csv_name = result
            upload_file(session, xlsx_name, folder_path)
            archive_file(csv_name)  # move the original CSV too
        else:
            print(f"File not found: {prefix}_{month_suffix}.csv")
        else:
            print(f"File not found: {prefix}_{month_suffix}.csv")


if __name__ == "__main__":
    main()
