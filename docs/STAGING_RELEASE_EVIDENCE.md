# Staging and release evidence package

This document records what was verified against a real, isolated staging stack,
the exact configuration each owner-controlled check needs, how to re-run every
check, how to roll back, and the blockers that remain. Nothing here has been
merged or deployed. Merge and deploy require the owner's explicit approval.

Verified state: `repair/security-audit-2026-10`. The full suite and the live CI
drills (`pytest`, `pytest-mysql`, `pytest-postgres`) passed on the branch head
at the time of writing. Confirm with `git rev-parse HEAD` and compare against
the release target before merging (see "Confirm the tested commit is the release
commit"). If HEAD differs in code, re-run the suite and the harness before
merging.

Last verified head: `2fca64d` ("Harden backup fingerprints: canonical hashing, HMAC
integrity, full coverage"). At that commit: the full suite passed (202 passed with
the live PostgreSQL and MySQL drills enabled; 198 passed, 4 skipped without them),
and all CI jobs (`pytest`, `pytest-mysql`, `pytest-postgres`, `deployment-smoke`)
were green.
The restore drills were also run directly against real servers, including a
PostgreSQL 17 dump restored into a PostgreSQL 16 server and a simulated disaster
(the source database dropped, restore verified from the stored archive).

## What the staging stack was

Production is GoDaddy cPanel + Apache + Phusion Passenger + MySQL/MariaDB
(see `CPANEL_MYSQL.md`). Staging mirrored those semantics without cPanel:

| Component | Staging stand-in | Why it is representative |
| --- | --- | --- |
| App server | gunicorn 23.0.0 on `wsgi:app` | Same WSGI entrypoint and proxy-header handling as production |
| Database | MariaDB 11.8.9 | Same engine family as cPanel MySQL/MariaDB |
| TLS terminator | nginx (self-signed cert) | Proves `Secure` cookies, HTTPS redirect, and CSRF Referer checks |
| Email | MailHog (local SMTP sink) | Proves the app really hands mail to SMTP |
| Antivirus | ClamAV via a probe wrapper | Proves a real scanner receives the whole file |

The app ran with `FLASK_ENV=production`, so the fail-closed guards were active.

## Owner-controlled checks

### 1. Production configuration

Command (against the staging env file):

```
env -u DATABASE_URL -u UPLOAD_FOLDER -u SECRET_KEY \
    ENV_FILE=/tmp/ables-staging.env python production_check.py
```

Result: every technical check passes. Only the four owner-attestation flags
remain, because only the owner can truthfully set them:

```
[OK] Production mode            [OK] Upload antivirus scanner
[OK] Secret key                 [OK] Durable upload storage
[OK] HTTPS site URL             [OK] Secure sessions
[OK] Production database        [TODO] Business details confirmed
[OK] Database driver installed  [TODO] Legal service scope confirmed
[OK] Database backup tool       [TODO] Legal privacy review confirmed
[OK] SMTP email                 [TODO] Approved photos ready
[OK] SMS reminders optional
[OK] HTTPS enforcement
[OK] Admin recovery codes required
```

Note: `load_dotenv` does not override variables already in the environment. A
stale exported `DATABASE_URL`, `UPLOAD_FOLDER`, or `SECRET_KEY` will shadow the
env file. Always clear them (as above) before running the check.

### 2. Email delivery

Command: the password-reset step of `tools/staging_verify.py`, plus the MailHog
API it reads.

Result: the password-reset email and inquiry notifications were delivered to
SMTP (MailHog reported the messages). This proves the app hands mail to a real
SMTP server. It does not prove inbox delivery to the owner's address; that is
the owner's live test with `MAIL_SERVER`, `MAIL_USERNAME`, `MAIL_PASSWORD`, and
`ADMIN_EMAIL` pointed at a real mailbox.

### 3. Upload persistence

Command:

```
ls -la "$UPLOAD_FOLDER/documents/"
```

Result: uploads landed in the durable configured folder
(`/var/lib/ables-staging-uploads/documents/` in staging), not under `/tmp`. The
`production_check.py` "Durable upload storage" check rejects `/tmp` and
`/var/tmp`, so a misconfigured path is caught before launch.

### 4. Scanner availability

Command: the scanner steps of `tools/staging_verify.py` with
`--scanner-probe-log`.

Result: a clean PDF was accepted; EICAR and an EICAR payload placed past the
4 KB mark were both rejected; and the probe log proved the external scanner
received all 9,018 bytes. This closes the "first 4 KB only" defect: the scanner
now receives the complete file. Without a configured scanner, production fails
closed (uploads rejected) unless `UPLOAD_SCAN_FAIL_OPEN=true` is set.

Unit coverage was widened so a truncation anywhere would be caught, not only a
sample that happens to fit the first buffer:

* `test_external_scanner_receives_a_megabyte_upload_intact` - a 1,048,576-byte
  body with a non-power-of-two tail is compared byte-for-byte against what the
  scanner command actually read.
* `test_eicar_deep_inside_a_large_file_is_rejected` - EICAR placed 900,000 bytes
  in, far past any 4 KB sample window.
* `test_eicar_straddling_a_read_boundary_is_rejected` - a signature that starts
  at byte 4,090 and crosses the old 4,096-byte boundary.

### 5. Database recovery

Command (production backend):

```
DATABASE_URL="mysql+pymysql://USER:PASS@HOST:PORT/ables_staging" \
    python backup_db.py --restore-to "mysql+pymysql://USER:PASS@HOST:PORT/ables_restore"
```

Result: a real dump was taken and restored into an isolated scratch database
with exact row-count parity (`user`, `inquiry`, `client_document`,
`appointment`) and a matching `alembic_version` (`8a1c2f4d9b30`). The
`--restore-to` guard refuses to target the live `DATABASE_URL` unless
`--allow-live` is passed.

The PostgreSQL path was drilled too, and it exposed a defect in how a restore
result is classified. `pg_restore` exits 1 and prints
`errors ignored on restore: N` whenever it ignored *any* error, and that
includes data-losing ones such as `relation "x" already exists` or a COPY that
hit a duplicate key. Treating that summary line as success would report a
partially restored database as healthy.

`backup_db.restore_postgres_dump` now accepts exit 1 only when every ignored
error is the understood version-skew GUC (`unrecognized configuration parameter`
from a `SET`, which carries no data), and then confirms the restore before
returning success. Anything else raises.

Verified live on a PostgreSQL 17 dump restored into a PostgreSQL 16 server (the
genuine version-skew case): the call returned success with the message
`... (unrecognized configuration parameter "transaction_timeout"; verified 3
table(s), 1 index(es), and row counts against the source)`, and
`SELECT id, name FROM patients` returned the seeded rows. A data-losing restore
(the source gained a row the dump did not contain) and a bad-target restore both
raise.

#### What "verified" now means

Confirming that tables exist is necessary but not sufficient: a restore can
create every table and still lose or alter the rows. A row-count match is a
strong check but not a complete one either: two tables can hold the same number
of rows with different values. `_verify_restored_database` therefore checks
content, for both the PostgreSQL and MySQL paths:

1. every table, view, and index the dump defines is present in the target;
2. the restored content matches the fingerprint recorded in the backup: per
   table, the row count and a SHA-256 over every value and the column names.
   This catches same-count/different-values, and it is authoritative even when
   the source database no longer exists (a genuine disaster).

The fingerprint is built to be trusted, not merely recorded:

- **Canonical hashing.** Values are type-tagged and serialized deterministically
  (`None` distinct from `''`, bytes distinct from text, `Decimal` normalized,
  floats at full precision, dates/times in ISO form), and rows are read in an
  explicit order (primary key when present, else every column) so identical
  contents fingerprint identically regardless of physical row order or driver.
- **Authenticated integrity.** The stored fingerprint is HMAC-SHA256 signed with
  `BACKUP_FINGERPRINT_KEY` (falling back to a domain-separated derivation of
  `SECRET_KEY`). A fingerprint that fails the MAC, or that is present but
  unsigned while a key is configured (a downgrade attempt), fails the restore
  rather than being trusted or silently falling back to the weaker check.
- **Complete coverage.** Every table the dump carried data for must have a
  recorded entry; a fingerprint that omits one fails the restore instead of
  leaving that table silently unverified.

Because the key authenticates the fingerprint, it must stay stable: set
`BACKUP_FINGERPRINT_KEY` explicitly and back it up. Rotating `SECRET_KEY`
(without a dedicated key) or the fingerprint key makes backups fingerprinted
under the old key unverifiable, which is the safe, fail-closed direction but
requires the key to be preserved alongside the backups.

When a backup predates fingerprinting (no sidecar), verification falls back to
comparing row counts against the live source; and when the source is gone, to
requiring that every table the dump carried data for is at least non-empty. That
fallback is weaker — non-empty tables cannot prove every expected row survived —
so the fingerprint is the primary check and the fallback is a compatibility path
only.

An unreachable target, or a dump whose table of contents cannot be read, is
reported as a failure rather than assumed to have succeeded. The schema objects
come from the dump itself: `pg_restore --list` for PostgreSQL, and the
`CREATE TABLE`/`INSERT INTO` statements for MySQL.

The checks were proven able to fail, not merely pass:

- adding one row to the source made a restore raise
  `row-count mismatch: patients: expected 3, got 2` on both backends (the
  fallback path);
- two tables with the same row count but different values produce different
  fingerprints, and a restore whose content differs from the backup raises
  `content mismatch (row values differ)` (the fingerprint path);
- editing a recorded fingerprint breaks its HMAC, and the restore is refused
  with `recorded fingerprint could not be authenticated` rather than trusting
  the forgery or falling back to the weaker check;
- stripping the MAC (writing a legacy unsigned fingerprint while a key is
  configured) is likewise refused, so the signature cannot be downgraded away;
- a fingerprint that omits a table the dump carried data for fails with
  `recorded fingerprint is incomplete`, so no table goes silently unverified;
- two databases with the same rows inserted in a different physical order
  fingerprint identically, so a reordered dump is not rejected by mistake;
- in a simulated disaster, the source database was dropped entirely and a
  restore from the stored archive still verified against the backup's
  authenticated fingerprint and succeeded.

The PostgreSQL drill, the MySQL drill, and the version-skew case all pass, and
the full suite is green with both live drills enabled.

### 6. Admin route authorization

A manual spot check of a few admin pages does not establish that the rest are
guarded. `tests/test_admin_authorization_sweep.py` enumerates every route under
`/admin/` from the live URL map and drives each one three ways:

* anonymous: must redirect to login or return 401/403, never admin content;
* a logged-in non-admin: must return 403;
* a real administrator: must not be denied (a 400/404/500 from missing test data
  is tolerated, a 403 is not).

Coverage is by construction: a newly registered `/admin/...` route is swept the
moment it exists, so a route that forgets its `require_role`/`require_permission`
call fails the test. The sweep was mutation-checked - removing one guard from
`admin_users` made `test_non_admin_is_refused_every_admin_route` fail with
`GET /admin/users -> 200` - so it is known to detect the failure mode, not just
pass. All 55 admin routes pass.

## Verification commands

```
# Admin authorization sweep: every /admin/* route as anonymous, non-admin,
# and admin (enumerated from the live URL map, so new routes are covered).
python -m pytest tests/test_admin_authorization_sweep.py -q

# Full automated suite (default backend)
python -m pytest -q

# With the live MariaDB drill (production engine family)
MYSQL_TEST_URL="mysql+pymysql://USER:PASS@HOST:PORT/" python -m pytest -q -rs

# End-to-end HTTP harness against a running stack
python tools/staging_recovery.py                 # prints one fresh recovery code
python tools/staging_verify.py --insecure \
    --email admin@staging.invalid --password "$STAGING_ADMIN_PASSWORD" \
    --recovery-code "$RECOVERY_CODE" \
    --scanner-probe-log /tmp/scan-probe-sizes.log

# Production configuration gate
env -u DATABASE_URL -u UPLOAD_FOLDER -u SECRET_KEY \
    ENV_FILE=/tmp/ables-staging.env python production_check.py
```

The harness covers: minimal `/healthz`; CSRF token issuance; public form
submission; admin login; recovery-code setup and gate; password-reset email
delivery; admin area reachability; clean/EICAR/deep-payload scanner behaviour;
full-file scanner proof; and document-download authorization (anonymous denied,
owner allowed, non-owner denied with 403).

## Exact configuration requirements

| Variable | Requirement | Check that enforces it |
| --- | --- | --- |
| `FLASK_ENV` | `production` | Secure session cookie |
| `SECRET_KEY` | >= 32 chars, not a development key | `config.secret_key()` raises otherwise |
| `SITE_BASE_URL` | final `https://` domain | HTTPS site URL |
| `DATABASE_URL` | `mysql+pymysql://...` (or `postgresql://...`) | Production database |
| `UPLOAD_FOLDER` | durable path, not `/tmp` or `/var/tmp` | Durable upload storage |
| `VIRUS_SCAN_COMMAND` | real scanner, e.g. `clamdscan --stream` | Upload antivirus scanner |
| `MAIL_SERVER` / `MAIL_USERNAME` / `MAIL_PASSWORD` / `ADMIN_EMAIL` | real mailbox | SMTP email |
| `FORCE_HTTPS` | `true` | HTTPS enforcement |
| `ADMIN_RECOVERY_CODES_REQUIRED` | `true` | Admin recovery codes required |
| `BUSINESS_DETAILS_CONFIRMED` | `true` after owner review | owner attestation |
| `LEGAL_SCOPE_CONFIRMED` | `true` after service/licensing review | owner attestation |
| `LEGAL_PRIVACY_REVIEW_CONFIRMED` | `true` after privacy review | owner attestation |
| `APPROVED_PHOTOS_READY` | `true` after replacing dev images | owner attestation |

## Rollback

The changes are additive and backend-aware; none alters stored data.

1. Code: `git revert <merge-commit>` (or redeploy the previous release tag).
   The app boots on the prior code without a data migration.
2. Database: no schema change is introduced by this work. If a release ever
   needs one, restore from the verified dump with
   `python backup_db.py --restore-to <scratch-url>` first, confirm counts, then
   restore into production with `--allow-live`.
3. Config: unsetting the new variables returns the app to its previous
   behaviour; only `FLASK_ENV=production` guards fail closed, which is intended.

## Blockers that remain

1. Live email delivery to the owner's real inbox is unverified here. Set the
   SMTP variables and send a real password reset and a form inquiry.
2. The four owner-attestation flags are deliberately unset. They require owner
   review of business details, service/licensing scope, privacy, and photos.
3. Production must use durable, private storage. cPanel home-disk paths persist;
   `/tmp` does not. Confirm the chosen path and its permissions.
4. Off-site backup copies are not automated here. Taking a dump is not the same
   as storing a recoverable copy elsewhere; upload verified dumps off-site. The
   restore drill now verifies the restored *content* against a fingerprint
   recorded in the backup (row count plus a per-table value hash), so a dump that
   restores incompletely or with altered rows is caught, but that is a local
   check only: the off-site copy and its restoration still need to be
   demonstrated against the real target.
5. The admin authorization sweep covers every `/admin/` route for anonymous,
   non-admin, and admin access (see "Admin route authorization"). It does not
   cover finer-grained `require_permission` distinctions within the admin role,
   client-versus-caregiver data access, or session invalidation after a password
   change; treat those as needs-verification.

## Confirm the tested commit is the release commit

```
git rev-parse HEAD                     # must equal the release target
git --no-pager diff --stat origin/main # review the exact change set
```

If HEAD is not the intended release commit, do not merge. Re-run the suite and
the harness against the exact commit that will be released.
