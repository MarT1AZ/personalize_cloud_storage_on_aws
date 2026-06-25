import argparse
import json
import os
from collections import defaultdict
from pathlib import Path

import boto3
from dotenv import load_dotenv


ROOT_PARENT_FOLDER_ID = "__root__"
FILE_ALLOWED_STATUSES = {"active", "deleted", "moved", "pending", "replacement_pending_delete"}
FOLDER_ALLOWED_STATUSES = {"active", "deleted", "moved", "pending"}
UPLOAD_ALLOWED_STATES = {"uploading", "pending_finalize", "finalizing", "repair_required"}


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
        "user_id": str(db_item.get("user_id") or ""),
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
        "user_id": str(db_item.get("user_id") or ""),
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


def validate_file_state(file_item, folders_by_id):
    issues = []
    file_id = str(file_item.get("file_id") or "")
    status = str(file_item.get("status") or "").strip()
    deleted_at = file_item.get("deleted_at")
    upload_state = str(file_item.get("upload_state") or "").strip()
    operation_id = str(file_item.get("folder_upload_operation_id") or "").strip()
    root_id = str(file_item.get("folder_upload_root_id") or "").strip()

    if status not in FILE_ALLOWED_STATUSES:
        issues.append({
            "kind": "file",
            "file_id": file_id,
            "issue": "invalid_status",
            "status": status,
        })
    if status == "active" and deleted_at not in {"", None}:
        issues.append({
            "kind": "file",
            "file_id": file_id,
            "issue": "active_with_deleted_at",
            "deleted_at": deleted_at,
        })
    if status == "deleted" and deleted_at in {"", None}:
        issues.append({
            "kind": "file",
            "file_id": file_id,
            "issue": "deleted_without_deleted_at",
        })
    if upload_state and upload_state not in UPLOAD_ALLOWED_STATES:
        issues.append({
            "kind": "file",
            "file_id": file_id,
            "issue": "invalid_upload_state",
            "upload_state": upload_state,
        })
    if upload_state and (not operation_id or not root_id):
        issues.append({
            "kind": "file",
            "file_id": file_id,
            "issue": "upload_state_missing_folder_upload_context",
            "upload_state": upload_state,
        })
    if root_id and root_id not in folders_by_id:
        issues.append({
            "kind": "file",
            "file_id": file_id,
            "issue": "missing_folder_upload_root_folder",
            "folder_upload_root_id": root_id,
        })
    if upload_state == "pending_finalize" and status != "active":
        issues.append({
            "kind": "file",
            "file_id": file_id,
            "issue": "pending_finalize_requires_active_status",
            "status": status,
        })
    if upload_state == "uploading" and status not in {"pending", "active"}:
        issues.append({
            "kind": "file",
            "file_id": file_id,
            "issue": "uploading_state_has_unexpected_status",
            "status": status,
        })

    return issues


