#!/usr/bin/env bash
# Генерация локального CA + серверного и клиентского сертификатов для mTLS Mosquitto.
# Файлы кладутся в infra/certs/ (в .gitignore — не коммитим).
#
# Серверный сертификат — с SAN (DNS:localhost, IP:127.0.0.1): без SAN современные
# TLS-клиенты (Python ssl) отвергают сертификат при проверке имени хоста.
# Кастомный хост Hub'а: SERVER_CN=hub.lan SERVER_SAN="DNS:hub.lan,IP:192.168.1.10"
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/certs"
mkdir -p "$DIR"
cd "$DIR"

DAYS="${DAYS:-3650}"
SERVER_CN="${SERVER_CN:-localhost}"
SERVER_SAN="${SERVER_SAN:-DNS:localhost,IP:127.0.0.1}"

echo "→ CA"
openssl genrsa -out ca.key 4096
openssl req -new -x509 -days "$DAYS" -key ca.key -out ca.crt -subj "/CN=Friday-CA"

gen_cert() {
  local name="$1" cn="$2" san="${3:-}"
  echo "→ $name (CN=$cn${san:+, SAN=$san})"
  openssl genrsa -out "$name.key" 4096
  openssl req -new -key "$name.key" -out "$name.csr" -subj "/CN=$cn"
  if [[ -n "$san" ]]; then
    openssl x509 -req -in "$name.csr" -CA ca.crt -CAkey ca.key -CAcreateserial \
      -out "$name.crt" -days "$DAYS" -extfile <(printf "subjectAltName=%s" "$san")
  else
    openssl x509 -req -in "$name.csr" -CA ca.crt -CAkey ca.key -CAcreateserial \
      -out "$name.crt" -days "$DAYS"
  fi
  rm -f "$name.csr"
}

gen_cert server "$SERVER_CN" "$SERVER_SAN"
gen_cert client "friday-client"

# Серверный ключ читает пользователь mosquitto внутри контейнера (uid 1883) —
# монтируется read-only, ключи локальные dev-сертификаты (каталог в .gitignore).
chmod 644 server.key server.crt ca.crt client.crt
chmod 600 ca.key client.key

echo "✓ Сертификаты в $DIR (ca.crt, server.*, client.*)"
echo "  Дальше: make broker (перезапуск с TLS-листенером 8883), в .env:"
echo "    FRIDAY_TLS=true"
echo "    FRIDAY_BROKER_PORT=8883"
echo "    FRIDAY_TLS_CA=infra/certs/ca.crt"
echo "    FRIDAY_TLS_CERT=infra/certs/client.crt"
echo "    FRIDAY_TLS_KEY=infra/certs/client.key"
