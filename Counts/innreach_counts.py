#!/usr/bin/env python3
# Run in counts

# ---------------------------
# INN-Reach Counts
# Lynn Uhlman - Maine InfoNet
# ---------------------------

import psycopg2
import csv
import configparser
import os
from datetime import datetime
import logging

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
# Logging
# ---------------------------
logging.basicConfig(
    filename="innreach.log",
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

logger = logging.getLogger(__name__)

console = logging.StreamHandler()
console.setLevel(logging.INFO)
console.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
logger.addHandler(console)

# ---------------------------
# Directories
# ---------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPORTS_DIR = os.path.join(BASE_DIR, "Reports")
os.makedirs(REPORTS_DIR, exist_ok=True)

# ---------------------------
# Database Connection
# ---------------------------
def get_db_connection():
    config = configparser.ConfigParser()
    config.read("config.ini")

    db = config["postgres"]

    return psycopg2.connect(
        host=db["host"],
        port=db["port"],
        database=db["database"],
        user=db["user"],
        password=db["password"],
        sslmode=db.get("sslmode", "require"),
        application_name=db.get("application_name", "script"),
        connect_timeout=int(db.get("connect_timeout", 10)),
    )

# ---------------------------
# CSV Writer
# ---------------------------
def write_csv(rows, headers, file_path):
    with open(file_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        writer.writerows(rows)

    logger.info(f"CSV written: {file_path}")
    return file_path



# ---------------------------
# Scalar query for bibs
# ---------------------------
def run_scalar_query(query):
    conn = None
    cursor = None

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(query)
        result = cursor.fetchone()
        return result[0] if result else None

    except Exception:
        logger.exception("Scalar query failed")
        return None

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()



# ---------------------------
# Query Runner
# ---------------------------
def run_query(query, output_file, return_rows=False):
    logger.info(f"Running query: {output_file}")

    conn = None
    cursor = None

    try:
        conn = get_db_connection()
        cursor = conn.cursor()


        print("Executing query...")

        cursor.execute(query)
        rows = cursor.fetchall()
        headers = [col[0] for col in cursor.description]

        logger.info(f"Returned {len(rows)} rows")

        write_csv(rows, headers, output_file)

        if return_rows:
            return rows

        return output_file

    except Exception as e:
        logger.exception("Query failed")
        print(f"ERROR running query: {e}")
        return None

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def fetch_rows(query):
    conn = None
    cursor = None

    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute(query)

        return cursor.fetchall()

    except Exception:
        logger.exception("Fetch query failed")
        return None

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


# ---------------------------
# Create PDF
# ---------------------------

def create_pdf_report(
    file_path,
    bib_total,
    item_total,
    system_summary,
    unique_title_summary
):
    doc = SimpleDocTemplate(
        file_path,
        pagesize=landscape(letter),
        topMargin=30

    )

    styles = getSampleStyleSheet()
    elements = []

    # -----------------------
    # Report Header
    # -----------------------

    elements.append(
        Paragraph(
            "MaineCat Holdings Report",
            styles["Title"]
        )
    )

    # -----------------------
    # Overall Totals Table
    # -----------------------

    totals_data = [
        ["Metric", "Count"],
        ["Bibliographic Records", f"{bib_total:,}"],
        ["Item Records", f"{item_total:,}"]
    ]

    totals_table = Table(
        totals_data,
        colWidths=[170, 90]
    )

    totals_table.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
            ("GRID", (0, 0), (-1, -1), 1, colors.black),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
        ])
    )

    totals_block = Table(
        [[
            Paragraph("Overall Totals", styles["Heading2"])
        ],
        [
            totals_table
        ]],
        colWidths=[350]
    )

    totals_block.setStyle(
        TableStyle([
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ])
    )

    left_column = Table(
    [[totals_block]],
    colWidths=[350],
    hAlign='LEFT'
)

    left_column.setStyle(
        TableStyle([
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ])
    )

    elements.append(left_column)
    elements.append(Spacer(1, 20))
    
    
    # -----------------------
    # System Summary Table
    # -----------------------

    system_data = [["System", "Bibs", "Items"]]

    for system, bibs, items in system_summary:
        system_data.append([
            system or "BLANK",
            f"{bibs:,}",
            f"{items:,}"
        ])

    system_table = Table(
        system_data,
        colWidths=[80, 90, 90]
    )

    system_table.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
            ("GRID", (0, 0), (-1, -1), 1, colors.black),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
        ])
    )

    # -----------------------
    # Unique Titles Table
    # -----------------------

    unique_data = [["System", "Unique Titles"]]

    for system, count in unique_title_summary:
        unique_data.append([
            system or "BLANK",
            f"{count:,}"
        ])

    unique_table = Table(
        unique_data,
        colWidths=[80, 120]
    )

    unique_table.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
            ("GRID", (0, 0), (-1, -1), 1, colors.black),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
        ])
    )

    # -----------------------
    # Side-by-side headers
    # -----------------------

    header_table = Table(
        [[
            Paragraph(
                "System Summary",
                styles["Heading2"]
            ),
            Paragraph(
                "Unique Titles Held By Only One System",
                styles["Heading2"]
            )
        ]],
        colWidths=[350, 350]
    )

    header_table.setStyle(
        TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ])
    )

    elements.append(header_table)
    elements.append(Spacer(1, 8))

    # -----------------------
    # Side-by-side tables
    # -----------------------

    side_by_side = Table(
        [[system_table, unique_table]],
        colWidths=[350, 350]
    )

    side_by_side.setStyle(
        TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ])
    )

    elements.append(side_by_side)

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
            filename=os.path.basename(attachment_path),
        )

    with smtplib.SMTP(smtp_server, smtp_port) as smtp:
        if use_tls:
            smtp.starttls()
        smtp.login(username, password)
        smtp.send_message(msg)

    logger.info("Email sent successfully")




