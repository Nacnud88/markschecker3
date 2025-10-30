#!/usr/bin/env bash
# Provision Marks Checker 3 with Gunicorn + Nginx + Certbot on Ubuntu/Debian.

set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
    echo "Run this script as root (sudo)." >&2
    exit 1
fi

read -rp "Primary domain (e.g. example.com): " PRIMARY_DOMAIN
if [[ -z "${PRIMARY_DOMAIN}" ]]; then
    echo "Domain required." >&2
    exit 1
fi

read -rp "Additional domains (space separated, optional): " EXTRA_DOMAINS
read -rp "Email for Let's Encrypt (optional to skip SSL now): " CERTBOT_EMAIL

APP_ROOT="/opt/markschecker3"
REPO_URL="${REPO_URL:-https://github.com/Nacnud88/markschecker3.git}"
RELEASE_DIR="${APP_ROOT}/current"
VENV_DIR="${APP_ROOT}/venv"
ENV_FILE="${APP_ROOT}/config.env"
SERVICE_FILE="/etc/systemd/system/markschecker3.service"
NGINX_CONF="/etc/nginx/sites-available/markschecker3"

apt update
apt install -y python3-venv python3-pip git nginx certbot python3-certbot-nginx

mkdir -p "${APP_ROOT}"
if [[ ! -d "${RELEASE_DIR}" ]]; then
    git clone "${REPO_URL}" "${RELEASE_DIR}"
else
    git -C "${RELEASE_DIR}" pull --ff-only
fi

python3 -m venv "${VENV_DIR}"
"${VENV_DIR}/bin/pip" install --upgrade pip
"${VENV_DIR}/bin/pip" install -r "${RELEASE_DIR}/requirements.txt"

SECRET="${MARKSCHECKER_SECRET:-$(openssl rand -hex 32)}"
cat >"${ENV_FILE}" <<EOF
MARKSCHECKER_BASE_DIR=${APP_ROOT}/instance
MARKSCHECKER_SECRET=${SECRET}
EOF

mkdir -p "${APP_ROOT}/instance/data"
chown -R www-data:www-data "${APP_ROOT}"

cat >"${SERVICE_FILE}" <<EOF
[Unit]
Description=Marks Checker 3
After=network.target

[Service]
Type=simple
User=www-data
Group=www-data
WorkingDirectory=${RELEASE_DIR}
EnvironmentFile=${ENV_FILE}
ExecStart=${VENV_DIR}/bin/gunicorn -c gunicorn.conf.py 'app:create_app()'
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

cat >"${RELEASE_DIR}/gunicorn.conf.py" <<'EOF'
import multiprocessing

bind = "127.0.0.1:5100"
workers = max(2, multiprocessing.cpu_count())
timeout = 180
graceful_timeout = 30
loglevel = "info"
EOF

cat >"${NGINX_CONF}" <<EOF
server {
    listen 80;
    server_name ${PRIMARY_DOMAIN} ${EXTRA_DOMAINS};

    client_max_body_size 16m;

    location /static/ {
        alias ${RELEASE_DIR}/app/static/;
    }

    location / {
        proxy_pass http://127.0.0.1:5100;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 600;
    }
}
EOF

ln -sf "${NGINX_CONF}" /etc/nginx/sites-enabled/markschecker3
nginx -t
systemctl reload nginx

systemctl daemon-reload
systemctl enable --now markschecker3.service

if [[ -n "${CERTBOT_EMAIL}" ]]; then
    domains=(-d "${PRIMARY_DOMAIN}")
    for d in ${EXTRA_DOMAINS}; do
        domains+=(-d "${d}")
    done
    certbot --nginx --agree-tos --no-eff-email -m "${CERTBOT_EMAIL}" --redirect "${domains[@]}"
else
    echo "Skipping TLS issuance. Run certbot --nginx later." >&2
fi

echo "Deployment finished. Visit https://${PRIMARY_DOMAIN}"
