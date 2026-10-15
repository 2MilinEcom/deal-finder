#!/bin/zsh
cd -- "${0:A:h}"
/usr/bin/pbcopy < .config/server.token
print 'Server access token copied. Paste it into Deal Finder > Home > Settings on your iPhone if needed.'
