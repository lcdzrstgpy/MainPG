#!/bin/sh
set -eu

backup_dir="/var/backups/mainpg-update-admin/$(date -u +%Y%m%dT%H%M%SZ)-website-channels"
install -d -m 0700 "$backup_dir"
cp -a /opt/mainpg-update-admin/app.py "$backup_dir/app.py"
cp -a /opt/mainpg-update-admin/static/app.js "$backup_dir/app.js"
cp -a /opt/mainpg-update-admin/static/app.css "$backup_dir/app.css"
cp -a /opt/mainpg-update-admin/static/index.html "$backup_dir/index.html"
cp -a /etc/systemd/system/mainpg-update-admin.service "$backup_dir/mainpg-update-admin.service"
cp -a /etc/mainpg-update-admin.env "$backup_dir/mainpg-update-admin.env"

if ! grep -q '^UPDATE_INTERNAL_DOWNLOAD_PATH=' /etc/mainpg-update-admin.env; then
    printf '\nUPDATE_INTERNAL_DOWNLOAD_PATH=/var/www/html/internal-downloads/MainPG-Internal-Setup.exe\n' >> /etc/mainpg-update-admin.env
fi
if ! grep -q '^UPDATE_PUBLIC_DOWNLOAD_PATH=' /etc/mainpg-update-admin.env; then
    printf 'UPDATE_PUBLIC_DOWNLOAD_PATH=/var/www/html/downloads/MainPG-Setup.exe\n' >> /etc/mainpg-update-admin.env
fi
if ! grep -q '^UPDATE_PLUGIN_PUBLISH_DIR=' /etc/mainpg-update-admin.env; then
    printf 'UPDATE_PLUGIN_PUBLISH_DIR=/var/www/html/downloads/plugin\n' >> /etc/mainpg-update-admin.env
    printf 'UPDATE_PLUGIN_PUBLIC_BASE_URL=https://workbench.haocoming.top/downloads\n' >> /etc/mainpg-update-admin.env
    printf 'UPDATE_PLUGIN_DOWNLOAD_DIR=/var/www/html/downloads\n' >> /etc/mainpg-update-admin.env
    printf 'UPDATE_PLUGIN_FILENAME_PREFIX=W-H-browser-extension\n' >> /etc/mainpg-update-admin.env
    printf 'UPDATE_PLUGIN_MAX_UPLOAD_BYTES=67108864\n' >> /etc/mainpg-update-admin.env
    printf 'UPDATE_PLUGIN_UPDATE_WEBSITE=1\n' >> /etc/mainpg-update-admin.env
fi

install -d -o root -g www-data -m 0775 /var/www/html/downloads/plugin
setfacl -m u:mainpg-update:rwx /var/www/html/internal-downloads /var/www/html/downloads /var/www/html/downloads/plugin
setfacl -d -m u:mainpg-update:rwx /var/www/html/downloads/plugin
install -o root -g root -m 0644 /tmp/app.py /opt/mainpg-update-admin/app.py
install -o root -g root -m 0644 /tmp/app.js /opt/mainpg-update-admin/static/app.js
install -o root -g root -m 0644 /tmp/app.css /opt/mainpg-update-admin/static/app.css
install -o root -g root -m 0644 /tmp/index.html /opt/mainpg-update-admin/static/index.html
install -o root -g root -m 0644 /tmp/mainpg-update-admin.service /etc/systemd/system/mainpg-update-admin.service

systemctl daemon-reload
systemctl restart mainpg-update-admin

attempt=0
while [ "$attempt" -lt 20 ]; do
    if curl -fsS http://127.0.0.1:8013/api/health; then
        printf '\n'
        break
    fi
    attempt=$((attempt + 1))
    sleep 1
done
if [ "$attempt" -ge 20 ]; then
    systemctl status mainpg-update-admin --no-pager
    exit 1
fi

systemctl is-active mainpg-update-admin
printf 'backup=%s\n' "$backup_dir"
rm -f /tmp/app.py /tmp/app.js /tmp/app.css /tmp/index.html /tmp/mainpg-update-admin.service
