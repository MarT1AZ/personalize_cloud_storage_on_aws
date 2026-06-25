import argparse
import json
import os
from pathlib import Path

import boto3
from dotenv import load_dotenv


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


def resolve_settings():
    load_env()
    return {
        "file_table": os.getenv("FILE_METADATA_TABLE", "file-meta-data").strip() or "file-meta-data",
        "folder_table": os.getenv("FOLDER_METADATA_TABLE", "folder-meta-data").strip() or "folder-meta-data",
        "deletion_log_table": os.getenv("DELETION_LOG_TABLE", "deletion_log").strip() or "deletion_log",
        "replacement_table": os.getenv("REPLACEMENT_TABLE", "replacement").strip() or "replacement",
        "folder_upload_log_table": os.getenv("FOLDER_UPLOAD_LOG_TABLE", "folder_upload_log").strip() or "folder_upload_log",
        "move_log_table": os.getenv("MOVE_LOG_TABLE", "move_log").strip() or "move_log",
        "purge_log_table": os.getenv("PURGE_LOG_TABLE", "purge_log").strip() or "purge_log",
    }


def build_lookup(items, key_name):
    return {
        str(item.get(key_name) or "").strip(): item
        for item in items
        if str(item.get(key_name) or "").strip()
    }


def append_issue(issues, table_name, log_id, issue, **extra):
    payload = {
        "table": table_name,
        "log_id": log_id,
        "issue": issue,
    }
    payload.update(extra)
    issues.append(payload)


def validate_deletion_logs(items, file_lookup, folder_lookup, issues):
    allowed_statuses = {"running", "failed", "done"}
    for item in items:
        log_id = str(item.get("log_id") or "").strip()
        status = str(item.get("status") or "").strip()
        root_kind = str(item.get("root_entry_kind") or "").strip()
        root_id = str(item.get("root_entry_id") or "").strip()

        if status not in allowed_statuses:
            append_issue(issues, "deletion_log", log_id, "invalid_status", status=status)
        if root_kind not in {"file", "folder"}:
            append_issue(issues, "deletion_log", log_id, "invalid_root_entry_kind", root_entry_kind=root_kind)
            continue
        if not root_id:
            append_issue(issues, "deletion_log", log_id, "missing_root_entry_id")
            continue

        lookup = file_lookup if root_kind == "file" else folder_lookup
        if root_id not in lookup and status == "running":
            append_issue(issues, "deletion_log", log_id, "running_log_missing_root_entry", root_entry_kind=root_kind, root_entry_id=root_id)


def validate_replacement_logs(items, file_lookup, issues):
    allowed_statuses = {"running", "failed", "done"}
    for item in items:
        log_id = str(item.get("log_id") or "").strip()
        status = str(item.get("status") or "").strip()
        old_file_id = str(item.get("old_file_id") or "").strip()
        new_file_id = str(item.get("new_file_id") or "").strip()

        if status not in allowed_statuses:
            append_issue(issues, "replacement", log_id, "invalid_status", status=status)
        if not old_file_id:
            append_issue(issues, "replacement", log_id, "missing_old_file_id")
        if old_file_id and old_file_id not in file_lookup and status == "running":
            append_issue(issues, "replacement", log_id, "running_log_missing_old_file", old_file_id=old_file_id)
        if new_file_id and new_file_id not in file_lookup and status == "running":
            append_issue(issues, "replacement", log_id, "running_log_missing_new_file", new_file_id=new_file_id)


def validate_folder_upload_logs(items, folder_lookup, issues):
    allowed_statuses = {"running", "failed", "done", "repair_required"}
    allowed_phases = {
        "ready_to_upload",
        "creating_folder_tree",
        "uploading_files",
        "finalizing",
        "repair_required",
        "failed",
        "done",
    }
    for item in items:
        log_id = str(item.get("log_id") or "").strip()
        status = str(item.get("status") or "").strip()
        phase = str(item.get("phase") or "").strip()
        root_folder_id = str(item.get("root_folder_id") or "").strip()

        if status not in allowed_statuses:
            append_issue(issues, "folder_upload_log", log_id, "invalid_status", status=status)
        if phase not in allowed_phases:
            append_issue(issues, "folder_upload_log", log_id, "invalid_phase", phase=phase)
        if not root_folder_id:
            append_issue(issues, "folder_upload_log", log_id, "missing_root_folder_id")
        elif root_folder_id not in folder_lookup and status in {"running", "repair_required"}:
            append_issue(issues, "folder_upload_log", log_id, "active_log_missing_root_folder", root_folder_id=root_folder_id)


def validate_move_logs(items, file_lookup, folder_lookup, issues):
    allowed_statuses = {"running", "failed", "done"}
    for item in items:
        log_id = str(item.get("log_id") or "").strip()
        status = str(item.get("status") or "").strip()
        source_kind = str(item.get("source_entry_kind") or "").strip()
        source_id = str(item.get("source_entry_id") or "").strip()

        if status not in allowed_statuses:
            append_issue(issues, "move_log", log_id, "invalid_status", status=status)
        if source_kind not in {"file", "folder"}:
            append_issue(issues, "move_log", log_id, "invalid_source_entry_kind", source_entry_kind=source_kind)
            continue
        if not source_id:
            append_issue(issues, "move_log", log_id, "missing_source_entry_id")
            continue

        lookup = file_lookup if source_kind == "file" else folder_lookup
        if source_id not in lookup and status == "running":
            append_issue(issues, "move_log", log_id, "running_log_missing_source_entry", source_entry_kind=source_kind, source_entry_id=source_id)


