import argparse
import json
import os
from collections import defaultdict
from pathlib import Path

import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv


ROOT_PARENT_FOLDER_ID = "__root__"


def find_env_path():
    current_file = Path(__file__).resolve()
    candidate_paths = [
        current_file.parents[2] / ".env",
        current_file.parents[1] / ".env",
        Path.cwd() / ".env",
    ]

    for candidate in candidate_paths:
        if candidate.exists():
            return candidate

    return None


def load_env():
    env_path = find_env_path()
    if env_path is not None:
        load_dotenv(env_path, override=False)


def scan_all_items(table):
    scan_kwargs = {}
    items = []

    while True:
        response = table.scan(**scan_kwargs)
        items.extend(response.get("Items", []))
        last_evaluated_key = response.get("LastEvaluatedKey")
        if not last_evaluated_key:
            break
        scan_kwargs["ExclusiveStartKey"] = last_evaluated_key

    return items


def normalize_parent_folder_id(value):
    if value in {"", None, ROOT_PARENT_FOLDER_ID}:
        return None
    return str(value).strip() or None


def normalize_optional_size(value):
    if value in {"", None}:
        return None

    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def build_folder_segments(folder_id, folders_by_id):
    if not folder_id:
        return []

    segments = []
    cursor = folders_by_id.get(folder_id)
    seen = set()

    while cursor:
        cursor_id = str(cursor["folder_id"])
        if cursor_id in seen:
            raise ValueError(f"Cycle detected while building folder path at folder '{cursor_id}'")
        seen.add(cursor_id)
        segments.append(cursor_id)
        parent_id = normalize_parent_folder_id(cursor.get("parent_folder_id"))
        if not parent_id:
            break
        cursor = folders_by_id.get(parent_id)

    return list(reversed(segments))


def build_file_key(file_item, folders_by_id):
    parent_id = normalize_parent_folder_id(file_item.get("parent_folder_id"))
    segments = build_folder_segments(parent_id, folders_by_id)
    segments.append(str(file_item["file_id"]))
    return "/".join(segments)


def build_folder_key(folder_item, folders_by_id):
    parent_id = normalize_parent_folder_id(folder_item.get("parent_folder_id"))
    segments = build_folder_segments(parent_id, folders_by_id)
    segments.append(str(folder_item["folder_id"]))
    return f"{'/'.join(segments)}/"


def head_object_or_issue(s3_client, bucket, key):
    try:
        response = s3_client.head_object(Bucket=bucket, Key=key)
        return {
            "exists": True,
            "content_length": int(response.get("ContentLength") or 0),
        }
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code", "Unknown")
        if error_code in {"404", "NoSuchKey", "NotFound"}:
            return {
                "exists": False,
                "error_code": error_code,
            }
        return {
            "exists": False,
            "error_code": error_code,
            "error_message": str(exc),
        }


def verify_file_item(file_item, folders_by_id, s3_client, bucket):
    key = build_file_key(file_item, folders_by_id)
    head_result = head_object_or_issue(s3_client, bucket, key)
    expected_size = normalize_optional_size(file_item.get("file_size"))

    result = {
        "kind": "file",
        "file_id": str(file_item["file_id"]),
        "file_name": str(file_item.get("file_name") or ""),
        "status": str(file_item.get("status") or ""),
        "parent_folder_id": normalize_parent_folder_id(file_item.get("parent_folder_id")) or "",
        "s3_key": key,
        "exists_in_s3": bool(head_result.get("exists")),
    }

    if head_result.get("exists"):
        actual_size = int(head_result.get("content_length") or 0)
        result["s3_size_bytes"] = actual_size
        if expected_size is not None:
            result["db_size_bytes"] = expected_size
            result["size_matches"] = expected_size == actual_size
        else:
            result["size_matches"] = True
    else:
        result["error_code"] = head_result.get("error_code", "Unknown")
        if head_result.get("error_message"):
            result["error_message"] = head_result["error_message"]
        result["size_matches"] = expected_size is None

    return result


def verify_folder_item(folder_item, folders_by_id, s3_client, bucket):
    key = build_folder_key(folder_item, folders_by_id)
    head_result = head_object_or_issue(s3_client, bucket, key)

    result = {
        "kind": "folder",
        "folder_id": str(folder_item["folder_id"]),
        "folder_name": str(folder_item.get("folder_name") or ""),
        "status": str(folder_item.get("status") or ""),
        "parent_folder_id": normalize_parent_folder_id(folder_item.get("parent_folder_id")) or "",
        "s3_key": key,
        "exists_in_s3": bool(head_result.get("exists")),
    }

    if head_result.get("exists"):
        result["s3_size_bytes"] = int(head_result.get("content_length") or 0)
    else:
        result["error_code"] = head_result.get("error_code", "Unknown")
        if head_result.get("error_message"):
            result["error_message"] = head_result["error_message"]

    return result


