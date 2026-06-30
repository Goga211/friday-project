#!/usr/bin/env bash
# Генерация локального CA + серверного и клиентского сертификатов для mTLS Mosquitto.
# Для защищённого режима. Файлы кладутся в infra/certs/ (он в .gitignore — не коммитим).
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/certs"
mkdir -p "$DIR"
cd "$DIR"

DAYS="${DAYS:-3650}"
SERVER_CN="${SERVER_CN:-localhost}"

echo "→ CA"
openssl genrsa -out ca.key 4096
openssl req -new -x509 -days "$DAYS" -key ca.key -out ca.crt -subj "/CN=Christopher-CA"

gen_cert() {
  local name="$1" cn="$2"
  echo "→ $name ($cn)"
  openssl genrsa -out "$name.key" 4096
  openssl req -new -key "$name.key" -out "$name.csr" -subj "/CN=$cn"
  openssl x509 -req -in "$name.csr" -CA ca.crt -CAkey ca.key -CAcreateserial \
    -out "$name.crt" -days "$DAYS"
  rm -f "$name.csr"
}

gen_cert server "$SERVER_CN"
gen_cert client "christopher-client"

echo "✓ Сертификаты в $DIR (ca.crt, server.*, client.*)"
echo "  Дальше: раскомментировать listener 8883 в mosquitto.conf, выставить CHRISTOPHER_TLS=true."