def validate_purge_logs(items, file_lookup, folder_lookup, issues):
    allowed_statuses = {"running", "failed", "done"}
    for item in items:
        log_id = str(item.get("log_id") or "").strip()
        status = str(item.get("status") or "").strip()
        root_kind = str(item.get("root_entry_kind") or "").strip()
        root_id = str(item.get("root_entry_id") or "").strip()

        if status not in allowed_statuses:
            append_issue(issues, "purge_log", log_id, "invalid_status", status=status)
        if root_kind not in {"file", "folder"}:
            append_issue(issues, "purge_log", log_id, "invalid_root_entry_kind", root_entry_kind=root_kind)
            continue
        if not root_id:
            append_issue(issues, "purge_log", log_id, "missing_root_entry_id")
            continue

        lookup = file_lookup if root_kind == "file" else folder_lookup
        if root_id not in lookup and status == "running":
            append_issue(issues, "purge_log", log_id, "running_log_missing_root_entry", root_entry_kind=root_kind, root_entry_id=root_id)


def run_validation(user_id: str | None):
    settings = resolve_settings()
    dynamodb = boto3.resource("dynamodb")

    file_items = scan_all_items(dynamodb.Table(settings["file_table"]))
    folder_items = scan_all_items(dynamodb.Table(settings["folder_table"]))
    if user_id:
        file_items = [item for item in file_items if str(item.get("user_id") or "").strip() == user_id]
        folder_items = [item for item in folder_items if str(item.get("user_id") or "").strip() == user_id]

    file_lookup = build_lookup(file_items, "file_id")
    folder_lookup = build_lookup(folder_items, "folder_id")

    deletion_logs = scan_all_items(dynamodb.Table(settings["deletion_log_table"]))
    replacement_logs = scan_all_items(dynamodb.Table(settings["replacement_table"]))
    folder_upload_logs = scan_all_items(dynamodb.Table(settings["folder_upload_log_table"]))
    move_logs = scan_all_items(dynamodb.Table(settings["move_log_table"]))
    purge_logs = scan_all_items(dynamodb.Table(settings["purge_log_table"]))

    if user_id:
        def same_user(items):
            return [item for item in items if str(item.get("user_id") or "").strip() == user_id]

        deletion_logs = same_user(deletion_logs)
        replacement_logs = same_user(replacement_logs)
        folder_upload_logs = same_user(folder_upload_logs)
        move_logs = same_user(move_logs)
        purge_logs = same_user(purge_logs)

    issues = []
    validate_deletion_logs(deletion_logs, file_lookup, folder_lookup, issues)
    validate_replacement_logs(replacement_logs, file_lookup, issues)
    validate_folder_upload_logs(folder_upload_logs, folder_lookup, issues)
    validate_move_logs(move_logs, file_lookup, folder_lookup, issues)
    validate_purge_logs(purge_logs, file_lookup, folder_lookup, issues)

    return {
        "user_id_filter": user_id or "",
        "tables": {
            "deletion_log": settings["deletion_log_table"],
            "replacement": settings["replacement_table"],
            "folder_upload_log": settings["folder_upload_log_table"],
            "move_log": settings["move_log_table"],
            "purge_log": settings["purge_log_table"],
        },
        "summary": {
            "deletion_log_count": len(deletion_logs),
            "replacement_log_count": len(replacement_logs),
            "folder_upload_log_count": len(folder_upload_logs),
            "move_log_count": len(move_logs),
            "purge_log_count": len(purge_logs),
            "issue_count": len(issues),
        },
        "issues": issues,
    }


def print_report(report):
    if report["user_id_filter"]:
        print(f"User filter: {report['user_id_filter']}")
    print("Log tables:")
    for label, table_name in report["tables"].items():
        print(f"- {label}: {table_name}")

    summary = report["summary"]
    print(f"- Deletion logs: {summary['deletion_log_count']}")
    print(f"- Replacement logs: {summary['replacement_log_count']}")
    print(f"- Folder upload logs: {summary['folder_upload_log_count']}")
    print(f"- Move logs: {summary['move_log_count']}")
    print(f"- Purge logs: {summary['purge_log_count']}")
    print(f"- Log issues: {summary['issue_count']}")

    if report["issues"]:
        print("Log issues:")
        for issue in report["issues"]:
            print(f"- {json.dumps(issue, default=str)}")


def main():
    parser = argparse.ArgumentParser(
        description="Validate operation log structure and referenced object presence.",
    )
    parser.add_argument(
        "--user-id",
        dest="user_id",
        help="Optional user id filter. Defaults to all users.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the full report as JSON instead of the text summary.",
    )
    args = parser.parse_args()

    report = run_validation(user_id=args.user_id)

    if args.json:
        print(json.dumps(report, indent=2, default=str))
        return

    print_report(report)


if __name__ == "__main__":
    main()
