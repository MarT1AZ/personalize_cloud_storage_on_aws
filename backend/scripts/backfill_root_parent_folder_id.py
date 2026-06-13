import argparse
import os
from pathlib import Path

import boto3
from boto3.dynamodb.conditions import Attr
from dotenv import load_dotenv


DEFAULT_ROOT_PARENT_FOLDER_ID = "__root__"


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


def scan_all_items(table, filter_expression):
    scan_kwargs = {"FilterExpression": filter_expression}
    items = []

    while True:
        response = table.scan(**scan_kwargs)
        items.extend(response.get("Items", []))
        last_evaluated_key = response.get("LastEvaluatedKey")
        if not last_evaluated_key:
            break
        scan_kwargs["ExclusiveStartKey"] = last_evaluated_key

    return items


def backfill_table(table, key_name: str, root_parent_folder_id: str):
    items = scan_all_items(
        table,
        Attr("parent_folder_id").not_exists() | Attr("parent_folder_id").eq(None),
    )

    updated_count = 0
    with table.batch_writer(overwrite_by_pkeys=[key_name]) as batch:
        for item in items:
            item["parent_folder_id"] = root_parent_folder_id
            batch.put_item(Item=item)
            updated_count += 1

    return updated_count


def resolve_table_names(file_table_name: str | None, folder_table_name: str | None):
    load_env()
    resolved_file_table_name = file_table_name or os.getenv("FILE_METADATA_TABLE", "file-meta-data").strip() or "file-meta-data"
    resolved_folder_table_name = (
        folder_table_name or os.getenv("FOLDER_METADATA_TABLE", "folder-meta-data").strip() or "folder-meta-data"
    )
    return resolved_file_table_name, resolved_folder_table_name


def run_backfill(file_table_name: str | None, folder_table_name: str | None, root_parent_folder_id: str):
    resolved_file_table_name, resolved_folder_table_name = resolve_table_names(file_table_name, folder_table_name)
    dynamodb = boto3.resource("dynamodb")

    file_table = dynamodb.Table(resolved_file_table_name)
    folder_table = dynamodb.Table(resolved_folder_table_name)

    updated_files = backfill_table(file_table, "file_id", root_parent_folder_id)
    updated_folders = backfill_table(folder_table, "folder_id", root_parent_folder_id)

    return {
        "file_table": resolved_file_table_name,
        "folder_table": resolved_folder_table_name,
        "updated_files": updated_files,
        "updated_folders": updated_folders,
        "root_parent_folder_id": root_parent_folder_id,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Replace null root parent_folder_id values with a string sentinel for DynamoDB metadata tables.",
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
        "--root-value",
        default=DEFAULT_ROOT_PARENT_FOLDER_ID,
        help=f"Sentinel value to store for root-level parent_folder_id. Defaults to {DEFAULT_ROOT_PARENT_FOLDER_ID!r}.",
    )
    args = parser.parse_args()

    result = run_backfill(
        file_table_name=args.file_table_name,
        folder_table_name=args.folder_table_name,
        root_parent_folder_id=args.root_value,
    )

    print(f"Updated file table '{result['file_table']}': {result['updated_files']} item(s)")
    print(f"Updated folder table '{result['folder_table']}': {result['updated_folders']} item(s)")
    print(f"Root parent sentinel: {result['root_parent_folder_id']}")


if __name__ == "__main__":
    main()
