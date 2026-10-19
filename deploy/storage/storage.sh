#!/usr/bin/env bash
# Privileged storage helper for the OffgridCloud "Speicher" page (needs root).
#
# The app runs unprivileged and calls this via a NOPASSWD sudoers rule (wired by
# deploy/install.sh). Every argument is re-validated here: the API's own checks
# are a convenience, this script is the actual safety boundary. It refuses any
# disk that has a filesystem mounted outside /media, /mnt or /run/media (i.e.
# the OS disk) and any disk holding the OffgridCloud data/buffer directory.
#
# Usage (as root):
#   storage.sh mount   /dev/sdX1                 -> prints the mountpoint
#   storage.sh unmount /dev/sdX1
#   storage.sh eject   /dev/sdX                   (unmount all + power off USB)
#   storage.sh format  /dev/sdX|/dev/sdX1 exfat|vfat|ext4 [LABEL]
#
# Formatting a whole disk (/dev/sdX) wipes it and writes one partition spanning
# the disk; formatting a partition keeps the partition table.
set -euo pipefail

MOUNT_BASE="/media/offgridcloud"
PREFIX="${OGC_PREFIX:-/opt/offgridcloud}"
DEV_RE='^/dev/(sd[a-z]+[0-9]*|vd[a-z]+[0-9]*|xvd[a-z]+[0-9]*|mmcblk[0-9]+(p[0-9]+)?|nvme[0-9]+n[0-9]+(p[0-9]+)?)$'

