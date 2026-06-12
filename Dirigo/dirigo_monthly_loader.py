#!/usr/bin/env python3
# Run in sic

# ---------------------------
# Dirigo Monthly Export & Upload to PRM (SSRS)
# Lynn Uhlman - Maine InfoNet
# ---------------------------

import os
import csv
import base64
import shutil
import pyodbc
import configparser
import pandas as pd
import requests
import logging
from requests_ntlm import HttpNtlmAuth
from datetime import datetime, timedelta, date


# --------------------------------------------------
# Paths
# --------------------------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

REPORTS_DIR = os.path.join(BASE_DIR, "Reports")
ARCHIVE_DIR = os.path.join(REPORTS_DIR, "old")

os.makedirs(REPORTS_DIR, exist_ok=True)
os.makedirs(ARCHIVE_DIR, exist_ok=True)


# --------------------------------------------------
# Logging
# --------------------------------------------------

LOG_FILE = os.path.join(BASE_DIR, "dirigo_reports.log")

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

logger = logging.getLogger(__name__)

console = logging.StreamHandler()
console.setLevel(logging.INFO)
console.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
logger.addHandler(console)

# --------------------------------------------------
# Load Config
# --------------------------------------------------

config = configparser.ConfigParser()
config.read(os.path.join(BASE_DIR, "config.ini"))

SQL_CONNECTION = config["sql"]["connection_string"]

BASE_URL = config["ssrs"]["base_url"]
USERNAME = config["ssrs"]["username"]
PASSWORD = config["ssrs"]["password"]


UPLOAD_MAP = {
    "Circulation_Summary": "/Polaris/Custom/Dirigo",
}


# --------------------------------------------------
# Date Helpers
# --------------------------------------------------

def get_previous_month_string():

    today = date.today()

    first_of_this_month = today.replace(day=1)
    last_day_previous_month = first_of_this_month - timedelta(days=1)

    return last_day_previous_month.strftime("%Y%m")


# --------------------------------------------------
# CSV Writer
# --------------------------------------------------

def csv_writer(rows, headers, csv_file):

    with open(csv_file, "w", encoding="utf-8", newline="") as f:

        writer = csv.writer(f)
        writer.writerow(headers)
        writer.writerows(rows)

    logger.info(f"CSV written: {csv_file}")


# --------------------------------------------------
# Run Query
# --------------------------------------------------

def run_query(query, csv_file):

    logger.info("Running SQL query")

    conn = pyodbc.connect(SQL_CONNECTION)
    cursor = conn.cursor()

    cursor.execute(query)

    headers = [column[0] for column in cursor.description]
    rows = cursor.fetchall()

    conn.close()

    logger.info(f"Returned {len(rows)} rows")

    csv_writer(rows, headers, csv_file)


# --------------------------------------------------
# CSV → XLSX
# --------------------------------------------------

def convert_csv_to_xlsx(csv_path):

    base, _ = os.path.splitext(csv_path)
    xlsx_path = base + ".xlsx"

    df = pd.read_csv(csv_path, encoding="utf-8-sig")

    df.to_excel(xlsx_path, index=False)

    logger.info(f"Converted to XLSX: {xlsx_path}")

    return xlsx_path


# --------------------------------------------------
# Upload
# --------------------------------------------------

def upload_file(session, file_path, folder_path):

    file_name = os.path.basename(file_path)

    logger.info(f"Uploading {file_name}")

    with open(file_path, "rb") as f:

        encoded_content = base64.b64encode(f.read()).decode("utf-8")

    payload = {
        "Name": file_name,
        "Path": f"{folder_path.rstrip('/')}/{file_name}",
        "Content": encoded_content,
        "ContentType": "application/octet-stream",
    }

    url = f"{BASE_URL}/api/v2.0/ExcelWorkbooks"

    response = session.post(url, json=payload)

    logger.info(f"Upload response: {response.status_code}")
    logger.info(response.text)

    if response.status_code in (200, 201):

        logger.info(f"Uploaded {file_name}")
        return True

    elif response.status_code == 409:

        logger.info(f"File already exists {file_name}")
        return True

    else:

        logger.error(response.text)
        return False


# --------------------------------------------------
# Archive
# --------------------------------------------------

def archive_file(file_path):

    file_name = os.path.basename(file_path)

    destination = os.path.join(ARCHIVE_DIR, file_name)

    if os.path.exists(destination):
        os.remove(destination)

    shutil.move(file_path, destination)

    logger.info(f"Archived {file_name}")


# --------------------------------------------------
# Main
# --------------------------------------------------

