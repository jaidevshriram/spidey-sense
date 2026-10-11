#!/bin/bash
# Build phase: writes the Mac's current LAN IP into the built app's Info.plist as SpideyMacHost,
# so a phone build finds the engine with no typing (fallback when Bonjour doesn't answer).
# Never fails the build.
IP="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null)"
[ -z "$IP" ] && IP="127.0.0.1"
PLIST="$TARGET_BUILD_DIR/$INFOPLIST_PATH"
if [ -f "$PLIST" ]; then
  /usr/libexec/PlistBuddy -c "Set :SpideyMacHost $IP" "$PLIST" 2>/dev/null \
    || /usr/libexec/PlistBuddy -c "Add :SpideyMacHost string $IP" "$PLIST" 2>/dev/null
  echo "SpideyMacHost = $IP -> $PLIST"
else
  echo "warning: Info.plist not found at $PLIST; SpideyMacHost not stamped"
fi
exit 0
