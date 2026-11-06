#!/bin/sh
# Creates a small internal certificate authority (once) and a server certificate for the
# FactoryPulse host. Prefer a certificate from the company PKI (Active Directory Certificate
# Services etc.) when one exists – then just place server.crt/server.key into ./tls.
#
#   ops/generate-tls-cert.sh <dns-name> [more DNS names or IP addresses ...]
#   ops/generate-tls-cert.sh factorypulse.werk.local factorypulse 192.168.10.20
#
# Output (directory FP_TLS_DIR, default ./tls):
#   ca.crt      – distribute to the client PCs (Windows: GPO "Trusted Root Certification Authorities")
#   ca.key      – KEEP OFFLINE / in the safe; needed only to issue new server certificates
#   server.crt  – server certificate (825 days, SAN = all given names)
#   server.key  – server private key (mode 600)
set -eu
[ $# -ge 1 ] || { sed -n '2,15p' "$0"; exit 1; }
DIR=${FP_TLS_DIR:-./tls}
DAYS=${FP_TLS_DAYS:-825}
mkdir -p "$DIR"
umask 077

if [ ! -f "$DIR/ca.key" ]; then
  echo "Creating internal CA in $DIR/ca.crt"
  openssl req -x509 -new -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -sha256 -days 3650 \
    -subj "/O=FactoryPulse internal CA/CN=FactoryPulse Root CA $(hostname)" \
    -addext "basicConstraints=critical,CA:TRUE,pathlen:0" -addext "keyUsage=critical,keyCertSign,cRLSign" \
    -keyout "$DIR/ca.key" -out "$DIR/ca.crt"
fi

SAN=""
for n in "$@"; do
  if echo "$n" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$|:'; then SAN="$SAN,IP:$n"; else SAN="$SAN,DNS:$n"; fi
done
SAN=${SAN#,}

openssl req -new -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -sha256 -subj "/CN=$1" \
  -keyout "$DIR/server.key" -out "$DIR/server.csr"
printf "subjectAltName=%s\nbasicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\nextendedKeyUsage=serverAuth\n" "$SAN" > "$DIR/server.ext"
openssl x509 -req -in "$DIR/server.csr" -CA "$DIR/ca.crt" -CAkey "$DIR/ca.key" -CAcreateserial \
  -days "$DAYS" -sha256 -extfile "$DIR/server.ext" -out "$DIR/server.crt"
rm -f "$DIR/server.csr" "$DIR/server.ext"
chmod 600 "$DIR/server.key" "$DIR/ca.key"
chmod 644 "$DIR/server.crt" "$DIR/ca.crt"
echo "Server certificate for: $SAN (valid $DAYS days)"
openssl x509 -in "$DIR/server.crt" -noout -subject -enddate
