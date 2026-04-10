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
        "Bib_Count": """ 
		SELECT 
			cir.AssignedBranchID, 
			o.Name, 
			COUNT(DISTINCT b.BibliographicRecordID) AS BibCount 
		FROM BibliographicRecords b 
		INNER JOIN CircItemRecords cir 
			ON b.BibliographicRecordID = cir.AssociatedBibRecordID 
		INNER JOIN Organizations o 
			ON cir.AssignedBranchID = o.OrganizationID 
		WHERE b.RecordStatusID = 1 
		GROUP BY cir.AssignedBranchID, o.Name 
		ORDER BY cir.AssignedBranchID; 
		""", 
		
		"Item_Count": """ 
		SELECT 
		    o.Name AS AssignedBranch, 
			sc.Description AS StatisticalCodeName, 
			COUNT(DISTINCT cir.ItemRecordID) AS ItemCount 
		FROM CircItemRecords cir WITH (NOLOCK) 
		INNER JOIN Organizations o WITH (NOLOCK) 
			ON cir.AssignedBranchID = o.OrganizationID 
		INNER JOIN StatisticalCodes sc WITH (NOLOCK) 
			ON cir.StatisticalCodeID = sc.StatisticalCodeID 
		WHERE cir.RecordStatusID = 1 
		    AND cir.ItemStatusID NOT IN (7,10,11) 
		    AND cir.ILLFlag = 0 
		GROUP BY o.Name, sc.Description 
		ORDER BY o.Name, sc.Description; 
		""", 
		
		"Patron_Count": """ 
		SELECT o.Name, 
		    pc.Description, 
		    COUNT(DISTINCT p.PatronID) AS PatronCount 
		FROM Patrons p WITH (NOLOCK) 
		INNER JOIN PatronRegistration pr WITH (NOLOCK) 
		    ON p.PatronID = pr.PatronID 
		INNER JOIN Organizations o WITH (NOLOCK) 
		    ON p.OrganizationID = o.OrganizationID 
		INNER JOIN PatronCodes pc WITH (NOLOCK) 
		    ON p.PatronCodeID = pc.PatronCodeID 
		WHERE pr.ExpirationDate IS NOT NULL 
		    AND pr.ExpirationDate >= DATEADD(YEAR, -3, CAST(GETDATE() AS DATE)) 
		    AND p.RecordStatusID = 1 
		GROUP BY o.Name, pc.Description 
		ORDER BY o.Name, pc.Description; 
		""", 
		
		"ShelfLocation_Count": """ 
		SELECT 
		    oi.Name AS ItemBranchName, 
		    sl.Description AS ShelfLocationDescription, 
		    COUNT(*) AS NumberOfItems 
		FROM CircItemRecords cir WITH (NOLOCK) 
		INNER JOIN ItemRecordDetails ird WITH (NOLOCK) 
		    ON cir.ItemRecordID = ird.ItemRecordID 
		INNER JOIN Organizations oi WITH (NOLOCK) 
		    ON cir.AssignedBranchID = oi.OrganizationID 
		LEFT JOIN ShelfLocations sl WITH (NOLOCK) 
		    ON cir.ShelfLocationID = sl.ShelfLocationID 
		    AND cir.AssignedBranchID = sl.OrganizationID 
		WHERE cir.ILLFlag = 0 
		    AND cir.RecordStatusID = 1 
		GROUP BY oi.Name, sl.Description 
		ORDER BY ItemBranchName, ShelfLocationDescription, COUNT(*) DESC; 
		"""
	}

    # Folder mapping
    folder_map = {
        "Item_Count": "/Polaris/Custom/MILS TOP HITS/Item Counts",
        "Patron_Count": "/Polaris/Custom/MILS TOP HITS/Patron Counts",
        "Bib_Count": "/Polaris/Custom/MILS TOP HITS/Bib Counts",
        "ShelfLocation_Count": "/Polaris/Custom/MILS TOP HITS/ITEM -- Shelf Location Statistics Reports"
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
