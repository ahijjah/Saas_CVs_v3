#!/usr/bin/env bash
# =============================================================================
#  CV Analyzer SaaS — VPS Bootstrap Script
#  Run as root on a fresh Ubuntu 22.04 / 24.04 VPS:
#
#    curl -fsSL https://raw.githubusercontent.com/ahijjah/Saas_CVs_v3/\
#claude/analyze-repo-summary-YtnGK/backend/deploy/setup.sh | bash
#
#  What this script does (idempotent — safe to re-run):
#    1. System packages + Docker (official repo)
#    2. UFW firewall (22, 80, 443 only)
#    3. Clone / update the application repo
#    4. Create directory structure + file permissions
#    5. Install Nginx with HTTP-only config (for Certbot challenge)
#    6. docker compose up postgres + redis
#    7. Print next-step instructions
# =============================================================================
set -euo pipefail

REPO_URL="https://github.com/ahijjah/Saas_CVs_v3.git"
REPO_BRANCH="claude/analyze-repo-summary-YtnGK"
APP_DIR="/opt/cv-analyzer"
FILES_DIR="/files"
FRONTEND_DIR="/var/www/cv-analyzer/dist"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
step()  { echo -e "\n${BLUE}▶  $*${NC}"; }
ok()    { echo -e "${GREEN}✓  $*${NC}"; }
warn()  { echo -e "${YELLOW}⚠  $*${NC}"; }
die()   { echo -e "${RED}✗  $*${NC}"; exit 1; }

[[ $EUID -ne 0 ]] && die "Must run as root"

# ── 1. System packages ────────────────────────────────────────────────────────
step "Updating system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get upgrade -y -qq
apt-get install -y --no-install-recommends \
    ca-certificates curl gnupg lsb-release \
    nginx certbot python3-certbot-nginx \
    git ufw fail2ban htop jq \
    postgresql-client-16 2>/dev/null || \
apt-get install -y --no-install-recommends \
    ca-certificates curl gnupg lsb-release \
    nginx certbot python3-certbot-nginx \
    git ufw fail2ban htop jq \
    postgresql-client
ok "System packages installed"

# ── 2. Docker ─────────────────────────────────────────────────────────────────
if ! command -v docker &>/dev/null; then
    step "Installing Docker (official repo)"
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
        | gpg --dearmor -o /etc/apt/keyrings/docker.gpg --batch --yes
    chmod a+r /etc/apt/keyrings/docker.gpg
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" \
        > /etc/apt/sources.list.d/docker.list
    apt-get update -qq
    apt-get install -y docker-ce docker-ce-cli containerd.io \
        docker-buildx-plugin docker-compose-plugin
    systemctl enable --now docker
    ok "Docker installed: $(docker --version)"
else
    ok "Docker already installed: $(docker --version)"
fi

# Ensure docker compose plugin works
docker compose version &>/dev/null || die "docker compose plugin not found"
ok "Docker Compose: $(docker compose version)"

# ── 3. Firewall ───────────────────────────────────────────────────────────────
step "Configuring UFW firewall"
ufw --force reset >/dev/null
ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
ufw allow 22/tcp   comment 'SSH'  >/dev/null
ufw allow 80/tcp   comment 'HTTP' >/dev/null
ufw allow 443/tcp  comment 'HTTPS' >/dev/null
ufw --force enable >/dev/null
ok "UFW active — allowed: 22 80 443"

# ── 4. Directory structure ────────────────────────────────────────────────────
step "Creating directory structure"
mkdir -p "${APP_DIR}"
mkdir -p "${FILES_DIR}"
mkdir -p "${FRONTEND_DIR}"
chmod 755 "${FILES_DIR}"
ok "Directories ready"

# ── 5. Clone / update repo ────────────────────────────────────────────────────
step "Fetching application code"
if [[ -d "${APP_DIR}/.git" ]]; then
    git -C "${APP_DIR}" fetch origin
    git -C "${APP_DIR}" checkout "${REPO_BRANCH}"
    git -C "${APP_DIR}" pull origin "${REPO_BRANCH}"
    ok "Repo updated at ${APP_DIR}"
else
    git clone --branch "${REPO_BRANCH}" "${REPO_URL}" "${APP_DIR}"
    ok "Repo cloned to ${APP_DIR}"
fi

BACKEND_DIR="${APP_DIR}/backend"

