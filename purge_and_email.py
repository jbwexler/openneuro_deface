import requests
import os
import argparse
import config
import subprocess
import glob
import re
import smtplib
import keyring as kr
from email.message import EmailMessage
import textwrap


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
    response = requests.post(url, headers=headers, json=json, cookies=cookies)
    return response.json()


def get_file_list(ds_path: str, regexes: list) -> list:
    """Returns a list of file paths that match the inputted regexes."""
    all_file_list = glob.glob(os.path.join(ds_path, "sub-*/**/*"), recursive=True)

    match_list = []
    for file_path in all_file_list:
        basename = os.path.basename(file_path)
        for reg in regexes:
            if re.match(reg, basename):
                match_list.append(file_path)

    return sorted(match_list)


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


def get_relevant_snapshots(dataset: str, path: str, ds_path: str) -> set:
    """Returns set of snapshots containing changes to a file."""
    snapshot_list = []

    log_sp = subprocess.run(
        f"git -C '{ds_path}' log --pretty=format:%H '{path}'",
        shell=True,
        capture_output=True,
    )
    commit_list = log_sp.stdout.decode("utf-8").split("\n")

    for commit in commit_list:
        log_sp = subprocess.run(
            f"git -C '{ds_path}' tag --contains {commit}",
            shell=True,
            capture_output=True,
        )
        tag_list = log_sp.stdout.decode("utf-8").split("\n")
        # First tag is the snapshot containing the relevant changes
        snapshot_list.append(tag_list[0])
    
    return set(snapshot_list)


def get_annex_key(path: str) -> str:
    """Returns the annex key of a file"""
    symlink = os.readlink(path)
    return os.path.basename(symlink)


def remove_annex_object(dataset: str, snapshot: str, path: str, ds_path: str, openneuro_api_key: str) -> None:
    """Performs removeAnnexObject mutation on particular file."""
    annex_key = get_annex_key(path)
    rel_path = os.path.relpath(path, ds_path)

    # Remove annex object
    query = (
        """
    mutation {
        removeAnnexObject(
            datasetId: "$dataset",
            snapshot: "$snapshot",
            annexKey: "$annex_key",
            filename: "$filename")
    }
    """.replace("$dataset", dataset)
        .replace("$snapshot", snapshot)
        .replace("$annex_key", annex_key)
        .replace("$filename", rel_path)
    )

    json = {"query": query}
    print(f"Removing annex object for {rel_path}")
    response = graphql_operation(json, openneuro_api_key)
    if "data" not in response or not response["data"]["removeAnnexObject"]:
        breakpoint()


def delete_file(dataset: str, path: str, ds_path: str, openneuro_api_key: str) -> None:
    """Performs deleteFiles mutation on particular file."""
    rel_path = os.path.relpath(path, ds_path)
    dirname = os.path.dirname(rel_path)
    basename = os.path.basename(rel_path)

    query = (
        """
    mutation {
        deleteFiles(
            datasetId: "$dataset",
            files: [
                {
                    path: "$path", 
                    filename: "$filename"
                }
            ]
        )
    }
    """.replace("$dataset", dataset)
        .replace("$path", dirname)
        .replace("$filename", basename)
    )

    json = {"query": query}
    print(f"Deleting file {rel_path}")
    response = graphql_operation(json, openneuro_api_key)
    if "data" not in response or not response["data"]["deleteFiles"]:
        breakpoint()


def purge_files(dataset: str, file_list: list, ds_path: str, openneuro_api_key: str, skip_delete: bool) -> None:
    """Purges files from an OpenNeuro dataset"""

    for path in file_list:
        snapshot_set = get_relevant_snapshots(dataset, path, ds_path)
        for snapshot in snapshot_set: 
            remove_annex_object(dataset, snapshot, path, ds_path, openneuro_api_key)
        if not skip_delete:
            delete_file(dataset, path, ds_path, openneuro_api_key)


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
    ds_path = os.path.join(config.ds_dir, args.dataset)
    gh_repo_url = os.path.join(config.gh_org_url, args.dataset) + ".git"

    if os.path.isdir(ds_path):
        subprocess.run(f"datalad update -d '{ds_path}' --merge", shell=True)
    else:
        subprocess.run(f"datalad clone {gh_repo_url} '{ds_path}'", shell=True)

    reg_list = [re.compile(x) for x in args.purge_list.split(",")]
    file_list = get_file_list(ds_path, reg_list)
    rel_file_list = [os.path.relpath(x, ds_path) for x in file_list]

    user_input_purge = (
        input(f"Purge list: {rel_file_list}\nPurge the above files? (y/n) ")
        .lower()
        .strip()
        == "y"
    )
    if user_input_purge:
        purge_files(args.dataset, file_list, ds_path, openneuro_api_key, args.skip_delete)

    if args.email:
        recipient_email = args.email
    else:
        recipient_email = get_uploader_email(args.dataset, openneuro_api_key)

    user_input_email = (
        input(f"Send email to {recipient_email}? (y/n) ").lower().strip() == "y"
    )
    if user_input_email:
        send_email(args.dataset, recipient_email, rel_file_list)


if __name__ == "__main__":
    main()
