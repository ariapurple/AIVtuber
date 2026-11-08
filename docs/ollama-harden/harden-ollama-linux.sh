#!/usr/bin/env bash
# Harden Ollama on Linux: ufw IP whitelist for TCP 11434
# Run as root on the Ollama PC.
set -euo pipefail
PORT=11434
ALLOW=(
  "127.0.0.1"
  "192.168.50.65"
  "203.86.236.178"
)

mkdir -p /etc/systemd/system/ollama.service.d
cat >/etc/systemd/system/ollama.service.d/override.conf <<EOF
[Service]
Environment="OLLAMA_HOST=0.0.0.0:${PORT}"
EOF
systemctl daemon-reload
systemctl restart ollama || true

if command -v ufw >/dev/null; then
  ufw allow from 127.0.0.1 to any port "$PORT" proto tcp || true
  for ip in "${ALLOW[@]}"; do
    [[ "$ip" == "127.0.0.1" ]] && continue
    ufw allow from "$ip" to any port "$PORT" proto tcp
    echo "ALLOW $ip"
  done
  # deny others explicitly (ufw default deny incoming is ideal)
  ufw deny "$PORT"/tcp || true
  ufw status | grep "$PORT" || true
elif command -v firewall-cmd >/dev/null; then
  firewall-cmd --permanent --remove-port=${PORT}/tcp || true
  for ip in "${ALLOW[@]}"; do
    firewall-cmd --permanent --add-rich-rule="rule family=ipv4 source address=$ip port port=$PORT protocol=tcp accept"
  done
  firewall-cmd --reload
else
  echo "No ufw/firewalld; adding iptables accepts then drop"
  for ip in "${ALLOW[@]}"; do
    iptables -I INPUT -p tcp -s "$ip" --dport "$PORT" -j ACCEPT
  done
  iptables -A INPUT -p tcp --dport "$PORT" -j DROP
fi

echo "Done. Test: curl http://127.0.0.1:${PORT}/api/tags"
