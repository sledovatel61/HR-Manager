# HTTPS reverse proxy: operator notes

The repository does **not** ship certificates, private keys or DNS records,
and makes no claim that a certificate or hostname exists for any deployment.
This document describes what the operator must provide and how to verify it.

## What the proxy does

- Terminates TLS on 443 (TLS 1.2/1.3) using `server.crt`/`server.key` from
  the mounted `TLS_CERT_DIR`.
- Redirects plaintext HTTP (port 80) to HTTPS. Policy: redirect; to switch to
  *reject*, change the `return 301` line in `default.conf.template` to
  `return 444;` and document the change.
- Sets security headers: `Strict-Transport-Security`,
  `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
  `Referrer-Policy: no-referrer`, `Permissions-Policy`, and a
  `Content-Security-Policy` proven compatible with the built UI (it mirrors
  `frontend/nginx.conf`).
- Limits request bodies to 10 MB (`client_max_body_size`).
- Publishes only 80/443; the application containers remain portless behind
  it (the production overlay removes every published port).

## Operator steps (one-time + per cert)

1. Obtain a certificate for your hostname, e.g. with certbot/Let's Encrypt
   (`certbot certonly --nginx -d hr-manager.example.com`) or generate a
   self-signed pair for staging with:
   `openssl req -x509 -nodes -newkey rsa:2048 -days 30 \
      -keyout server.key -out server.crt \
      -subj "/CN=hr-manager.example.com"`
2. Place both files in a host directory, e.g. `/etc/hr-manager/tls/`, with
   mode `0600` for the key (`chmod 600 server.key`).
3. Start the stack with the proxy overlay:
   ```bash
   export SERVER_NAME=hr-manager.example.com
   export TLS_CERT_DIR=/etc/hr-manager/tls
   docker compose -f infra/docker-compose.yml -f infra/compose.prod.yml \
       -f infra/docker-compose.proxy.yml up -d
   ```
4. Verify: `curl -fsS https://hr-manager.example.com/health` and
   `curl -sSI http://hr-manager.example.com/ | grep -i '^location:'`
   (must point to `https://...`).

## Expiry check (run from cron/monitoring)

```bash
openssl x509 -in "$TLS_CERT_DIR/server.crt" -noout -enddate -checkend $((30*86400))
```

`-checkend` exits non-zero when the certificate expires within 30 days — wire
it to the alerting described in `docs/ARCHITECTURE.md`.

## Explicit non-claims

- No certificate/DNS configuration is assumed to exist anywhere; if it does
  not, the proxy fails closed on TLS (nginx refuses to start with a missing
  key pair) and plaintext traffic is only redirected, never served.
