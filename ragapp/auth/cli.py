"""Command-line user creation: `python -m ragapp.auth.cli`."""

import getpass

from ragapp.auth.db import initialize_database
from ragapp.auth.users import create_user


def main():
    initialize_database()

    username = input("Username: ").strip()
    password = getpass.getpass("Password: ")
    is_admin = input("Grant admin rights (can upload shared documents)? [y/N]: ").strip().lower() == "y"

    try:
        create_user(username, password, is_admin=is_admin)
        role = "admin" if is_admin else "standard"
        print(f"User '{username}' created successfully ({role}).")
    except ValueError as error:
        print(f"Error: {error}")


if __name__ == "__main__":
    main()