die() { echo "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "storage.sh muss als root laufen."

# The service account we mount for (sudo sets these); falls back to root.
OWNER_UID="${SUDO_UID:-0}"
OWNER_GID="${SUDO_GID:-0}"

check_dev() {
  local dev="$1"
  [[ "$dev" =~ $DEV_RE ]] || die "Ungültiges Gerät: $dev"
  [[ -b "$dev" ]] || die "Kein Blockgerät: $dev"
}

# Parent disk of a device (the device itself when it already is a disk).
disk_of() {
  local dev="$1" pk
  pk="$(lsblk -ndo PKNAME "$dev" 2>/dev/null | head -1 || true)"
  if [[ -n "$pk" ]]; then echo "/dev/$pk"; else echo "$dev"; fi
}

# Filesystem type / label: lsblk reads udev's cache, blkid probes directly
# (covers a fresh format before udev has caught up, or hosts without udev).
fs_of() { local v; v="$(lsblk -ndo FSTYPE "$1" 2>/dev/null | head -1)"; echo "${v:-$(blkid -o value -s TYPE "$1" 2>/dev/null || true)}"; }
label_of() { local v; v="$(lsblk -ndo LABEL "$1" 2>/dev/null | head -1)"; echo "${v:-$(blkid -o value -s LABEL "$1" 2>/dev/null || true)}"; }

# Directories that must never live on a managed disk.
protected_dirs() {
  echo "$PREFIX"
  local env="$PREFIX/.env" v
  if [[ -r "$env" ]]; then
    for key in OGC_BUFFER_DIR OGC_DATA_DIR; do
      v="$(sed -n "s/^${key}=//p" "$env" | tail -1 | tr -d '"'"'")"
      [[ -n "$v" ]] && echo "$v"
    done
  fi
}

check_disk_safe() {
  local disk="$1" mp dir real
  while IFS= read -r mp; do
    [[ -z "$mp" ]] && continue
    case "$mp" in
      /media/*|/mnt/*|/run/media/*) ;;
      *) die "Geschützt: $disk enthält ein Systemdateisystem ($mp)." ;;
    esac
    while IFS= read -r dir; do
      [[ -z "$dir" ]] && continue
      real="$(realpath -m "$dir")"
      if [[ "$real" == "$mp" || "$real" == "$mp"/* ]]; then
        die "Geschützt: $disk enthält den OffgridCloud-Puffer ($mp)."
      fi
    done < <(protected_dirs)
  done < <(lsblk -nlpo MOUNTPOINT "$disk" 2>/dev/null)
  if [[ "$(lsblk -nlpo FSTYPE "$disk" 2>/dev/null)" == *swap* ]]; then
    die "Geschützt: $disk enthält Swap."
  fi
}

unmount_all() {
  local disk="$1" dev mp
  while read -r dev mp; do
    [[ -z "${mp:-}" ]] && continue
    umount "$dev" || die "Aushängen von $dev fehlgeschlagen (Datei noch geöffnet?)."
    [[ "$mp" == "$MOUNT_BASE"/* ]] && rmdir "$mp" 2>/dev/null || true
  done < <(lsblk -nlpo NAME,MOUNTPOINT "$disk" 2>/dev/null)
}

do_mount() {
  local dev="$1" fstype label name mp opts
  check_dev "$dev"
  check_disk_safe "$(disk_of "$dev")"
  mp="$(lsblk -ndo MOUNTPOINT "$dev" | head -1)"
  if [[ -n "$mp" ]]; then echo "$mp"; return; fi
  fstype="$(fs_of "$dev")"
  [[ -n "$fstype" ]] || die "Kein Dateisystem auf $dev."
  label="$(label_of "$dev")"
  name="$(printf '%s' "${label:-$(basename "$dev")}" | tr -c 'A-Za-z0-9_.-' '_' | cut -c1-40)"
  mp="$MOUNT_BASE/$name"
  if mountpoint -q "$mp" 2>/dev/null || [[ -e "$mp" && -n "$(ls -A "$mp" 2>/dev/null)" ]]; then
    mp="$MOUNT_BASE/${name}-$(basename "$dev")"
  fi
  mkdir -p "$mp"
  opts="nosuid,nodev,noexec"
  case "$fstype" in
    vfat|exfat|ntfs|ntfs3) opts="$opts,uid=$OWNER_UID,gid=$OWNER_GID,umask=0022" ;;
  esac
  local err
  if ! err="$(mount -o "$opts" "$dev" "$mp" 2>&1)"; then
    rmdir "$mp" 2>/dev/null || true
    die "Einhängen fehlgeschlagen: $(printf '%s' "$err" | head -1)"
  fi
  echo "$mp"
}

do_unmount() {
  local dev="$1" mp
  check_dev "$dev"
  check_disk_safe "$(disk_of "$dev")"
  mp="$(lsblk -ndo MOUNTPOINT "$dev" | head -1)"
  [[ -z "$mp" ]] && return 0
  sync
  umount "$dev" || die "Aushängen fehlgeschlagen (Datei noch geöffnet?)."
  [[ "$mp" == "$MOUNT_BASE"/* ]] && rmdir "$mp" 2>/dev/null || true
}

do_eject() {
  local disk="$1" name
  check_dev "$disk"
  [[ "$(disk_of "$disk")" == "$disk" ]] || die "Auswerfen nur für ganze Laufwerke."
  check_disk_safe "$disk"
  sync
  unmount_all "$disk"
  name="$(basename "$disk")"
  # USB/SCSI devices can be detached cleanly; SD readers just stay unmounted.
  if [[ -w "/sys/block/$name/device/delete" ]]; then
    echo 1 > "/sys/block/$name/device/delete"
  fi
}

do_format() {
  local dev="$1" fstype="$2" label="${3:-}" disk part pttype max pkg
  check_dev "$dev"
  case "$fstype" in
    exfat) max=15; pttype=7;  pkg=exfatprogs ;;
    vfat)  max=11; pttype=c;  pkg=dosfstools ;;
    ext4)  max=16; pttype=83; pkg=e2fsprogs ;;
    *) die "Dateisystem nicht unterstützt: $fstype" ;;
  esac
  [[ "$label" =~ ^[A-Za-z0-9_-]*$ ]] && (( ${#label} <= max )) || die "Ungültige Bezeichnung."
  disk="$(disk_of "$dev")"
  check_disk_safe "$disk"
  command -v "mkfs.$fstype" >/dev/null 2>&1 || die "mkfs.$fstype fehlt (Paket $pkg installieren)."
  [[ "$(lsblk -ndo RO "$disk" | tr -d ' ')" == "1" ]] && die "Laufwerk ist schreibgeschützt."
  sync
  unmount_all "$disk"

  part="$dev"
  if [[ "$dev" == "$disk" ]]; then
    wipefs -a -q "$disk"
    printf 'label: dos\n,,%s\n' "$pttype" | sfdisk -q "$disk" >/dev/null
    command -v partprobe >/dev/null 2>&1 && partprobe "$disk" 2>/dev/null || true
    command -v udevadm >/dev/null 2>&1 && udevadm settle 2>/dev/null || true
    if [[ "$disk" =~ [0-9]$ ]]; then part="${disk}p1"; else part="${disk}1"; fi
    for _ in 1 2 3 4 5 6 7 8 9 10; do [[ -b "$part" ]] && break; sleep 0.5; done
    [[ -b "$part" ]] || die "Neue Partition $part nicht gefunden."
  else
    wipefs -a -q "$part"
  fi

  case "$fstype" in
    exfat) mkfs.exfat ${label:+-L "$label"} "$part" >/dev/null ;;
    vfat)  mkfs.vfat -F 32 ${label:+-n "${label^^}"} "$part" >/dev/null ;;
    ext4)
      mkfs.ext4 -F -q -m 0 ${label:+-L "$label"} "$part"
      # Hand the fresh filesystem's root to the service account so it can write.
      local tmp; tmp="$(mktemp -d)"
      mount "$part" "$tmp" && chown "$OWNER_UID:$OWNER_GID" "$tmp" && umount "$tmp"
      rmdir "$tmp"
      ;;
  esac
  sync
  echo "$part"
}

action="${1:-}"
shift || true
case "$action" in
  mount)   [[ $# -eq 1 ]] || die "Usage: storage.sh mount DEV";   do_mount "$1" ;;
  unmount) [[ $# -eq 1 ]] || die "Usage: storage.sh unmount DEV"; do_unmount "$1" ;;
  eject)   [[ $# -eq 1 ]] || die "Usage: storage.sh eject DISK";  do_eject "$1" ;;
  format)  [[ $# -ge 2 && $# -le 3 ]] || die "Usage: storage.sh format DEV FSTYPE [LABEL]"
           do_format "$1" "$2" "${3:-}" ;;
  *) die "Usage: storage.sh mount|unmount|eject|format …" ;;
esac
