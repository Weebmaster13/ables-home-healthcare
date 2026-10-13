# Deploy Ables Home Healthcare to a GoDaddy VPS (self-managed Linux)

> Do NOT touch GoDaddy DNS, delete Railway, migrate production data, or spend
> money until the VPS deployment is tested and the owner approves the cutover.
> Railway stays live throughout. Rollback = restoring old DNS values.

## Why a VPS (verified in GoDaddy's own help docs)

- Website Builder cannot run code; Web Hosting (cPanel) documents only
  PHP/WordPress/MySQL — no Python runtime, no PostgreSQL. Our app needs
  Flask + PostgreSQL + persistent uploads, so only a VPS (full root SSH)
  works on GoDaddy.
- Technical minimum (engineering requirement, not a price): 1+ vCPU,
  **2 GB+ RAM** (Postgres + Flask + Nginx in Docker), 25 GB+ disk,
  Ubuntu 22.04 or 24.04.

## Server prep (run over SSH on the VPS)

```bash
# Docker (official Docker apt repository)
sudo apt-get update && sudo apt-get install -y ca-certificates curl git
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" | \
sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update && sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
sudo ufw allow OpenSSH && sudo ufw allow 80/tcp && sudo ufw allow 443/tcp && sudo ufw --force enable

# App (Railway stays live; this VM is tested first, cut over only on approval)
git clone https://github.com/Weebmaster13/ables-home-healthcare ables && cd ables
cp .env.oracle.example .env.oracle
# Fill secrets now: generate the DB password once, paste it in BOTH
# POSTGRES_PASSWORD and DATABASE_URL (letters/numbers only, or URL-encode
# special characters); SMTP values from Railway variables.
nano .env.oracle
docker compose -f docker-compose.oracle.yml up -d --build
docker compose -f docker-compose.oracle.yml logs web   # expect 200s
```

Then seed the admin (same `seed_production_admin` values as Railway),
change the password, and add a weekly database backup cron on the VM:

```bash
# Weekly Postgres backup to the VM disk (also copy off-host monthly)
(crontab -l 2>/dev/null; echo "0 3 * * 0 docker exec ables-db-1 pg_dump -U ables ables | gzip > /root/ables-backup-$(date +\%F).sql.gz") | crontab -
```

Note: `SITE_BASE_URL` is pre-set to the apex domain, so test the VM over
its IP address or a temporary hosts entry — sitemap/canonical tags already
point at the final domain, which is correct for cutover.

## Cutover (ONLY with explicit owner approval)

1. GoDaddy → domain → DNS: remove `AAAA` for root/www; set **A** `@` to the
   VPS public IP; **CNAME** `www` to `ableshomehealthcare.com`. Keep every
   MX/TXT mail record unchanged.
2. On the VPS: `sudo snap install --classic certbot && sudo certbot --nginx
   -d ableshomehealthcare.com -d www.ableshomehealthcare.com` (auto-renew).
3. Verify `https://ableshomehealthcare.com`, `/care`, forms, login. Keep
   Railway until verified.
