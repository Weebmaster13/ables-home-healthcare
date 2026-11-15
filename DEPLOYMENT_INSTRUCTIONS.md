# Ables Home Healthcare — cPanel Deployment Instructions
**Target:** GoDaddy cPanel at `https://aks.a28.mytemp.website/`
**Package:** `ables-deployment-package.zip` (5.2 MB)
**Production domain:** `https://ableshomehealthcare.com/` — DNS NOT changed yet

---

## ⚠️ Access Required From You

I cannot access cPanel directly — the File Manager is blocked by an SSO login loop on every navigation, and I have no FTP/SSH credentials. **You need to perform the file access step.**

**Option A (fastest):** Use cPanel **Git Version Control** (if available in your cPanel):
1. Log in to cPanel → **Git Version Control**
2. Create a repository from `https://github.com/Weebmaster13/ables-home-healthcare` on branch `cpanel-mysql-port`
3. Deploy to `/home/soev6l6iqyxn/ables-home-healthcare/`
4. This automatically pulls all updated files — no manual upload needed

**Option B:** Use cPanel **Terminal** or **SSH**:
1. Log in to cPanel → **Terminal** (or connect via SSH if enabled)
2. `cd /home/soev6l6iqyxn/`
3. `git clone -b cpanel-mysql-port https://github.com/Weebmaster13/ables-home-healthcare ables-home-healthcare`
4. This clones the repo with all updated files

**Option C:** Use **FTP/SFTP** (if you have credentials):
1. Connect to your cPanel FTP/SFTP host
2. Navigate to `/home/soev6l6iqyxn/ables-home-healthcare/`
3. Upload the contents of `ables-deployment-package.zip`, overwriting existing files
4. Do NOT overwrite `instance/`, `.env`, or `.secrets.cpanel`

**Option D (last resort):** Ask GoDaddy human support to fix the SSO loop:
- Tell them: "My cPanel File Manager redirects to SSO login on every page navigation. I need File Manager access restored so I can upload application files."
- Once restored, upload `ables-deployment-package.zip` contents via File Manager

---

## Step 1: Backup the Existing Production Application and Database

**Do this BEFORE uploading any files.** This creates a recoverable backup.

### 1a. Backup application files via cPanel File Manager or Terminal:
```bash
cd /home/soev6l6iqyxn/
tar -czf ables-app-backup-$(date +%F).tar.gz ables-home-healthcare/
```

### 1b. Backup the MySQL database via cPanel:
1. Log in to cPanel → **MySQL Databases** or **phpMyAdmin**
2. Select the Ables database
3. **Export** → SQL format → Download to your local computer
4. Save this file as `ables-db-backup-YYYY-MM-DD.sql`

### 1c. Backup uploaded documents:
```bash
tar -czf ables-uploads-backup-$(date +%F).tar.gz ables-home-healthcare/instance/uploads/
```

**Verify backups exist before proceeding.** Keep all three backup files locally.

---

## Step 2: Upload Updated Application Files

Upload the contents of `ables-deployment-package.zip` to `/home/soev6l6iqyxn/ables-home-healthcare/`, overwriting existing files.

**DO NOT overwrite or delete:**
- `instance/` folder (contains production MySQL SQLite fallback, uploads, logs)
- `.env` file
- `.secrets.cpanel` file
- Any `*.db` files

**Files being updated (all in the package):**
- `app.py`, `config.py`, `routes.py`, `security.py`, `extensions.py`, `forms.py`, `models.py`, `notifications.py`, `backup_db.py`, `wsgi.py`, `passenger_wsgi.py`
- `auth/routes.py`
- `migrations/` (schema already exists — do NOT run `flask db upgrade`)
- All 20+ templates in `templates/`
- `static/css/style.css`, `static/img/logo.svg`, `static/img/brand/`

---

## Step 3: Set cPanel Environment Variables

In cPanel → **Setup Python App** → **Environment Variables**, set these (values are NOT stored in files):

