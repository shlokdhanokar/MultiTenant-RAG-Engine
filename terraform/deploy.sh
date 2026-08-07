#!/bin/bash
set -euo pipefail

echo "🚀 Installing RAG Engine..."

REPO_DIR=/home/ubuntu/MultiTenant-RAG-Engine
VENV="$REPO_DIR/venv"

# ---------------------------------------------------------------- swap
# E2.1.Micro has 1 GB RAM. Compiling wheels during `pip install` and OCR on
# large images both exceed that. Swap is what keeps this shape usable.
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
                    build-essential

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
PUBLIC_IP=$(curl -s -H "Authorization: Bearer Oracle" \
  http://169.254.169.254/opc/v2/instance/ | grep -o '"region"' >/dev/null 2>&1 \
  && curl -s ifconfig.me || curl -s ifconfig.me)

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
GROQ_API_KEY=your_groq_key_here
GEMINI_API_KEY=your_gemini_key_here
CREDENTIAL_ENCRYPTION_KEY=$ENC_KEY
IMAGE_URL_SIGNING_SECRET=$SIGN_SECRET
APP_BASE_URL=http://$PUBLIC_IP
LLM_PROVIDER=groq
EOF
fi

if [ "${STAGED:-0}" = "1" ]; then
  echo "✅ Using staged .env (credentials uploaded ahead of time)."
else
  echo ""
  echo "⚠️  Edit $REPO_DIR/.env and set your real values:"
  echo "     MONGODB_URI, GROQ_API_KEY, GEMINI_API_KEY"
  echo "   APP_BASE_URL is pre-filled as http://$PUBLIC_IP"
  echo ""
  read -r -p "Press Enter once you've edited .env..."
fi

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
sudo tee /etc/nginx/sites-available/rag-engine > /dev/null <<'EOF'
server {
    listen 80;
    server_name _;

    client_max_body_size 25M;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
    }
}
EOF

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

echo ""
echo "════════════════════════════════════════════"
echo "✅ RAG ENGINE DEPLOYED"
echo "════════════════════════════════════════════"
echo "Public IP : $PUBLIC_IP"
echo "Health    : curl http://$PUBLIC_IP/health"
echo ""
echo "Logs      : sudo journalctl -u rag-engine -f"
echo "Restart   : sudo systemctl restart rag-engine"
echo ""
echo "For HTTPS, point a domain here then run:"
echo "  sudo certbot --nginx -d your-domain.com"
echo "  # then set APP_BASE_URL=https://your-domain.com in .env and restart"
echo "════════════════════════════════════════════"
