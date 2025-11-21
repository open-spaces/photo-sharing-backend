"""Initialize the database (create tables).

Usage:
  python scripts/setup_db.py
"""
from app.db.database import init_db


def main():
    init_db()
    print("Database initialized.")


if __name__ == "__main__":
    main()

