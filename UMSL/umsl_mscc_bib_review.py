#!/usr/bin/env python3


# ---------------------------
# UMSL - MSCC Review
# Lynn Uhlman - Maine InfoNet
#>>>>>>>>>>>>>>>>>>>>>>>>>>>>>
# compares spreadsheet of 
# titles from another library
# to the UMSL collection.
# ---------------------------

import sys
from pathlib import Path

import pandas as pd

# ------------------------------------------------------------
# Script paths
# ------------------------------------------------------------

SCRIPT_PATH = Path(__file__).resolve()
SCRIPTS_DIR = SCRIPT_PATH.parent.parent

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


# ------------------------------------------------------------
# Shared modules
# ------------------------------------------------------------

from shared.alma import AlmaClient
from shared.config import load_config
from shared.logger import get_logger


# ------------------------------------------------------------
# Constants
# ------------------------------------------------------------

PROJECT_NAME = 'UMSL Bib Review'
PROJECT_CONFIG = 'umsl_bib_review'
LOG_FILE = 'umsl_bib_review.log'


RESULT_COLUMNS = [
    'Normalized OCLC',
    'Alma MMS ID',
    'Alma Title',
    'Alma Author',
    'Alma Publisher',
    'Alma Network Numbers',
    'UMS Libraries',
    'UMS Library Codes',
    'UMS Locations',
    'MSCC MMS IDs',
    'MSCC Libraries',
    'MSCC Library Codes',
    'MSCC Locations',
    'Match Method',
    'Match Count',
    'Classification',
    'Review Notes',
]


# ------------------------------------------------------------
# Logging
# ------------------------------------------------------------

logger = get_logger(
    name=PROJECT_NAME,
    logfile=LOG_FILE,
)


# ------------------------------------------------------------
# Configuration
# ------------------------------------------------------------

config = load_config(
    project=PROJECT_CONFIG
)

INPUT_FILENAME = config[
    'input'
]['filename']

INPUT_SHEET = config[
    'input'
].get(
    'sheet_name',
    'Sheet1',
)

OUTPUT_FILENAME = config[
    'output'
]['filename']

FILES_DIR = Path(
    config[
        'paths'
    ]['files']
)

REPORTS_DIR = Path(
    config[
        'paths'
    ]['reports']
)

COMMITMENT_TEXT = config[
    'mscc'
]['commitment_text']

COMMITMENT_NOTE = config[
    'mscc'
]['commitment_note']


COLUMN_BIB_RECORD = config[
    'columns'
]['bib_record']

COLUMN_TITLE = config[
    'columns'
]['title']

COLUMN_AUTHOR = config[
    'columns'
]['author']

COLUMN_PUBLISHER = config[
    'columns'
]['publisher']

COLUMN_OCLC = config[
    'columns'
]['oclc']


# ------------------------------------------------------------
# Project-specific MSCC rules
# ------------------------------------------------------------

def has_mscc_commitment(
    holding,
):
    """
    Determine whether an Alma holding contains the configured
    MSCC retention commitment.

    The configured 583$a and 583$z values must occur in the
    same 583 field.
    """

    expected_text = (
        COMMITMENT_TEXT
        .strip()
        .lower()
    )

    expected_note = (
        COMMITMENT_NOTE
        .strip()
        .lower()
    )

    for field in holding.get(
        '583_fields',
        [],
    ):
        values_a = [
            value.strip().lower()
            for value in field.get(
                'a',
                [],
            )
        ]

        values_z = [
            value.strip().lower()
            for value in field.get(
                'z',
                [],
            )
        ]

        if (
            expected_text in values_a
            and expected_note in values_z
        ):
            return True

    return False


# ------------------------------------------------------------
# General helpers
# ------------------------------------------------------------

def clean_value(
    value,
):
    """
    Convert spreadsheet values to clean strings.

    Blank and NaN values become empty strings.
    """

    if pd.isna(value):
        return ''

    return str(
        value
    ).strip()


def unique_join(
    values,
):
    """
    Join nonblank unique values while preserving their
    original order.
    """

    results = []
    seen = set()

    for value in values:
        value = clean_value(
            value
        )

        if not value:
            continue

        if value not in seen:
            seen.add(
                value
            )

            results.append(
                value
            )

    return '; '.join(
        results
    )


def empty_result():
    """
    Return an empty result structure for one Portland row.
    """

    return {
        column: ''
        for column in RESULT_COLUMNS
    }


# ------------------------------------------------------------
# Alma helpers
# ------------------------------------------------------------

def get_alma_client():
    """
    Create the Alma API client using shared connection
    configuration.
    """

    alma_config = config[
        'alma_bibs_api'
    ]

    api_key = alma_config[
        'API_KEY'
    ]

    base_url = alma_config.get(
        'BASE_URL',
        'https://api-na.hosted.exlibrisgroup.com/almaws/v1',
    )

    return AlmaClient(
        api_key=api_key,
        base_url=base_url,
    )


