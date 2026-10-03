#!/usr/bin/env bash
# Verify that the running deployment matches the intended branch.
#
# This script queries the production server for its current deployed commit
# and compares it against the latest commit on the intended branch. It warns
# if they diverge, which can happen if a manual deploy wasn't followed by
# a script update (as occurred with claude/analyze-repo-summary-YtnGK).
#
# Usage:
#   ./backend/deploy/verify-sync.sh
#
# Exit codes:
#   0 = deployed commit matches branch head
#   1 = mismatch, or SSH/git query failed

set -euo pipefail

SERVER_IP="${1:-72.62.31.221}"
REPO_BRANCH="feature/scoring-v2-evidence-engine"
APP_DIR="/opt/cv-analyzer"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'

echo "Verifying deployment sync for branch: $REPO_BRANCH"
echo "Server: $SERVER_IP"
echo ""

# Query deployed commit hash on server
echo "Fetching deployed commit from server..."
deployed_commit=$(ssh -o ConnectTimeout=5 "root@${SERVER_IP}" \
    "cd ${APP_DIR} && git rev-parse HEAD" 2>/dev/null) || {
    echo -e "${RED}✗ Failed to connect to server or query git${NC}"
    echo "  Check: SSH access to root@${SERVER_IP}, git installed at ${APP_DIR}"
    exit 1
}

# Query branch head from GitHub
echo "Fetching branch head from GitHub..."
branch_commit=$(git ls-remote https://github.com/ahijjah/Saas_CVs_v3.git \
    "refs/heads/${REPO_BRANCH}" | awk '{print $1}') || {
    echo -e "${RED}✗ Failed to query GitHub${NC}"
    exit 1
}

echo "  Deployed:     ${deployed_commit:0:12}"
echo "  Branch HEAD:  ${branch_commit:0:12}"
echo ""

if [[ "$deployed_commit" == "$branch_commit" ]]; then
    echo -e "${GREEN}✓ Deployment is in sync${NC}"
    exit 0
else
    echo -e "${RED}✗ MISMATCH: Deployed code does not match branch HEAD${NC}"
    echo ""
    echo "  This can happen if:"
    echo "  1. A manual deployment was made but setup.sh wasn't updated"
    echo "  2. The server needs to pull the latest changes"
    echo "  3. The wrong branch is deployed"
    echo ""
    echo "  To fix, SSH to the server and run:"
    echo "  ${YELLOW}cd ${APP_DIR} && git fetch origin && git checkout ${REPO_BRANCH} && git pull${NC}"
    echo "  Then rebuild: ${YELLOW}docker compose up -d --build${NC}"
    exit 1
fi
