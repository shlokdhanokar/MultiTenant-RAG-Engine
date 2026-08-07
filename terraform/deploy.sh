#!/bin/bash
set -e

echo "🚀 Installing RAG Engine..."

# Update system
sudo apt update && sudo apt upgrade -y

# Install dependencies
sudo apt install -y python3.11 python3-pip git nginx certbot python3-certbot-nginx tesseract-ocr

# Clone repo
cd /home/ubuntu
git clone https://github.com/shlokdhanokar/MultiTenant-RAG-Engine.git
cd MultiTenant-RAG-Engine

# Install Python dependencies
pip install -r requirements.txt
pip install gunicorn

# Create .env file (EDIT THESE VALUES)
cat > .env << 'EOF'
MONGODB_URI=mongodb+srv://username:password@cluster.mongodb.net/
MONGODB_DB_NAME=rag_db
GROQ_API_KEY=your_groq_key_here
GEMINI_API_KEY=your_gemini_key_here
CREDENTIAL_ENCRYPTION_KEY=$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
IMAGE_URL_SIGNING_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
APP_BASE_URL=https://your-domain.com
LLM_PROVIDER=groq
EOF

echo "⚠️  EDIT .env file with your actual credentials:"
echo "   - MONGODB_URI"
echo "   - GROQ_API_KEY"
echo "   - GEMINI_API_KEY"
echo "   - APP_BASE_URL (your domain)"
echo ""
read -p "Press Enter once you've edited .env..."

# Create systemd service
sudo tee /etc/systemd/system/rag-engine.service > /dev/null << 'EOF'
[Unit]
Description=RAG Engine
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/MultiTenant-RAG-Engine
ExecStart=/usr/local/bin/gunicorn -w 4 -b 127.0.0.1:8000 server:app
Restart=always
RestartSec=10
Environment="PATH=/usr/local/bin:/usr/bin"

[Install]
WantedBy=multi-user.target
EOF

# Enable and start service
sudo systemctl daemon-reload
sudo systemctl enable rag-engine
sudo systemctl start rag-engine
echo "✅ RAG Engine service started"

# Setup Nginx
sudo tee /etc/nginx/sites-available/rag-engine > /dev/null << 'EOF'
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
        proxy_read_timeout 60s;
    }
}
EOF

# Enable Nginx site
sudo ln -sf /etc/nginx/sites-available/rag-engine /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl restart nginx
echo "✅ Nginx configured"

# Get public IP
PUBLIC_IP=$(curl -s http://169.254.169.254/opc/v2/instance/vnics/ | grep -o '"publicIp":"[^"]*' | cut -d'"' -f4)

echo ""
echo "════════════════════════════════════════════"
echo "✅ RAG ENGINE DEPLOYED!"
echo "════════════════════════════════════════════"
echo "Instance IP: $PUBLIC_IP"
echo ""
echo "📋 NEXT STEPS:"
echo "1. Point your domain to: $PUBLIC_IP"
echo "2. Get SSL certificate:"
echo "   sudo certbot --nginx -d your-domain.com"
echo "3. Test: curl http://$PUBLIC_IP/health"
echo "4. Deploy UI to Vercel with:"
echo "   VITE_API_BASE=https://your-domain.com npm run build"
echo "════════════════════════════════════════════"
