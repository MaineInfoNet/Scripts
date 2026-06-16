#!/usr/bin/env python3
# Run in counts

# ---------------------------
# ILS Counts
# Lynn Uhlman - Maine InfoNet
# ---------------------------

import csv
import configparser
import logging
from pathlib import Path
from datetime import datetime
import xml.etree.ElementTree as ET

import requests
import pyodbc
import psycopg2

from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle
)

from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.pagesizes import letter, landscape

import smtplib
from email.message import EmailMessage

# ---------------------------
# PATHS
# ---------------------------

BASE_DIR = Path(__file__).resolve().parent

CONFIG_FILE = BASE_DIR / "config.ini"

REPORTS_DIR = BASE_DIR / "Reports"
REPORTS_DIR.mkdir(exist_ok=True)

# ---------------------------
# LOGGING
# ---------------------------

LOG_FILE = BASE_DIR / "ils_counts.log"

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)

logger = logging.getLogger(__name__)

console = logging.StreamHandler()
console.setLevel(logging.INFO)
console.setFormatter(
    logging.Formatter("%(asctime)s %(levelname)s %(message)s")
)
logger.addHandler(console)

# ---------------------------
# CONFIG
# ---------------------------

config = configparser.ConfigParser(interpolation=None)
config.read(CONFIG_FILE)

# ---------------------------
# ALMA
# ---------------------------

ROWSET_NS = {
    "rs": "urn:schemas-microsoft-com:xml-analysis:rowset"
}


def get_alma_counts(url):

    response = requests.get(url, timeout=60)
    response.raise_for_status()

    root = ET.fromstring(response.text)

    row = root.find(".//rs:Row", ROWSET_NS)

    if row is None:
        raise ValueError("No Row found in Alma Analytics response.")

    item_count = int(row.findtext("rs:Column1", namespaces=ROWSET_NS))
    bib_count = int(row.findtext("rs:Column2", namespaces=ROWSET_NS))

    return {
        "items": item_count,
        "bibs": bib_count
    }


def run_alma_counts():

    logger.info("Processing Alma")

    total_records_url = config["alma"]["total_records_url"]
    contributed_records_url = config["alma"]["contributed_records_url"]

    total_counts = get_alma_counts(total_records_url)
    contributed_counts = get_alma_counts(contributed_records_url)

    return {
        "system": "UMS Alma",
        "total_bibs": total_counts["bibs"],
        "total_items": total_counts["items"],
        "mainecat_bibs": contributed_counts["bibs"],
        "mainecat_items": contributed_counts["items"]
    }


# ---------------------------
# POLARIS
# ---------------------------

MILS_TOTAL_QUERY = """
SELECT
    'Final Bibs' AS RecordType,
    COUNT(*) AS TotalCount
FROM Polaris.BibliographicRecords br
WHERE br.RecordStatusID = 1
    AND ISNULL(br.ILLFlag,0)=0

UNION ALL

SELECT
    'Final Items',
    COUNT(*)
FROM Polaris.CircItemRecords cir
WHERE cir.RecordStatusID = 1
    AND ISNULL(cir.ILLFlag,0)=0;
"""

MILS_CONTRIBUTED_QUERY = """
SELECT
    'Final Bibs' AS RecordType,
    COUNT(DISTINCT br.BibliographicRecordID) AS TotalCount
FROM Polaris.BibliographicRecords br
INNER JOIN Polaris.CircItemRecords cir
    ON br.BibliographicRecordID = cir.AssociatedBibRecordID
WHERE br.RecordStatusID = 1
  AND cir.RecordStatusID = 1
  AND ISNULL(cir.LoanableOutsideSystem,0)=1

UNION ALL

SELECT
    'Final Items',
    COUNT(*)
FROM Polaris.CircItemRecords cir
WHERE cir.RecordStatusID = 1
  AND ISNULL(cir.LoanableOutsideSystem,0)=1
  AND AssignedCollectionID NOT IN (19)
  AND MaterialTypeID NOT IN (5,6,17);
"""

DIRIGO_TOTAL_QUERY = MILS_TOTAL_QUERY

DIRIGO_CONTRIBUTED_QUERY = """
SELECT
    'Final Bibs' AS RecordType,
    COUNT(DISTINCT br.BibliographicRecordID) AS TotalCount
FROM Polaris.BibliographicRecords br
INNER JOIN Polaris.CircItemRecords cir
    ON br.BibliographicRecordID = cir.AssociatedBibRecordID
WHERE br.RecordStatusID = 1
  AND cir.RecordStatusID = 1
  AND ISNULL(cir.LoanableOutsideSystem,0)=1

UNION ALL

SELECT
    'Final Items',
    COUNT(*)
FROM Polaris.CircItemRecords cir
WHERE cir.RecordStatusID = 1
  AND ISNULL(cir.LoanableOutsideSystem,0)=1
  AND AssignedCollectionID NOT IN
    (53,65,67,73,87,89,90,92,119,124,125,127,
     128,129,130,131,132,133,137,138)
  AND MaterialTypeID NOT IN
    (1,2,3,14,24,15,19,20);
"""


