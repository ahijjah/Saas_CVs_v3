#!/usr/bin/env bash
# =============================================================================
#  CV Analyzer SaaS — VPS Bootstrap Script (IP-based, no SSL)
#  Server: 72.62.31.221
#
#  Run as root on a fresh Ubuntu 22.04 / 24.04 VPS:
#
#    curl -fsSL https://raw.githubusercontent.com/ahijjah/Saas_CVs_v3/\
#claude/analyze-repo-summary-YtnGK/backend/deploy/setup.sh | bash
#
#  URLs after setup:
#    Frontend: http://72.62.31.221
#    API:      http://72.62.31.221:8000
# =============================================================================
set -euo pipefail

SERVER_IP="72.62.31.221"
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
    nginx git ufw fail2ban htop jq
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

docker compose version &>/dev/null || die "docker compose plugin not found"
ok "Docker Compose: $(docker compose version)"

# ── 3. Firewall ───────────────────────────────────────────────────────────────
step "Configuring UFW firewall"
ufw --force reset >/dev/null
ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
ufw allow 22/tcp   comment 'SSH'      >/dev/null
ufw allow 80/tcp   comment 'HTTP'     >/dev/null
ufw allow 8000/tcp comment 'FastAPI'  >/dev/null
ufw --force enable >/dev/null
ok "UFW active — allowed: 22, 80, 8000"

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
    warn ".env created — edit it with real secrets before starting services"
    warn "  nano ${BACKEND_DIR}/.env"
else
    ok ".env already exists (not overwritten)"
fi

# ── 7. Nginx ──────────────────────────────────────────────────────────────────
step "Configuring Nginx (frontend on port 80)"
cp "${BACKEND_DIR}/deploy/nginx.conf" /etc/nginx/sites-available/cv-analyzer
ln -sf /etc/nginx/sites-available/cv-analyzer /etc/nginx/sites-enabled/cv-analyzer
rm -f /etc/nginx/sites-enabled/default

# Placeholder index so Nginx doesn't 404 before frontend is deployed
if [[ ! -f "${FRONTEND_DIR}/index.html" ]]; then
    mkdir -p "${FRONTEND_DIR}"
    cat > "${FRONTEND_DIR}/index.html" <<HTML
<!DOCTYPE html><html><body>
<h2>CV Analyzer — deploying frontend...</h2>
<p>API: <a href="http://${SERVER_IP}:8000/health">http://${SERVER_IP}:8000/health</a></p>
</body></html>
HTML
fi

nginx -t
systemctl enable --now nginx
systemctl reload nginx
ok "Nginx configured"

# ── 8. Start postgres + redis ─────────────────────────────────────────────────
step "Starting PostgreSQL and Redis"
cd "${BACKEND_DIR}"
docker compose up -d postgres redis

echo "Waiting for PostgreSQL..."
for i in $(seq 1 30); do
    if docker compose exec -T postgres pg_isready -U cv_app -d cv_analyzer_prod &>/dev/null; then
        ok "PostgreSQL ready"
        break
    fi
    sleep 2
    [[ $i -eq 30 ]] && die "PostgreSQL did not become healthy in 60s"
done

docker compose exec -T redis redis-cli ping | grep -q PONG && ok "Redis ready"

# ── 9. Summary ────────────────────────────────────────────────────────────────
step "Status"
docker compose ps
echo ""
echo "  Disk:"; df -h / | tail -1
echo "  RAM:";  free -h | grep Mem

cat <<NEXT

${GREEN}════════════════════════════════════════════════════════════${NC}
${GREEN}  Bootstrap COMPLETE${NC}
${GREEN}════════════════════════════════════════════════════════════${NC}

${YELLOW}REQUIRED NEXT STEPS:${NC}

1. Edit secrets:
   ${BLUE}nano ${BACKEND_DIR}/.env${NC}
   → DB_PASSWORD, JWT_SECRET, OPENAI_API_KEY,
     SMTP_PASSWORD, IMAP_PASSWORD

2. Build and start all services (~10 min first time):
   ${BLUE}cd ${BACKEND_DIR} && docker compose up -d --build${NC}

3. Verify API is live:
   ${BLUE}curl http://${SERVER_IP}:8000/health${NC}
   Expected: {"status":"ok"}

4. Deploy the React frontend:
   Build locally:  npm run build
   Then copy:      ${BLUE}rsync -avz dist/ root@${SERVER_IP}:${FRONTEND_DIR}/${NC}
   Or build on server:
     ${BLUE}cd ${APP_DIR} && npm ci && npm run build && rsync -a dist/ ${FRONTEND_DIR}/${NC}

5. Open in browser:
   Frontend: ${BLUE}http://${SERVER_IP}${NC}
   API docs: ${BLUE}http://${SERVER_IP}:8000/docs${NC}

NEXT
