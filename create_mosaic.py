import argparse
import datetime
import os
import re
import keyring as kr
from purge_and_email import graphql_operation, get_latest_snapshot, get_snapshot_tags, read_ds_list
from sheet import MOSAIC_CREATION_COL, get_cell, get_rows, get_worksheet, write_row


def get_mosaic_url(dataset: str, ref: str, openneuro_url: str = "https://openneuro.org/") -> str:
    """Returns the url the mosaic pdf is served from.

    Openneuro resolves any refish and labels the pdf with the tag or the first 8
    characters of the hexsha, so the full hash is unnecessary here.
    """
    label = ref[:8] if re.fullmatch("[0-9a-f]{40}", ref) else ref
    return os.path.join(openneuro_url, "crn/datasets", dataset, "mosaic", label)


def get_draft_head(dataset: str, openneuro_api_key: str) -> str:
    """Returns the commit the dataset draft currently points at."""
    query = """
    query($dataset: ID!) {
        dataset(id: $dataset) {
            draft {
                head
            }
        }
    }
    """

    json = {"query": query, "variables": {"dataset": dataset}}
    response = graphql_operation(json, openneuro_api_key)
    return response["data"]["dataset"]["draft"]["head"]


def check_dataset(dataset: str, openneuro_api_key: str) -> None:
    """Raises ValueError if openneuro does not have the dataset."""
    query = """
    query($dataset: ID!) {
        dataset(id: $dataset) {
            id
        }
    }
    """

    json = {"query": query, "variables": {"dataset": dataset}}
    response = graphql_operation(json, openneuro_api_key)
    if "errors" in response:
        raise ValueError(response["errors"][0].get("message"))


def get_ref(dataset: str, tag: str, draft: bool, openneuro_api_key: str) -> str:
    """Returns the git ref to build the mosaic from."""
    check_dataset(dataset, openneuro_api_key)

    if draft:
        return get_draft_head(dataset, openneuro_api_key)

    if tag:
        tag_list = get_snapshot_tags(dataset, openneuro_api_key)
        if tag not in tag_list:
            raise ValueError(f"snapshot {tag} not found, dataset has {tag_list}")
        return tag

    return get_latest_snapshot(dataset, openneuro_api_key)


def create_mosaic(dataset: str, ref: str, openneuro_api_key: str) -> bool:
    """Performs createMosaic mutation on a dataset at the given ref."""
    query = """
    mutation($dataset: ID!, $ref: String!) {
        createMosaic(datasetId: $dataset, ref: $ref)
    }
    """

    json = {"query": query, "variables": {"dataset": dataset, "ref": ref}}
    print(f"Creating mosaic for {dataset} at {ref}")
    response = graphql_operation(json, openneuro_api_key)

    if "errors" in response:
        for error in response["errors"]:
            print(f"  error: {error.get('message')}")
        return False

    return bool(response["data"]["createMosaic"])


def main():
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("dataset", nargs="?", help="Dataset number")
    source.add_argument(
        "-f",
        "--ds-list",
        help="File holding one dataset number per line, built in place of a dataset.",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "-t",
        "--tag",
        help="Snapshot tag to build the mosaic from. Defaults to the latest snapshot.",
    )
    group.add_argument(
        "-d",
        "--draft",
        action="store_true",
        help="Build the mosaic from the current draft rather than a snapshot.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Request the mosaic even if the sheet's mosaic_creation shows it was "
        "already requested.",
    )
    args = parser.parse_args()

    openneuro_api_key = kr.get_password('openneuro_deface', 'openneuro_api_key')

    if args.ds_list:
        dataset_list = read_ds_list(args.ds_list)
    else:
        dataset_list = [args.dataset]

    worksheet = get_worksheet()
    rows = get_rows(worksheet)

    failed_list = []
    for dataset in dataset_list:
        requested = get_cell(rows[dataset][1], MOSAIC_CREATION_COL) if dataset in rows else ""
        if requested and not args.force:
            print(f"Skipping {dataset}: mosaic_creation shows it was requested {requested}, "
                  "pass --force to request again")
            continue

        try:
            ref = get_ref(dataset, args.tag, args.draft, openneuro_api_key)
        except ValueError as error:
            print(f"Skipping {dataset}: {error}")
            failed_list.append(dataset)
            continue

        if create_mosaic(dataset, ref, openneuro_api_key):
            print(f"  will be available at {get_mosaic_url(dataset, ref)}")
            if dataset in rows:
                write_row(worksheet, rows[dataset][0],
                          {MOSAIC_CREATION_COL: str(datetime.date.today())})
            else:
                print(f"  {dataset} has no row in the sheet, so mosaic_creation was not filled in")
        else:
            failed_list.append(dataset)

    if failed_list:
        raise SystemExit(f"Mosaic creation failed for {failed_list}")


if __name__ == "__main__":
    main()
