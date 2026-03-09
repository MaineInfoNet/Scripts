#!/usr/bin/env python3

# Run in sic

"""
Jeremy Goldstein

Gather daily collection data for LibraryIQ and FTP results as csv files
"""

import psycopg2
import csv
import configparser
import paramiko
import os
from datetime import datetime
from datetime import date

#logging
import logging

logging.basicConfig(
    filename="libraryiq.log",
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

logger = logging.getLogger(__name__)

console = logging.StreamHandler()
console.setLevel(logging.INFO)
console.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
logger.addHandler(console)

# test mode flag
TEST_MODE = False        # Set to False for production
SKIP_SFTP = False        # Set to False to allow sftp send to LibraryIQ
LIMIT_ROWS = False       # Set to False for full data file
TEST_LIMIT = 10          # Small result set to validate structure


# manual full export override
FORCE_FULL_EXPORT = False   # Set to True when full files are needed

# determine full vs delta behavior
IS_TUESDAY = datetime.today().weekday() == 1
RUN_FULL_EXPORT = IS_TUESDAY or FORCE_FULL_EXPORT

# save files to Reports directory to avoid clutter
# Reports directory
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPORTS_DIR = os.path.join(BASE_DIR, "Reports")

# Create Reports directory if it doesn't exist
os.makedirs(REPORTS_DIR, exist_ok=True)

# populate csv file with results of a sql query
def csv_writer(query_results, headers, csv_file):

    with open(csv_file, "w", encoding="utf-8", newline="") as tempFile:
        myFile = csv.writer(tempFile, delimiter=",")
        myFile.writerow(headers)
        myFile.writerows(query_results)

    return csv_file


# connect to Sierra-db and store results of an sql query
def run_query(query, csv_file):

    logger.info(f"Starting query for {csv_file}")

    config = configparser.ConfigParser()
    config.read("config.ini")

    try:
        conn = psycopg2.connect(config["sql"]["connection_string"])
        logger.info("Database connection established")
    except Exception:
        logger.exception("Database connection failed")
        return

    cursor = conn.cursor()

    safe_query = apply_test_limit(query) if LIMIT_ROWS else query

    try:
        cursor.execute(safe_query)
        headers = [i[0] for i in cursor.description]
        rows = cursor.fetchall()
        logger.info(f"Query returned {len(rows)} rows")
    except Exception:
        logger.exception("Query execution failed")
        conn.close()
        return

    conn.close()

    end_file = csv_writer(rows, headers, csv_file)

    logger.info(f"CSV file written: {csv_file}")

    return end_file


# add a LIMIT to test mode

def apply_test_limit(query):
    """
    Wraps a query in a subquery and applies a small LIMIT
    to reduce load during TEST_MODE.
    """
    return f"SELECT * FROM ({query}) test_query LIMIT {TEST_LIMIT}"


# alt function combining runquery() and csvWriter() to handle full item holdings file
def run_large_query(csv_file):
    # instantiate offset value for use with query
    offset = 0
    # see runquery() function for config file example
    config = configparser.ConfigParser()
    config.read("config.ini")

    try:
        # variable connection string should be defined in the imported config file
        conn = psycopg2.connect(config["sql"]["connection_string"])
    except Exception:
        logger.exception("Database connection failed in run_large_query")
        return


    # Opening a session and querying the database for weekly new items
    cursor = conn.cursor()
    with open(csv_file, "w", encoding="utf-8", newline="") as tempFile:
        myFile = csv.writer(tempFile, delimiter=",")
        # repeat query and csv writing until offset hits a value greater than the total number of items in system
        while offset < 6000000:

            logger.info(f"Running large item batch with OFFSET {offset}")

            large_items_query = """\
                SELECT
                  rmi.record_type_code||rmi.record_num AS "ItemNum",
                  ip.barcode,
                  rmb.record_type_code||rmb.record_num AS "BibNum",
                  STRING_AGG(SUBSTRING(num.content FROM '[0-9xX]+'),';') FILTER(WHERE num.marc_tag = '020') AS isbn,
                  STRING_AGG(num.content,';') FILTER(WHERE num.marc_tag = '022') issn,
                  STRING_AGG(SUBSTRING(num.content FROM '[0-9]+'),';') FILTER(WHERE num.marc_tag = '024') AS upc,
                  i.icode1,
                  i.itype_code_num AS itype,
                  it.name AS "ItypeName",
                  mp.name AS "MaterialType",
                  SUBSTRING(i.location_code,1,3) AS "BranchId",
                  TRIM(LEADING '|a' FROM TRIM(ip.call_number))||COALESCE(' '||v.field_content,'') AS "CallNumber",
                  i.location_code,
                  loc.name AS location_name,
                  TO_CHAR(rmi.creation_date_gmt,'YYYY-MM-DD HH24:MI:SS') AS "CREATED",
                  CASE
                    WHEN o.id IS NULL THEN isp.name
                    WHEN o.id IS NOT NULL AND isp.code != '-' THEN isp.name
                    ELSE 'CHECKED OUT'
                  END AS status,
                  TO_CHAR(i.last_checkout_gmt,'YYYY-MM-DD HH24:MI:SS') AS "LOutDate",
                  TO_CHAR(o.checkout_gmt,'YYYY-MM-DD HH24:MI:SS') AS "OutDate",
                  TO_CHAR(i.last_checkin_gmt,'YYYY-MM-DD HH24:MI:SS') AS "CheckInDate",
                  TO_CHAR(o.due_gmt,'YYYY-MM-DD HH24:MI:SS') AS "DueDate",
                  i.year_to_date_checkout_total AS "YTDCIRC",
                  i.last_year_to_date_checkout_total AS "LYRCIRC",
                  i.checkout_total AS "TOT_CHKOUT",
                  i.renewal_total AS "TOT_RENEW"
      
                  FROM sierra_view.item_record i
                  JOIN sierra_view.record_metadata rmi
                    ON i.id = rmi.id
                  JOIN sierra_view.item_record_property ip
                    ON i.id = ip.item_record_id
                  JOIN sierra_view.bib_record_item_record_link l
                    ON i.id = l.item_record_id
                  JOIN sierra_view.record_metadata rmb
                    ON l.bib_record_id = rmb.id
                  JOIN sierra_view.bib_record_property bp
                    ON l.bib_record_id = bp.bib_record_id
                  JOIN sierra_view.itype_property_myuser it
                    ON i.itype_code_num = it.code
                  JOIN sierra_view.location_myuser loc
                    ON i.location_code = loc.code
                  JOIN sierra_view.material_property_myuser mp
                    ON bp.material_code = mp.code
                  JOIN sierra_view.item_status_property_myuser isp
                    ON i.item_status_code = isp.code
                  LEFT JOIN sierra_view.subfield num
                    ON bp.bib_record_id = num.record_id
                    AND num.marc_tag IN ('020','022','024')
                    AND num.tag = 'a'
                  LEFT JOIN sierra_view.checkout o
                    ON i.id = o.item_record_id
                  LEFT JOIN sierra_view.varfield v
                    ON i.id = v.record_id
                    AND v.varfield_type_code = 'v'
            
                  GROUP BY 1,2,3,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24
        
                  ---pulling 250,000 results per loop
                  LIMIT 250000
                  OFFSET {}""".format(offset)

            try:
                cursor.execute(large_items_query)
                rows = cursor.fetchall()
                logger.info(f"Batch at OFFSET {offset} returned {len(rows)} rows")
            except Exception:
                logger.exception(f"Query failed at OFFSET {offset}")
                break


            # first time through the loop, add a header row to the csv file
            if offset == 0:
                headers = [i[0] for i in cursor.description]
                myFile.writerow(headers)
            myFile.writerows(rows)
            # increment offset value for next loop
            offset += 250000

    tempFile.close()
    conn.close()
    
    logger.info(f"Completed large item export: {csv_file}")
    
    return csv_file


# function to sftp a specified file
def sftp_file(file1):
    if SKIP_SFTP:
        logger.info(f"[DEV MODE] Skipping SFTP upload for {file1}")
        return

    config = configparser.ConfigParser()
    config.read("config.ini")

    host = config["libraryiq"]["host"]
    username = config["libraryiq"]["user"]
    password = config["libraryiq"]["pw"]

    try:
        transport = paramiko.Transport((host, 22))
        transport.connect(username=username, password=password)

        sftp = paramiko.SFTPClient.from_transport(transport)
        remote_filename = os.path.basename(file1)
        remote_path = f"/upload/{remote_filename}"

        sftp.put(file1, remote_path)


        logger.info(f"SFTP upload successful: {file1}")

        sftp.close()
        transport.close()

        os.remove(file1)

    except Exception:
        logger.exception(f"SFTP upload failed for {file1}")



def main():

    logger.info("LibraryIQ export job started")
    
    bibs_query_full = """\
    SELECT
      rm.record_type_code||rm.record_num AS "BibNum",
      STRING_AGG(SUBSTRING(num.content FROM '[0-9xX]+'),';' ORDER BY num.occ_num) FILTER(WHERE num.marc_tag = '020') AS isbn,
      STRING_AGG(num.content,';' ORDER BY num.occ_num) FILTER(WHERE num.marc_tag = '022') issn,
      STRING_AGG(SUBSTRING(num.content FROM '[0-9]+'),';' ORDER BY num.occ_num) FILTER(WHERE num.marc_tag = '024') AS upc,
      mp.name AS "MaterialType",
      b.best_title,
      b.best_author,
      b.publish_year,
      TRIM(TRAILING ',' FROM pub.content) AS publisher

    FROM sierra_view.bib_record_property b
    JOIN sierra_view.record_metadata rm
      ON b.bib_record_id = rm.id
    LEFT JOIN sierra_view.subfield pub
      ON b.bib_record_id = pub.record_id
      AND pub.marc_tag IN ('260','264')
      AND pub.tag = 'b'
    LEFT JOIN sierra_view.subfield num
      ON b.bib_record_id = num.record_id
      AND num.marc_tag IN ('020','022','024')
      AND num.tag = 'a'
    JOIN sierra_view.material_property_myuser mp
      ON b.material_code = mp.code

    GROUP BY 1,5,6,7,8,9
    """

    bibs_query_delta = """\
    SELECT
      rm.record_type_code||rm.record_num AS "BibNum",
      STRING_AGG(SUBSTRING(num.content FROM '[0-9xX]+'),';' ORDER BY num.occ_num) FILTER(WHERE num.marc_tag = '020') AS isbn,
      STRING_AGG(num.content,';' ORDER BY num.occ_num) FILTER(WHERE num.marc_tag = '022') issn,
      STRING_AGG(SUBSTRING(num.content FROM '[0-9]+'),';' ORDER BY num.occ_num) FILTER(WHERE num.marc_tag = '024') AS upc,
      mp.name AS "MaterialType",
      b.best_title,
      b.best_author,
      b.publish_year,
      TRIM(TRAILING ',' FROM pub.content) AS publisher

    FROM sierra_view.bib_record_property b
    JOIN sierra_view.record_metadata rm
      ON b.bib_record_id = rm.id
    LEFT JOIN sierra_view.subfield pub
      ON b.bib_record_id = pub.record_id
      AND pub.marc_tag IN ('260','264')
      AND pub.tag = 'b'
    LEFT JOIN sierra_view.subfield num
      ON b.bib_record_id = num.record_id
      AND num.marc_tag IN ('020','022','024')
      AND num.tag = 'a'
    JOIN sierra_view.material_property_myuser mp
      ON b.material_code = mp.code

    WHERE rm.record_last_updated_gmt::DATE = CURRENT_DATE - INTERVAL '1 day'

    GROUP BY 1,5,6,7,8,9
    """


    items_query = """\
    SELECT
      rmi.record_type_code||rmi.record_num AS "ItemNum",
      ip.barcode,
      rmb.record_type_code||rmb.record_num AS "BibNum",
      STRING_AGG(SUBSTRING(num.content FROM '[0-9xX]+'),';') FILTER(WHERE num.marc_tag = '020') AS isbn,
      STRING_AGG(num.content,';') FILTER(WHERE num.marc_tag = '022') issn,
      STRING_AGG(SUBSTRING(num.content FROM '[0-9]+'),';') FILTER(WHERE num.marc_tag = '024') AS upc,
      i.icode1,
      i.itype_code_num AS itype,
      it.name AS "ItypeName",
      mp.name AS "MaterialType",
      SUBSTRING(i.location_code,1,3) AS "BranchId",
      TRIM(LEADING '|a' FROM TRIM(ip.call_number))||COALESCE(' '||v.field_content,'') AS "CallNumber",
      i.location_code,
      loc.name AS location_name,
      TO_CHAR(rmi.creation_date_gmt,'YYYY-MM-DD HH24:MI:SS') AS "CREATED",
      --Conditional logic to treat checked out as if it were a status
      CASE
        WHEN o.id IS NULL THEN isp.name
        WHEN o.id IS NOT NULL AND isp.code != '-' THEN isp.name
        ELSE 'CHECKED OUT'
      END AS status,
      TO_CHAR(i.last_checkout_gmt,'YYYY-MM-DD HH24:MI:SS') AS "LOutDate",
      TO_CHAR(o.checkout_gmt,'YYYY-MM-DD HH24:MI:SS') AS "OutDate",
      TO_CHAR(i.last_checkin_gmt,'YYYY-MM-DD HH24:MI:SS') AS "CheckInDate",
      TO_CHAR(o.due_gmt,'YYYY-MM-DD HH24:MI:SS') AS "DueDate",
      i.year_to_date_checkout_total AS "YTDCIRC",
      i.last_year_to_date_checkout_total AS "LYRCIRC",
      i.checkout_total AS "TOT_CHKOUT",
      i.renewal_total AS "TOT_RENEW"
  
    FROM sierra_view.item_record i
    JOIN sierra_view.record_metadata rmi
      ON i.id = rmi.id
      --4 day buffer per guidance from libraryIQ
     AND rmi.record_last_updated_gmt::DATE > CURRENT_DATE - INTERVAL '4 days'
    JOIN sierra_view.item_record_property ip
      ON i.id = ip.item_record_id
    JOIN sierra_view.bib_record_item_record_link l
      ON i.id = l.item_record_id
    JOIN sierra_view.record_metadata rmb
      ON l.bib_record_id = rmb.id
    JOIN sierra_view.bib_record_property bp
      ON l.bib_record_id = bp.bib_record_id
    JOIN sierra_view.itype_property_myuser it
      ON i.itype_code_num = it.code
    JOIN sierra_view.location_myuser loc
      ON i.location_code = loc.code
    JOIN sierra_view.material_property_myuser mp
      ON bp.material_code = mp.code
    JOIN sierra_view.item_status_property_myuser isp
      ON i.item_status_code = isp.code
    LEFT JOIN sierra_view.subfield num
      ON bp.bib_record_id = num.record_id
      AND num.marc_tag IN ('020','022','024')
      AND num.tag = 'a'
    LEFT JOIN sierra_view.checkout o
      ON i.id = o.item_record_id
    LEFT JOIN sierra_view.varfield v
      ON i.id = v.record_id
      AND v.varfield_type_code = 'v'

    GROUP BY 1,2,3,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24
    """

    holds_query = """\
    SELECT
      DISTINCT rm.record_type_code||rm.record_num AS "BibNum",
      SUBSTRING(h.pickup_location_code,1,3) AS "BranchID",
      COUNT (DISTINCT h.id) AS "Number of requests"
      
    FROM sierra_view.hold h
    JOIN sierra_view.patron_record p 
      ON h.patron_record_id = p.id
    --address both item and bib level holds
    JOIN sierra_view.bib_record_item_record_link l
      ON h.record_id = l.bib_record_id 
      OR h.record_id = l.item_record_id
    JOIN sierra_view.record_metadata rm 
      ON l.bib_record_id = rm.id

    WHERE p.ptype_code IN ('280','281','282','283','284','181','182','183','184','192')

    GROUP BY 1,2

    """

    patrons_query_full = """\
    SELECT
      rmp.record_type_code||rmp.record_num AS PatronNum,
      TO_CHAR(p.expiration_date_gmt,'YYYY-MM-DD HH24:MI:SS') AS "ExpireDate",
      p.ptype_code AS "PatronType",
      pt.name AS "PatronTypeName",
      l.code AS "PatronBranch",
      p.checkout_total + p.renewal_total AS "TotalCheckout",
      TO_CHAR(p.activity_gmt,'YYYY-MM-DD HH24:MI:SS') AS "ActivityDate",
      TO_CHAR((
        SELECT MAX(rh.checkout_gmt)
        FROM sierra_view.reading_history rh 
        WHERE rmp.id = rh.patron_record_metadata_id)
      ,'YYYY-MM-DD HH24:MI:SS') AS "LastCheckout",
      TO_CHAR(rmp.creation_date_gmt,'YYYY-MM-DD HH24:MI:SS') AS "CreateDate",
      a.addr1 AS "AddressLn1",
      a.addr2 AS "AddressLn2",
      COALESCE(REGEXP_REPLACE(REGEXP_REPLACE(TRIM(a.city),'\d','','g'),'\s(me|ME)$','','i'),'') AS "AddressCity",
      COALESCE(CASE
        WHEN a.region = '' AND (LOWER(a.city) ~ '\sme$' OR p.pcode3 BETWEEN '1' AND '200') THEN 'ME'
        ELSE a.region
      END,'') AS "AddressState",
      a.postal_code AS "AddressZip"

    FROM sierra_view.patron_record p
    JOIN sierra_view.ptype_property_myuser pt
      ON p.ptype_code = pt.value
    JOIN sierra_view.location_myuser l
      ON SUBSTRING(pt.name,1,3) = l.code
    JOIN sierra_view.record_metadata rmp
      ON p.id = rmp.id
    JOIN sierra_view.patron_record_address a
      ON p.id = a.patron_record_id
      AND a.patron_record_address_type_id = 1

    WHERE p.ptype_code IN ('280','281','282','283','284','181','182','183','184','192')
    """
    patrons_query_delta = patrons_query_full + """
        AND rmp.record_last_updated_gmt::DATE = CURRENT_DATE - INTERVAL '1 day'
        """
        
    circ_query = """\
    SELECT
      rmi.record_type_code||rmi.record_num AS "ItemNum",
      ip.barcode AS "Barcode",
      rmb.record_type_code||rmb.record_num AS "BibNum",
	  CASE
		  WHEN TRIM(t.patron_home_library_code) IN ('cml','brr')
		  THEN rmp.record_type_code || rmp.record_num
		  ELSE '-'
	  END AS "PatronNum",
      TO_CHAR(t.transaction_gmt, 'YYYY-MM-DD HH24:MI:SS') AS "CheckoutDate",
      SUBSTRING(sg.location_code,1,3) AS "TransactionBranchCodeNum",
      --translate operation codes
      CASE
        WHEN t.op_code = 'r' THEN 'RENEWAL'
        WHEN t.op_code = 'o' THEN 'CHECKOUT'
        WHEN t.op_code = 'u' THEN 'USE COUNT'
      END AS "TransactionType",
      t.due_date_gmt::DATE AS "DueDate",
      i.last_checkin_gmt::DATE AS "CheckInDate",
      --identifies virtual records generated from ILL
      CASE
        WHEN rmi.campus_code = 'ncip' THEN TRUE
        ELSE FALSE
      END AS "IsVirtual"
  
    FROM sierra_view.circ_trans t
    JOIN sierra_view.item_record i
      ON t.item_record_id = i.id
    JOIN sierra_view.record_metadata rmi
      ON i.id = rmi.id
    JOIN sierra_view.item_record_property ip
      ON i.id = ip.item_record_id
    JOIN sierra_view.record_metadata rmb
      ON t.bib_record_id = rmb.id
    LEFT JOIN sierra_view.record_metadata rmp
      ON t.patron_record_id = rmp.id
    JOIN sierra_view.statistic_group_myuser sg
      ON t.stat_group_code_num = sg.code

    --limit to checkouts, renewals and internal use transaction within the past 4 days
    WHERE t.op_code IN ('o','r','u')
      AND t.transaction_gmt::DATE > CURRENT_DATE - INTERVAL '4 days'
		AND (
		      t.patron_home_library_code IN ('cml','brr')
		      OR LEFT(i.location_code,3) IN ('cml','brr')
		      OR LEFT(sg.location_code,3) IN ('cml','brr')
		)
    """

    fulfilled_holds_query = """\
    SELECT
      rm.record_type_code||rm.record_num AS "bibliographicRecordID",
      t.id AS "holdID",
      /*stat_group defines login where the transaction occured
      how outreach or mobile device transactions are recorded will depend on customer setup*/
      SUBSTRING(sg.location_code,1,3) AS "requestedLocation",
      t.transaction_gmt AS "fulfilledDate",
      CURRENT_DATE AS "reportDate"

    FROM sierra_view.circ_trans t
    JOIN sierra_view.record_metadata rm
      ON t.bib_record_id = rm.id
    JOIN sierra_view.statistic_group_myuser sg
      ON t.stat_group_code_num = sg.code
    JOIN sierra_view.bib_record_property bp
      ON rm.id = bp.bib_record_id

    WHERE 
      /*op_code f = filled hold*/
      t.op_code = 'f'
      --Limit to transactions from Curtis and Brewer
      AND t.stat_group_code_num IN ('830','100','101','102','103','104','105','107')
      AND t.transaction_gmt::DATE > CURRENT_DATE - INTERVAL '4 days'
      --filter out digital records that holds can, but should not fall on
      AND bp.material_code NOT IN ('x','u','m','oer')

    ORDER BY t.transaction_gmt
    """

    requested_holds_query = """\
    SELECT
      rm.record_type_code||rm.record_num AS "bibliographicRecordID",
      t.id AS "holdID",
      /*
      home_library_code is the default pickup location for the patron placing the hold
      the patron does have the option to change it on the fly when placing the hold
      */
      l.code AS "patronLocation",
      t.transaction_gmt AS "requestedDate",
      CURRENT_DATE AS "reportDate"

    FROM sierra_view.circ_trans t
    JOIN sierra_view.record_metadata rm
      ON t.bib_record_id = rm.id
    JOIN sierra_view.patron_record p
      ON t.patron_record_id = p.id  
    JOIN sierra_view.location_myuser l
  	ON p.home_library_code = l.code
    JOIN sierra_view.bib_record_property bp
      ON rm.id = bp.bib_record_id
      --*filter out digital records that holds can, but should not fall on
      --AND bp.material_code NOT IN ('b','y','s','h','w','l')

    WHERE 
      /*
      different types of holds are assigned different op_code values
      looking for any starting with an n or h will capture all options
      */
      t.op_code ~ '^(n|h)'
      AND t.transaction_gmt::DATE > CURRENT_DATE - INTERVAL '4 days'
      --limit to Brewer and Curtis patrons
      AND p.ptype_code IN ('280','281','282','283','284','181','182','183','184','192')

    ORDER BY t.transaction_gmt
    """

    unfilled_holds_query = """\
    SELECT
      DISTINCT h.id AS "holdID",
      rm.record_type_code||rm.record_num AS "bibliographicRecordID",
      h.placed_gmt AS "requestedDate",
      /*logic for using first 3 characters of location code to designate branch specific to Minuteman*/
      SUBSTRING(h.pickup_location_code,1,3) AS "requestedLocation",
      CURRENT_DATE AS "reportDate"
 
    FROM sierra_view.hold h
    JOIN sierra_view.patron_record p
      ON h.patron_record_id = p.id
	 /*Using or in the join to reconcile both bib holds and item holds to a bib record*/
    JOIN sierra_view.bib_record_item_record_link li
      ON h.record_id = li.bib_record_id
      OR h.record_id = li.item_record_id
    JOIN sierra_view.record_metadata rm
      ON li.bib_record_id = rm.id
    JOIN sierra_view.bib_record_property bp
      ON rm.id = bp.bib_record_id
      --*filter out digital records that holds can, but should not fall on
      --AND bp.material_code NOT IN ('b','y','s','h','w','l')

      --limit to Brewer and Curtis patrons
    WHERE p.ptype_code IN ('280','281','282','283','284','181','182','183','184','192')
      AND (h.expires_gmt > CURRENT_DATE OR h.expires_gmt IS NULL)
      --limit results to just holds with a status of on hold
      AND h.status = '0'
    """

    
    # Define Full versus Delta
    export_type = "Full" if RUN_FULL_EXPORT else "Delta"
    logger.info(f"Export type for this run: {export_type}")
    today_str = date.today().strftime("%Y%m%d")

    # Instantiate .csv files with names including today's date
    bibs_file = os.path.join(REPORTS_DIR, f"Biblio_{export_type}_{today_str}.csv")
    items_file = os.path.join(REPORTS_DIR, f"Items_{export_type}_{today_str}.csv")
    holds_file = os.path.join(REPORTS_DIR, f"Holds_{export_type}_{today_str}.csv")
    patrons_file = os.path.join(REPORTS_DIR, f"Patrons_{export_type}_{today_str}.csv")
    circ_file = os.path.join(REPORTS_DIR, f"Circ_{export_type}_{today_str}.csv")
    fulfilled_holds_file = os.path.join(REPORTS_DIR, f"Holds_Fulfilled_{export_type}_{today_str}.csv")
    requested_holds_file = os.path.join(REPORTS_DIR, f"Holds_Requested_{export_type}_{today_str}.csv")
    unfilled_holds_file = os.path.join(REPORTS_DIR, f"Holds_Unfilled_{export_type}_{today_str}.csv")

    # for each file, run associated query, populate the file, and sftp it to libraryiq
    # --- HOLDS ---
    holds_csv = run_query(holds_query, holds_file)
    if holds_csv:
       sftp_file(holds_csv)
    
    # --- FULFILLED HOLDS ---
    fulfilled_holds_csv = run_query(fulfilled_holds_query, fulfilled_holds_file)
    if fulfilled_holds_csv:
       sftp_file(fulfilled_holds_csv)
    
    # --- REQUESTED HOLDS ---    
    requested_holds_csv = run_query(requested_holds_query, requested_holds_file)
    if requested_holds_csv:
       sftp_file(requested_holds_csv)
        
    # --- UNFILLED HOLDS ---
    unfilled_holds_csv = run_query(unfilled_holds_query, unfilled_holds_file)
    if unfilled_holds_csv:
       sftp_file(unfilled_holds_csv)
        
    # --- CIRC ---
    circ_csv = run_query(circ_query, circ_file)
    if circ_csv:
       sftp_file(circ_csv)

    # --- ITEMS ---
    if TEST_MODE:
        logger.info("[TEST MODE] Skipping items query")
    else:
        if RUN_FULL_EXPORT:
            logger.info("Running full items export")
            items_csv = run_large_query(items_file)
        else:
            logger.info("Running delta items export")
            items_csv = run_query(items_query, items_file)

        if items_csv:
           sftp_file(items_csv)

    # --- BIBS ---
    if TEST_MODE:
        logger.info("[TEST MODE] Skipping bibs query")
    else:
        if RUN_FULL_EXPORT:
           logger.info("Running full bib export")
           bibs_csv = run_query(bibs_query_full, bibs_file)
        else:
           logger.info("Running delta bib export")
           bibs_csv = run_query(bibs_query_delta, bibs_file)

        if bibs_csv:
           sftp_file(bibs_csv)

    # --- PATRONS ---
    if TEST_MODE:
        logger.info("[TEST MODE] Skipping patrons query")
    else:
        if RUN_FULL_EXPORT:
           logger.info("Running full patrons export")
           patrons_csv = run_query(patrons_query_full, patrons_file)
        else:
           logger.info("Running delta patrons export")
           patrons_csv = run_query(patrons_query_delta, patrons_file)

        if patrons_csv:
           sftp_file(patrons_csv)

    logger.info("LibraryIQ export job completed")

main()