def get_holding_ids(
    holdings_response,
):
    """
    Extract holding IDs from an Alma holdings-list response.
    """

    holding_ids = []

    for holding in holdings_response.get(
        'holding',
        [],
    ):
        holding_id = holding.get(
            'holding_id'
        )

        if holding_id:
            holding_ids.append(
                holding_id
            )

    return holding_ids


def retrieve_holdings(
    alma,
    mms_id,
):
    """
    Retrieve and parse all holdings for one Alma bib.
    """

    holdings_response = alma.get_holdings(
        mms_id
    )

    holding_ids = get_holding_ids(
        holdings_response
    )

    logger.info(
        'MMS ID %s has %d holding(s).',
        mms_id,
        len(holding_ids),
    )

    parsed_holdings = []

    for holding_id in holding_ids:
        try:
            holding = alma.get_holding(
                mms_id,
                holding_id,
            )

            parsed = alma.parse_holding(
                holding
            )

            parsed[
                'mscc_commitment'
            ] = has_mscc_commitment(
                parsed
            )

            parsed_holdings.append(
                parsed
            )

        except Exception:
            logger.exception(
                'Unable to retrieve or parse holding %s '
                'for MMS ID %s.',
                holding_id,
                mms_id,
            )

    return parsed_holdings


# ------------------------------------------------------------
# Record review
# ------------------------------------------------------------

