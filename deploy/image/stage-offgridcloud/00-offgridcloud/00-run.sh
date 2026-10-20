#!/bin/bash -e
# pi-gen (host side): drop the OffgridCloud checkout into the image as the
# bootstrap layout (/opt/offgridcloud/src), so one-click updates work later.
# The workflow (.github/workflows/image.yml) stages it under files/ — a git
# checkout with tags and a prebuilt frontend/dist.
install -d "${ROOTFS_DIR}/opt/offgridcloud"
rm -rf "${ROOTFS_DIR}/opt/offgridcloud/src"
cp -a files/offgridcloud "${ROOTFS_DIR}/opt/offgridcloud/src"
chown -R root:root "${ROOTFS_DIR}/opt/offgridcloud/src"
