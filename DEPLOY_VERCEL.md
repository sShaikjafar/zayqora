# Deploying ZAYQORA on Vercel

> Written without access to a Vercel account, so it is untested. If a step fails, read the build log or
> Project > Logs in Vercel and fix the message it names.

Vercel runs the app as a serverless function with a read-only disk, so it needs a hosted PostgreSQL database
(the local SQLite file cannot be used).

## 1. Database (free): Neon
1. Create an account at neon.tech and a project (any name, region near you).
2. Copy the **connection string** (it starts with `postgresql://`). Prefer the **pooled** one (host contains `-pooler`).

## 2. Create the tables, the admin and demo data (from your laptop)
In the VS Code terminal with `(.venv)` active (PowerShell):
```
pip install -r requirements.txt
$env:DATABASE_URL="PASTE-THE-NEON-STRING-HERE"
python -m app.manage create-admin you@yourdomain.com --password A-LONG-PASSWORD
python -m app.manage seed-demo
```
(`$env:DATABASE_URL` only lasts for that terminal window. Never commit the string.)

## 3. Deploy
1. Push the project to GitHub.
2. vercel.com > **Add New > Project** > import the `zayqora` repository. Framework: FastAPI (detected automatically).
3. Add these **Environment Variables** before deploying:

| Name | Value |
| --- | --- |
| `DATABASE_URL` | the Neon connection string |
| `SECRET_KEY` | a random 48+ character value (`python -c "import secrets; print(secrets.token_urlsafe(48))"`) |
| `ENVIRONMENT` | `production` |
| `TRUST_PROXY` | `1` |

4. Click **Deploy**. When it finishes, the site is at `https://YOUR-PROJECT.vercel.app`.

## 4. Check it
- `/api/v1/health` returns `{"status":"ok"...}`
- `/r/demo-bistro` and `/r/demo-bistro/menu` show the demo restaurant
- `/` is the dashboard: `demo@zayqora.test` / `Demo12345!`

## Good to know
- With `ENVIRONMENT=production` the app refuses to start if `SECRET_KEY` is missing or shorter than 32 characters
  (the deployment then shows errors in Logs).
- Emails are stored in the notification log (status LOGGED); real sending needs the `SMTP_*` variables.
- Rate limits are per serverless instance, so they are a soft protection on Vercel.
- Remove or change the demo accounts and keep `/admin` credentials private before sharing widely.
