import argparse
from pathlib import Path

import boto3
from botocore.exceptions import ClientError
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


def build_create_table_request(source_table: dict, target_table_name: str):
    key_schema = source_table["KeySchema"]
    key_attribute_names = {item["AttributeName"] for item in key_schema}
    attribute_definitions = [
        item for item in source_table["AttributeDefinitions"] if item["AttributeName"] in key_attribute_names
    ]

    request = {
        "TableName": target_table_name,
        "KeySchema": key_schema,
        "AttributeDefinitions": attribute_definitions,
    }

    billing_mode_summary = source_table.get("BillingModeSummary") or {}
    billing_mode = billing_mode_summary.get("BillingMode", "PAY_PER_REQUEST")
    request["BillingMode"] = billing_mode

    if billing_mode == "PROVISIONED":
        throughput = source_table["ProvisionedThroughput"]
        request["ProvisionedThroughput"] = {
            "ReadCapacityUnits": throughput["ReadCapacityUnits"],
            "WriteCapacityUnits": throughput["WriteCapacityUnits"],
        }

    return request


def ensure_table_missing(dynamodb_client, table_name: str):
    try:
        dynamodb_client.describe_table(TableName=table_name)
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code")
        if error_code == "ResourceNotFoundException":
            return
        raise

    raise SystemExit(f"Table '{table_name}' already exists.")


def create_temp_table(source_table_name: str, target_table_name: str):
    load_env()
    dynamodb_client = boto3.client("dynamodb")

    source_response = dynamodb_client.describe_table(TableName=source_table_name)
    source_table = source_response["Table"]
    ensure_table_missing(dynamodb_client, target_table_name)

    create_request = build_create_table_request(source_table, target_table_name)
    dynamodb_client.create_table(**create_request)

    waiter = dynamodb_client.get_waiter("table_exists")
    waiter.wait(TableName=target_table_name)

    return create_request


def main():
    parser = argparse.ArgumentParser(
        description="Create a DynamoDB table with the same primary key schema as an existing table.",
    )
    parser.add_argument("source_table", help="Existing DynamoDB table to copy the primary key schema from.")
    parser.add_argument("target_table", help="New DynamoDB table name to create.")
    args = parser.parse_args()

    create_request = create_temp_table(args.source_table, args.target_table)

    print(f"Created table '{args.target_table}' from '{args.source_table}'.")
    print(f"Billing mode: {create_request['BillingMode']}")
    print("Key schema:")
    for item in create_request["KeySchema"]:
        print(f"- {item['AttributeName']} ({item['KeyType']})")


if __name__ == "__main__":
    main()
