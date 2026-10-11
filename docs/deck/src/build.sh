#!/bin/zsh
# Rebuild docs/deck/index.html from the slide artboards in docs/deck/src/*.dc.html.
# Uses the hackathon-slide-deck skill's compiler, then applies the dark theme + speaker notes.
set -e
HERE=${0:A:h}
DECK=${HERE:h}
BUILT=$(mktemp -d)
node ~/.claude/skills/hackathon-slide-deck/scripts/build-deck.mjs "$HERE" "$BUILT" --title "Spidey Sense"
node "$HERE/postprocess.mjs" "$BUILT" "$DECK"
/bin/rm -rf "$BUILT"
