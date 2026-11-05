#!/usr/bin/env bash
# One-time setup on Ubuntu: installs Docker if missing, creates the local config
# files from the templates, asks for the Discord values, then starts the bot and
# Ollama. Safe to run again: existing files and values are kept.
#
#   ./setup.sh
set -euo pipefail

cd "$(dirname "$0")"

case "$(uname -s)" in
    MINGW*|MSYS*|CYGWIN*)
        echo "This is the Ubuntu setup. On Windows, double-click setup.cmd instead." >&2
        exit 1 ;;
esac

MIN_RAM_GB=28          # the default model needs about 25 GB while the digest builds
DEFAULT_MODEL="qwen3.6:35b"

say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
fail() { printf '\nerror: %s\n' "$*" >&2; exit 1; }

need_sudo() {
    if [ "$(id -u)" -eq 0 ]; then "$@"; else sudo "$@"; fi
}

# ---------------------------------------------------------------------------
# 1. Docker
# ---------------------------------------------------------------------------
install_docker() {
    say "Installing Docker Engine from Docker's apt repository"
    . /etc/os-release
    need_sudo apt-get update
    need_sudo apt-get install -y ca-certificates curl
    need_sudo install -m 0755 -d /etc/apt/keyrings
    need_sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    need_sudo chmod a+r /etc/apt/keyrings/docker.asc
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${UBUNTU_CODENAME:-$VERSION_CODENAME} stable" \
        | need_sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
    need_sudo apt-get update
    need_sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    need_sudo systemctl enable --now docker
}

if [ -r /etc/os-release ] && ! grep -qi '^ID=ubuntu' /etc/os-release; then
    say "This script is written for Ubuntu; continuing, but the Docker install step may not fit this system."
fi

if ! command -v docker > /dev/null || ! docker compose version > /dev/null 2>&1; then
    install_docker
fi

TARGET_USER="${SUDO_USER:-$USER}"
if ! id -nG "$TARGET_USER" | grep -qw docker; then
    need_sudo usermod -aG docker "$TARGET_USER"
    say "Added $TARGET_USER to the docker group. It applies after you log out and back in; until then this script uses sudo."
fi

if docker info > /dev/null 2>&1 || [ "$(id -u)" -eq 0 ]; then DOCKER=(docker); else DOCKER=(sudo docker); fi

# ---------------------------------------------------------------------------
# 2. Local config files from the templates (never overwritten)
# ---------------------------------------------------------------------------
for pair in ".env.example:.env" "sources.example.yml:sources.yml" "digest.example.yml:digest.yml"; do
    template="${pair%%:*}"; target="${pair##*:}"
    if [ -d "$target" ]; then
        # docker leaves an empty directory behind when a mounted file was missing
        need_sudo rmdir "$target" 2> /dev/null || fail "$target is a directory with files in it; move it away and run again"
    fi
    if [ ! -f "$target" ]; then
        cp "$template" "$target"
        echo "created $target from $template"
    fi
done
mkdir -p data

env_get() { grep -E "^$1=" .env | head -n1 | cut -d= -f2- || true; }

env_set() {
    local key="$1" value="$2" escaped
    escaped=$(printf '%s' "$value" | sed -e 's/[\\|&]/\\&/g')
    if grep -qE "^$key=" .env; then
        sed -i "s|^$key=.*|$key=$escaped|" .env
    else
        printf '%s=%s\n' "$key" "$value" >> .env
    fi
}

# Asks only when the value in .env is still empty.
ask() {
    local key="$1" prompt="$2" pattern="$3" optional="${4:-}" value
    [ -n "$(env_get "$key")" ] && return
    while true; do
        read -r -p "$prompt: " value < /dev/tty
        if [ -z "$value" ] && [ -n "$optional" ]; then return; fi
        if [[ "$value" =~ $pattern ]]; then env_set "$key" "$value"; return; fi
        echo "  that does not look right, try again"
    done
}

say "Discord values (Enter keeps what is already in .env; see the README for where each one is)"
ask DISCORD_TOKEN          "Bot token"                                '^[A-Za-z0-9._-]{50,}$'
ask DISCORD_CHANNEL_ID     "Channel ID to post in"                    '^[0-9]{15,21}$'
ask DISCORD_GUILD_ID       "Server ID (optional, Enter to skip)"      '^[0-9]{15,21}$' optional
ask DISCORD_APPLICATION_ID "Application ID (optional, Enter to skip)" '^[0-9]{15,21}$' optional

# The bot container runs as this user so it can write data/ and sources.yml (/watch edits it).
env_set HOST_UID "$(id -u "$TARGET_USER")"
env_set HOST_GID "$(id -g "$TARGET_USER")"
need_sudo chown -R "$(id -u "$TARGET_USER"):$(id -g "$TARGET_USER")" data sources.yml digest.yml .env
chmod 600 .env

# ---------------------------------------------------------------------------
# 3. Model
# ---------------------------------------------------------------------------
current_model=$(grep -E '^model:' digest.yml | head -n1 | sed -E 's/^model:[[:space:]]*//')
say "Ollama model for the daily digest. Tags: https://ollama.com/library/qwen3.6"
read -r -p "Model tag [${current_model:-$DEFAULT_MODEL}]: " model < /dev/tty
if [ -n "$model" ]; then
    sed -i -E "s|^model:.*|model: $model|" digest.yml
fi

ram_gb=$(awk '/MemTotal/ {printf "%d", $2 / 1048576}' /proc/meminfo)
if [ "$ram_gb" -lt "$MIN_RAM_GB" ]; then
    say "This machine has ${ram_gb} GB of RAM; the default model needs about 25 GB free while the digest builds."
fi

# ---------------------------------------------------------------------------
# 4. Start
# ---------------------------------------------------------------------------
say "Building and starting the bot and Ollama"
"${DOCKER[@]}" compose up -d --build

say "Running. The bot downloads the model on first start (about 23 GB, once)."
cat <<DONE
  Logs:          ${DOCKER[*]} compose logs -f newsbot
  Test feeds:    ${DOCKER[*]} compose run --rm newsbot python -m newsbot check
  Test digest:   ${DOCKER[*]} compose run --rm newsbot python -m newsbot digest
  Edit:          sources.yml / digest.yml, then /reload in Discord
                 .env, then: ${DOCKER[*]} compose up -d
Both containers restart on their own after a reboot.
DONE
