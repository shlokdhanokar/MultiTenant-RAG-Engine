#!/bin/bash
set -euo pipefail

echo "🚀 Installing RAG Engine..."

REPO_DIR=/home/ubuntu/MultiTenant-RAG-Engine
VENV="$REPO_DIR/venv"
UI_ROOT=/var/www/rag-ui

# ---------------------------------------------------------------- inputs
# All optional. With none of them set the script produces a working http-only
# box on its bare IP; with DOMAIN set it also obtains a certificate and
# redirects to https, which is what the live demo runs.
#
#   DOMAIN         hostname to serve and request a certificate for
#   CERTBOT_EMAIL  expiry notices; required by Let's Encrypt for -n
#   DUCKDNS_TOKEN  only when DOMAIN is a *.duckdns.org name
DOMAIN="${DOMAIN:-}"
CERTBOT_EMAIL="${CERTBOT_EMAIL:-}"
DUCKDNS_TOKEN="${DUCKDNS_TOKEN:-}"

# A token staged as a file avoids putting it in the shell history or in the
# process list, where every user on the box can read it.
if [ -z "$DUCKDNS_TOKEN" ] && [ -r /home/ubuntu/duckdns.token ]; then
  DUCKDNS_TOKEN=$(cat /home/ubuntu/duckdns.token)
fi

# ---------------------------------------------------------------- swap
# E2.1.Micro has 1 GB RAM. Compiling wheels during `pip install`, OCR on large
# images, and the Vite build all exceed that. Swap is what keeps this shape
# usable.
if [ ! -f /swapfile ]; then
  sudo fallocate -l 4G /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile
  sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
  # Default swappiness of 60 thrashes on a box this small.
  echo 'vm.swappiness=10' | sudo tee /etc/sysctl.d/99-swap.conf
  sudo sysctl -p /etc/sysctl.d/99-swap.conf
fi

# ---------------------------------------------------------------- packages
sudo apt update && sudo apt upgrade -y
sudo apt install -y software-properties-common
sudo add-apt-repository -y ppa:deadsnakes/ppa
sudo apt update
# The project requires 3.11+, but Ubuntu 22.04 ships 3.10 as `python3`.
sudo apt install -y python3.11 python3.11-venv python3.11-dev \
                    git nginx certbot python3-certbot-nginx tesseract-ocr \
                    build-essential rsync

# Node is only needed to build the UI bundle, but building it here is what
# makes this script reproduce the deployment rather than half of it.
if ! command -v node > /dev/null; then
  curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
  sudo apt install -y nodejs
fi

# ---------------------------------------------------------------- source
if [ ! -d "$REPO_DIR" ]; then
  git clone https://github.com/shlokdhanokar/MultiTenant-RAG-Engine.git "$REPO_DIR"
fi
cd "$REPO_DIR"

# A venv keeps gunicorn at a known absolute path. A bare `pip install` as the
# ubuntu user lands in ~/.local/bin, which the systemd unit below cannot see.
python3.11 -m venv "$VENV"
"$VENV/bin/pip" install --upgrade pip
"$VENV/bin/pip" install -r requirements.txt
"$VENV/bin/pip" install gunicorn

