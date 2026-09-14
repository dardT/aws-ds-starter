#!/usr/bin/env bash
# Bootstrap de secours pour une machine de travail sur Ubuntu nu (sans AMI dorée,
# voir workstation_ami_id dans variables.tf et décision D2 bis, docs/01-decisions.md).
# Idempotent : peut être relancé sans effet de bord.
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

# --- Docker CE + buildx, dépôt apt officiel -----------------------------------------
# Piège documenté (D2 bis) : le paquet Ubuntu `docker.io` ne fournit pas buildx, requis
# par §11 (docker buildx build --platform linux/amd64) et vérifié par le contrôle 11
# du preflight (scripts/preflight.py). Il faut le dépôt officiel Docker, pas docker.io.
if ! command -v docker >/dev/null 2>&1; then
  sudo apt-get update -y
  sudo apt-get install -y ca-certificates curl gnupg

  sudo install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
    | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  sudo chmod a+r /etc/apt/keyrings/docker.gpg

  . /etc/os-release
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
    | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

  sudo apt-get update -y
  sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin

  sudo usermod -aG docker ubuntu
fi

# --- git ------------------------------------------------------------------------------
if ! command -v git >/dev/null 2>&1; then
  sudo apt-get install -y git
fi

# --- uv (gère son propre interpréteur Python, voir décision D2 bis / .python-version) -
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sudo -u ubuntu sh
fi
