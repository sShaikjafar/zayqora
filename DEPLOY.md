# Deploying ZAYQORA

> These files were written without access to a Docker host, so they are **untested**.
> Try everything on a staging server first.

## What you need
- A Linux server (VPS) with Docker and Docker Compose
- A domain name pointing at the server's IP address (needed for automatic HTTPS)
- Ports 80 and 443 open

## Steps
1. Copy the project to the server.
2. `cp .env.example .env` and fill in `DOMAIN`, `DB_PASSWORD`, `SECRET_KEY` (and the optional email settings).
3. `docker compose up -d --build`
4. Create the platform administrator:
   `docker compose exec app python -m app.manage create-admin you@yourdomain.com`
5. Open `https://your-domain/admin` and log in.
6. Optional demo data: `docker compose exec app python -m app.manage seed-demo`

With `ENVIRONMENT=production` the app refuses to start unless `SECRET_KEY` is at least 32 characters.

## Backups (spec 21.4)
Nightly dump, kept for 14 days (run from cron on the server):
```
docker compose exec -T db pg_dump -U zayqora zayqora | gzip > /backups/zayqora-$(date +%F).sql.gz
find /backups -name 'zayqora-*.sql.gz' -mtime +14 -delete
```
Copy the dumps off the server too (another machine or object storage).
**Test a restore regularly** on a separate machine:
```
gunzip -c zayqora-DATE.sql.gz | docker compose exec -T db psql -U zayqora zayqora_restore_test
```

## Monitoring
- Point an uptime monitor at `https://your-domain/api/v1/health` (it returns `{"status":"ok"}`).
- Logs: `docker compose logs -f app`
- Check the Email notifications card on the dashboard: entries marked FAILED mean the mail settings need attention.

## Before real customers use it
- [ ] Strong `SECRET_KEY` and `DB_PASSWORD`; `.env` is not in source control
- [ ] HTTPS works (Caddy handles certificates automatically)
- [ ] Platform admin created with a strong password; demo accounts removed if demo data was loaded
- [ ] A backup was taken **and restored successfully** on another machine
- [ ] Uptime monitoring is active
- [ ] Test orders and reservations from a phone

## Known gaps (planned work)
- Database changes use a simple add-column helper; adopt Alembic migrations before the next schema change.
- Rate limiting is per process; add a shared store (for example Redis) if you run more than one app instance.
- Cash payments only; online payment, staff-management screens, email verification, opening hours and branding are not built yet.
- No Content Security Policy header yet (the pages use small inline scripts).
