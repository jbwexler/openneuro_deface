import requests
import os
import argparse
import config
import re
import smtplib
import keyring as kr
import textwrap
from email.message import EmailMessage
from requests.adapters import HTTPAdapter, Retry


def graphql_operation(json, openneuro_api_key, openneuro_url="https://openneuro.org/"):
    """Submits graphql operation."""
    headers = {
        "Accept-Encoding": "gzip, deflate, br",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Connection": "keep-alive",
        "DNT": "1",
        "Origin": openneuro_url,
    }
    headers = {"Content-Type": "application/json"}
    cookies = {"accessToken": openneuro_api_key}
    url = os.path.join(openneuro_url, "crn/graphql")
    
    s = requests.Session()
    retries = Retry(total=5, backoff_factor=1, status_forcelist=[502, 503, 504])
    s.mount('https://', HTTPAdapter(max_retries=retries))
    response = s.post(url, headers=headers, json=json, cookies=cookies)
    
    try:
        return response.json()
    except requests.exceptions.JSONDecodeError:
        breakpoint()
        return

def get_draft_files(dataset: str, openneuro_api_key: str) -> list:
    """Returns every file in the dataset draft. Annexed files carry their annex key in 'id'."""
    query = """
    query($dataset: ID!) {
        dataset(id: $dataset) {
            draft {
                files(recursive: true) {
                    id
                    filename
                    directory
                    annexed
                }
            }
        }
    }
    """

    json = {"query": query, "variables": {"dataset": dataset}}
    response = graphql_operation(json, openneuro_api_key)
    return response["data"]["dataset"]["draft"]["files"]


def get_snapshot_files(dataset: str, tag: str, openneuro_api_key: str) -> list:
    """Returns every file in a snapshot. Annexed files carry their annex key in 'id'."""
    query = """
    query($dataset: ID!, $tag: String!) {
        snapshot(datasetId: $dataset, tag: $tag) {
            files(recursive: true) {
                id
                filename
                directory
                annexed
            }
        }
    }
    """

    json = {"query": query, "variables": {"dataset": dataset, "tag": tag}}
    response = graphql_operation(json, openneuro_api_key)
    return response["data"]["snapshot"]["files"]


def get_snapshot_tags(dataset: str, openneuro_api_key: str) -> list:
    """Returns the tags of every snapshot, oldest first."""
    query = """
    query($dataset: ID!) {
        dataset(id: $dataset) {
            snapshots {
                tag
            }
        }
    }
    """

    json = {"query": query, "variables": {"dataset": dataset}}
    response = graphql_operation(json, openneuro_api_key)
    return [x["tag"] for x in response["data"]["dataset"]["snapshots"]]


def get_all_files(dataset: str, openneuro_api_key: str) -> tuple:
    """Returns the draft file list and a {snapshot tag: file list} dict for every snapshot."""
    draft_files = get_draft_files(dataset, openneuro_api_key)

    files_by_snapshot = {}
    for tag in get_snapshot_tags(dataset, openneuro_api_key):
        print(f"Checking snapshot {tag}")
        files_by_snapshot[tag] = get_snapshot_files(dataset, tag, openneuro_api_key)
    
    return draft_files, files_by_snapshot


def get_file_list(draft_files: list, files_by_snapshot: dict, regexes: list) -> list:
    """Returns subject file paths matching the regexes in the draft or any snapshot."""
    match_list = set()
    for files in [draft_files] + list(files_by_snapshot.values()):
        for file in files:
            if file["directory"] or not file["filename"].startswith("sub-"):
                continue
            basename = os.path.basename(file["filename"])
            for reg in regexes:
                if re.match(reg, basename):
                    match_list.add(file["filename"])
                    break

    return sorted(match_list)


def filter_to_draft(draft_files: list, file_list: list) -> list:
    """Returns the files still present in the draft."""
    draft_names = {x["filename"] for x in draft_files}
    return [x for x in file_list if x in draft_names]


def get_latest_snapshot(dataset: str, openneuro_api_key: str) -> str:
    """Returns the tag of the most recent snapshot."""
    query = """
    query {
        dataset(id: "$dataset") {
            latestSnapshot {
                tag
            }
        }
    }
    """.replace("$dataset", dataset)

    json = {"query": query}
    response = graphql_operation(json, openneuro_api_key)
    return response["data"]["dataset"]["latestSnapshot"]["tag"]


def get_annex_objects(files_by_snapshot: dict, file_list: list) -> list:
    """Returns (snapshot, filename, annex key) for every snapshot version of the given files."""
    targets = set(file_list)
    annex_objects = []

    for tag, files in files_by_snapshot.items():
        for file in files:
            if file["filename"] in targets and file["annexed"]:
                annex_objects.append((tag, file["filename"], file["id"]))

    return annex_objects


