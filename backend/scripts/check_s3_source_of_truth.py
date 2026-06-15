import argparse
import json
import os
from collections import defaultdict
from pathlib import Path

import boto3
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


def list_all_s3_objects(bucket_name: str):
    s3_client = boto3.client("s3")
    paginator = s3_client.get_paginator("list_objects_v2")
    objects = []

    for page in paginator.paginate(Bucket=bucket_name):
        objects.extend(page.get("Contents", []))

    return objects


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


def build_expected_file_key(file_item, folders_by_id):
    parent_id = normalize_parent_folder_id(file_item.get("parent_folder_id"))
    segments = build_folder_segments(parent_id, folders_by_id)
    segments.append(str(file_item["file_id"]))
    return "/".join(segments)


def build_expected_folder_key(folder_item, folders_by_id):
    parent_id = normalize_parent_folder_id(folder_item.get("parent_folder_id"))
    segments = build_folder_segments(parent_id, folders_by_id)
    segments.append(str(folder_item["folder_id"]))
    return f"{'/'.join(segments)}/"


def parse_file_object_key(key: str):
    normalized_key = str(key or "").strip("/")
    if not normalized_key:
        return None

    parts = normalized_key.split("/")
    file_id = parts[-1]
    ancestor_folder_ids = parts[:-1]
    return {
        "file_id": file_id,
        "ancestor_folder_ids": ancestor_folder_ids,
    }


def parse_folder_object_key(key: str):
    if not str(key or "").endswith("/"):
        return None

    normalized_key = str(key or "").strip("/")
    if not normalized_key:
        return None

    parts = normalized_key.split("/")
    folder_id = parts[-1]
    ancestor_folder_ids = parts[:-1]
    return {
        "folder_id": folder_id,
        "ancestor_folder_ids": ancestor_folder_ids,
    }


def classify_s3_object(key: str):
    if str(key or "").endswith("/"):
        parsed = parse_folder_object_key(key)
        if not parsed:
            return None
        return {
            "kind": "folder",
            **parsed,
        }

    parsed = parse_file_object_key(key)
    if not parsed:
        return None
    return {
        "kind": "file",
        **parsed,
    }


def compare_ancestor_chain(ancestor_folder_ids, db_parent_folder_id, folders_by_id):
    expected_ancestors = build_folder_segments(db_parent_folder_id, folders_by_id)
    return expected_ancestors == list(ancestor_folder_ids), expected_ancestors


def verify_s3_file_object(object_item, parsed, files_by_id, folders_by_id):
    file_id = parsed["file_id"]
    db_item = files_by_id.get(file_id)
    result = {
        "kind": "file",
        "file_id": file_id,
        "s3_key": str(object_item["Key"]),
        "s3_size_bytes": int(object_item.get("Size") or 0),
    }

    if not db_item:
        result["issue"] = "missing_db_entry"
        return result

    db_parent_folder_id = normalize_parent_folder_id(db_item.get("parent_folder_id"))
    expected_key = build_expected_file_key(db_item, folders_by_id)
    chain_matches, expected_ancestors = compare_ancestor_chain(
        parsed["ancestor_folder_ids"],
        db_parent_folder_id,
        folders_by_id,
    )

    result.update({
        "db_parent_folder_id": db_parent_folder_id or "",
        "db_status": str(db_item.get("status") or ""),
        "expected_key": expected_key,
        "path_matches": expected_key == str(object_item["Key"]),
        "ancestor_chain_matches": chain_matches,
        "expected_ancestor_folder_ids": expected_ancestors,
    })

    db_size = normalize_optional_size(db_item.get("file_size"))
    if db_size is not None:
        result["db_size_bytes"] = db_size
        result["size_matches"] = db_size == result["s3_size_bytes"]
    else:
        result["size_matches"] = True

    if not result["path_matches"] or not result["ancestor_chain_matches"] or not result["size_matches"]:
        result["issue"] = "db_mismatch"

    return result


def verify_s3_folder_object(object_item, parsed, folders_by_id):
    folder_id = parsed["folder_id"]
    db_item = folders_by_id.get(folder_id)
    result = {
        "kind": "folder",
        "folder_id": folder_id,
        "s3_key": str(object_item["Key"]),
        "s3_size_bytes": int(object_item.get("Size") or 0),
    }

    if not db_item:
        result["issue"] = "missing_db_entry"
        return result

    db_parent_folder_id = normalize_parent_folder_id(db_item.get("parent_folder_id"))
    expected_key = build_expected_folder_key(db_item, folders_by_id)
    chain_matches, expected_ancestors = compare_ancestor_chain(
        parsed["ancestor_folder_ids"],
        db_parent_folder_id,
        folders_by_id,
    )

    result.update({
        "db_parent_folder_id": db_parent_folder_id or "",
        "db_status": str(db_item.get("status") or ""),
        "expected_key": expected_key,
        "path_matches": expected_key == str(object_item["Key"]),
        "ancestor_chain_matches": chain_matches,
        "expected_ancestor_folder_ids": expected_ancestors,
    })

    if not result["path_matches"] or not result["ancestor_chain_matches"]:
        result["issue"] = "db_mismatch"

    return result