def walk_folder_dfs(folder_item, children_by_parent, folders_by_id, s3_client, bucket, visited_folder_ids, visited_file_ids, findings):
    folder_id = str(folder_item["folder_id"])
    if folder_id in visited_folder_ids:
        findings["folder_issues"].append({
            "kind": "folder",
            "folder_id": folder_id,
            "issue": "cycle_or_duplicate_visit",
        })
        return

    visited_folder_ids.add(folder_id)
    child_folder_items = children_by_parent["folders"].get(folder_id, [])
    child_file_items = children_by_parent["files"].get(folder_id, [])

    for child_folder in child_folder_items:
        walk_folder_dfs(child_folder, children_by_parent, folders_by_id, s3_client, bucket, visited_folder_ids, visited_file_ids, findings)

    for child_file in child_file_items:
        child_file_id = str(child_file["file_id"])
        if child_file_id in visited_file_ids:
            findings["file_issues"].append({
                "kind": "file",
                "file_id": child_file_id,
                "issue": "duplicate_visit",
            })
            continue

        visited_file_ids.add(child_file_id)
        verification = verify_file_item(child_file, folders_by_id, s3_client, bucket)
        if not verification["exists_in_s3"] or not verification.get("size_matches", True):
            findings["file_issues"].append(verification)
        else:
            findings["verified_files"].append(verification)

    folder_verification = verify_folder_item(folder_item, folders_by_id, s3_client, bucket)
    if not folder_verification["exists_in_s3"]:
        findings["folder_issues"].append(folder_verification)
    else:
        findings["verified_folders"].append(folder_verification)


def build_children_index(folder_items, file_items):
    children_by_parent = {
        "folders": defaultdict(list),
        "files": defaultdict(list),
    }

    for folder_item in folder_items:
        parent_id = normalize_parent_folder_id(folder_item.get("parent_folder_id"))
        children_by_parent["folders"][parent_id].append(folder_item)

    for file_item in file_items:
        parent_id = normalize_parent_folder_id(file_item.get("parent_folder_id"))
        children_by_parent["files"][parent_id].append(file_item)

    for key in children_by_parent["folders"]:
        children_by_parent["folders"][key].sort(
            key=lambda item: ((str(item.get("folder_name") or "")).lower(), str(item.get("folder_id") or ""))
        )
    for key in children_by_parent["files"]:
        children_by_parent["files"][key].sort(
            key=lambda item: ((str(item.get("file_name") or "")).lower(), str(item.get("file_id") or ""))
        )

    return children_by_parent


def validate_user_tree(user_id, folder_items, file_items, s3_client, bucket):
    folders_by_id = {
        str(item["folder_id"]): item
        for item in folder_items
    }
    files_by_id = {
        str(item["file_id"]): item
        for item in file_items
    }
    children_by_parent = build_children_index(folder_items, file_items)
    root_folders = children_by_parent["folders"].get(None, [])
    root_files = children_by_parent["files"].get(None, [])

    findings = {
        "user_id": user_id,
        "root_folder_count": len(root_folders),
        "root_file_count": len(root_files),
        "verified_files": [],
        "verified_folders": [],
        "file_issues": [],
        "folder_issues": [],
        "orphan_files": [],
        "orphan_folders": [],
    }

    visited_folder_ids = set()
    visited_file_ids = set()

    for root_file in root_files:
        root_file_id = str(root_file["file_id"])
        visited_file_ids.add(root_file_id)
        verification = verify_file_item(root_file, folders_by_id, s3_client, bucket)
        if not verification["exists_in_s3"] or not verification.get("size_matches", True):
            findings["file_issues"].append(verification)
        else:
            findings["verified_files"].append(verification)

    for root_folder in root_folders:
        walk_folder_dfs(
            root_folder,
            children_by_parent,
            folders_by_id,
            s3_client,
            bucket,
            visited_folder_ids,
            visited_file_ids,
            findings,
        )

    for folder_id, folder_item in folders_by_id.items():
        if folder_id not in visited_folder_ids:
            findings["orphan_folders"].append({
                "folder_id": folder_id,
                "folder_name": str(folder_item.get("folder_name") or ""),
                "parent_folder_id": normalize_parent_folder_id(folder_item.get("parent_folder_id")) or "",
                "status": str(folder_item.get("status") or ""),
            })

    for file_id, file_item in files_by_id.items():
        if file_id not in visited_file_ids:
            findings["orphan_files"].append({
                "file_id": file_id,
                "file_name": str(file_item.get("file_name") or ""),
                "parent_folder_id": normalize_parent_folder_id(file_item.get("parent_folder_id")) or "",
                "status": str(file_item.get("status") or ""),
            })

    findings["summary"] = {
        "verified_file_count": len(findings["verified_files"]),
        "verified_folder_count": len(findings["verified_folders"]),
        "file_issue_count": len(findings["file_issues"]),
        "folder_issue_count": len(findings["folder_issues"]),
        "orphan_file_count": len(findings["orphan_files"]),
        "orphan_folder_count": len(findings["orphan_folders"]),
    }

    return findings


