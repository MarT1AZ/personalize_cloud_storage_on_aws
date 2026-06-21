import argparse
import getpass

import bcrypt


def hash_password(password: str, rounds: int) -> str:
    encoded_password = password.encode("utf-8")
    salt = bcrypt.gensalt(rounds=rounds)
    hashed_password = bcrypt.hashpw(encoded_password, salt)
    return hashed_password.decode("utf-8")


def main():
    parser = argparse.ArgumentParser(description="Generate a bcrypt password hash.")
    parser.add_argument("--password", help="Plain text password to hash.")
    parser.add_argument("--rounds", type=int, default=12, help="Bcrypt cost factor. Default is 12.")
    args = parser.parse_args()

    password = args.password or getpass.getpass("Password: ")
    if not password:
        raise SystemExit("Password is required.")

    print(hash_password(password, args.rounds))


if __name__ == "__main__":
    main()
