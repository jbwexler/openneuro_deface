import argparse
import datetime
import os
import readline  # noqa: F401  -- gives input() arrow key editing and history
import subprocess
import tempfile
import keyring as kr
import requests
from prompt_toolkit import PromptSession
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from create_mosaic import get_mosaic_url, get_ref
from purge_and_email import needs_purge_or_email, process_dataset, read_ds_list
from sheet import (
    COMMENTS_COL,
    DEFACE_CHECK_DATE_COL,
    DEFACED_COL,
    MADE_PUBLIC_COL,
    NON_DEFACED_CONSENT_COL,
    NON_DEFACED_FILES_COL,
    SKULL_STRIPPED_COL,
    get_cell,
    get_rows,
    get_worksheet,
    write_row,
)

DEFACED_CHOICES = ["yes", "no", "n/a"]


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


def join_lines(text: str) -> str:
    """Returns the text with its newlines dropped and each line's ends stripped."""
    return "".join(line.strip() for line in text.splitlines())


paste_bindings = KeyBindings()


@paste_bindings.add(Keys.BracketedPaste)
def insert_paste_on_one_line(event) -> None:
    """Inserts a paste with its newlines dropped, so it neither submits the prompt
    nor shows across several lines."""
    event.current_buffer.insert_text(join_lines(event.data))


text_session = None


def input_one_line(prompt: str) -> str:
    """Prompts for free text, keeping a multi-line paste on one line."""
    global text_session
    if text_session is None:
        # Built on first use since it needs the terminal
        text_session = PromptSession(key_bindings=paste_bindings)
    return join_lines(text_session.prompt(prompt))


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
        review[NON_DEFACED_FILES_COL] = input_one_line("non_defaced_files (regex): ")

    review[SKULL_STRIPPED_COL] = input_one_line("skull_stripped (blank to skip): ")
    review[COMMENTS_COL] = input_one_line("comments_joe (blank to skip): ")

    # A blank answer means leave the cell alone rather than clear it
    return {column: value for column, value in review.items() if value}


def needs_review(row: list) -> bool:
    """Returns whether the dataset is public and the sheet has no defaced verdict yet."""
    # Unreleased datasets show #N/A, since the lookups only cover public ones
    return (not get_cell(row, DEFACED_COL)
            and get_cell(row, MADE_PUBLIC_COL) not in ("", "#N/A"))


def get_pending(rows: dict) -> list:
    """Returns the datasets still to review or to purge and email, leaving out those
    whose submitters consented to sharing non-defaced data."""
    return [
        dataset for dataset, (_, row) in rows.items()
        if (needs_review(row) or needs_purge_or_email(row))
        and get_cell(row, NON_DEFACED_CONSENT_COL).lower() != "yes"
    ]


def purge_and_email(worksheet, dataset: str, row_number: int, row: list,
                    openneuro_api_key: str, dry_run: bool) -> None:
    """Runs the purge and email prompts for a dataset reviewed as not defaced."""
    updates = process_dataset(dataset, row, None, None, None, None, openneuro_api_key)
    if updates:
        write_or_print(worksheet, row_number, updates, dry_run)


def apply_updates(row: list, updates: dict) -> list:
    """Returns a copy of the row values with the {column: value} updates applied."""
    row = list(row)
    for column, value in updates.items():
        index = ord(column) - ord("A")
        row.extend([""] * (index + 1 - len(row)))
        row[index] = value
    return row


def write_or_print(worksheet, row_number: int, updates: dict, dry_run: bool) -> None:
    """Writes the updates into the row, or prints them on a dry run."""
    if dry_run:
        print(f"  would write row {row_number}: {updates}")
    else:
        write_row(worksheet, row_number, updates)
        print(f"  wrote row {row_number}: {updates}")


def main():
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "dataset",
        nargs="?",
        help="Dataset number. Without a dataset or --ds-list, every public dataset the "
        "sheet has no defaced verdict for is reviewed, and every one reviewed as not "
        "defaced but not yet purged and emailed goes straight to purging and emailing.",
    )
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
        help="Prompt as usual but print the sheet updates instead of writing them. "
        "Purging and emailing still happen if confirmed.",
    )
    args = parser.parse_args()

    openneuro_api_key = kr.get_password('openneuro_deface', 'openneuro_api_key')

    worksheet = get_worksheet()
    rows = get_rows(worksheet)

    if args.ds_list:
        dataset_list = read_ds_list(args.ds_list)
    elif args.dataset:
        dataset_list = [args.dataset]
    else:
        dataset_list = get_pending(rows)
        print(f"Found {len(dataset_list)} datasets still to review, purge or email")

    skipped_list = []
    with tempfile.TemporaryDirectory() as out_dir:
        for number, dataset in enumerate(dataset_list, start=1):
            print(f"\n=== {dataset} ({number}/{len(dataset_list)}) ===")

            if dataset not in rows:
                print(f"Skipping {dataset}: no row in the sheet")
                skipped_list.append(dataset)
                continue

            row_number, row = rows[dataset]
            if needs_purge_or_email(row):
                print(f"Already reviewed as {get_cell(row, DEFACED_COL)!r}, "
                      "going straight to purge and email")
                purge_and_email(worksheet, dataset, row_number, row,
                                openneuro_api_key, args.dry_run)
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
            write_or_print(worksheet, row_number, review, args.dry_run)

            if review[DEFACED_COL] == "no":
                # Purge from the row as just reviewed, so the new non_defaced_files is used
                purge_and_email(worksheet, dataset, row_number, apply_updates(row, review),
                                openneuro_api_key, args.dry_run)

    if skipped_list:
        print(f"\nSkipped {skipped_list}")


if __name__ == "__main__":
    main()