def review_oclc(
    alma,
    oclc_value,
    source_title='',
):
    """
    Search Alma using a Portland OCLC number and return
    structured comparison results.
    """

    result = empty_result()

    normalized_oclc = alma.normalize_oclc(
        oclc_value
    )

    result[
        'Normalized OCLC'
    ] = normalized_oclc or ''

    if not normalized_oclc:
        result[
            'Classification'
        ] = 'Review Needed'

        result[
            'Review Notes'
        ] = 'No usable OCLC number.'

        return result

    logger.info(
        'Searching OCLC %s | %s',
        normalized_oclc,
        source_title,
    )

    bibs = alma.search_oclc(
        normalized_oclc
    )

    result[
        'Match Count'
    ] = len(bibs)

    result[
        'Match Method'
    ] = 'OCLC'

    if not bibs:
        result[
            'Classification'
        ] = 'C - Electronic version found'

        result[
            'Review Notes'
        ] = (
            'No Alma match found by OCLC.'
        )

        return result

    if len(bibs) > 1:
        result[
            'Classification'
        ] = 'Review Needed'

        result[
            'Alma MMS ID'
        ] = unique_join(
            bib.get(
                'mms_id',
                '',
            )
            for bib in bibs
        )

        result[
            'Alma Title'
        ] = unique_join(
            bib.get(
                'title',
                '',
            )
            for bib in bibs
        )

        result[
            'Alma Author'
        ] = unique_join(
            bib.get(
                'author',
                '',
            )
            for bib in bibs
        )

        result[
            'Alma Publisher'
        ] = unique_join(
            bib.get(
                'publisher_const',
                '',
            )
            for bib in bibs
        )

        result[
            'Alma Network Numbers'
        ] = unique_join(
            network_number
            for bib in bibs
            for network_number in bib.get(
                'network_number',
                [],
            )
        )

        all_holdings = []
        mscc_holdings = []
        checked_mms_ids = []
        failed_mms_ids = []

        for bib in bibs:
            mms_id = clean_value(
                bib.get(
                    'mms_id'
                )
            )

            if not mms_id:
                continue

            checked_mms_ids.append(
                mms_id
            )

            try:
                holdings = retrieve_holdings(
                    alma,
                    mms_id,
                )

            except Exception:
                logger.exception(
                    'Unable to retrieve holdings for '
                    'candidate MMS ID %s.',
                    mms_id,
                )

                failed_mms_ids.append(
                    mms_id
                )

                continue

            for holding in holdings:
                holding[
                    'mms_id'
                ] = mms_id

            all_holdings.extend(
                holdings
            )

            mscc_holdings.extend(
                holding
                for holding in holdings
                if holding.get(
                    'mscc_commitment'
                )
            )

        result[
            'UMS Libraries'
        ] = unique_join(
            holding.get(
                'library_name'
            )
            for holding in all_holdings
        )

        result[
            'UMS Library Codes'
        ] = unique_join(
            holding.get(
                'library_code'
            )
            for holding in all_holdings
        )

        result[
            'UMS Locations'
        ] = unique_join(
            (
                f"{holding.get('library_code', '')}:"
                f"{holding.get('location_code', '')}"
            )
            for holding in all_holdings
            if holding.get(
                'library_code'
            )
        )

        result[
            'MSCC MMS IDs'
        ] = (
            mms_id
            if mscc_holdings
            else ''
        )
        
        result[
            'MSCC Libraries'
        ] = unique_join(
            holding.get(
                'library_name'
            )
            for holding in mscc_holdings
        )

        result[
            'MSCC Library Codes'
        ] = unique_join(
            holding.get(
                'library_code'
            )
            for holding in mscc_holdings
        )

        result[
            'MSCC Locations'
        ] = unique_join(
            (
                f"{holding.get('library_code', '')}:"
                f"{holding.get('location_code', '')}"
            )
            for holding in mscc_holdings
            if holding.get(
                'library_code'
            )
        )

        mscc_mms_ids = unique_join(
            holding.get(
                'mms_id'
            )
            for holding in mscc_holdings
        )

        result[
            'MSCC MMS IDs'
        ] = mscc_mms_ids
        
        
        if mscc_holdings:
            result[
                'Review Notes'
            ] = (
                f'Multiple Alma records matched this OCLC. '
                f'MSCC retention found on '
                f'{len(set(holding.get("mms_id") for holding in mscc_holdings))} '
                f'candidate record(s): {mscc_mms_ids}.'
            )

        else:
            result[
                'Review Notes'
            ] = (
                f'Multiple Alma records matched this OCLC. '
                f'No MSCC retention found on '
                f'{len(checked_mms_ids)} candidate record(s).'
            )

        if failed_mms_ids:
            result[
                'Review Notes'
            ] += (
                ' Holdings could not be retrieved for: '
                + '; '.join(
                    failed_mms_ids
                )
                + '.'
            )

        return result

    logger.info(
        'Matched OCLC %s to MMS ID %s.',
        normalized_oclc,
        mms_id,
    )

    if not mms_id:
        result[
            'Classification'
        ] = 'Review Needed'

        result[
            'Review Notes'
        ] = (
            'Alma match did not contain an MMS ID.'
        )

        return result

    try:
        holdings = retrieve_holdings(
            alma,
            mms_id,
        )

    except Exception:
        logger.exception(
            'Unable to retrieve holdings for MMS ID %s.',
            mms_id,
        )

        result[
            'Classification'
        ] = 'Review Needed'

        result[
            'Review Notes'
        ] = (
            'Alma bib matched, but holdings could not '
            'be retrieved.'
        )

        return result

    if not holdings:
        result[
            'Classification'
        ] = 'C - Electronic version found'

        result[
            'Review Notes'
        ] = (
            'Alma bib matched, but no UMS holdings '
            'were returned.'
        )

        return result

    result[
        'UMS Libraries'
    ] = unique_join(
        holding.get(
            'library_name'
        )
        for holding in holdings
    )

    result[
        'UMS Library Codes'
    ] = unique_join(
        holding.get(
            'library_code'
        )
        for holding in holdings
    )

    result[
        'UMS Locations'
    ] = unique_join(
        (
            f"{holding.get('library_code', '')}:"
            f"{holding.get('location_code', '')}"
        )
        for holding in holdings
        if holding.get(
            'library_code'
        )
    )

    mscc_holdings = [
        holding
        for holding in holdings
        if holding.get(
            'mscc_commitment'
        )
    ]

    result[
        'MSCC MMS IDs'
    ] = (
        mms_id
        if mscc_holdings
        else ''
    )
    
    result[
        'MSCC Libraries'
    ] = unique_join(
        holding.get(
            'library_name'
        )
        for holding in mscc_holdings
    )

    result[
        'MSCC Library Codes'
    ] = unique_join(
        holding.get(
            'library_code'
        )
        for holding in mscc_holdings
    )

    result[
        'MSCC Locations'
    ] = unique_join(
        (
            f"{holding.get('library_code', '')}:"
            f"{holding.get('location_code', '')}"
        )
        for holding in mscc_holdings
        if holding.get(
            'library_code'
        )
    )

    if mscc_holdings:
        result[
            'Classification'
        ] = 'A - MSCC Commitment'

    else:
        result[
            'Classification'
        ] = (
            'B - UMS Owned / No Commitment'
        )

    return result


# ------------------------------------------------------------
# Spreadsheet
# ------------------------------------------------------------

def validate_columns(
    dataframe,
):
    """
    Confirm that the configured Portland columns exist.
    """

    configured_columns = [
        value
        for key, value
        in config[
            'columns'
        ].items()
    ]

    missing = [
        column
        for column in configured_columns
        if column not in dataframe.columns
    ]

    if missing:
        raise ValueError(
            'Missing required spreadsheet column(s): '
            + ', '.join(
                missing
            )
        )


