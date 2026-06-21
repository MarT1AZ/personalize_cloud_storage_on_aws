import argparse
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


def get_primary_key_names(table_definition: dict):
    return [item["AttributeName"] for item in table_definition["KeySchema"]]


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


def copy_items(source_table_name: str, target_table_name: str):
    load_env()
    dynamodb_resource = boto3.resource("dynamodb")
    dynamodb_client = boto3.client("dynamodb")

    source_definition = dynamodb_client.describe_table(TableName=source_table_name)["Table"]
    target_definition = dynamodb_client.describe_table(TableName=target_table_name)["Table"]

    source_key_names = get_primary_key_names(source_definition)
    target_key_names = get_primary_key_names(target_definition)
    if source_key_names != target_key_names:
        raise SystemExit(
            f"Key schema mismatch. Source keys: {source_key_names}. Target keys: {target_key_names}."
        )

    source_table = dynamodb_resource.Table(source_table_name)
    target_table = dynamodb_resource.Table(target_table_name)
    items = scan_all_items(source_table)

    with target_table.batch_writer(overwrite_by_pkeys=target_key_names) as batch:
        for item in items:
            batch.put_item(Item=item)

    return len(items), target_key_names


def main():
    parser = argparse.ArgumentParser(
        description="Copy all DynamoDB items from one table into another table with the same primary key schema.",
    )
    parser.add_argument("source_table", help="Existing DynamoDB table to copy data from.")
    parser.add_argument("target_table", help="Existing DynamoDB table to copy data into.")
    args = parser.parse_args()

    copied_count, key_names = copy_items(args.source_table, args.target_table)
    print(f"Copied {copied_count} item(s) from '{args.source_table}' to '{args.target_table}'.")
    print(f"Primary key fields: {', '.join(key_names)}")


if __name__ == "__main__":
    main()
