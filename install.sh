#!/usr/bin/env bash
#
# epiplan installer - sets up the terminal planner on this computer.
# Run it with:   bash install.sh
#
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
APP="${XDG_DATA_HOME:-$HOME/.local/share}/epiplan-app"
BIN="$HOME/.local/bin"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/epiplan"

say() { printf '\n\033[1m%s\033[0m\n' "$1"; }

# ---- 1. Python -------------------------------------------------------------
if ! command -v python3 >/dev/null 2>&1; then
    echo "Python 3 is required but was not found. Install it and run this again." >&2
    exit 1
fi

# ---- 2. Copy the app -------------------------------------------------------
say "Installing epiplan into $APP"
mkdir -p "$APP" "$BIN"
cp "$SRC/epiplan.py" "$APP/epiplan.py"
[ -f "$SRC/README.md" ]    && cp "$SRC/README.md"    "$APP/README.md"
[ -f "$SRC/README.fr.md" ] && cp "$SRC/README.fr.md" "$APP/README.fr.md"

# ---- 3. Virtual env + Playwright (for automatic login; optional) -----------
PYTHON="python3"
if python3 -m venv "$APP/.venv" >/dev/null 2>&1; then
    PYTHON="$APP/.venv/bin/python"
    say "Setting up automatic intra login (Playwright) - this may take a minute"
    if "$APP/.venv/bin/pip" install --quiet --upgrade pip playwright >/dev/null 2>&1; then
        echo "  automatic login ready (uses your installed Google Chrome)."
    else
        echo "  could not install Playwright - you can still log in with: epiplan login --paste"
    fi
else
    echo "  (no venv module - using system Python; log in with: epiplan login --paste)"
fi

# ---- 4. The 'epiplan' command ---------------------------------------------
cat > "$BIN/epiplan" <<EOF
#!/bin/sh
exec "$PYTHON" "$APP/epiplan.py" "\$@"
EOF
chmod +x "$BIN/epiplan"

# ---- 5. Pick a language ----------------------------------------------------
say "Choose a language / Choisis une langue"
echo "  1) English"
echo "  2) Français"
echo "  3) Auto-detect from this computer / détection automatique"
printf "Your choice [1/2/3, default 3]: "
read -r choice </dev/tty || choice=3
case "$choice" in
    1) LANG_CHOICE="en" ;;
    2) LANG_CHOICE="fr" ;;
    *) LANG_CHOICE="" ;;
esac

say "Daily break to keep free / Pause quotidienne à garder libre"
echo "  e.g. a lunch break. Leave empty for none."
printf "From-to time [HH:MM-HH:MM, e.g. 12:30-13:30, empty = none]: "
read -r BREAK </dev/tty || BREAK=""

mkdir -p "$CONFIG_DIR"
LANG_CHOICE="$LANG_CHOICE" BREAK="$BREAK" python3 - "$CONFIG_DIR/config.json" <<'PY'
import json, os, re, sys
path = sys.argv[1]
try:
    with open(path) as f:
        cfg = json.load(f)
except (FileNotFoundError, ValueError):
    cfg = {}
cfg["lang"] = os.environ.get("LANG_CHOICE", "")
match = re.match(r"\s*(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})\s*$", os.environ.get("BREAK", ""))
if match:
    cfg["breaks"] = [{"days": "mon-fri", "start": match.group(1), "end": match.group(2), "label": "Lunch"}]
elif os.environ.get("BREAK", "").strip() == "":
    cfg["breaks"] = []
with open(path, "w") as f:
    json.dump(cfg, f, indent=2, ensure_ascii=False)
os.chmod(path, 0o600)
PY

# ---- 6. PATH check + where things are --------------------------------------
README="$APP/README.md"
[ "$LANG_CHOICE" = "fr" ] && [ -f "$APP/README.fr.md" ] && README="$APP/README.fr.md"

say "Done!"
case ":$PATH:" in
    *":$BIN:"*) ;;
    *) echo "NOTE: $BIN is not in your PATH. Add this line to your ~/.bashrc or ~/.zshrc:"
       echo "      export PATH=\"\$HOME/.local/bin:\$PATH\""
       echo "      then open a new terminal (or run it once now).";;
esac
echo
echo "The guide (README) is here:"
echo "    $README"
echo
echo "Next steps:"
echo "    epiplan login      # connect your Epitech intra account (once)"
echo "    epiplan            # open the planner"
if [ "$(uname)" = "Linux" ]; then
    echo "    epiplan notifications on   # reminders before activities (Linux)"
fi
echo
echo "Switch language any time with:  epiplan lang en   |   epiplan lang fr"