def remove_annex_object(dataset: str, snapshot: str, filename: str, annex_key: str, openneuro_api_key: str) -> None:
    """Performs removeAnnexObject mutation on particular file."""
    query = """
    mutation($dataset: ID!, $snapshot: String!, $annexKey: String!, $filename: String!) {
        removeAnnexObject(
            datasetId: $dataset,
            snapshot: $snapshot,
            annexKey: $annexKey,
            filename: $filename)
    }
    """

    json = {
        "query": query,
        "variables": {
            "dataset": dataset,
            "snapshot": snapshot,
            "annexKey": annex_key,
            "filename": filename,
        },
    }
    print(f"Removing annex object for {filename} in {snapshot}")
    response = graphql_operation(json, openneuro_api_key)
    if "data" not in response or not response["data"]["removeAnnexObject"]:
        breakpoint()


def delete_file(dataset: str, filename: str, openneuro_api_key: str) -> None:
    """Performs deleteFiles mutation on particular file."""
    query = """
    mutation($dataset: ID!, $path: String!, $filename: String!) {
        deleteFiles(
            datasetId: $dataset,
            files: [
                {
                    path: $path,
                    filename: $filename
                }
            ]
        )
    }
    """

    json = {
        "query": query,
        "variables": {
            "dataset": dataset,
            "path": os.path.dirname(filename),
            "filename": os.path.basename(filename),
        },
    }
    print(f"Deleting file {filename}")
    response = graphql_operation(json, openneuro_api_key)
    if "data" not in response or not response["data"]["deleteFiles"]:
        breakpoint()


def purge_files(dataset: str, draft_files: list, files_by_snapshot: dict, file_list: list,
                openneuro_api_key: str, skip_delete: bool) -> None:
    """Purges files from an OpenNeuro dataset"""
    for snapshot, filename, annex_key in get_annex_objects(files_by_snapshot, file_list):
        remove_annex_object(dataset, snapshot, filename, annex_key, openneuro_api_key)

    if not skip_delete:
        # Files already gone from the draft can only be purged from snapshots
        for filename in filter_to_draft(draft_files, file_list):
            delete_file(dataset, filename, openneuro_api_key)


def get_uploader_email(dataset: str, openneuro_api_key: str) -> str:
    """Returns the email address of the person who uploaded an OpenNeuro dataset."""

    query = """
    query {
        dataset(id: "$dataset") {
            uploader {
                name,
                email
            }
        }
    }
    """.replace("$dataset", dataset)

    json = {"query": query}
    response = graphql_operation(json, openneuro_api_key)
    uploader = response["data"]["dataset"]["uploader"]
    return uploader["email"]

def send_email(dataset: str, recipient: str, file_list: list) -> None:
    """Sends email to uploader about non-defaced data."""

    email_body = textwrap.dedent("""
    <p>Hello,<br>We've recently discovered that the following images in OpenNeuro dataset $dataset
    have not been defaced and thus have been removed. We would greatly appreciate it
    if you could take the time to deface these images and re-upload! We recommend
    using <a href="https://github.com/poldracklab/pydeface">pydeface</a>. Please reach
    out if you have any questions.</p>
    <pre>$file_list</pre>
    <p>Best,<br>Joe</p>
    <p>--<br>OpenNeuro<br>Stanford University</p>
    """).replace("$dataset", dataset).replace("$file_list", "\n".join(file_list))

    msg = EmailMessage()
    msg['Subject'] = f"OpenNeuro Dataset {dataset} Defacing"
    msg['From'] = config.email 
    msg['To'] = recipient
    msg['Bcc'] = config.email
    msg.set_content(email_body, 'html')
    
    email_pw = kr.get_password('openneuro_deface', 'stanford_pw')
    with smtplib.SMTP('smtp.stanford.edu', 587) as mailserver:
        mailserver.ehlo()
        mailserver.starttls()
        mailserver.login(config.email, email_pw)
        mailserver.send_message(msg)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("-d", "--dataset", help="Dataset number")
    parser.add_argument(
        "-p",
        "--purge-list",
        help="Comma-separated list of regex patterns for files to purge",
    )
    parser.add_argument(
        "-e",
        "--email",
        help="Send to included email address rather than to one pulled from graphql",
    )
    parser.add_argument(
        "--skip-delete",
        action=argparse.BooleanOptionalAction,
        help="Skips the deleteFiles operation when purging.",
    )
    args = parser.parse_args()

    openneuro_api_key = kr.get_password('openneuro_deface', 'openneuro_api_key')

    reg_list = [re.compile(x) for x in args.purge_list.split(",")]
    draft_files, files_by_snapshot = get_all_files(args.dataset, openneuro_api_key)
    file_list = get_file_list(draft_files, files_by_snapshot, reg_list)
    draft_file_list = filter_to_draft(draft_files, file_list)

    user_input_purge = (
        input(f"Purge list: {file_list}\nPurge the above files? (y/n) ")
        .lower()
        .strip()
        == "y"
    )
    if user_input_purge:
        purge_files(args.dataset, draft_files, files_by_snapshot, file_list,
                    openneuro_api_key, args.skip_delete)

    if not draft_file_list:
        print("No matching files in the draft, skipping email")
        return

    if args.email:
        recipient_email = args.email
    else:
        recipient_email = get_uploader_email(args.dataset, openneuro_api_key)

    user_input_email = (
        input(f"Send email to {recipient_email}? (y/n) ").lower().strip() == "y"
    )
    if user_input_email:
        send_email(args.dataset, recipient_email, draft_file_list)


if __name__ == "__main__":
    main()