def main():

    logger.info("Starting Dirigo Monthly Job")

    month_suffix = get_previous_month_string()

    circulation_summary_query = """ 
    WITH TransactionDetailAttributes AS (
        -- This CTE retrieves the CollectionID and OwningBranchID from the TransactionDetails table.
        -- It pivots rows to columns for a given TransactionID.

        SELECT
            TransactionID,

            -- Extracts CollectionID if TransactionSubTypeID is 61 (can be NULL)
            MAX(CASE WHEN TransactionSubTypeID = 61 THEN numValue END) AS CollectionIDValue,

            -- Extracts OwningBranchID if TransactionSubTypeID is 125 (must be present)
            MAX(CASE WHEN TransactionSubTypeID = 125 THEN numValue END) AS OwningBranchIDValue,

            -- Extracts MaterialTypeID if TransactionSubTypeID is 4 (must be present)
            MAX(CASE WHEN TransactionSubTypeID = 4 THEN numValue END) AS MaterialTypeIDValue,

            -- Extracts PatronCodeID if TransactionSubTypeID is 7 (must be present)
            MAX(CASE WHEN TransactionSubTypeID = 7 THEN numValue END) AS PatronCodeIDValue,

            -- Extracts ItemStatisticalCodeID if TransactionSubTypeID is 60 (must be present)
            MAX(CASE WHEN TransactionSubTypeID = 60 THEN numValue END) AS ItemStatisticalCodeIDValue

        FROM PolarisTransactions.Polaris.TransactionDetails

        WHERE TransactionSubTypeID IN (4, 7, 60, 61, 125)

        GROUP BY TransactionID

        HAVING
            -- Ensure OwningBranchID exists
            MAX(CASE WHEN TransactionSubTypeID = 125 THEN numValue END) IS NOT NULL
    )

    SELECT
        -- The CKO (checkout location)
        CKO_Org.Abbreviation AS CKOLocation,

        -- Collection of the item (may be NULL)
        C.Abbreviation AS collection,

        -- Owning branch of the item
        Owning_Org.Abbreviation AS OwningLocation,

        -- Material type description
        MT.Description AS MaterialType,

        -- Patron code
        P.Description AS PatronCode,

        -- Statistical code
        SC.Description AS StatisticalCode,

        -- Count of unique checkout transactions
        COUNT(DISTINCT TH.TransactionID) AS CirculationCount

    FROM PolarisTransactions.Polaris.TransactionHeaders TH

    -- Join CTE containing pivoted transaction attributes
    JOIN TransactionDetailAttributes TDA
        ON TH.TransactionID = TDA.TransactionID

    -- Translate CollectionID → Collection Name
    LEFT JOIN Polaris.Polaris.Collections C
        ON C.CollectionID = TDA.CollectionIDValue

    -- Checkout location
    JOIN Polaris.Polaris.Organizations CKO_Org
        ON CKO_Org.OrganizationID = TH.OrganizationID

    -- Owning location
    JOIN Polaris.Polaris.Organizations Owning_Org
        ON Owning_Org.OrganizationID = TDA.OwningBranchIDValue

    -- Material type lookup
    JOIN Polaris.Polaris.MaterialTypes MT
        ON MT.MaterialTypeID = TDA.MaterialTypeIDValue

    -- Patron code lookup
    JOIN Polaris.Polaris.PatronCodes P
        ON P.PatronCodeID = TDA.PatronCodeIDValue

    -- Statistical code lookup
    JOIN Polaris.Polaris.StatisticalCodes SC
        ON SC.StatisticalCodeID = TDA.ItemStatisticalCodeIDValue

    WHERE
        -- Checkout transactions
        TH.TransactionTypeID = 6001

        -- Previous month date range
        AND TH.TranClientDate >= DATEADD(MONTH, DATEDIFF(MONTH, 0, GETDATE()) - 1, 0)
        AND TH.TranClientDate <  DATEADD(MONTH, DATEDIFF(MONTH, 0, GETDATE()), 0)

        -- Exclude INN-Reach statistical code
        AND SC.Description <> 'INN-Reach'

    GROUP BY
        CKO_Org.Abbreviation,
        Owning_Org.Abbreviation,
        MT.Description,
        C.Abbreviation,
        P.Description,
        SC.Description

    ORDER BY
        CKO_Org.Abbreviation,
        Owning_Org.Abbreviation,
        MT.Description,
        C.Abbreviation,
        P.Description,
        SC.Description;
    """

    csv_path = os.path.join(
        REPORTS_DIR,
        f"Circulation_Summary_{month_suffix}.csv"
    )

    # Run SQL
    run_query(circulation_summary_query, csv_path)

    # Convert
    xlsx_path = convert_csv_to_xlsx(csv_path)

    # Upload
    session = requests.Session()
    session.auth = HttpNtlmAuth(USERNAME, PASSWORD)
    session.headers.update({"Content-Type": "application/json"})

    upload_success = upload_file(
        session,
        xlsx_path,
        "/Polaris/Custom/Dirigo"
    )

    if upload_success:

        archive_file(csv_path)
        archive_file(xlsx_path)

    logger.info("Dirigo Monthly Job Complete")


if __name__ == "__main__":
    main()
