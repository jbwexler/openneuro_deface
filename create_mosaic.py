import argparse
import keyring as kr
from purge_and_email import graphql_operation, get_latest_snapshot, get_snapshot_tags


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
    parser.add_argument("dataset", help="Dataset number")
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
    args = parser.parse_args()

    openneuro_api_key = kr.get_password('openneuro_deface', 'openneuro_api_key')

    if args.draft:
        ref = get_draft_head(args.dataset, openneuro_api_key)
    elif args.tag:
        tag_list = get_snapshot_tags(args.dataset, openneuro_api_key)
        if args.tag not in tag_list:
            raise SystemExit(f"Snapshot {args.tag} not found, dataset has {tag_list}")
        ref = args.tag
    else:
        ref = get_latest_snapshot(args.dataset, openneuro_api_key)

    if not create_mosaic(args.dataset, ref, openneuro_api_key):
        raise SystemExit("Mosaic creation failed")

    print("Mosaic creation queued")


if __name__ == "__main__":
    main()
