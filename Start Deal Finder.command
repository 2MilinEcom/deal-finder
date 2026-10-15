#!/bin/zsh
service="gui/$UID/com.dealfinder.server"
if /bin/launchctl print "$service" >/dev/null 2>&1; then
 /bin/launchctl kickstart "$service"
else
 /bin/launchctl bootstrap "gui/$UID" "$HOME/Library/LaunchAgents/com.dealfinder.server.plist"
fi
print 'Protected Deal Finder server starting. Keep Tailscale connected and the Mac awake.'
