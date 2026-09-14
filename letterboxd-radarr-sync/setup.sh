#!/usr/bin/env sh
# Interactive first-time setup: collects the four Radarr values, writes .env,
# and runs the read-only connectivity check.
#
# Safe to re-run. It never overwrites an existing .env without asking, and it
# writes nothing to Radarr.

set -eu

cd "$(dirname "$0")"

if [ ! -f .env.example ]; then
    echo "error: run this from inside the letterboxd-radarr-sync directory" >&2
    exit 1
fi

if [ -f .env ]; then
    printf '.env already exists. Overwrite it? [y/N] '
    read -r reply
    case "$reply" in
        [yY]*) ;;
        *) echo "Keeping the existing .env. Nothing changed."; exit 0 ;;
    esac
fi

cat <<'INTRO'

letterboxd-radarr-sync setup
----------------------------
Four values, all from Radarr's own settings pages. Press Ctrl-C to bail out;
nothing is written until every question is answered.

INTRO

cat <<'URLHELP'
1/4  Radarr URL
     Where this container reaches Radarr. Note that "localhost" will NOT
     work from inside a container -- it refers to the container itself.
       - Radarr in the same compose stack:  http://radarr:7878
       - anything else:                     http://<host LAN IP>:7878
URLHELP
printf '     Radarr URL: '
read -r radarr_url
radarr_url="${radarr_url%/}"

case "$radarr_url" in
    *localhost*|*127.0.0.1*)
        echo
        echo "     WARNING: that address refers to the container itself, not your"
        echo "     host. Use the host's LAN IP unless you know otherwise."
        ;;
esac
case "$radarr_url" in
    */api*) echo "     WARNING: drop the /api suffix; just the base URL." ;;
esac

echo
echo "2/4  API key   (Radarr -> Settings -> General -> Security -> API Key)"
printf '     API key (hidden): '
stty -echo 2>/dev/null || true
read -r radarr_key
stty echo 2>/dev/null || true
echo

echo
cat <<'ROOTHELP'
3/4  Root folder  (Radarr -> Settings -> Media Management -> Root Folders)
     Copy it exactly as Radarr shows it. This is the path AS RADARR SEES IT,
     which inside Docker is usually /movies, not your host path.
ROOTHELP
printf '     Root folder [/movies]: '
read -r radarr_root
radarr_root="${radarr_root:-/movies}"

echo
echo "4/4  Quality profile  (Radarr -> Settings -> Profiles), e.g. HD-1080p"
printf '     Quality profile [HD-1080p]: '
read -r radarr_profile
radarr_profile="${radarr_profile:-HD-1080p}"

# Rewrite .env.example line by line so comments and every other setting are
# preserved exactly. Avoids sed escaping problems with slashes in paths.
umask 077
: > .env.tmp
while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
        RADARR_URL=*)             printf 'RADARR_URL=%s\n' "$radarr_url" ;;
        RADARR_API_KEY=*)         printf 'RADARR_API_KEY=%s\n' "$radarr_key" ;;
        RADARR_ROOT_FOLDER=*)     printf 'RADARR_ROOT_FOLDER=%s\n' "$radarr_root" ;;
        RADARR_QUALITY_PROFILE=*) printf 'RADARR_QUALITY_PROFILE=%s\n' "$radarr_profile" ;;
        *)                        printf '%s\n' "$line" ;;
    esac
done < .env.example >> .env.tmp
mv .env.tmp .env
chmod 600 .env

echo
echo "Wrote .env (permissions 600, readable only by you)."
echo

printf 'Run the read-only connectivity check now? [Y/n] '
read -r reply
case "$reply" in
    [nN]*)
        echo
        echo "Skipped. When ready:"
        echo "  docker compose run --rm letterboxd-radarr-sync --check"
        exit 0
        ;;
esac

echo
if ! command -v docker >/dev/null 2>&1; then
    echo "docker not found on PATH. Install Docker, then run:"
    echo "  docker compose run --rm letterboxd-radarr-sync --check"
    exit 1
fi

docker compose run --rm letterboxd-radarr-sync --check || {
    echo
    echo "The check reported problems. Fix them in .env, then re-run:"
    echo "  docker compose run --rm letterboxd-radarr-sync --check"
    exit 1
}

cat <<'DONE'
Next steps:

  1. Preview the decisions without writing to Radarr:
       docker compose run --rm letterboxd-radarr-sync --once --dry-run

  2. Start it for real:
       docker compose up -d
       docker compose logs -f

  3. In your download client, set maximum active downloads to 1.

Nothing will download until a NEW film is added to one of the watchlists.
That is expected, not a fault.
DONE
