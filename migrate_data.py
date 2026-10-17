"""One-way copy of application rows (PostgreSQL -> MySQL/MariaDB).

Safe by design:
- Default mode is a dry run that only counts and reports; nothing is
  written unless ``--execute`` is passed.
- Refuses to write when any target table already has rows (rerun-safe:
  re-running after a completed copy aborts instead of duplicating).
- Never drops, truncates, deletes, or alters schema on either side.
- Copies explicit primary keys so every relationship (appointments to
  users, care plans to inquiries, messages to senders, ...) survives.
- Uploaded files (resumes, documents) live on disk, NOT in the database:
  the script only inventories their filenames so the operator copies them
  separately over SFTP. It never touches the filesystem.

Usage (run from a machine that can reach both databases):
    python migrate_data.py --from "$SOURCE_DATABASE_URL" --to "$TARGET_DATABASE_URL"
    python migrate_data.py --from ... --to ... --execute

Precondition: the target schema must already exist — run
`flask --app app db upgrade` against the target first. This script only
reads the source and inserts rows; it never creates or alters tables.
"""

import argparse
import sys
from datetime import datetime, timezone

from sqlalchemy import create_engine, func, select

from models import (
    AdminNote,
    AdminRecoveryCode,
    Appointment,
    CaregiverApplication,
    CaregiverAvailability,
    CaregiverOnboardingItem,
    CarePlan,
    ClientDocument,
    Inquiry,
    NotificationLog,
    PortalMessage,
    Referral,
    User,
)

# Parent tables first so foreign keys resolve on insert.
TABLES = [
    User,
    Inquiry,
    Referral,
    CaregiverApplication,
    NotificationLog,
    CarePlan,
    Appointment,
    PortalMessage,
    ClientDocument,
    AdminNote,
    CaregiverAvailability,
    CaregiverOnboardingItem,
    AdminRecoveryCode,
]


def clean_value(value):
    if isinstance(value, datetime) and value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def row_dicts(model, connection):
    table = model.__table__
    rows = connection.execute(select(table)).mappings().all()
    return [
        {column.name: clean_value(row[column.name]) for column in table.columns}
        for row in rows
    ]


def target_counts(target):
    """Count rows per expected table, failing loudly on any error.

    A missing or inaccessible table must stop the migration, never be
    reported as an empty (zero-row) table.
    """
    counts = {}
    for model in TABLES:
        counts[model.__name__] = target.execute(
            select(func.count()).select_from(model.__table__)
        ).scalar()
    return counts


def file_inventory(source):
    files = set()
    for row in source.execute(select(CaregiverApplication.__table__)).mappings().all():
        if row["resume_filename"]:
            files.add(("application resume", row["resume_filename"]))
    for row in source.execute(select(ClientDocument.__table__)).mappings().all():
        if row["filename"]:
            files.add(("client document", row["filename"]))
    return sorted(files)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="source_url", required=True)
    parser.add_argument("--to", dest="target_url", required=True)
    parser.add_argument("--execute", action="store_true",
                        help="Write rows. Without it, only report (dry run).")
    args = parser.parse_args(argv)

    if args.source_url == args.target_url:
        print("Refusing: source and target are the same database.")
        return 2

    source = create_engine(args.source_url)
    target = create_engine(args.target_url)

    with source.connect() as connection:
        planned = {model.__name__: row_dicts(model, connection) for model in TABLES}
        files = file_inventory(connection)

    total = sum(len(rows) for rows in planned.values())
    for name, rows in planned.items():
        print(f"  {name}: {len(rows)} row(s)")
    print(f"Total rows to copy: {total}")
    print(f"Uploaded files referenced (copy separately over SFTP): {len(files)}")
    for kind, name in files:
        print(f"  [{kind}] {name}")

    with target.connect() as connection:
        try:
            existing = target_counts(connection)
        except Exception as exc:
            detail = (str(exc).strip().splitlines() or [type(exc).__name__])[0]
            print("Refusing: target schema is missing or unreadable — run "
                  "`flask --app app db upgrade` against the target first "
                  f"({type(exc).__name__}: {detail})")
            return 2
    nonempty = {name: count for name, count in existing.items() if count}
    if nonempty:
        print("Refusing: target already has rows: " + ", ".join(
            f"{name}={count}" for name, count in nonempty.items()))
        return 2

    if not args.execute:
        print("Dry run only — nothing written. Re-run with --execute to copy.")
        return 0

    with target.connect() as connection:
        with connection.begin():
            for model in TABLES:
                rows = planned[model.__name__]
                if rows:
                    connection.execute(model.__table__.insert(), rows)
        verify = target_counts(connection)

    mismatched = [name for name in planned
                  if verify[name] != len(planned[name])]
    if mismatched:
        print("Count mismatch after copy: " + ", ".join(mismatched))
        return 1
    print(f"Copy complete and verified: {total} rows across {len(TABLES)} tables.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
