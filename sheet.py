import os
import gspread
from google.auth.exceptions import RefreshError

AUTHORIZED_USER_PATH = os.path.expanduser("~/.config/gspread/authorized_user.json")
SPREADSHEET_KEY = "1kk9D_uVTUUeuaV6LYmZORS3g9DN_qBPHBQ9apH0mMKo"
WORKSHEET_ID = 1163961336

# Spreadsheet columns, keyed by the header they sit under
NON_DEFACED_CONSENT_COL = "E"
DEFACED_COL = "G"
DEFACE_CHECK_DATE_COL = "H"
NON_DEFACED_FILES_COL = "I"
COMMENTS_COL = "J"
PURGED_DATA_COL = "K"
EMAILED_SUBMITTER_COL = "L"
EMAILED_DATE_COL = "M"
SKULL_STRIPPED_COL = "R"


def open_worksheet() -> gspread.Worksheet:
    """Returns the deface tracking worksheet using the stored authorization."""
    client = gspread.oauth()
    return client.open_by_key(SPREADSHEET_KEY).get_worksheet_by_id(WORKSHEET_ID)


def get_worksheet() -> gspread.Worksheet:
    """Returns the deface tracking worksheet, reauthorizing if the token has expired."""
    try:
        return open_worksheet()
    except RefreshError:
        # gspread only runs the browser flow when the stored token is missing
        print("Google authorization expired, reauthorizing in the browser")
        os.remove(AUTHORIZED_USER_PATH)
        return open_worksheet()


def get_row_numbers(worksheet: gspread.Worksheet) -> dict:
    """Returns a {dataset: row number} dict for the whole sheet."""
    dataset_column = worksheet.col_values(1)
    return {dataset: number for number, dataset in enumerate(dataset_column, start=1) if dataset}


def get_rows(worksheet: gspread.Worksheet) -> dict:
    """Returns a {dataset: (row number, row values)} dict for the whole sheet."""
    rows = {}
    for number, row in enumerate(worksheet.get_all_values(), start=1):
        if row and row[0]:
            rows[row[0]] = (number, row)
    return rows


def get_cell(row: list, column: str) -> str:
    """Returns the value under a column letter, or '' past the end of the row."""
    index = ord(column) - ord("A")
    return row[index].strip() if index < len(row) else ""


def write_row(worksheet: gspread.Worksheet, row_number: int, updates: dict) -> None:
    """Writes the {column: value} updates into the dataset's row."""
    worksheet.batch_update(
        [
            {"range": f"{column}{row_number}", "values": [[value]]}
            for column, value in updates.items()
        ]
    )