def validate_folder_state(folder_item, folders_by_id):
    issues = []
    folder_id = str(folder_item.get("folder_id") or "")
    status = str(folder_item.get("status") or "").strip()
    deleted_at = folder_item.get("deleted_at")
    upload_state = str(folder_item.get("upload_state") or "").strip()
    upload_warning = str(folder_item.get("upload_warning") or "").strip()
    operation_id = str(folder_item.get("folder_upload_operation_id") or "").strip()
    root_id = str(folder_item.get("folder_upload_root_id") or "").strip()

    if status not in FOLDER_ALLOWED_STATUSES:
        issues.append({
            "kind": "folder",
            "folder_id": folder_id,
            "issue": "invalid_status",
            "status": status,
        })
    if status == "active" and deleted_at not in {"", None}:
        issues.append({
            "kind": "folder",
            "folder_id": folder_id,
            "issue": "active_with_deleted_at",
            "deleted_at": deleted_at,
        })
    if status == "deleted" and deleted_at in {"", None}:
        issues.append({
            "kind": "folder",
            "folder_id": folder_id,
            "issue": "deleted_without_deleted_at",
        })
    if upload_state and upload_state not in UPLOAD_ALLOWED_STATES:
        issues.append({
            "kind": "folder",
            "folder_id": folder_id,
            "issue": "invalid_upload_state",
            "upload_state": upload_state,
        })
    if upload_state and (not operation_id or not root_id):
        issues.append({
            "kind": "folder",
            "folder_id": folder_id,
            "issue": "upload_state_missing_folder_upload_context",
            "upload_state": upload_state,
        })
    if root_id and root_id not in folders_by_id:
        issues.append({
            "kind": "folder",
            "folder_id": folder_id,
            "issue": "missing_folder_upload_root_folder",
            "folder_upload_root_id": root_id,
        })
    if root_id and folder_id == root_id and not upload_state:
        issues.append({
            "kind": "folder",
            "folder_id": folder_id,
            "issue": "folder_upload_root_missing_upload_state",
        })
    if folder_id != root_id and upload_warning:
        issues.append({
            "kind": "folder",
            "folder_id": folder_id,
            "issue": "non_root_folder_has_upload_warning",
            "upload_warning": upload_warning,
        })

    return issues


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

    db_state_issues = []
    for file_item in files_by_id.values():
        db_state_issues.extend(validate_file_state(file_item, folders_by_id))
    for folder_item in folders_by_id.values():
        db_state_issues.extend(validate_folder_state(folder_item, folders_by_id))

    s3_objects = list_all_s3_objects(resolved_bucket_name)
    matched_file_ids = set()
    matched_folder_ids = set()
    s3_only_file_issues = []
    s3_only_folder_issues = []
    db_mismatch_file_issues = []
    db_mismatch_folder_issues = []
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
            if result.get("issue") == "missing_db_entry":
                s3_only_file_issues.append(result)
            elif result.get("issue"):
                db_mismatch_file_issues.append(result)
            else:
                matched_file_ids.add(result["file_id"])
                verified_files.append(result)
            continue

        result = verify_s3_folder_object(object_item, parsed, folders_by_id)
        if result.get("issue") == "missing_db_entry":
            s3_only_folder_issues.append(result)
        elif result.get("issue"):
            db_mismatch_folder_issues.append(result)
        else:
            matched_folder_ids.add(result["folder_id"])
            verified_folders.append(result)

    db_only_files = []
    for file_id, file_item in files_by_id.items():
        expected_key = build_expected_file_key(file_item, folders_by_id)
        if file_id not in matched_file_ids:
            db_only_files.append({
                "kind": "file",
                "file_id": file_id,
                "file_name": str(file_item.get("file_name") or ""),
                "user_id": str(file_item.get("user_id") or ""),
                "status": str(file_item.get("status") or ""),
                "expected_key": expected_key,
                "issue": "missing_s3_object",
            })

    db_only_folders = []
    for folder_id, folder_item in folders_by_id.items():
        expected_key = build_expected_folder_key(folder_item, folders_by_id)
        if folder_id not in matched_folder_ids:
            db_only_folders.append({
                "kind": "folder",
                "folder_id": folder_id,
                "folder_name": str(folder_item.get("folder_name") or ""),
                "user_id": str(folder_item.get("user_id") or ""),
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
            "s3_only_file_count": len(s3_only_file_issues),
            "s3_only_folder_count": len(s3_only_folder_issues),
            "db_only_file_count": len(db_only_files),
            "db_only_folder_count": len(db_only_folders),
            "db_mismatch_file_count": len(db_mismatch_file_issues),
            "db_mismatch_folder_count": len(db_mismatch_folder_issues),
            "db_state_issue_count": len(db_state_issues),
            "skipped_object_count": len(skipped_objects),
        },
        "verified_files": verified_files,
        "verified_folders": verified_folders,
        "s3_only_file_issues": s3_only_file_issues,
        "s3_only_folder_issues": s3_only_folder_issues,
        "db_only_files": db_only_files,
        "db_only_folders": db_only_folders,
        "db_mismatch_file_issues": db_mismatch_file_issues,
        "db_mismatch_folder_issues": db_mismatch_folder_issues,
        "db_state_issues": db_state_issues,
        "skipped_objects": skipped_objects,
    }


def print_group(title, issues):
    if not issues:
        return

    print(title)
    for issue in issues:
        print(f"- {json.dumps(issue, default=str)}")


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
    print(f"- S3-only files: {summary['s3_only_file_count']}")
    print(f"- S3-only folders: {summary['s3_only_folder_count']}")
    print(f"- DB-only files: {summary['db_only_file_count']}")
    print(f"- DB-only folders: {summary['db_only_folder_count']}")
    print(f"- DB mismatch files: {summary['db_mismatch_file_count']}")
    print(f"- DB mismatch folders: {summary['db_mismatch_folder_count']}")
    print(f"- DB state issues: {summary['db_state_issue_count']}")
    print(f"- Skipped objects: {summary['skipped_object_count']}")

    print_group("S3-only file issues:", report["s3_only_file_issues"])
    print_group("S3-only folder issues:", report["s3_only_folder_issues"])
    print_group("DB-only files:", report["db_only_files"])
    print_group("DB-only folders:", report["db_only_folders"])
    print_group("DB mismatch file issues:", report["db_mismatch_file_issues"])
    print_group("DB mismatch folder issues:", report["db_mismatch_folder_issues"])
    print_group("DB state issues:", report["db_state_issues"])
    print_group("Skipped S3 objects:", report["skipped_objects"])


def main():
    parser = argparse.ArgumentParser(
        description="Validate storage objects using S3 as primary truth while also checking DB-only objects and DB state validity.",
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
