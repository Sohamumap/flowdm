#!/bin/bash
# FlowDM: automatic first-boot setup for an AWS EC2 server (Ubuntu 24.04).
#
# HOW TO USE: when launching the instance, open "Advanced details", scroll to "User data",
# paste this WHOLE file, and fill in the four values below. That's it: the server installs
# everything by itself. It takes about 10 minutes after the instance starts.
#
# Progress / problems: EC2 console → select the instance → Actions → Monitor and troubleshoot →
# Get system log (look for lines starting with "FlowDM:"). Full log on the server:
# /var/log/cloud-init-output.log

# ============================ FILL THESE IN ============================
DUCKDNS_SUBDOMAIN="yourbot"                 # only the part before .duckdns.org
DUCKDNS_TOKEN="paste-your-duckdns-token"    # shown at the top of duckdns.org after you sign in
ADMIN_EMAIL="you@example.com"               # your dashboard login
ADMIN_PASSWORD="choose-a-password"          # 8+ characters, no quotes; change it later in Settings
# =======================================================================

REPO_URL="https://github.com/Sohamumap/flowdm.git"
APP_DIR="/opt/flowdm"

set -euo pipefail

say() { echo "FlowDM: $*"; }
fail() {
  say "SETUP FAILED: $*"
  exit 1
}

# ---------------------------------------------------------------- check the inputs
[[ "$DUCKDNS_SUBDOMAIN" =~ ^[a-z0-9-]+$ && "$DUCKDNS_SUBDOMAIN" != "yourbot" ]] ||
  fail "set DUCKDNS_SUBDOMAIN to your DuckDNS name (lowercase, without .duckdns.org)"
[[ "$DUCKDNS_TOKEN" =~ ^[a-f0-9-]{30,40}$ ]] ||
  fail "set DUCKDNS_TOKEN to the token shown on duckdns.org"
[[ "$ADMIN_EMAIL" == *@*.* && "$ADMIN_EMAIL" != "you@example.com" ]] ||
  fail "set ADMIN_EMAIL to your email address"
[[ ${#ADMIN_PASSWORD} -ge 8 && "$ADMIN_PASSWORD" != "choose-a-password" ]] ||
  fail "set ADMIN_PASSWORD to at least 8 characters"
[[ "$ADMIN_PASSWORD" != *[\'\"\\\$\`]* ]] ||
  fail "ADMIN_PASSWORD can't contain quotes, backslashes, \$ or backticks"

DOMAIN="${DUCKDNS_SUBDOMAIN}.duckdns.org"
say "setting up https://${DOMAIN}"

# ---------------------------------------------------------------- swap (1 GB RAM is tight for building)
if ! swapon --show | grep -q /swapfile; then
  say "adding 2 GB swap"
  fallocate -l 2G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile >/dev/null
  swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
  echo 'vm.swappiness=10' > /etc/sysctl.d/99-flowdm.conf
  sysctl -q -p /etc/sysctl.d/99-flowdm.conf
fi

# ---------------------------------------------------------------- packages and Docker
say "installing Docker"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q git curl ca-certificates cron openssl
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sh
fi
mkdir -p /etc/docker
if [[ ! -f /etc/docker/daemon.json ]]; then
  echo '{ "log-driver": "json-file", "log-opts": { "max-size": "10m", "max-file": "3" } }' \
    > /etc/docker/daemon.json
fi
systemctl enable docker >/dev/null 2>&1
systemctl restart docker
usermod -aG docker ubuntu 2>/dev/null || true

# ---------------------------------------------------------------- point the domain at this server
say "pointing ${DOMAIN} at this server"
answer="$(curl -fsS "https://www.duckdns.org/update?domains=${DUCKDNS_SUBDOMAIN}&token=${DUCKDNS_TOKEN}&ip=" || true)"
[[ "$answer" == "OK" ]] ||
  fail "DuckDNS rejected the update (got '${answer}'). Check DUCKDNS_SUBDOMAIN and DUCKDNS_TOKEN."

# ---------------------------------------------------------------- the app
say "downloading the app"
if [[ -d "$APP_DIR/.git" ]]; then
  git -C "$APP_DIR" pull --ff-only
else
  git clone --depth 50 "$REPO_URL" "$APP_DIR"
fi
cd "$APP_DIR"

if [[ ! -f .env ]]; then
  say "creating the configuration with random secrets"
  ./deploy/init-env.sh "$DOMAIN" "$ADMIN_EMAIL" > /dev/null
  sed -i "/^ADMIN_PASSWORD=/d; /^DUCKDNS_TOKEN=/d" .env
  {
    echo "ADMIN_PASSWORD='${ADMIN_PASSWORD}'"
    echo "DUCKDNS_TOKEN=${DUCKDNS_TOKEN}"
  } >> .env
  chmod 600 .env
fi

# ---------------------------------------------------------------- scheduled jobs
cat > /etc/cron.d/flowdm <<CRON
# Keep ${DOMAIN} pointed at this server (the public IP changes if the instance is stopped)
*/5 * * * * root ${APP_DIR}/deploy/duckdns-update.sh >/dev/null 2>&1
# Daily database backup to ${APP_DIR}/backups (last 14 kept)
15 3 * * * root ${APP_DIR}/deploy/backup.sh >/dev/null 2>&1
CRON
chmod 644 /etc/cron.d/flowdm

# ---------------------------------------------------------------- start
say "building and starting (about 5-10 minutes on a t3.micro)"
docker compose up -d --build

for _ in $(seq 1 60); do
  status="$(docker inspect --format '{{.State.Health.Status}}' "$(docker compose ps -q api)" 2>/dev/null || true)"
  [[ "$status" == "healthy" ]] && break
  sleep 5
done
[[ "${status:-}" == "healthy" ]] || fail "the app didn't become healthy; see 'docker compose logs api' in ${APP_DIR}"

say "DONE. Open https://${DOMAIN} and log in as ${ADMIN_EMAIL}"
say "(the HTTPS certificate can take another minute or two the very first time)"
