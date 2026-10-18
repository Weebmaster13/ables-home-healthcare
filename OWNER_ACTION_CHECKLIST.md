# Owner Action Checklist

The remaining launch blockers are **configuration and environment**, not code.
Nothing in this file changes production, weakens a readiness check, or asks for a
paid service. Each item gives the exact setting, the command to run, the
evidence to capture, and how to undo it.

Two ground rules:

- **Production is GoDaddy cPanel (Apache + Passenger) with MySQL/MariaDB.** That
  is what `DEPLOYMENT.md` records as authoritative and what the live host
  reports. PostgreSQL, Render, and Railway are supported in code but are **not**
  the production target; do not configure a second platform. (An earlier review
  referred to "the PostgreSQL production database" — the real engine is
  MySQL/MariaDB, which changes the exact backup commands below.)
- **Every command here is read-only or writes only to a scratch/temporary
  target.** Nothing writes to the live database. Any step that would is marked
  and needs separate approval.

Status meanings: **[BLOCKER]** = do not collect real client data until done;
**[REVIEW]** = owner decision, no code.

---

## 1. Real off-site backup destination — [BLOCKER]

**Why:** a backup on the same hosting account is lost with that account. Code,
transport, verification, and the restore drill are complete; only a real remote
host is missing. `/admin/launch-checklist` shows "Off-site backup verified" as
not ready until a verified upload exists, so a configured-but-unused destination
cannot be mistaken for protection.

**Config (set in cPanel → Python App → Environment Variables, never in files):**

```text
OFFSITE_BACKUP_URL=sftp://BACKUP_USER@BACKUP_HOST/home/BACKUP_USER/ables
OFFSITE_BACKUP_KEY=/home/soev6l6iqyxn/.ssh/id_offsite      # private key path
OFFSITE_SFTP_KNOWN_HOSTS=/home/soev6l6iqyxn/.ssh/known_hosts
BACKUP_FINGERPRINT_KEY=<long random value, stored in a password manager>
```

`OFFSITE_BACKUP_URL` must be `sftp://`. A password in the URL is refused by the
code, so use a key. The destination must be a different host (or a
provider-hosted SFTP account) — not a second folder on the same account.

**Verification, in order:**

```bash
# a. Pin the host key (do this once; compare the fingerprint with the provider).
ssh-keyscan -p PORT BACKUP_HOST >> "$OFFSITE_SFTP_KNOWN_HOSTS"

# b. Confirm the destination is reachable and the key works — read-only.
python backup_db.py --offsite-list
#    Expected: "0 complete recovery set(s)" or a list. No backup is created.

# c. Create a backup and copy it off-site, verifying the remote copy.
python backup_db.py --offsite
#    Expected: "Off-site backup verified: <name> -> sftp://... (remote content
#    matches the local backup (SHA-256 verified))" and exit 0.

# d. Prove the remote copy ALONE can rebuild the database.
#    Create the scratch DB first in cPanel → MySQL Databases (it assigns the
#    cpaneluser_ prefix and the grants; a client CREATE DATABASE will fail).
python backup_db.py --restore-from-offsite ablehome-mysql-YYYYMMDD-HHMMSS.zip \
    --restore-to "mysql+pymysql://USER:PASS@localhost/SCRATCH_DB"
#    Expected: "Restored database.sql into SCRATCH_DB (verified ... content
#    fingerprints against the backup)" and exit 0.
```

**Evidence to keep:** the `--offsite-list` output, the `--offsite` verified
line, the `--restore-from-offsite` verified line, and the row counts compared
against production:

```bash
mysql "$SCRATCH_DB" -e \
  "SELECT (SELECT COUNT(*) FROM inquiry) inquiries, \
          (SELECT COUNT(*) FROM caregiver_application) applications, \
          (SELECT COUNT(*) FROM notification_log) notifications, \
          (SELECT version_num FROM alembic_version) migration;"
```

**Honesty caveat:** a restore into a scratch DB proves the *mechanism*. It does
not prove recovery of the full production data volume or attachments until run
against a copy of the real production data. Record the row counts you compared;
do not call a backup verified on the strength of an empty or sample database.

**Rollback:** unset `OFFSITE_BACKUP_URL`. The checklist returns to "not ready";
nothing else changes. The `backups/` directory and the database are untouched.

---

## 2. Live email delivery — [BLOCKER]

**Why:** password reset and every public-form notification depend on SMTP.
When SMTP is unconfigured the code logs and **skips** the email rather than
failing the request, so the site can look healthy while no mail is delivered.
This is the item the earlier live audit flagged as "reported broken".

