"""
Migrate data from SQLite to PostgreSQL.

This script exports data from the existing SQLite database and imports it into PostgreSQL.

Usage:
  python scripts/migrate_sqlite_to_postgres.py

Requirements:
  - SQLite database must exist at the path specified in SQLITE_DB_PATH
  - PostgreSQL database must be running and accessible
  - Tables will be created automatically in PostgreSQL if they don't exist
"""
import sys
import os
from pathlib import Path

# Add parent directory to path to import app modules
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from datetime import datetime
import json

# Database URLs
SQLITE_DB_PATH = os.getenv("SQLITE_DB_PATH", "./data/app.db")
SQLITE_URL = f"sqlite:///{SQLITE_DB_PATH}"

# PostgreSQL URL from environment or default
POSTGRES_URL = os.getenv(
    "DB_URL",
    "postgresql://wedding_user:wedding_secure_password_2024@localhost:5432/wedding_db"
)

print("=" * 60)
print("SQLite to PostgreSQL Migration Script")
print("=" * 60)
print(f"Source: {SQLITE_URL}")
print(f"Target: {POSTGRES_URL.replace(POSTGRES_URL.split('@')[0].split('://')[1], '***')}")
print()


def create_engines():
    """Create SQLAlchemy engines for both databases."""
    sqlite_engine = create_engine(SQLITE_URL, connect_args={"check_same_thread": False})
    postgres_engine = create_engine(POSTGRES_URL)
    return sqlite_engine, postgres_engine


def create_tables_in_postgres(postgres_engine):
    """Create tables in PostgreSQL using SQLAlchemy models."""
    print("Creating tables in PostgreSQL...")
    from app.db.database import Base
    from app.db import models  # noqa: F401 - Import to register models

    Base.metadata.create_all(bind=postgres_engine)
    print("✓ Tables created successfully")
    print()


def get_table_count(engine, table_name):
    """Get count of records in a table."""
    with engine.connect() as conn:
        result = conn.execute(text(f"SELECT COUNT(*) FROM {table_name}"))
        return result.scalar()


def migrate_table(sqlite_engine, postgres_engine, table_name, order_by="id"):
    """Migrate data from SQLite table to PostgreSQL table."""
    print(f"Migrating table: {table_name}")

    # Get count
    sqlite_count = get_table_count(sqlite_engine, table_name)
    print(f"  Source records: {sqlite_count}")

    if sqlite_count == 0:
        print(f"  ✓ No data to migrate")
        print()
        return 0

    # Read from SQLite
    with sqlite_engine.connect() as sqlite_conn:
        result = sqlite_conn.execute(text(f"SELECT * FROM {table_name} ORDER BY {order_by}"))
        columns = result.keys()
        rows = [dict(zip(columns, row)) for row in result.fetchall()]

    # Write to PostgreSQL
    with postgres_engine.connect() as postgres_conn:
        # Begin transaction
        trans = postgres_conn.begin()
        try:
            for row in rows:
                # Convert SQLite boolean integers (0/1) to PostgreSQL booleans (True/False)
                converted_row = {}
                for key, value in row.items():
                    # Check if this column should be a boolean (common boolean column names)
                    if key in ['revoked'] and value in [0, 1]:
                        converted_row[key] = bool(value)
                    else:
                        converted_row[key] = value

                # Build INSERT query
                cols = ", ".join(converted_row.keys())
                placeholders = ", ".join([f":{key}" for key in converted_row.keys()])
                query = text(f"INSERT INTO {table_name} ({cols}) VALUES ({placeholders})")

                postgres_conn.execute(query, converted_row)

            trans.commit()
            print(f"  ✓ Migrated {len(rows)} records")
        except Exception as e:
            trans.rollback()
            print(f"  ✗ Error migrating {table_name}: {str(e)}")
            raise

    # Verify
    postgres_count = get_table_count(postgres_engine, table_name)
    if postgres_count == sqlite_count:
        print(f"  ✓ Verification passed: {postgres_count} records in target")
    else:
        print(f"  ✗ Verification failed: Expected {sqlite_count}, got {postgres_count}")

    print()
    return len(rows)


def reset_sequences(postgres_engine):
    """Reset PostgreSQL sequences to continue from max ID."""
    print("Resetting PostgreSQL sequences...")

    tables = ["users", "sessions", "photos", "persons", "faces"]

    with postgres_engine.connect() as conn:
        for table in tables:
            try:
                # Get max ID
                result = conn.execute(text(f"SELECT MAX(id) FROM {table}"))
                max_id = result.scalar() or 0

                if max_id > 0:
                    # Reset sequence
                    sequence_name = f"{table}_id_seq"
                    conn.execute(text(f"SELECT setval('{sequence_name}', {max_id})"))
                    print(f"  ✓ {table}: sequence set to {max_id}")
                else:
                    print(f"  - {table}: no data, skipping")
            except Exception as e:
                print(f"  ! {table}: {str(e)}")

        conn.commit()

    print()


def main():
    """Main migration function."""
    # Check if SQLite database exists
    if not os.path.exists(SQLITE_DB_PATH):
        print(f"✗ SQLite database not found at: {SQLITE_DB_PATH}")
        print("  Please ensure the database file exists before running migration.")
        sys.exit(1)

    print("Step 1: Connecting to databases...")
    try:
        sqlite_engine, postgres_engine = create_engines()
        print("✓ Connected to both databases")
        print()
    except Exception as e:
        print(f"✗ Failed to connect to databases: {str(e)}")
        sys.exit(1)

    print("Step 2: Creating tables in PostgreSQL...")
    try:
        create_tables_in_postgres(postgres_engine)
    except Exception as e:
        print(f"✗ Failed to create tables: {str(e)}")
        sys.exit(1)

    print("Step 3: Migrating data...")
    print()

    # Migrate tables in order (respecting foreign keys)
    tables_order = [
        ("users", "id"),
        ("sessions", "id"),
        ("photos", "id"),
        ("persons", "id"),
        ("faces", "id"),
    ]

    total_records = 0
    try:
        for table_name, order_by in tables_order:
            count = migrate_table(sqlite_engine, postgres_engine, table_name, order_by)
            total_records += count
    except Exception as e:
        print(f"✗ Migration failed: {str(e)}")
        print("\nNote: You may need to manually clean up the PostgreSQL database")
        print("      and re-run the migration.")
        sys.exit(1)

    print("Step 4: Resetting sequences...")
    try:
        reset_sequences(postgres_engine)
    except Exception as e:
        print(f"! Warning: Failed to reset sequences: {str(e)}")
        print("  You may need to reset them manually.")

    print("=" * 60)
    print("Migration Summary")
    print("=" * 60)
    print(f"Total records migrated: {total_records}")
    print()

    # Final verification
    print("Final Verification:")
    for table_name, _ in tables_order:
        sqlite_count = get_table_count(sqlite_engine, table_name)
        postgres_count = get_table_count(postgres_engine, table_name)
        status = "✓" if sqlite_count == postgres_count else "✗"
        print(f"  {status} {table_name}: SQLite={sqlite_count}, PostgreSQL={postgres_count}")

    print()
    print("=" * 60)
    print("✓ Migration completed successfully!")
    print("=" * 60)
    print()
    print("Next steps:")
    print("  1. Verify the data in PostgreSQL")
    print("  2. Test your application with PostgreSQL")
    print("  3. Keep the SQLite database as a backup")
    print("  4. Update your .env file to use PostgreSQL")
    print()


if __name__ == "__main__":
    main()