# ---------------------------
# Query
# ---------------------------
BCOUNT_QUERY = """\
SELECT COUNT(*) FROM sierra_view.bib_record_property;
"""

ICOUNT_QUERY = """\
SELECT COUNT(*) AS total_items
FROM sierra_view.item_record;
"""

SYSTEMTOTALS_QUERY = """\
SELECT
    i.location_code AS system,
    COUNT(DISTINCT l.bib_record_id) AS bib_count,
    COUNT(DISTINCT i.id) AS item_count
FROM sierra_view.item_record i
JOIN sierra_view.bib_record_item_record_link l
    ON i.id = l.item_record_id
GROUP BY i.location_code
ORDER BY i.location_code;
"""

IUNIQUES_QUERY = """\
SELECT
    owning_system,
    COUNT(*) AS bib_count
FROM (
    SELECT
        b.id,
        MIN(i.location_code) AS owning_system
    FROM sierra_view.bib_record b
    JOIN sierra_view.bib_record_item_record_link l
        ON b.id = l.bib_record_id
    JOIN sierra_view.item_record i
        ON l.item_record_id = i.id
    GROUP BY b.id
    HAVING COUNT(DISTINCT i.location_code) = 1
) x
GROUP BY owning_system
ORDER BY owning_system;
"""

BUNIQUES_QUERY = """\
SELECT
    b.id AS bib_id,
    bp.best_title,
    MIN(i.location_code) AS owning_system,
    COUNT(DISTINCT i.id) AS item_count
FROM sierra_view.bib_record b
JOIN sierra_view.bib_record_property bp
    ON b.id = bp.bib_record_id
JOIN sierra_view.bib_record_item_record_link l
    ON b.id = l.bib_record_id
JOIN sierra_view.item_record i
    ON l.item_record_id = i.id
GROUP BY
    b.id,
    bp.best_title
HAVING COUNT(DISTINCT i.location_code) = 1
ORDER BY owning_system, bp.best_title;
"""

# ---------------------------
# Main
# ---------------------------

def main():
    logger.info("MaineCat counts job started")

    today = datetime.now().strftime("%Y%m%d")

    bibcounts_file = os.path.join(REPORTS_DIR, f"Bib Counts_{today}.csv")
    itemcounts_file = os.path.join(REPORTS_DIR, f"Item Counts_{today}.csv")
    uniquecounts_file = os.path.join(REPORTS_DIR, f"Unique Titles_{today}.csv")
    pdf_file = os.path.join(REPORTS_DIR, f"MaineCat_Report_{today}.pdf")

    run_query(BCOUNT_QUERY, bibcounts_file)
    run_query(SYSTEMTOTALS_QUERY, itemcounts_file)
    run_query(IUNIQUES_QUERY, uniquecounts_file)

    bib_total = run_scalar_query(BCOUNT_QUERY)
    item_total = run_scalar_query(ICOUNT_QUERY)

    system_summary = fetch_rows(SYSTEMTOTALS_QUERY)
    unique_title_summary = fetch_rows(IUNIQUES_QUERY)

    if (
        bib_total is None
        or item_total is None
        or system_summary is None
        or unique_title_summary is None
    ):
        logger.error("Failed to retrieve report data")
        return

    create_pdf_report(
        pdf_file,
        bib_total,
        item_total,
        system_summary,
        unique_title_summary
    )

    send_email_with_attachment(
        subject="MaineCat Counts Report",
        body="Attached is the MaineCat counts report.",
        attachment_path=pdf_file
    )

    logger.info("MaineCat counts job completed")


if __name__ == "__main__":
    main()  