def resolve_settings(file_table_name: str | None, folder_table_name: str | None, bucket_name: str | None):
    load_env()
    resolved_file_table_name = file_table_name or os.getenv("FILE_METADATA_TABLE", "file-meta-data").strip() or "file-meta-data"
    resolved_folder_table_name = folder_table_name or os.getenv("FOLDER_METADATA_TABLE", "folder-meta-data").strip() or "folder-meta-data"
    resolved_bucket_name = bucket_name or os.getenv("S3_BUCKET", "").strip()

    if not resolved_bucket_name:
        raise SystemExit("S3_BUCKET is required")

    return resolved_file_table_name, resolved_folder_table_name, resolved_bucket_name


def run_validation(file_table_name: str | None, folder_table_name: str | None, bucket_name: str | None, user_id: str | None):
    resolved_file_table_name, resolved_folder_table_name, resolved_bucket_name = resolve_settings(
        file_table_name,
        folder_table_name,
        bucket_name,
    )

    dynamodb = boto3.resource("dynamodb")
    s3_client = boto3.client("s3")
    file_table = dynamodb.Table(resolved_file_table_name)
    folder_table = dynamodb.Table(resolved_folder_table_name)

    file_items = scan_all_items(file_table)
    folder_items = scan_all_items(folder_table)

    grouped_files = defaultdict(list)
    grouped_folders = defaultdict(list)

    for file_item in file_items:
        current_user_id = str(file_item.get("user_id") or "").strip()
        if user_id and current_user_id != user_id:
            continue
        grouped_files[current_user_id].append(file_item)

    for folder_item in folder_items:
        current_user_id = str(folder_item.get("user_id") or "").strip()
        if user_id and current_user_id != user_id:
            continue
        grouped_folders[current_user_id].append(folder_item)

    target_user_ids = sorted(set(grouped_files.keys()) | set(grouped_folders.keys()))
    results = []

    for current_user_id in target_user_ids:
        results.append(
            validate_user_tree(
                user_id=current_user_id,
                folder_items=grouped_folders.get(current_user_id, []),
                file_items=grouped_files.get(current_user_id, []),
                s3_client=s3_client,
                bucket=resolved_bucket_name,
            )
        )

    return {
        "bucket": resolved_bucket_name,
        "file_table": resolved_file_table_name,
        "folder_table": resolved_folder_table_name,
        "users": results,
    }


def print_report(report):
    print(f"Bucket: {report['bucket']}")
    print(f"File table: {report['file_table']}")
    print(f"Folder table: {report['folder_table']}")

    for user_report in report["users"]:
        summary = user_report["summary"]
        print("")
        print(f"User: {user_report['user_id'] or '<missing-user-id>'}")
        print(f"- Root folders: {user_report['root_folder_count']}")
        print(f"- Root files: {user_report['root_file_count']}")
        print(f"- Verified files: {summary['verified_file_count']}")
        print(f"- Verified folders: {summary['verified_folder_count']}")
        print(f"- File issues: {summary['file_issue_count']}")
        print(f"- Folder issues: {summary['folder_issue_count']}")
        print(f"- Orphan files: {summary['orphan_file_count']}")
        print(f"- Orphan folders: {summary['orphan_folder_count']}")

        if user_report["file_issues"]:
            print("  File issues:")
            for issue in user_report["file_issues"]:
                print(f"  - {json.dumps(issue, default=str)}")

        if user_report["folder_issues"]:
            print("  Folder issues:")
            for issue in user_report["folder_issues"]:
                print(f"  - {json.dumps(issue, default=str)}")

        if user_report["orphan_files"]:
            print("  Orphan files:")
            for issue in user_report["orphan_files"]:
                print(f"  - {json.dumps(issue, default=str)}")

        if user_report["orphan_folders"]:
            print("  Orphan folders:")
            for issue in user_report["orphan_folders"]:
                print(f"  - {json.dumps(issue, default=str)}")


def main():
    parser = argparse.ArgumentParser(
        description="Validate Dynamo metadata against S3 objects using the DB as the source of truth.",
    )
    parser.add_argument(
        "--file-table",
        dest="file_table_name",
        help="File metadata table name. Defaults to FILE_METADATA_TABLE from .env.",
    )
    parser.add_argument(
        "--folder-table",
        dest="folder_table_name",
        help="Folder metadata table name. Defaults to FOLDER_METADATA_TABLE from .env.",
    )
    parser.add_argument(
        "--bucket",
        dest="bucket_name",
        help="S3 bucket name. Defaults to S3_BUCKET from .env.",
    )
    parser.add_argument(
        "--user-id",
        dest="user_id",
        help="Optional user id filter. Defaults to all users in the tables.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the full report as JSON instead of the text summary.",
    )
    args = parser.parse_args()

    report = run_validation(
        file_table_name=args.file_table_name,
        folder_table_name=args.folder_table_name,
        bucket_name=args.bucket_name,
        user_id=args.user_id,
    )

    if args.json:
        print(json.dumps(report, indent=2, default=str))
        return

    print_report(report)


if __name__ == "__main__":
    main()
