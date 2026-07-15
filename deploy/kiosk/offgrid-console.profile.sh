# OffgridCloud console on SSH login. Installed by deploy/kiosk/install.sh to
# /etc/profile.d/offgrid-console.sh, and sourced by interactive login shells.
#
# It launches the dashboard so an SSH session lands on the OffgridCloud console
# instead of a bare prompt. Guards keep it out of the way:
#   * interactive shells only (scp/sftp/rsync are non-interactive → skipped),
#   * SSH sessions only (local tty logins keep their normal shell),
#   * a real terminal on stdout, and a recursion guard.
# Quitting the console (press q) drops to the normal shell — no lock-in over SSH.
case $- in
  *i*) ;;
  *) return 2>/dev/null ;;
esac
if [ -n "${SSH_CONNECTION:-}" ] && [ -t 1 ] && [ -z "${OGC_CONSOLE_ACTIVE:-}" ] \
   && command -v offgrid-console >/dev/null 2>&1; then
  OGC_CONSOLE_ACTIVE=1 offgrid-console
fi
