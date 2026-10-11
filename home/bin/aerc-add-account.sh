#!/bin/bash
set -e
echo "Provider: 1) Gmail  2) iCloud  3) Other (enter servers manually)"
read -p "Choice [1-3]: " prov
read -p "Account name (one word, e.g. personal): " acct
read -p "Your full name: " fullname
read -p "Email address: " email
read -s -p "App password (hidden): " pass; echo

case "$prov" in
  1) imap="imaps://imap.gmail.com:993";   smtp="smtps://smtp.gmail.com:465"; copyto="" ;;
  2) imap="imaps://imap.mail.me.com:993"; smtp="smtp://smtp.mail.me.com:587"; copyto="Sent Messages" ;;
  3) read -p "IMAP server (e.g. imap.example.com): " ih
     read -p "SMTP server (e.g. smtp.example.com): " sh
     imap="imaps://$ih:993"; smtp="smtp://$sh:587"; copyto="Sent" ;;
  *) echo "Invalid choice"; exit 1 ;;
esac

user=$(python3 -c 'import sys,urllib.parse; print(urllib.parse.quote(sys.argv[1], safe=""))' "$email")
mkdir -p ~/.config/aerc
passfile=~/.config/aerc/pass-$acct
printf '%s' "$pass" > "$passfile"; chmod 600 "$passfile"

conf=~/.config/aerc/accounts.conf
touch "$conf"; chmod 600 "$conf"
cat >> "$conf" <<CONF

[$acct]
source            = ${imap/:\/\//://$user@}
source-cred-cmd   = cat $passfile
outgoing          = ${smtp/:\/\//://$user@}
outgoing-cred-cmd = cat $passfile
default           = INBOX
from              = $fullname <$email>
copy-to           = $copyto
cache-headers     = true
CONF
echo "Account '$acct' added to $conf"