| Variable | Value |
|----------|-------|
| `SECRET_KEY` | Generate: `python -c "import secrets; print(secrets.token_hex(32))"` |
| `FLASK_ENV` | `production` |
| `SITE_BASE_URL` | `https://aks.a28.mytemp.website` |
| `UPLOAD_FOLDER` | `/home/soev6l6iqyxn/ables-home-healthcare/instance/uploads` |
| `ADMIN_EMAIL` | `admin@ableshomehealthcare.com` |
| `DATABASE_URL` | `mysql+pymysql://[USER]:[PASS]@[HOST]:3306/[DBNAME]` (from cPanel MySQL Databases) |
| `MAIL_SERVER` | [from GoDaddy SMTP relay doc] |
| `MAIL_PORT` | `587` |
| `MAIL_USE_TLS` | `true` |
| `MAIL_USERNAME` | [from cPanel] |
| `MAIL_PASSWORD` | [from cPanel] |
| `FORCE_HTTPS` | `true` |

**Do not share these values in chat.** Enter them directly in cPanel.

---

## Step 4: Run Preflight Check (Read-Only, Safe)

In cPanel **Terminal** (same session, export env vars first):

```bash
export SECRET_KEY="[your-generated-key]"
export FLASK_ENV="production"
export DATABASE_URL="mysql+pymysql://[USER]:[PASS]@[HOST]:3306/[DBNAME]"
export SITE_BASE_URL="https://aks.a28.mytemp.website"
export UPLOAD_FOLDER="/home/soev6l6iqyxn/ables-home-healthcare/instance/uploads"
export ADMIN_EMAIL="admin@ableshomehealthcare.com"
export MAIL_SERVER="[relay-host]"
export MAIL_PORT="587"
export MAIL_USE_TLS="true"
export FORCE_HTTPS="true"

cd /home/soev6l6iqyxn/ables-home-healthcare
python preflight_cpanel.py
```

**Expected output:** All PASS, no FAIL. If FAIL, stop and report the error.

---

## Step 5: Restart Passenger

In cPanel → **Setup Python App** → Click **Restart** for the Ables application.

Watch `/home/soev6l6iqyxn/logs/passenger.log` for 2 minutes for any tracebacks. Any traceback = stop, restore from backup, report.

---

## Step 6: Test the Temporary Domain

Test `https://aks.a28.mytemp.website/` for:

| Page | What to verify |
|------|---------------|
| `/` | New header (no navy strip), "Pursuing Excellence", phone **301-326-2225**, footer "After Hours: 301-455-3030", RSA license R3114 |
| `/care` | Care form, CTA button phone 301-326-2225 |
| `/contact` | Contact form, phone 301-326-2225 |
| `/brochure` | Brochure page with new phone |
| `/administrator` | Admin contact page |
| `/pricing-payment` | Pricing page |
| `/auth/login` | Login page loads |
| `/auth/register` | Registration page loads |
| `/portal` | Portal page loads |
| `/healthz` | Returns `{"status": "ok"}` with `database: "ok"`, `uploads: "ok"` |
| Login/registration | Submit forms, verify database writes work |
| File upload | Upload a document, verify it saves to `instance/uploads/` |
| Mobile rendering | Open on a mobile device, verify responsive layout |

**Critical:** Confirm the old business phone `301-455-3030` does NOT appear in any `tel:` link or regular contact line — it should only appear in the "After Hours" footer line.

---

## Step 7: No DNS Changes Yet

Do NOT change DNS or forwarding for `ableshomehealthcare.com`. Continue using `aks.a28.mytemp.website` until you approve the cutover.

---

## What I Need From You

1. **Perform the file upload** (via Git Version Control, Terminal/SSH, FTP/SFTP, or File Manager once SSO is fixed)
2. **Set environment variables** in cPanel (do not share secrets in chat)
3. **Run the preflight check** and report the result
4. **Restart Passenger** and report any errors
5. **Test the temporary domain** and confirm all pages work

Once the temporary domain is fully verified, we proceed to DNS cutover for `ableshomehealthcare.com` on your explicit approval.