def run_polaris_counts(connection_string,
                       total_query,
                       contributed_query,
                       system_name):

    logger.info(f"Processing {system_name}")

    conn = pyodbc.connect(connection_string)

    cursor = conn.cursor()

    cursor.execute(total_query)

    totals = {}

    for row in cursor.fetchall():
        totals[row.RecordType] = row.TotalCount

    cursor.execute(contributed_query)

    contrib = {}

    for row in cursor.fetchall():
        contrib[row.RecordType] = row.TotalCount

    conn.close()

    return {
        "system": system_name,
        "total_bibs": totals["Final Bibs"],
        "total_items": totals["Final Items"],
        "mainecat_bibs": contrib["Final Bibs"],
        "mainecat_items": contrib["Final Items"]
    }


# ---------------------------
# SIERRA / MINERVA
# ---------------------------

MINERVA_TOTAL_QUERY = """
SELECT
    'Total Bibs' AS RecordType,
    COUNT(*) AS TotalCount
FROM sierra_view.bib_record
WHERE record_id IS NOT NULL

UNION ALL

SELECT
    'Total Items',
    COUNT(*)
FROM sierra_view.item_record
WHERE record_id IS NOT NULL
  AND item_status_code NOT IN ('w','z','l','$');
"""

MINERVA_CONTRIBUTED_QUERY = """
SELECT
    'Total Bibs' AS RecordType,
    COUNT(*) AS TotalCount
FROM sierra_view.bib_record
WHERE record_id IS NOT NULL
  AND bcode3 NOT IN ('0','n')

UNION ALL

SELECT
    'Total Items',
    COUNT(*)
FROM sierra_view.item_record
WHERE record_id IS NOT NULL
  AND item_status_code NOT IN ('w','z','l','$')
  AND icode2 NOT IN ('e','z','w')
  AND itype_code_num < 100;
"""


def run_minerva_counts():

    logger.info("Processing Minerva")

    conn = psycopg2.connect(
        host=config["minerva"]["host"],
        port=config["minerva"]["port"],
        dbname=config["minerva"]["database"],
        user=config["minerva"]["user"],
        password=config["minerva"]["password"]
    )

    cursor = conn.cursor()

    cursor.execute(MINERVA_TOTAL_QUERY)

    totals = {}

    for record_type, count in cursor.fetchall():
        totals[record_type] = count

    cursor.execute(MINERVA_CONTRIBUTED_QUERY)

    contrib = {}

    for record_type, count in cursor.fetchall():
        contrib[record_type] = count

    conn.close()

    return {
        "system": "Minerva",
        "total_bibs": totals["Total Bibs"],
        "total_items": totals["Total Items"],
        "mainecat_bibs": contrib["Total Bibs"],
        "mainecat_items": contrib["Total Items"]
    }


# ---------------------------
# CSV OUTPUT
# ---------------------------

def write_csv(results):

    output_file = REPORTS_DIR / (
        f"ils_counts_{datetime.now():%Y%m%d}.csv"
    )

    with open(output_file, "w", newline="", encoding="utf-8") as csvfile:

        writer = csv.DictWriter(
            csvfile,
            fieldnames=[
                "run_date",
                "system",
                "total_bibs",
                "total_items",
                "mainecat_bibs",
                "mainecat_items",
                "not_contributed_bibs",
                "not_contributed_items"
            ]
        )

        writer.writeheader()

        for row in results:

            row["run_date"] = datetime.now().strftime("%Y-%m-%d")

            row["not_contributed_bibs"] = (
                row["total_bibs"] - row["mainecat_bibs"]
            )

            row["not_contributed_items"] = (
                row["total_items"] - row["mainecat_items"]
            )

            writer.writerow(row)

    logger.info(f"CSV written: {output_file}")

    return output_file


