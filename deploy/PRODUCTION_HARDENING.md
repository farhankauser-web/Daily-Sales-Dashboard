# Production hardening cutover (VAPT H1/H2/H3)

Run **at the end**, once `dashboard.infinitee.biz` resolves to the Elastic IP
`13.62.83.159` and TLS is ready. Everything below is idempotent-ish; do it in a
maintenance window (brief downtime when switching runserver → gunicorn).

The code changes (settings hardening + WhiteNoise) are already in `main`; they
stay **dormant** until the env vars below are set — so pulling them early is safe.

---

## 1. Pull + install new deps
```bash
cd ~/Daily-Sales-Dashboard && git pull origin main
./venv/bin/pip install -r requirements.txt        # adds whitenoise, gunicorn 23
```

## 2. Collect static (WhiteNoise serves these under DEBUG=False)
```bash
mkdir -p static && ./venv/bin/python manage.py collectstatic --noinput
```

## 3. Add production env vars to `.env`
Append/update these (the domain + HTTPS flags activate the dormant settings):
```
DJANGO_DEBUG=False
ALLOWED_HOSTS=dashboard.infinitee.biz,13.62.83.159,127.0.0.1,localhost
CSRF_TRUSTED_ORIGINS=https://dashboard.infinitee.biz
SECURE_SSL_REDIRECT=True
SECURE_HSTS_SECONDS=3600            # start at 1h; raise to 31536000 once confident
```
> Leave `SECURE_SSL_REDIRECT=False` **until** TLS actually works end-to-end, or
> you can lock yourself out with a redirect loop. Verify HTTPS first, then flip it.

## 4. Switch runserver → gunicorn
```bash
sudo cp deploy/infinitee-gunicorn.service /etc/systemd/system/infinitee.service
sudo systemctl daemon-reload && sudo systemctl restart infinitee
sudo systemctl is-active infinitee
curl -sI http://127.0.0.1:8000/ | head -1     # gunicorn responding locally
```

## 5. TLS + reverse proxy
**If the network guy terminates TLS on the box (nginx + certbot):**
```bash
sudo cp deploy/nginx-dashboard.conf /etc/nginx/sites-available/dashboard
sudo ln -sf /etc/nginx/sites-available/dashboard /etc/nginx/sites-enabled/dashboard
sudo rm -f /etc/nginx/sites-enabled/default
sudo certbot --nginx -d dashboard.infinitee.biz
sudo nginx -t && sudo systemctl reload nginx
```
Then open **443** (and **80** for the ACME challenge/redirect) in the EC2 Security
Group; you can then **close 8000** to the world (gunicorn is localhost-only now).

**If TLS is upstream (Cloudflare / load balancer):** point it at the origin, make
sure it forwards `X-Forwarded-Proto: https`, and confirm the origin port is
reachable (either keep on-box nginx on 80, or expose gunicorn per the LB's needs).

## 6. Verify
```bash
# from your laptop:
curl -sI https://dashboard.infinitee.biz/ | head -5     # 200/302, valid cert
```
- Log in over HTTPS — no cookie/CSRF errors.
- Dashboard fetch/sync buttons work (CSRF_TRUSTED_ORIGINS covers them).
- Trigger an error path → generic 500 page, **no traceback** (DEBUG=False working).

## Rollback (if anything breaks)
```bash
# restore runserver service (the old unit) OR just flip back:
sudo sed -i 's/^SECURE_SSL_REDIRECT=True/SECURE_SSL_REDIRECT=False/' ~/Daily-Sales-Dashboard/.env
# and, if needed, revert to the previous infinitee.service, then:
sudo systemctl daemon-reload && sudo systemctl restart infinitee
```

## Post-cutover
- Raise `SECURE_HSTS_SECONDS` to `31536000` once HTTPS is proven stable.
- Optionally switch WhiteNoise to `CompressedManifestStaticFilesStorage` for hashed cache-busting.
- Then close out remaining VAPT: dep security bumps (Django/Pillow/cryptography), `json_script` for the 4 `|safe` templates.
