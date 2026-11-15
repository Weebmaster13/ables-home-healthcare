# Ables Home Healthcare — cPanel Deployment Package Manifest
# Target: GoDaddy cPanel at /home/soev6l6iqyxn/ables-home-healthcare/
# Temporary domain: https://aks.a28.mytemp.website/
# Production domain: https://ableshomehealthcare.com/ (DNS cutover on owner approval only)
#
# Instructions: Upload the files listed below to the cPanel app root.
# Do NOT upload: instance/ (database, uploads, logs), .env, .secrets.cpanel,
# __pycache__/, .git/, tests/, *.db, *.log, backups/, docs/, tools/, wordpress/.
#
# The server already has a MySQL database with existing data. Do NOT run
# flask db upgrade, do NOT seed a new admin, do NOT overwrite files until
# existing data has been accounted for.

# --- Application core (upload all) ---
app.py
config.py
extensions.py
forms.py
models.py
notifications.py
routes.py
security.py
backup_db.py
migrate_data.py
preflight_cpanel.py
deploy_check.py
production_check.py
seed_production_admin.py
wsgi.py
passenger_wsgi.py

# --- Auth blueprint ---
auth/routes.py

# --- Migrations (schema already exists on production MySQL; do NOT re-run upgrade) ---
migrations/alembic.ini
migrations/env.py
migrations/README
migrations/script.py.mako
migrations/versions/

# --- Templates (20+ changed files) ---
templates/about.html
templates/administrator.html
templates/base.html
templates/brochure.html
templates/care.html
templates/care_process.html
templates/care_programs.html
templates/contact.html
templates/employment_application.html
templates/faq.html
templates/forgot_password.html
templates/home.html
templates/local_seo.html
templates/login.html
templates/pricing_payment.html
templates/privacy.html
templates/referrals.html
templates/register.html
templates/reset_password.html
templates/service_detail.html
templates/terms.html
templates/testimonials.html
templates/why_choose_us.html

# --- Static (CSS + images) ---
static/css/style.css
static/img/logo.svg
static/img/brand/  (postcard JPGs to be replaced separately — see postcard task)

# --- Requirements ---
requirements-cpanel.txt

# --- cPanel Python App settings ---
# Python version: 3.11.16
# Application root: /home/soev6l6iqyxn/ables-home-healthcare
# Application URL: https://aks.a28.mytemp.website/ (root path)
# Startup file: passenger_wsgi.py
# Entry point: application

# --- Environment variables (set in cPanel, NOT in files) ---
# SECRET_KEY = [generate with python -c "import secrets; print(secrets.token_hex(32))"]
# FLASK_ENV = production
# SITE_BASE_URL = https://aks.a28.mytemp.website
# UPLOAD_FOLDER = /home/soev6l6iqyxn/ables-home-healthcare/instance/uploads
# ADMIN_EMAIL = admin@ableshomehealthcare.com
# DATABASE_URL = mysql+pymysql://[DB_USER]:[DB_PASS]@[DB_HOST]:3306/[DB_NAME] (set after MySQL DB created)
# MAIL_SERVER = [RELAY_HOST] (from GoDaddy SMTP relay doc)
# MAIL_PORT = 587
# MAIL_USE_TLS = true
# MAIL_USERNAME = [RELAY_USERNAME] (cPanel only)
# MAIL_PASSWORD = [RELAY_PASSWORD] (cPanel only)
# FORCE_HTTPS = true
