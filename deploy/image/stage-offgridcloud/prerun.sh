#!/bin/bash -e
# pi-gen: continue from the previous stage's rootfs (stage2 = Raspberry Pi OS Lite).
if [ ! -d "${ROOTFS_DIR}" ]; then
  copy_previous
fi
