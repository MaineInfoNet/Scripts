#!/usr/bin/env python3
# Run in sic

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
from datetime import date, timedelta

# ----------------------------
# Paths
# ----------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

REPORTS_DIR = os.path.join(BASE_DIR, "Reports")
ARCHIVE_DIR = os.path.join(REPORTS_DIR, "old")

os.makedirs(REPORTS_DIR, exist_ok=True)
os.makedirs(ARCHIVE_DIR, exist_ok=True)


# ----------------------------
# Logging
# ----------------------------

logging.basicConfig(
    filename="mils_reports.log",
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

logger = logging.getLogger(__name__)

console = logging.StreamHandler()
console.setLevel(logging.INFO)
console.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
logger.addHandler(console)

# ----------------------------
# Config
# ----------------------------

config = configparser.ConfigParser()
config.read(os.path.join(BASE_DIR, "config.ini"))

SQL_CONNECTION = config["sql"]["connection_string"]
BASE_URL = config["ssrs"]["base_url"]
USERNAME = config["ssrs"]["username"]
PASSWORD = config["ssrs"]["password"]

# ----------------------------
# CSV Writer
# ----------------------------

def csv_writer(rows, headers, csv_file):
    with open(csv_file, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        writer.writerows(rows)
    logger.info(f"CSV written: {csv_file}")

# ----------------------------
# Run Query
# ----------------------------

def run_query(query, csv_file):
    conn = pyodbc.connect(SQL_CONNECTION)
    cursor = conn.cursor()

    cursor.execute(query)

    headers = [column[0] for column in cursor.description]
    rows = cursor.fetchall()

    conn.close()

    csv_writer(rows, headers, csv_file)

# ----------------------------
# CSV → XLSX
# ----------------------------

def convert_csv_to_xlsx(csv_path):
    base, _ = os.path.splitext(csv_path)
    xlsx_path = base + ".xlsx"

    df = pd.read_csv(csv_path, encoding="utf-8-sig")
    df.to_excel(xlsx_path, index=False)

    logger.info(f"Converted to XLSX: {xlsx_path}")
    return xlsx_path

# ----------------------------
# Upload
# ----------------------------

def upload_file(session, file_path, folder_path):
    file_name = os.path.basename(file_path)

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

    return response.status_code in (200, 201, 409)

# ----------------------------
# Archive
# ----------------------------

def archive_file(file_path):
    file_name = os.path.basename(file_path)
    destination = os.path.join(ARCHIVE_DIR, file_name)

    if os.path.exists(destination):
        os.remove(destination)

    shutil.move(file_path, destination)
    logger.info(f"Archived {file_name}")

# ----------------------------
# Main
# ----------------------------

def main():

    logger.info("MILS export job started")

    # Date
    today = date.today()
    first_of_this_month = today.replace(day=1)
    last_day_previous_month = first_of_this_month - timedelta(days=1)
    ym = last_day_previous_month.strftime("%Y%m")

    # Queries
    queries = {
        "LibraryUseValue": """
WITH TransactionData AS (
    SELECT
        th.TransactionID,
        th.TranClientDate,

        MAX(CASE 
            WHEN td.TransactionSubTypeID = 38 THEN td.numValue 
        END) AS ItemRecordID,

        MAX(CASE 
            WHEN td.TransactionSubTypeID = 6 THEN td.numValue 
        END) AS PatronID

    FROM PolarisTransactions.Polaris.TransactionHeaders th
    INNER JOIN PolarisTransactions.Polaris.TransactionTypes tt
        ON th.TransactionTypeID = tt.TransactionTypeID
    INNER JOIN PolarisTransactions.Polaris.TransactionDetails td
        ON th.TransactionID = td.TransactionID

    WHERE tt.TransactionTypeDescription = 'Check Out'

    GROUP BY th.TransactionID, th.TranClientDate
)

SELECT
    o.Name AS PatronBranch,
    pc.Description AS PatronType,
    mt.Description AS MaterialType,
    COUNT(*) AS TotalCheckouts,

    SUM(
        CASE
            WHEN ird.Price IS NOT NULL AND ird.Price > 0 THEN ird.Price
            ELSE
                CASE
                    WHEN mt.Description LIKE '%Periodical Magazine%' THEN 5.00
                    WHEN mt.Description LIKE '%Ebook%' 
                      OR mt.Description LIKE '%Library of Things%'
                      OR mt.Description LIKE '%E-Audiobook%' THEN 10.00
                    WHEN mt.Description LIKE '%Microform%' THEN 11.00
                    WHEN mt.Description LIKE '%Music CD%' THEN 13.00
                    WHEN mt.Description LIKE '%Graphic Novel%' THEN 14.00
                    WHEN mt.Description LIKE '%INN-Reach 7 Day%'
                      OR mt.Description LIKE '%VHS%'
                      OR mt.Description LIKE '%Map, Atlas%' 
                      OR mt.Description LIKE '%DVD%' THEN 15.00
                    WHEN mt.Description LIKE '%Sheet Music%' THEN 16.00
                    WHEN mt.Description LIKE '%INN-Reach 3 Week%' THEN 18.00
                    WHEN mt.Description LIKE '%Book%' THEN 18.00
                    WHEN mt.Description LIKE '%E-Journal%'
                      OR mt.Description LIKE '%Computer File%'
                      OR mt.Description LIKE '%Blu-Ray%' THEN 20.00
                    WHEN mt.Description LIKE '%ILL Item%' THEN 25.00
                    WHEN mt.Description LIKE '%Video Game%'
                      OR mt.Description LIKE '%Game%' THEN 30.00
                    WHEN mt.Description LIKE '%Large Print%' THEN 31.00
                    WHEN mt.Description LIKE '%Audiobook%' THEN 35.00
                    WHEN mt.Description LIKE '%Kit%' THEN 48.00
                    WHEN mt.Description LIKE '%Pass%' THEN 50.00
                    WHEN mt.Description LIKE '%Equipment%' THEN 90.00
                    ELSE 10.00
                END
        END
    ) AS EstimatedValueSaved,

    SUM(CASE WHEN ird.Price IS NOT NULL AND ird.Price > 0 THEN 1 ELSE 0 END) AS UsedActualPrice,
    SUM(CASE WHEN ird.Price IS NULL OR ird.Price = 0 THEN 1 ELSE 0 END) AS UsedEstimatedPrice

FROM TransactionData t

INNER JOIN Polaris.Polaris.CircItemRecords cir
    ON t.ItemRecordID = cir.ItemRecordID

INNER JOIN Polaris.Polaris.ItemRecordDetails ird
    ON cir.ItemRecordID = ird.ItemRecordID

INNER JOIN Polaris.Polaris.MaterialTypes mt
    ON cir.MaterialTypeID = mt.MaterialTypeID

INNER JOIN Polaris.Polaris.Patrons p
    ON t.PatronID = p.PatronID

INNER JOIN Polaris.Polaris.PatronCodes pc
    ON p.PatronCodeID = pc.PatronCodeID

INNER JOIN Polaris.Polaris.Organizations o
    ON p.OrganizationID = o.OrganizationID

WHERE
    t.ItemRecordID IS NOT NULL
    AND t.PatronID IS NOT NULL
    AND mt.MaterialTypeID NOT IN (5,17,21,28,29,34)
    AND t.TranClientDate >= DATEADD(MONTH, DATEDIFF(MONTH, 0, GETDATE()) - 1, 0)
    AND t.TranClientDate < DATEADD(MONTH, DATEDIFF(MONTH, 0, GETDATE()), 0)
    AND p.PatronCodeID NOT IN (8,9,10,11,12,23,24,25)

GROUP BY
    o.Name,
    pc.Description,
    mt.Description

ORDER BY
    o.Name,
    pc.Description,
    mt.Description;
        """
    }

    # Folder mapping
    folder_map = {
        "LibraryUseValue": "/Polaris/Custom/MILS TOP HITS/Library Use Value",
    }

    # Session
    session = requests.Session()
    session.auth = HttpNtlmAuth(USERNAME, PASSWORD)
    session.headers.update({"Content-Type": "application/json"})

    # Main loop
    for name, query in queries.items():

        logger.info(f"Processing {name}")

        csv_path = os.path.join(REPORTS_DIR, f"{name}_{ym}.csv")

        run_query(query, csv_path)

        xlsx_path = convert_csv_to_xlsx(csv_path)

        folder = folder_map.get(name)

        if not folder:
            logger.error(f"No folder mapping found for {name}")
            continue

        success = upload_file(session, xlsx_path, folder)

        if success:
            archive_file(csv_path)
            archive_file(xlsx_path)
        else:
            logger.error(f"{name} upload failed")

    logger.info("MILS export job completed")


if __name__ == "__main__":
    main()
