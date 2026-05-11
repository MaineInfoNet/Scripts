#!/usr/bin/env python3

import os
import base64
import shutil
import pyodbc
import configparser
import pandas as pd
import requests
import logging
from requests_ntlm import HttpNtlmAuth
from datetime import date, timedelta
import warnings
warnings.filterwarnings("ignore", message="pandas only supports SQLAlchemy")

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
# Load Pricing Excel
# ----------------------------

PRICE_XLSX = os.path.join(BASE_DIR, "mils_roi.xlsx")

price_df = pd.read_excel(PRICE_XLSX, sheet_name="Prices")
est_df = pd.read_excel(PRICE_XLSX, sheet_name="Est")

price_df.columns = price_df.columns.str.strip()
est_df.columns = est_df.columns.str.strip()

# Lookup: (MaterialType, StatisticalCodeID)
price_lookup = {
    (
        str(row["Mattype Description"]).strip(),
        int(row["StatisticalCodeID"])
    ): float(row["Price"])
    for _, row in price_df.iterrows()
    if pd.notnull(row["Price"])
}

# Fallback: MaterialType only
est_lookup = {
    str(row["Material Type"]).strip(): float(row["Est. Price"])
    for _, row in est_df.iterrows()
    if pd.notnull(row["Est. Price"])
}

# ----------------------------
# Pricing Logic
# ----------------------------

def apply_pricing(df):

    def get_price(row):
        # Skip empty rows safely
        if pd.isna(row["MaterialType"]) or pd.isna(row["StatisticalCodeID"]):
            return None

        try:
            key = (
                str(row["MaterialType"]).strip(),
                int(row["StatisticalCodeID"])
            )

            if key in price_lookup:
                return price_lookup[key]

            if row["MaterialType"] in est_lookup:
                return est_lookup[row["MaterialType"]]

            return 10.00

        except Exception as e:
            print("Pricing error:", e, row)
            return None

    df = df.copy()

    df.loc[:, "EstimatedPrice"] = df.apply(get_price, axis=1)

    df = df.dropna(subset=["EstimatedPrice"]).copy()

    df.loc[:, "EstimatedValueSaved"] = (
        df["EstimatedPrice"] * df["TotalCheckouts"]
)

    return df

# ----------------------------
# Run Query
# ----------------------------

def run_query(query):
    conn = pyodbc.connect(SQL_CONNECTION)
    df = pd.read_sql(query, conn)
    conn.close()
    return df

# ----------------------------
# Save Outputs
# ----------------------------

def save_outputs(df, csv_path):
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")

    xlsx_path = csv_path.replace(".csv", ".xlsx")
    df.to_excel(xlsx_path, index=False)

    logger.info(f"Saved CSV + XLSX: {csv_path}")
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
    return response.status_code in (200, 201, 409)

# ----------------------------
# Archive
# ----------------------------

def archive_file(file_path):
    dest = os.path.join(ARCHIVE_DIR, os.path.basename(file_path))
    if os.path.exists(dest):
        os.remove(dest)
    shutil.move(file_path, dest)

# ----------------------------
# Main
# ----------------------------

def main():

    logger.info("MILS export job started")

    today = date.today()
    first_of_this_month = today.replace(day=1)
    last_day_previous_month = first_of_this_month - timedelta(days=1)
    ym = last_day_previous_month.strftime("%Y%m")

    query = """
WITH BaseTransactions AS (
    SELECT DISTINCT
        TH.TransactionID,
        TH.OrganizationID,
        TD.NumValue AS PatronCodeID
    FROM PolarisTransactions.Polaris.TransactionDetails TD WITH (NOLOCK)

    INNER JOIN PolarisTransactions.Polaris.TransactionHeaders TH WITH (NOLOCK)
        ON TD.TransactionID = TH.TransactionID
        AND TD.TransactionSubTypeID = 7

    WHERE
        TH.TransactionTypeID = 6001
        AND TH.TranClientDate >= DATEADD(MONTH, DATEDIFF(MONTH, 0, GETDATE()) - 1, 0)
        AND TH.TranClientDate < DATEADD(MONTH, DATEDIFF(MONTH, 0, GETDATE()), 0)
),

ItemPerTransaction AS (
    SELECT
        TD.TransactionID,
        MIN(TD.NumValue) AS ItemRecordID
    FROM PolarisTransactions.Polaris.TransactionDetails TD WITH (NOLOCK)
    WHERE TD.TransactionSubTypeID = 38
    GROUP BY TD.TransactionID
),

BaseData AS (
    SELECT
        O.Name AS PatronBranch,
        PC.Description AS PatronType,
        IPT.ItemRecordID,
        ISNULL(CIR.StatisticalCodeID, 7) AS StatisticalCodeID,
        CASE 
            WHEN CIR.StatisticalCodeID IS NULL THEN 'NA'
            ELSE SC.Description
        END AS StatisticalCode,

        CASE
            WHEN MT.Description IS NULL
                THEN 'None - Deleted Item Record'
            ELSE MT.Description
        END AS MaterialType

    FROM BaseTransactions BT

    LEFT JOIN ItemPerTransaction IPT
        ON BT.TransactionID = IPT.TransactionID

    LEFT JOIN Polaris.Polaris.CircItemRecords CIR WITH (NOLOCK)
        ON IPT.ItemRecordID = CIR.ItemRecordID

    LEFT JOIN Polaris.Polaris.MaterialTypes MT WITH (NOLOCK)
        ON CIR.MaterialTypeID = MT.MaterialTypeID

    LEFT JOIN Polaris.Polaris.StatisticalCodes SC WITH (NOLOCK)
        ON CIR.StatisticalCodeID = SC.StatisticalCodeID
        AND SC.OrganizationID = CIR.AssignedBranchID

    INNER JOIN Polaris.Organizations O WITH (NOLOCK)
        ON BT.OrganizationID = O.OrganizationID

    INNER JOIN Polaris.PatronCodes PC WITH (NOLOCK)
        ON PC.PatronCodeID = BT.PatronCodeID


)

SELECT
    PatronBranch,
    PatronType,
    MaterialType,
    StatisticalCodeID,
    StatisticalCode,
    COUNT(*) AS TotalCheckouts

FROM BaseData

GROUP BY
    PatronBranch,
    PatronType,
    MaterialType,
    StatisticalCodeID,
    StatisticalCode

ORDER BY
    PatronBranch,
    PatronType,
    MaterialType;
"""

    df = run_query(query)

    # Debug visibility
    print("Row count:", len(df))

    # Remove completely empty rows
    df = df.dropna(how="all")

    # Clean strings
    df["MaterialType"] = df["MaterialType"].astype(str).str.strip()

    # Apply pricing
    df = apply_pricing(df)

    csv_path = os.path.join(REPORTS_DIR, f"LibraryUseValue_{ym}.csv")

    xlsx_path = save_outputs(df, csv_path)

    session = requests.Session()
    session.auth = HttpNtlmAuth(USERNAME, PASSWORD)

    if upload_file(session, xlsx_path, "/Polaris/Custom/MILS TOP HITS/Library Use Value"):
        archive_file(csv_path)
        archive_file(xlsx_path)

    logger.info("MILS export job completed")

# ----------------------------

if __name__ == "__main__":
    main()
