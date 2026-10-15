#!/bin/zsh
set -euo pipefail
cd -- "${0:A:h}"
print 'Paste your Tavily API key below. It stays on this Mac and is hidden while you type.'
read -rs 'dealfinder_key?Tavily key: '
print
if [[ -z "$dealfinder_key" ]]; then print 'Nothing changed.'; exit 0; fi
umask 077
print -rn -- "$dealfinder_key" > "$HOME/Library/Application Support/Deal Finder/.config/tavily.key"
unset dealfinder_key
/bin/launchctl kickstart -k "gui/$UID/com.dealfinder.server"
print 'Search key saved privately; protected server restarting.'
