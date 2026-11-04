import argparse
import datetime
import os
import subprocess
import tempfile
import gspread
import keyring as kr
import requests
from google.auth.exceptions import RefreshError
from create_mosaic import get_mosaic_url, get_ref, read_ds_list

AUTHORIZED_USER_PATH = os.path.expanduser("~/.config/gspread/authorized_user.json")
SPREADSHEET_KEY = "1kk9D_uVTUUeuaV6LYmZORS3g9DN_qBPHBQ9apH0mMKo"
WORKSHEET_ID = 1163961336

# Spreadsheet columns the review fills in, keyed by the header they sit under
DEFACED_COL = "G"
DEFACE_CHECK_DATE_COL = "H"
NON_DEFACED_FILES_COL = "I"
COMMENTS_COL = "J"
SKULL_STRIPPED_COL = "R"

DEFACED_CHOICES = [
    "yes",
    "n/a",
    "no - emailed",
    "no - in progress",
    "no - unresolvable",
]


def download_mosaic(dataset: str, ref: str, openneuro_api_key: str, out_dir: str) -> str:
    """Downloads the mosaic pdf and returns its path."""
    url = get_mosaic_url(dataset, ref)
    response = requests.get(url, cookies={"accessToken": openneuro_api_key})

    # The endpoint answers 200 with a json error body when there is no mosaic
    if not response.content.startswith(b"%PDF"):
        raise ValueError(f"no mosaic at {url}")

    out_path = os.path.join(out_dir, f"{dataset}_mosaic.pdf")
    with open(out_path, "wb") as out_file:
        out_file.write(response.content)

    return out_path


def display_mosaic(path: str) -> None:
    """Opens the mosaic pdf in the default viewer."""
    subprocess.run(["open", path])


def ask_defaced() -> str:
    """Prompts for the defaced column, which has a fixed vocabulary."""
    for number, choice in enumerate(DEFACED_CHOICES, start=1):
        print(f"  {number}) {choice}")

    while True:
        answer = input("defaced? ").strip()
        if answer.isdigit() and 1 <= int(answer) <= len(DEFACED_CHOICES):
            return DEFACED_CHOICES[int(answer) - 1]
        print(f"  Enter 1-{len(DEFACED_CHOICES)}")


def review_dataset(dataset: str) -> dict:
    """Prompts for the review of one dataset, returning the columns to write."""
    defaced = ask_defaced()
    review = {
        DEFACED_COL: defaced,
        DEFACE_CHECK_DATE_COL: str(datetime.date.today()),
    }

    if defaced.startswith("no"):
        review[NON_DEFACED_FILES_COL] = input("non_defaced_files (regex): ").strip()

    review[SKULL_STRIPPED_COL] = input("skull_stripped (blank to skip): ").strip()
    review[COMMENTS_COL] = input("comments_joe (blank to skip): ").strip()

    # A blank answer means leave the cell alone rather than clear it
    return {column: value for column, value in review.items() if value}


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


def write_review(worksheet: gspread.Worksheet, row_number: int, review: dict) -> None:
    """Writes the reviewed columns into the dataset's row."""
    worksheet.batch_update(
        [
            {"range": f"{column}{row_number}", "values": [[value]]}
            for column, value in review.items()
        ]
    )


def main():
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("dataset", nargs="?", help="Dataset number")
    source.add_argument(
        "-f",
        "--ds-list",
        help="File holding one dataset number per line, reviewed in place of a dataset.",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "-t",
        "--tag",
        help="Snapshot tag the mosaic was built from. Defaults to the latest snapshot.",
    )
    group.add_argument(
        "-d",
        "--draft",
        action="store_true",
        help="Use the mosaic built from the current draft rather than a snapshot.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Prompt as usual but print the review instead of writing to the sheet.",
    )
    args = parser.parse_args()

    openneuro_api_key = kr.get_password('openneuro_deface', 'openneuro_api_key')

    if args.ds_list:
        dataset_list = read_ds_list(args.ds_list)
    else:
        dataset_list = [args.dataset]

    worksheet = get_worksheet()
    row_numbers = get_row_numbers(worksheet)

    skipped_list = []
    with tempfile.TemporaryDirectory() as out_dir:
        for number, dataset in enumerate(dataset_list, start=1):
            print(f"\n=== {dataset} ({number}/{len(dataset_list)}) ===")

            if dataset not in row_numbers:
                print(f"Skipping {dataset}: no row in the sheet")
                skipped_list.append(dataset)
                continue

            try:
                ref = get_ref(dataset, args.tag, args.draft, openneuro_api_key)
                path = download_mosaic(dataset, ref, openneuro_api_key, out_dir)
            except ValueError as error:
                print(f"Skipping {dataset}: {error}")
                skipped_list.append(dataset)
                continue

            display_mosaic(path)
            review = review_dataset(dataset)

            if args.dry_run:
                print(f"  would write row {row_numbers[dataset]}: {review}")
            else:
                write_review(worksheet, row_numbers[dataset], review)
                print(f"  wrote row {row_numbers[dataset]}")

    if skipped_list:
        print(f"\nSkipped {skipped_list}")


if __name__ == "__main__":
    main()