# ── 6. .env file ─────────────────────────────────────────────────────────────
if [[ ! -f "${BACKEND_DIR}/.env" ]]; then
    step "Creating .env from template"
    cp "${BACKEND_DIR}/.env.example" "${BACKEND_DIR}/.env"
    warn ".env created — YOU MUST EDIT IT with real secrets before starting services"
    warn "  nano ${BACKEND_DIR}/.env"
else
    ok ".env already exists (not overwritten)"
fi

# ── 7. Nginx — HTTP-only (pre-SSL) config ─────────────────────────────────────
step "Configuring Nginx (HTTP only, for Certbot challenge)"
cat > /etc/nginx/sites-available/cv-analyzer <<'NGINX'
# Pre-SSL configuration — replaced by certbot after cert issuance

server {
    listen 80;
    server_name api.ai970.cloud;
    client_max_body_size 15M;

    location /.well-known/acme-challenge/ { root /var/www/html; }

    location / {
        proxy_pass         http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
    }
}

server {
    listen 80;
    server_name app.ai970.cloud;

    location /.well-known/acme-challenge/ { root /var/www/html; }

    location / {
        root  /var/www/cv-analyzer/dist;
        index index.html;
        try_files $uri $uri/ /index.html;
    }

    location ~* \.(js|css|png|jpg|jpeg|gif|ico|svg|woff2?)$ {
        root    /var/www/cv-analyzer/dist;
        expires 1y;
        add_header Cache-Control "public, immutable";
    }
}
NGINX

ln -sf /etc/nginx/sites-available/cv-analyzer /etc/nginx/sites-enabled/cv-analyzer
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl enable --now nginx
systemctl reload nginx
ok "Nginx configured and reloaded"

# ── 8. Start infrastructure (postgres + redis) ────────────────────────────────
step "Starting PostgreSQL and Redis"
cd "${BACKEND_DIR}"
docker compose up -d postgres redis

echo "Waiting for postgres to be healthy..."
for i in $(seq 1 30); do
    if docker compose exec -T postgres pg_isready -U cv_app -d cv_analyzer_prod &>/dev/null; then
        ok "PostgreSQL is ready"
        break
    fi
    sleep 2
    [[ $i -eq 30 ]] && die "PostgreSQL did not become healthy in time"
done

docker compose exec -T redis redis-cli ping | grep -q PONG && ok "Redis is ready"

# ── 9. System summary ─────────────────────────────────────────────────────────
step "System summary"
echo ""
echo "  Docker containers:"
docker compose ps 2>/dev/null || docker ps --format 'table {{.Names}}\t{{.Status}}'
echo ""
echo "  Disk:"; df -h / | tail -1
echo "  RAM:";  free -h | grep Mem
echo ""

# ── 10. Next steps ────────────────────────────────────────────────────────────
cat <<NEXT

${GREEN}════════════════════════════════════════════════════════════${NC}
${GREEN}  Phase 0 provisioning COMPLETE${NC}
${GREEN}════════════════════════════════════════════════════════════${NC}

${YELLOW}REQUIRED NEXT STEPS (in order):${NC}

1. Edit secrets in ${BACKEND_DIR}/.env
   ${BLUE}nano ${BACKEND_DIR}/.env${NC}
   → Set: DATABASE_URL, DB_PASSWORD, JWT_SECRET,
           OPENAI_API_KEY, SMTP_PASSWORD, IMAP_PASSWORD

2. Point DNS A records to this server (72.62.31.221):
     api.ai970.cloud  →  72.62.31.221
     app.ai970.cloud  →  72.62.31.221
   Wait for propagation, then verify:
     ${BLUE}curl http://api.ai970.cloud/ ${NC}   (should reach Nginx)

3. Issue SSL certificates:
   ${BLUE}certbot --nginx -d api.ai970.cloud -d app.ai970.cloud --non-interactive --agree-tos -m admin@ai970.cloud${NC}

4. Build and start all services:
   ${BLUE}cd ${BACKEND_DIR} && docker compose up -d --build${NC}
   (First build takes ~10 min — downloads LibreOffice + ML model)

5. Verify API is up:
   ${BLUE}curl https://api.ai970.cloud/health${NC}   → should return {"status":"ok"}

6. Run the intelligence upgrade migration (if DB already has data):
   ${BLUE}docker compose exec api psql \$DATABASE_URL -f db/migrations/002_intelligence_upgrade.sql${NC}

7. Deploy the frontend build:
   ${BLUE}rsync -avz /path/to/local/dist/ root@72.62.31.221:${FRONTEND_DIR}/${NC}
   (or build on the server: cd ${APP_DIR} && npm ci && npm run build && cp -r dist/ ${FRONTEND_DIR}/)

NEXT