# ---------------------------------------------------------------- secrets
# Generated into shell vars first. Writing them inside a quoted heredoc would
# emit the literal $(...) text instead of the generated value.
ENC_KEY=$("$VENV/bin/python" -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
SIGN_SECRET=$("$VENV/bin/python" -c "import secrets; print(secrets.token_hex(32))")
PUBLIC_IP=$(curl -s ifconfig.me)
BASE_URL="http://$PUBLIC_IP"
[ -n "$DOMAIN" ] && BASE_URL="https://$DOMAIN"

# A staged .env uploaded ahead of time wins over the template below. This is
# how the real credential set (integrations, embedding dims) gets in without
# retyping it over SSH.
if [ -f /home/ubuntu/env.staging ]; then
  cp /home/ubuntu/env.staging "$REPO_DIR/.env"
  chmod 600 "$REPO_DIR/.env"
  STAGED=1
elif [ ! -f "$REPO_DIR/.env" ]; then
  STAGED=0
cat > "$REPO_DIR/.env" <<EOF
MONGODB_URI=mongodb+srv://username:password@cluster.mongodb.net/
MONGODB_DB_NAME=rag_db
GEMINI_API_KEY=your_gemini_key_here
CREDENTIAL_ENCRYPTION_KEY=$ENC_KEY
IMAGE_URL_SIGNING_SECRET=$SIGN_SECRET
APP_BASE_URL=$BASE_URL
LLM_PROVIDER=gemini
EOF
fi

if [ "${STAGED:-0}" = "1" ]; then
  echo "✅ Using staged .env (credentials uploaded ahead of time)."
else
  echo ""
  echo "⚠️  Edit $REPO_DIR/.env and set your real values:"
  echo "     MONGODB_URI, GEMINI_API_KEY"
  echo "   APP_BASE_URL is pre-filled as $BASE_URL"
  echo ""
  read -r -p "Press Enter once you've edited .env..."
fi

# APP_BASE_URL only signs the absolute image links handed to WhatsApp, but a
# stale value there produces links that 404 for everyone, so keep it in step
# with whatever this box is actually reachable as.
if grep -q '^APP_BASE_URL=' "$REPO_DIR/.env"; then
  sudo sed -i "s#^APP_BASE_URL=.*#APP_BASE_URL=$BASE_URL#" "$REPO_DIR/.env"
else
  echo "APP_BASE_URL=$BASE_URL" >> "$REPO_DIR/.env"
fi

# ---------------------------------------------------------------- UI bundle
# nginx serves the compiled SPA directly and proxies only the API paths, so the
# browser talks to one origin and never needs CORS or a second hostname.
(cd "$REPO_DIR/ui" && npm ci && npm run build)
sudo mkdir -p "$UI_ROOT"
sudo rsync -a --delete "$REPO_DIR/ui/dist/" "$UI_ROOT/"
sudo chown -R www-data:www-data "$UI_ROOT"

# ---------------------------------------------------------------- service
# 1 OCPU / 1 GB: 2 workers is the ceiling before swap thrashing dominates.
# --max-requests recycles workers to bound the memory PyMuPDF leaks on big PDFs.
sudo tee /etc/systemd/system/rag-engine.service > /dev/null <<EOF
[Unit]
Description=RAG Engine
After=network.target

[Service]
User=ubuntu
WorkingDirectory=$REPO_DIR
EnvironmentFile=$REPO_DIR/.env
ExecStart=$VENV/bin/gunicorn -w 2 -b 127.0.0.1:8000 --timeout 120 --max-requests 200 --max-requests-jitter 50 server:app
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable rag-engine
sudo systemctl restart rag-engine

# ---------------------------------------------------------------- nginx
# The proxy headers live in a snippet because six location blocks need them
# and a copy that drifts from the others is the kind of bug that only shows up
# as a rate limit counting the wrong client.
sudo mkdir -p /etc/nginx/snippets
sudo tee /etc/nginx/snippets/rag-proxy.conf > /dev/null <<'EOF'
proxy_pass http://127.0.0.1:8000;
proxy_set_header Host $host;
proxy_set_header X-Real-IP $remote_addr;
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
proxy_set_header X-Forwarded-Proto $scheme;
proxy_read_timeout 120s;
EOF

sudo tee /etc/nginx/sites-available/rag-engine > /dev/null <<'EOF'
server {
    listen 80;
    server_name __SERVER_NAME__;

    client_max_body_size 25M;

    root /var/www/rag-ui;
    index index.html;

    # API surface. Everything here belongs to Flask, not the SPA.
    location = /health                        { include snippets/rag-proxy.conf; }
    location ^~ /api/                         { include snippets/rag-proxy.conf; }
    location ^~ /chat/                        { include snippets/rag-proxy.conf; }
    location ^~ /upload/                      { include snippets/rag-proxy.conf; }
    location ^~ /admin/                       { include snippets/rag-proxy.conf; }
    location ^~ /image/                       { include snippets/rag-proxy.conf; }

    # Hashed asset filenames are content-addressed, so they can cache hard.
    location /assets/ {
        expires 1y;
        add_header Cache-Control "public, immutable";
    }

    # SPA fallback: unknown paths render the app, not a 404.
    location / {
        try_files $uri $uri/ /index.html;
    }
}
EOF
sudo sed -i "s/__SERVER_NAME__/${DOMAIN:-_}/" /etc/nginx/sites-available/rag-engine

sudo ln -sf /etc/nginx/sites-available/rag-engine /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl restart nginx

# ---------------------------------------------------------------- firewall
# Oracle's Ubuntu images ship iptables REJECT rules that block 80/443 even
# when the VCN security list allows them. Opening the security list alone is
# not enough — this is the single most common "why is it unreachable" cause.
# The rules must go BEFORE the catch-all REJECT, since iptables matches
# top-down and stops at the first hit. Its position varies by image, so find
# it rather than hardcoding an index — inserting after it silently does nothing.
REJECT_LINE=$(sudo iptables -L INPUT -n --line-numbers | awk '/REJECT/ {print $1; exit}')
REJECT_LINE=${REJECT_LINE:-1}
sudo iptables -I INPUT "$REJECT_LINE" -m state --state NEW -p tcp --dport 80 -j ACCEPT
sudo iptables -I INPUT "$REJECT_LINE" -m state --state NEW -p tcp --dport 443 -j ACCEPT
sudo netfilter-persistent save || {
  sudo apt install -y iptables-persistent
  sudo netfilter-persistent save
}

# ---------------------------------------------------------------- dynamic DNS
# Oracle hands out an ephemeral public IP that can change across a stop/start.
# Without this the hostname — and therefore the certificate, which is bound to
# it — silently points at nothing after a reboot.
if [ -n "$DUCKDNS_TOKEN" ] && [[ "$DOMAIN" == *.duckdns.org ]]; then
  DUCKDNS_SUB="${DOMAIN%%.duckdns.org}"
  echo "$DUCKDNS_TOKEN" | sudo tee /etc/duckdns.token > /dev/null
  sudo chmod 600 /etc/duckdns.token

  sudo tee /usr/local/bin/duckdns-update.sh > /dev/null <<EOF
#!/bin/bash
# Re-points the DuckDNS record at whatever public IP this box currently has.
# An empty ip= parameter tells DuckDNS to use the source address of this
# request, so the box never has to work out its own external address.
# Token is read from /etc/duckdns.token (mode 600, not in git).
[ -r /etc/duckdns.token ] || exit 0
TOKEN=\$(cat /etc/duckdns.token)
curl -s -m 20 "https://www.duckdns.org/update?domains=$DUCKDNS_SUB&token=\${TOKEN}&ip=" \\
  >> /var/log/duckdns.log 2>&1
echo " \$(date -Is)" >> /var/log/duckdns.log
EOF
  sudo chmod 755 /usr/local/bin/duckdns-update.sh
  sudo /usr/local/bin/duckdns-update.sh

  # Replace rather than append, so re-running the script does not accumulate
  # duplicate cron entries.
  (crontab -l 2>/dev/null | grep -v duckdns-update.sh; \
   echo "*/5 * * * * /usr/local/bin/duckdns-update.sh") | crontab -
fi

# ---------------------------------------------------------------- TLS
# certbot rewrites the site above in place: it adds the 443 server block and a
# 301 from 80. Renewal is handled by the certbot.timer the package installs;
# --deploy-hook reloads nginx so a renewed certificate is actually served.
if [ -n "$DOMAIN" ] && [ -n "$CERTBOT_EMAIL" ]; then
  sudo certbot --nginx -d "$DOMAIN" \
       --redirect --agree-tos --no-eff-email -m "$CERTBOT_EMAIL" -n \
       --deploy-hook "systemctl reload nginx"
  sudo systemctl enable --now certbot.timer
elif [ -n "$DOMAIN" ]; then
  echo "ℹ️  CERTBOT_EMAIL not set — skipping TLS. To finish:"
  echo "     sudo certbot --nginx -d $DOMAIN --redirect"
fi

echo ""
echo "════════════════════════════════════════════"
echo "✅ RAG ENGINE DEPLOYED"
echo "════════════════════════════════════════════"
echo "URL       : $BASE_URL"
echo "Health    : curl $BASE_URL/health"
echo ""
echo "Logs      : sudo journalctl -u rag-engine -f"
echo "Restart   : sudo systemctl restart rag-engine"
echo ""
echo "Seed the demo tenants (from a machine with the same MONGODB_URI):"
echo "  python scripts/seed_demo.py"
echo "════════════════════════════════════════════"
