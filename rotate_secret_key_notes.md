# Secret Key Rotation Notes

Use this only during a planned maintenance window.

1. Create a fresh value:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

2. Update `SECRET_KEY` in the hosting provider environment.

3. Restart the web service.

4. Ask active users to log in again. Existing password-reset links and sessions may stop working.

5. Run:

```powershell
python deploy_check.py
```

Keep old secret values private. Do not commit `.env` or `.env.production`.


