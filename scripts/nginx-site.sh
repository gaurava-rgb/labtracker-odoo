#!/usr/bin/env bash
# For servers that ALREADY run nginx on ports 80/443 (so the Caddy "tls" profile cannot be used).
# Creates an nginx site for DOMAIN that proxies to the Odoo container and gets a Let's Encrypt certificate.
# Run as root on the server:   scripts/nginx-site.sh lab.example.com
set -euo pipefail
D=${1:?usage: $0 your.domain.com}
cd "$(dirname "$0")/.."
PORT=$(grep -E '^ODOO_PORT=' .env 2>/dev/null | cut -d= -f2 | tr -d ' ' || true); PORT=${PORT:-8069}
CHAT=$(grep -E '^ODOO_CHAT_PORT=' .env 2>/dev/null | cut -d= -f2 | tr -d ' ' || true); CHAT=${CHAT:-8072}
command -v certbot >/dev/null || apt-get install -y certbot python3-certbot-nginx
cat > /etc/nginx/sites-available/$D <<NG
upstream odoo_${PORT} { server 127.0.0.1:${PORT}; }
upstream odoo_chat_${CHAT} { server 127.0.0.1:${CHAT}; }
server {
    listen 80;
    server_name $D;
    client_max_body_size 64m;
    proxy_read_timeout 720s; proxy_connect_timeout 720s; proxy_send_timeout 720s;
    proxy_set_header X-Forwarded-Host \$host;
    proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto \$scheme;
    proxy_set_header X-Real-IP \$remote_addr;
    location /websocket { proxy_pass http://odoo_chat_${CHAT}; proxy_http_version 1.1; proxy_set_header Upgrade \$http_upgrade; proxy_set_header Connection "Upgrade"; }
    location / { proxy_pass http://odoo_${PORT}; proxy_redirect off; }
}
NG
ln -sf /etc/nginx/sites-available/$D /etc/nginx/sites-enabled/$D
nginx -t && systemctl reload nginx
certbot --nginx -d "$D" --non-interactive --agree-tos --redirect --register-unsafely-without-email
systemctl reload nginx
echo "Done: https://$D -> Odoo on 127.0.0.1:${PORT}"