# ---------------------------
# CREATE PDF
# ---------------------------
def create_pdf_report(file_path, results):

    file_path = str(file_path)

    print(type(file_path))
    print(file_path)

    doc = SimpleDocTemplate(
        file_path,
        pagesize=landscape(letter),
        topMargin=30
    )

    print(type(file_path))
    print(file_path)

    styles = getSampleStyleSheet()

    elements = []
    
    
    # -----------------------
    # Report Header
    # -----------------------
    
    elements.append(
        Paragraph(
            "ILS Counts Report",
            styles["Title"]
        )
    )

    elements.append(Spacer(1, 20))


    # -----------------------
    # Overall Totals Table
    # -----------------------

    header_table = Table(
        [[Paragraph("System Totals", styles["Heading2"])]],
        colWidths=[320],
        hAlign="LEFT"
    )

    header_table.setStyle(
        TableStyle([
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ])
    )

    elements.append(header_table)


    table_data = [[
        "System",
        "Total Bibs",
        "Total Items",
        "MaineCat Bibs",
        "MaineCat Items"
    ]]

    total_bibs = 0
    total_items = 0
    total_mc_bibs = 0
    total_mc_items = 0

    for row in results:

        table_data.append([
            row["system"],
            f'{row["total_bibs"]:,}',
            f'{row["total_items"]:,}',
            f'{row["mainecat_bibs"]:,}',
            f'{row["mainecat_items"]:,}'
        ])

        total_bibs += row["total_bibs"]
        total_items += row["total_items"]
        total_mc_bibs += row["mainecat_bibs"]
        total_mc_items += row["mainecat_items"]

    table = Table(
        table_data,
        colWidths=[100,90,90,90,90],
        hAlign="LEFT"
    )

    table.setStyle(
        TableStyle([
            ("BACKGROUND",(0,0),(-1,0),colors.lightgrey),
            ("GRID",(0,0),(-1,-1),1,colors.black),
            ("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),
            ("ALIGN",(1,1),(-1,-1),"RIGHT")
        ])
    )

    elements.append(table)

    elements.append(Spacer(1,20))

    summary_data = [
        ["Metric","Count"],
        ["Total Bibs", f"{total_bibs:,}"],
        ["Total Items", f"{total_items:,}"],
        ["Total MaineCat Bibs", f"{total_mc_bibs:,}"],
        ["Total MaineCat Items", f"{total_mc_items:,}"]
    ]

    summary_table = Table(
        summary_data,
        colWidths=[200,120],
        hAlign="LEFT"
    )

    summary_table.setStyle(
        TableStyle([
            ("BACKGROUND",(0,0),(-1,0),colors.lightgrey),
            ("GRID",(0,0),(-1,-1),1,colors.black),
            ("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),
            ("ALIGN",(1,1),(-1,-1),"RIGHT")
        ])
    )

    header_table = Table(
        [[Paragraph("Summary Totals", styles["Heading2"])]],
        colWidths=[320],
        hAlign="LEFT"
    )

    header_table.setStyle(
        TableStyle([
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ])
    )

    elements.append(header_table)

    elements.append(summary_table)

    
    # -----------------------
    # Footer
    # -----------------------

    elements.append(Spacer(1, 20))

    def add_footer(canvas, doc):
        canvas.saveState()    
        footer_text = f"Run Date: {datetime.now():%Y-%m-%d}"

        canvas.drawRightString(
            doc.pagesize[0] - 36,
            20,
            footer_text
        )

        canvas.restoreState()
        
    # -----------------------
    # Build PDF
    # -----------------------

    doc.build(
        elements,
        onFirstPage=add_footer,
        onLaterPages=add_footer
    )
    
    logger.info(f"PDF written: {file_path}")

    return file_path


# ---------------------------
# Email
# ---------------------------

def send_email_with_attachment(subject, body, attachment_path):
    config = configparser.ConfigParser()
    config.read("config.ini")

    email_cfg = config["email"]

    smtp_server = email_cfg["smtp_server"]
    smtp_port = int(email_cfg.get("smtp_port", 587))
    username = email_cfg["username"]
    password = email_cfg["password"]
    from_email = email_cfg.get("from_email", username)
    to_emails = [e.strip() for e in email_cfg["to_emails"].split(",")]
    use_tls = email_cfg.get("use_tls", "true").lower() == "true"

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_email
    msg["To"] = ", ".join(to_emails)
    msg.set_content(body)

    with open(attachment_path, "rb") as f:
        msg.add_attachment(
            f.read(),
            maintype="application",
            subtype="pdf",
            filename=Path(attachment_path).name,
        )

    with smtplib.SMTP(smtp_server, smtp_port) as smtp:
        if use_tls:
            smtp.starttls()
        smtp.login(username, password)
        smtp.send_message(msg)

    logger.info("Email sent successfully")

    
# ---------------------------
# MAIN
# ---------------------------

def main():

    logger.info("ILS Counts Job Started")

    results = []

    results.append(run_alma_counts())

    results.append(
        run_polaris_counts(
            config["mils"]["mils_connection_string"],
            MILS_TOTAL_QUERY,
            MILS_CONTRIBUTED_QUERY,
            "MILS"
        )
    )

    results.append(
        run_polaris_counts(
            config["dirigo"]["dirigo_connection_string"],
            DIRIGO_TOTAL_QUERY,
            DIRIGO_CONTRIBUTED_QUERY,
            "Dirigo"
        )
    )

    results.append(run_minerva_counts())

    # Write CSV
    output_file = write_csv(results)

    # Create PDF
    pdf_file = REPORTS_DIR / (
        f"ils_counts_{datetime.now():%Y%m%d}.pdf"
    )

    create_pdf_report(
        pdf_file,
        results
    )
    
    logger.info(f"CSV Output: {output_file}")
    logger.info(f"PDF Output: {pdf_file}")
    

    # Send Email with PDF
    logger.info(f"Attachment path: {pdf_file}")
    logger.info(f"Attachment exists: {pdf_file.exists()}")
    logger.info(f"Attachment size: {pdf_file.stat().st_size:,} bytes")
    logger.info("Sending email")

    send_email_with_attachment(
        subject="ILS Counts Report",
        body="Attached is the latest ILS Counts report.",
        attachment_path=str(pdf_file)
    )

    logger.info("Email sent")


    logger.info("ILS Counts Job Completed")

if __name__ == "__main__":
    main()
