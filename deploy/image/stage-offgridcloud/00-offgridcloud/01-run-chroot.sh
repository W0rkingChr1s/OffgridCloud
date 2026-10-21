#!/bin/bash -e
# pi-gen (inside the image): install OffgridCloud in image-build mode — no
# secrets, nothing started, first-boot setup wizard armed (deploy/setup/).
apt-get update
apt-get install -y --no-install-recommends git curl ca-certificates unzip  # unzip: rclone installer

# Node.js 22 like bootstrap.sh: not needed to run, but one-click updates
# rebuild the frontend on the box.
if ! command -v node >/dev/null 2>&1; then
  curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
  apt-get install -y nodejs
fi

git config --global --add safe.directory /opt/offgridcloud/src

OGC_IMAGE_BUILD=1 \
OGC_NONINTERACTIVE=1 \
OGC_WITH_HTTPS=1 \
OGC_WITH_AP_FALLBACK=1 \
OGC_WITH_FFMPEG=1 \
OGC_WITH_SPEEDTEST=1 \
OGC_WITH_VPN=0 \
OGC_WITH_KIOSK=0 \
OGC_WITH_BLE=1 \
OGC_WITH_NETMERGE=1 \
  bash /opt/offgridcloud/src/deploy/install.sh

# `enable --now` inside a chroot only half-works; make sure the units are on.
systemctl enable NetworkManager avahi-daemon caddy offgridcloud-mdns-alias offgridcloud-netwatch offgridcloud \
  bluetooth offgridcloud-ble offgridcloud-netmerge

apt-get clean
rm -rf /var/lib/apt/lists/* /root/.npm /root/.cache