**Config (cPanel env vars; provider = Google Workspace, Microsoft 365, Zoho, or
the cPanel host's own mail):**

```text
MAIL_SERVER=smtp.PROVIDER.tld
MAIL_PORT=587
MAIL_USE_TLS=true
MAIL_USERNAME=admin@ableshomehealthcare.com
MAIL_PASSWORD=<app password, cPanel only>
MAIL_DEFAULT_SENDER=admin@ableshomehealthcare.com
ADMIN_EMAIL=admin@ableshomehealthcare.com
```

**Verification, in order:**

```bash
# a. Direct SMTP send to the owner inbox.
python test_email.py
#    Expected: "SMTP test email sent. Check ADMIN_EMAIL inbox." and exit 0.
#    A non-zero exit means SMTP is not configured — fix before continuing.

# b. Password reset: request one for a real admin account and confirm the mail
#    arrives at the owner inbox and the link works end to end.

# c. Every public form, submitted through the live site:
#    /contact  /free-consultation  /referrals  /employment-application
#    Confirm each arrival in the owner inbox AND that the record appears in
#    /admin/work-queue (the record is saved even when the email is skipped).
```

**Evidence to keep:** the `test_email.py` success line, the received reset email,
and one received email per public form (with the matching work-queue row).

**If mail still does not arrive:** check `/admin/system-status`, which lists
recent `skipped` and `failed` notifications with the reason. "Sent" in the log
means the SMTP call returned without error — it does **not** prove inbox
delivery, so always confirm the inbox itself.

**Rollback:** clear the `MAIL_*` variables. Notifications revert to
logged-and-skipped; no data is lost.

---

## 3. Durable, private upload storage — [BLOCKER]

**Why:** uploaded documents are client data. They must survive a restart/redeploy
and must not be readable by guessing a URL. `/tmp` is wiped on restart; the
checked-in Render config pointed there, which is unsuitable.

**Config:**

```text
UPLOAD_FOLDER=/home/soev6l6iqyxn/ables-home-healthcare/instance/uploads
```

Persistent home-directory path (or a mounted volume / object store) — never
`/tmp` or `/var/tmp`.

**Verification:**

```bash
# a. Config/DB/migrations/upload-folder/entrypoint, read-only.
python preflight_cpanel.py
#    Expected: PASS on the upload folder line, no FAIL items.

# b. Prove the folder accepts a write (opt-in; creates and removes one probe).
python preflight_cpanel.py --write-test
```

Then, on the live site:

1. Upload a document to a client record.
2. Restart the Python app (cPanel → Setup Python App → Restart), and confirm the
   document is still downloadable.
3. As an anonymous browser and as a **different** logged-in user, request the
   document's download URL and confirm both are refused (403/redirect), while the
   owner account can download it.

**Evidence to keep:** preflight output, a screenshot of the document still
present after restart, and the anonymous/non-owner refusal responses.

**Rollback:** point `UPLOAD_FOLDER` back to the previous path. Existing files
stay where they are; move them deliberately, not as part of a rollback.

---

## 4. Upload antivirus scanner — [BLOCKER]

**Why:** with no scanner configured, production **fails closed** and rejects
uploads (the built-in EICAR check is not a real engine). The scanner now receives
the **whole file** streamed on stdin, so a payload beyond the first bytes is
scanned.

**Config:**

```text
VIRUS_SCAN_COMMAND=clamdscan --stream
```

Install ClamAV on the host first. `UPLOAD_SCAN_FAIL_OPEN=true` accepts unscanned
uploads — that is a documented risk and should stay unset.

**Verification:**

```bash
# Clean file must be accepted; EICAR test string must be rejected; a payload
# placed beyond the first 4 KB must be rejected (proves full-file scanning).
python -m pytest tests/ -q -k "scan or upload or eicar"
```

Then upload a real PDF through the live site and confirm it is stored; upload a
known-bad file (EICAR) and confirm it is refused.

**Evidence to keep:** the scanner test results and the accepted/rejected live
uploads.

**Rollback:** unset `VIRUS_SCAN_COMMAND`. Uploads then fail closed (rejected)
rather than being stored unscanned.

---

## 5. Owner attestation flags — [REVIEW]

**Why:** these are business/legal confirmations only the owner can make. The
code and `production_check.py` require them; setting them truthfully is the
owner's decision, not a code task. Do **not** set them to make a check pass.

**Config (set each only after the named review is actually done):**

| Flag | Set it only after… |
|---|---|
| `BUSINESS_DETAILS_CONFIRMED=true` | Business name, address, phone, email, hours, service areas, payment options are final and correct on the site. |
| `LEGAL_SCOPE_CONFIRMED=true` | Service scope and licensing wording reviewed (e.g. non-medical only, any RSA/license language); the site does not imply unlicensed services. |
| `LEGAL_PRIVACY_REVIEW_CONFIRMED=true` | Privacy policy, terms, consent language, and the document-upload/retention policy are reviewed. |
| `APPROVED_PHOTOS_READY=true` | Development images are replaced with licensed stock or company photos with signed permission. |

**Verification:**

```bash
python deploy_check.py --production
#    Expected: no [TODO] items once every flag is set truthfully.
python production_check.py
#    Expected: "Production configuration checks passed." (exit 0)
```

**Evidence to keep:** the passing output plus the underlying review artifacts
(signed photo permissions, legal sign-off) kept outside the repo.

**Rollback:** set any flag back to `false`. The readiness gate reports not ready
again; nothing else changes.

---

## 6. Final release review — [REVIEW]

**Why:** confirm the change set, CI, migration path, and rollback before merge.
This is a review step; it changes nothing in production.

**Steps:**

1. **Full diff from the base branch.**
   ```bash
   git fetch origin
   git --no-pager diff --stat origin/main...HEAD
   git --no-pager diff origin/main...HEAD
   ```
   Expected evidence: every change maps to a reported finding; no stray files
   (`backups/`, `.env`, `instance/` are git-ignored).

2. **CI jobs on the final commit.**
   ```bash
   SHA=$(git rev-parse HEAD)
   curl -s -H "Authorization: Bearer $GITHUB_TOKEN" \
     "https://api.github.com/repos/Weebmaster13/ables-home-healthcare/actions/runs?head_sha=$SHA" \
   | python -c "
   import sys, json, os, urllib.request
   tok = os.environ['GITHUB_TOKEN']
   for run in json.load(sys.stdin)['workflow_runs']:
       req = urllib.request.Request(run['jobs_url'],
                                    headers={'Authorization': f'Bearer {tok}'})
       for job in json.load(urllib.request.urlopen(req))['jobs']:
           print(run['head_sha'][:8], job['name'], job['conclusion'])
   "
   ```
   Expected evidence: `pytest`, `pytest-mysql`, `pytest-postgres`, and
   `pytest-offsite-sftp` all `success`.

3. **Migration state and rollback.** Four linear revisions
   (`c5e94a48ff0d` → `1c6de4227665` → `7b2f4d31a9ce` → `8a1c2f4d9b30`); every
   one has a real `downgrade()`, so a migration can be reversed.
   ```bash
   flask --app app db current      # applied revision (exit 0 = reachable)
   flask --app app db check        # models vs head; "not up to date" is expected
                                   # on a fresh DB, not a connectivity error
   flask --app app db upgrade      # apply, once, as an explicit deploy step
   ```
   Rollback of a migration (only on a scratch DB, never production without a
   verified backup first):
   ```bash
   flask --app app db downgrade -1
   ```
   Expected evidence: `db current` prints the same head before and after the
   drill on a scratch DB.

4. **Deploy procedure and its rollback.** Follow `DEPLOYMENT.md` (authoritative)
   and `CPANEL_MYSQL.md`. Deploy = upload the package, `flask --app app db
   upgrade` once, restart the app, then re-run the read-only checks:
   ```bash
   python preflight_cpanel.py
   python deploy_check.py --production
   python test_email.py
   curl -s https://ableshomehealthcare.com/healthz   # expect {"status": "ok"}
   ```
   **Rollback:** re-upload the previous package and restart the app; restore the
   DNS record if the cutover changed it; if a migration was applied and must be
   reverted, `flask --app app db downgrade -1` **after** a verified backup exists.

5. **Confirm the public health endpoint leaks nothing.**
   ```bash
   curl -s https://ableshomehealthcare.com/healthz
   #    Expected: {"status": "ok"} only. Detailed diagnostics are at
   #    /admin/healthz behind admin login.
   ```

**Evidence to keep:** the diff stat, the CI job conclusions, the `db current`
head, and the preflight/deploy-check/test-email outputs.

---

## What is explicitly out of scope here

- No production change, no merge, no deploy — the owner approves those.
- No paid service is required by any item above (a free/owned SFTP account and
  the cPanel host's own mail suffice).
- No readiness check is weakened; `production_check.py` and `/admin/launch-checklist`
  keep reporting "not ready" until each item is genuinely done.
