#!/usr/bin/env bash
# One-time administrator setup. Normal app users do not need SSH or Docker.
set -euo pipefail
if [[ ${EUID} -ne 0 || $# -ne 2 || $2 != --agree-tos ]]; then
  echo 'Usage: sudo bash enable_execution_https.sh PUBLIC_IPV4 --agree-tos' >&2
  echo "Open inbound TCP 80/443 in the cloud security group first. This accepts the Let's Encrypt subscriber agreement." >&2
  exit 2
fi
public_ip=$1
IFS=. read -r -a octets <<< "$public_ip"
[[ ${#octets[@]} == 4 ]] || exit 2
for octet in "${octets[@]}"; do
  [[ $octet =~ ^(0|[1-9][0-9]{0,2})$ ]] && (( 10#$octet <= 255 )) || exit 2
done
# Refuse replacing another site or certificate name accidentally.
if [[ -s /etc/codefix-https-ip && $(cat /etc/codefix-https-ip) != "$public_ip" ]]; then
  echo 'An existing CodeFix HTTPS IP differs; migrate it explicitly.' >&2; exit 1
fi
docker image inspect python:3.14-slim >/dev/null
curl --silent --max-time 5 --output /dev/null http://127.0.0.1:8780/v1/health
# Health is authenticated: transport reachability, not a successful qualification.
dnf install -y nginx
if command -v firewall-cmd >/dev/null && systemctl is-active --quiet firewalld; then
  firewall-cmd --add-service=http --add-service=https
  firewall-cmd --permanent --add-service=http --add-service=https
fi
install -d -m 755 /var/lib/codefix-acme /opt/codefix-certbot
install -d -m 700 /etc/codefix-letsencrypt /var/lib/codefix-letsencrypt /var/log/codefix-letsencrypt
# Reuse the already imported Python image; no Docker Hub pull is needed.
if ! docker run --rm --pull=never --network none -e PYTHONPATH=/packages \
  --mount type=bind,src=/opt/codefix-certbot,dst=/packages,readonly \
  python:3.14-slim python -c 'from certbot.main import main; from importlib.metadata import version; v=tuple(map(int,version("certbot").split(".")[:2])); assert (5,4) <= v < (7,0)' ; then
docker run --rm --pull=never --network host \
  --mount type=bind,src=/opt/codefix-certbot,dst=/packages \
  python:3.14-slim python -m pip --isolated install --upgrade \
    --index-url https://pypi.org/simple --timeout 60 --retries 3 \
    --only-binary=cryptography,cffi --target /packages 'certbot>=5.4,<7'
fi
cat > /usr/local/sbin/codefix-certbot <<'CERTBOT'
#!/usr/bin/env bash
set -euo pipefail
exec docker run --rm --pull=never --network host \
  -e PYTHONPATH=/packages \
  --mount type=bind,src=/opt/codefix-certbot,dst=/packages,readonly \
  --mount type=bind,src=/etc/codefix-letsencrypt,dst=/etc/letsencrypt \
  --mount type=bind,src=/var/lib/codefix-letsencrypt,dst=/var/lib/letsencrypt \
  --mount type=bind,src=/var/log/codefix-letsencrypt,dst=/var/log/letsencrypt \
  --mount type=bind,src=/var/lib/codefix-acme,dst=/acme \
  python:3.14-slim python -c 'from certbot.main import main; raise SystemExit(main())' "$@"
CERTBOT
chmod 755 /usr/local/sbin/codefix-certbot
/usr/local/sbin/codefix-certbot --version
config=/etc/nginx/conf.d/codefix-execution.conf
if [[ -e $config ]]; then cp -p -- "$config" "$config.bak-$(date +%s)"; fi
cat > "$config" <<NGINX
server {
    listen 80;
    server_name $public_ip;
    location ^~ /.well-known/acme-challenge/ { root /var/lib/codefix-acme; }
    location / { return 404; }
}
NGINX
# Permit Nginx -> localhost upstream on SELinux-enabled Alibaba Cloud Linux.
if command -v getenforce >/dev/null && [[ $(getenforce) == Enforcing ]]; then
  setsebool -P httpd_can_network_connect 1
fi
# A newly created directory may be unlabeled: -t alone cannot create a full context.
# Do not try to label files when SELinux is disabled.
if command -v getenforce >/dev/null && [[ $(getenforce) != Disabled ]]; then
  chcon -R system_u:object_r:httpd_sys_content_t:s0 /var/lib/codefix-acme
fi
nginx -t
systemctl enable --now nginx
systemctl reload nginx
/usr/local/sbin/codefix-certbot certonly --non-interactive --agree-tos --register-unsafely-without-email \
  --preferred-profile shortlived --webroot --webroot-path /acme \
  --cert-name codefix-execution --ip-address "$public_ip"
cat >> "$config" <<NGINX
server {
    listen 443 ssl;
    server_name $public_ip;
    ssl_certificate /etc/codefix-letsencrypt/live/codefix-execution/fullchain.pem;
    ssl_certificate_key /etc/codefix-letsencrypt/live/codefix-execution/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    client_max_body_size 2m;
    access_log off;
    location / {
        proxy_pass http://127.0.0.1:8780;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header Authorization \$http_authorization;
        proxy_read_timeout 90s;
        proxy_send_timeout 90s;
    }
}
NGINX
if command -v restorecon >/dev/null; then restorecon -R /etc/codefix-letsencrypt; fi
nginx -t
systemctl reload nginx
cat > /usr/local/sbin/codefix-renew-https <<'RENEW'
#!/usr/bin/env bash
set -euo pipefail
/usr/local/sbin/codefix-certbot renew --quiet
nginx -t
systemctl reload nginx
RENEW
chmod 755 /usr/local/sbin/codefix-renew-https
cat > /etc/systemd/system/codefix-renew-https.service <<'UNIT'
[Unit]
Description=Renew CodeFix IP HTTPS certificate
After=docker.service nginx.service network-online.target
Requires=docker.service
[Service]
Type=oneshot
ExecStart=/usr/local/sbin/codefix-renew-https
UNIT
cat > /etc/systemd/system/codefix-renew-https.timer <<'UNIT'
[Unit]
Description=Check CodeFix short-lived certificate renewal twice daily
[Timer]
OnBootSec=5min
OnUnitActiveSec=12h
RandomizedDelaySec=15min
Persistent=true
[Install]
WantedBy=timers.target
UNIT
printf '%s\n' "$public_ip" > /etc/codefix-https-ip
systemctl daemon-reload
systemctl enable --now codefix-renew-https.timer
status=$(curl --silent --show-error --max-time 10 --resolve "$public_ip:443:127.0.0.1" --output /dev/null --write-out '%{http_code}' "https://$public_ip/v1/health")
[[ $status == 401 ]] || { echo "Unexpected unauthenticated health status: $status" >&2; exit 1; }
printf 'HTTPS ready: https://%s\nUnauthenticated requests rejected (401); app will check the access code and real isolation probe.\n' "$public_ip"
