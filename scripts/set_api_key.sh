#!/bin/zsh
# Saves your Anthropic API key to the repo-root .env (chmod 600, gitignored). Input is hidden.
cd "$(dirname "$0")/.."
echo -n "Paste ANTHROPIC_API_KEY (hidden), then Enter: "
read -s KEY; echo
[[ -z "$KEY" ]] && { echo "Nothing entered, no changes."; exit 1; }
umask 077
grep -v '^ANTHROPIC_API_KEY=' .env 2>/dev/null > .env.tmp || true
echo "ANTHROPIC_API_KEY=$KEY" >> .env.tmp && mv .env.tmp .env && chmod 600 .env
unset KEY; echo "Saved to $(pwd)/.env"