def process_spreadsheet(
    alma,
):
    """
    Read the Portland workbook and review each row.
    """

    input_path = (
        FILES_DIR
        / INPUT_FILENAME
    )

    if not input_path.exists():
        raise FileNotFoundError(
            f'Input spreadsheet not found: '
            f'{input_path}'
        )

    logger.info(
        'Reading Portland spreadsheet: %s',
        input_path,
    )

    dataframe = pd.read_excel(
        input_path,
        sheet_name=INPUT_SHEET,
        dtype=str,
    )

    validate_columns(
        dataframe
    )

    logger.info(
        'Loaded %d Portland row(s).',
        len(dataframe),
    )

    results = []

    total_rows = len(
        dataframe
    )

    for index, row in dataframe.iterrows():
        row_number = index + 2

        title = clean_value(
            row.get(
                COLUMN_TITLE
            )
        )

        oclc = clean_value(
            row.get(
                COLUMN_OCLC
            )
        )

        bib_record = clean_value(
            row.get(
                COLUMN_BIB_RECORD
            )
        )

        logger.info(
            'Processing row %d of %d | '
            'Bib: %s | OCLC: %s | Title: %s',
            index + 1,
            total_rows,
            bib_record,
            oclc,
            title,
        )

        try:
            result = review_oclc(
                alma,
                oclc,
                source_title=title,
            )

        except Exception:
            logger.exception(
                'Unexpected error processing '
                'spreadsheet row %d.',
                row_number,
            )

            result = empty_result()

            result[
                'Classification'
            ] = 'Review Needed'

            result[
                'Review Notes'
            ] = (
                'Unexpected processing error. '
                'See log for details.'
            )

        results.append(
            result
        )

    result_dataframe = pd.DataFrame(
        results
    )

    combined = pd.concat(
        [
            dataframe.reset_index(
                drop=True
            ),
            result_dataframe,
        ],
        axis=1,
    )

    return combined


def write_report(
    dataframe,
):
    """
    Write the completed comparison workbook.
    """

    REPORTS_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    timestamp = datetime.now().strftime(
        '%Y%m%d_%H%M%S'
    )

    output_filename = Path(
        OUTPUT_FILENAME
    )

    timestamped_filename = (
        f'{output_filename.stem}_'
        f'{timestamp}'
        f'{output_filename.suffix}'
    )

    output_path = (
        REPORTS_DIR
        / timestamped_filename
    )

    logger.info(
        'Writing report: %s',
        output_path,
    )
    
    with pd.ExcelWriter(
        output_path,
        engine='openpyxl',
    ) as writer:

        dataframe.to_excel(
            writer,
            sheet_name='All Results',
            index=False,
        )

        dataframe[
            dataframe[
                'Classification'
            ] == 'A - MSCC Commitment'
        ].to_excel(
            writer,
            sheet_name='A - MSCC',
            index=False,
        )

        dataframe[
            dataframe[
                'Classification'
            ] == (
                'B - UMS Owned / No Commitment'
            )
        ].to_excel(
            writer,
            sheet_name='B - UMS Owned',
            index=False,
        )

        dataframe[
            dataframe[
                'Classification'
            ] == 'C - Electronic version found'
        ].to_excel(
            writer,
            sheet_name='C - Electronic version',
            index=False,
        )

        dataframe[
            dataframe[
                'Classification'
            ] == 'Review Needed'
        ].to_excel(
            writer,
            sheet_name='Review Needed',
            index=False,
        )

    logger.info(
        'Report written successfully: %s',
        output_path,
    )

    return output_path


# ------------------------------------------------------------
# Main
# ------------------------------------------------------------

def main():
    """
    Compare the Portland title spreadsheet against UMS Alma
    holdings and MSCC retention commitments.
    """

    logger.info(
        '=================================================='
    )

    logger.info(
        'UMSL bibliographic review started.'
    )

    try:
        alma = get_alma_client()

        logger.info(
            'Alma API configuration loaded.'
        )

        results = process_spreadsheet(
            alma
        )

        output_path = write_report(
            results
        )

        counts = (
            results[
                'Classification'
            ]
            .value_counts(
                dropna=False
            )
            .to_dict()
        )

        logger.info(
            'Processing summary:'
        )

        logger.info(
            'A - MSCC Commitment: %d',
            counts.get(
                'A - MSCC Commitment',
                0,
            ),
        )

        logger.info(
            'B - UMS Owned / No Commitment: %d',
            counts.get(
                'B - UMS Owned / No Commitment',
                0,
            ),
        )

        logger.info(
            'C - Electronic version found: %d',
            counts.get(
                'C - Electronic version found',
                0,
            ),
        )

        logger.info(
            'Review Needed: %d',
            counts.get(
                'Review Needed',
                0,
            ),
        )

        logger.info(
            'Output: %s',
            output_path,
        )

        logger.info(
            'UMSL bibliographic review completed.'
        )

    except Exception:
        logger.exception(
            'UMSL bibliographic review failed.'
        )


if __name__ == '__main__':
    main()
