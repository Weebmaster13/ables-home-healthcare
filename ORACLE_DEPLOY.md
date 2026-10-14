# Deploy Ables Home Healthcare to Oracle Cloud Always Free ($0/mo)

> Do NOT touch GoDaddy DNS, delete Railway, migrate production data, or spend
> money until the VM deployment is tested and the owner approves the cutover.
> Railway stays live throughout. Rollback = restoring old DNS values.

## Why this host (verified in Oracle's current docs)

- Ampere A1 VM (up to 2 OCPU / 12 GB) + 200 GB disk + 10 TB/mo egress,
  free with no expiry, in the tenancy home region.
- Runs our exact Dockerized stack: Flask + self-hosted Postgres 15 +
  Nginx + free Let's Encrypt TLS. No credit-based trial needed for these.
- Private uploads live on the VM disk (durable, unlike free PaaS), with 5
  free volume backups available.
- Outbound SMTP on port 587 is allowed (only port 25 is blocked).
- GoDaddy keeps the domain + business email; only A/CNAME records move.

## Honest caveats (from the same docs)

- Signup needs a **mobile number + credit card** (verification only — no
  charge unless you upgrade). A $300/30-day trial is attached automatically;
  Always Free resources persist after it lapses.
- New A1 instances can hit **"out of host capacity"** — retry later, try
  another availability domain, or use an E2.Micro shape instead.
- **Idle VMs may be reclaimed** after 7 days under 20% CPU/network/memory.
  A quiet care site qualifies as idle: keep Railway live as fallback, keep
  volume backups enabled, and watch the notification email on the account.

## VM setup (owner or admin with the Oracle login)

1. Home region: **US East (Ashburn)**. Create 1x Ampere A1 Ubuntu instance
   (2 OCPU / 12 GB, 50 GB boot volume), public subnet, public IP. Open
   ingress for ports 80/443 (security list) plus your SSH source.
2. On the VM: install Docker + Compose plugin, clone
   `Weebmaster13/ables-home-healthcare`, `cd` in.
3. `cp .env.oracle.example .env.oracle` and fill secrets (generate the DB
   password once, paste it in both `POSTGRES_PASSWORD` and `DATABASE_URL`;
   SMTP values from the current Railway variables).
4. `docker compose -f docker-compose.oracle.yml up -d --build`, then
   `docker compose -f docker-compose.oracle.yml logs web` — expect 200s.
5. Seed the admin (same `seed_production_admin` values as Railway), change
   the password, enable a weekly `pg_dump` cron to Object Storage (20 GB
   free) or off-host copy.
6. TLS (only after DNS points here): `certbot --nginx -d
   ableshomehealthcare.com -d www.ableshomehealthcare.com` (auto-renew).

## Cutover DNS in GoDaddy (ONLY with explicit owner approval)

- Remove any `AAAA` records for the root/www.
- Root: **A** `@` → **the VM's public IPv4** (shown in OCI Console).
- `www`: **CNAME** `www` → `ableshomehealthcare.com`.
- Keep all MX/TXT mail records exactly as they are.
- Canonical: apex `https://ableshomehealthcare.com` (www redirects at Nginx
  once TLS is issued; add the redirect block at cutover).