def resolve_settings(file_table_name: str | None, folder_table_name: str | None, bucket_name: str | None):
    load_env()
    resolved_file_table_name = file_table_name or os.getenv("FILE_METADATA_TABLE", "file-meta-data").strip() or "file-meta-data"
    resolved_folder_table_name = (
        folder_table_name or os.getenv("FOLDER_METADATA_TABLE", "folder-meta-data").strip() or "folder-meta-data"
    )
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
    file_table = dynamodb.Table(resolved_file_table_name)
    folder_table = dynamodb.Table(resolved_folder_table_name)

    file_items = scan_all_items(file_table)
    folder_items = scan_all_items(folder_table)

    files_by_id = {
        str(item["file_id"]): item
        for item in file_items
        if not user_id or str(item.get("user_id") or "").strip() == user_id
    }
    folders_by_id = {
        str(item["folder_id"]): item
        for item in folder_items
        if not user_id or str(item.get("user_id") or "").strip() == user_id
    }

    s3_objects = list_all_s3_objects(resolved_bucket_name)
    matched_file_ids = set()
    matched_folder_ids = set()
    file_issues = []
    folder_issues = []
    verified_files = []
    verified_folders = []
    skipped_objects = []

    for object_item in s3_objects:
        parsed = classify_s3_object(object_item.get("Key", ""))
        if not parsed:
            skipped_objects.append({
                "s3_key": str(object_item.get("Key") or ""),
                "issue": "unrecognized_key_shape",
            })
            continue

        if parsed["kind"] == "file":
            result = verify_s3_file_object(object_item, parsed, files_by_id, folders_by_id)
            if result.get("issue"):
                file_issues.append(result)
            else:
                matched_file_ids.add(result["file_id"])
                verified_files.append(result)
            continue

        result = verify_s3_folder_object(object_item, parsed, folders_by_id)
        if result.get("issue"):
            folder_issues.append(result)
        else:
            matched_folder_ids.add(result["folder_id"])
            verified_folders.append(result)

    missing_db_files = []
    for file_id, file_item in files_by_id.items():
        expected_key = build_expected_file_key(file_item, folders_by_id)
        if file_id not in matched_file_ids:
            missing_db_files.append({
                "file_id": file_id,
                "file_name": str(file_item.get("file_name") or ""),
                "status": str(file_item.get("status") or ""),
                "expected_key": expected_key,
                "issue": "missing_s3_object",
            })

    missing_db_folders = []
    for folder_id, folder_item in folders_by_id.items():
        expected_key = build_expected_folder_key(folder_item, folders_by_id)
        if folder_id not in matched_folder_ids:
            missing_db_folders.append({
                "folder_id": folder_id,
                "folder_name": str(folder_item.get("folder_name") or ""),
                "status": str(folder_item.get("status") or ""),
                "expected_key": expected_key,
                "issue": "missing_s3_object",
            })

    return {
        "bucket": resolved_bucket_name,
        "file_table": resolved_file_table_name,
        "folder_table": resolved_folder_table_name,
        "user_id_filter": user_id or "",
        "summary": {
            "s3_object_count": len(s3_objects),
            "verified_file_count": len(verified_files),
            "verified_folder_count": len(verified_folders),
            "file_issue_count": len(file_issues),
            "folder_issue_count": len(folder_issues),
            "missing_db_file_count": len(missing_db_files),
            "missing_db_folder_count": len(missing_db_folders),
            "skipped_object_count": len(skipped_objects),
        },
        "verified_files": verified_files,
        "verified_folders": verified_folders,
        "file_issues": file_issues,
        "folder_issues": folder_issues,
        "missing_db_files": missing_db_files,
        "missing_db_folders": missing_db_folders,
        "skipped_objects": skipped_objects,
    }


def print_report(report):
    print(f"Bucket: {report['bucket']}")
    print(f"File table: {report['file_table']}")
    print(f"Folder table: {report['folder_table']}")
    if report["user_id_filter"]:
        print(f"User filter: {report['user_id_filter']}")

    summary = report["summary"]
    print(f"- S3 objects: {summary['s3_object_count']}")
    print(f"- Verified files: {summary['verified_file_count']}")
    print(f"- Verified folders: {summary['verified_folder_count']}")
    print(f"- File issues: {summary['file_issue_count']}")
    print(f"- Folder issues: {summary['folder_issue_count']}")
    print(f"- Missing DB files in S3 check: {summary['missing_db_file_count']}")
    print(f"- Missing DB folders in S3 check: {summary['missing_db_folder_count']}")
    print(f"- Skipped objects: {summary['skipped_object_count']}")

    if report["file_issues"]:
        print("File issues:")
        for issue in report["file_issues"]:
            print(f"- {json.dumps(issue, default=str)}")

    if report["folder_issues"]:
        print("Folder issues:")
        for issue in report["folder_issues"]:
            print(f"- {json.dumps(issue, default=str)}")

    if report["missing_db_files"]:
        print("DB files missing from S3 walk:")
        for issue in report["missing_db_files"]:
            print(f"- {json.dumps(issue, default=str)}")

    if report["missing_db_folders"]:
        print("DB folders missing from S3 walk:")
        for issue in report["missing_db_folders"]:
            print(f"- {json.dumps(issue, default=str)}")

    if report["skipped_objects"]:
        print("Skipped S3 objects:")
        for issue in report["skipped_objects"]:
            print(f"- {json.dumps(issue, default=str)}")


def main():
    parser = argparse.ArgumentParser(
        description="Validate Dynamo metadata against S3 objects using S3 as the source of truth.",
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
        help="Optional user id filter. Defaults to all matching DB entries.",
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
