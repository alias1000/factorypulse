#!/bin/sh
# Selects the nginx configuration: FP_TLS=true -> HTTPS (tls.conf), otherwise plain HTTP (http.conf).
set -e
if [ "${FP_TLS:-false}" = "true" ]; then
  for f in /etc/nginx/tls/server.crt /etc/nginx/tls/server.key; do
    if [ ! -r "$f" ]; then
      echo "FP_TLS=true but $f is missing. Mount the certificate directory (FP_TLS_DIR) – see docs/INSTALLATION.md" >&2
      exit 1
    fi
  done
  cp /etc/nginx/fp/tls.conf /etc/nginx/conf.d/default.conf
  # redirect target for plain-HTTP requests: the configured public URL (keeps non-standard ports)
  if [ -n "${FP_PUBLIC_URL:-}" ]; then
    if ! echo "$FP_PUBLIC_URL" | grep -Eq '^https://[A-Za-z0-9.-]+(:[0-9]{1,5})?$'; then
      echo "FP_PUBLIC_URL must look like https://host[:port] (no path), got: $FP_PUBLIC_URL" >&2
      exit 1
    fi
    sed -i "s|return 301 https://\$host\$request_uri;|return 301 ${FP_PUBLIC_URL}\$request_uri;|" /etc/nginx/conf.d/default.conf
  fi
  echo "FactoryPulse: HTTPS enabled"
else
  cp /etc/nginx/fp/http.conf /etc/nginx/conf.d/default.conf
  echo "FactoryPulse: plain HTTP (single workstation / demo only)"
fi
nginx -t -q
exec nginx -g 'daemon off;'
