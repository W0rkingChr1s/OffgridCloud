"""Fire-and-forget system power actions: restart the service, reboot, shut down.

These are deliberately dumb: launch an operator-configured privileged command
detached and return immediately. Restarting the service or powering the box off
kills this very process, so we never wait on the child — we start it in its own
session, after a short delay, so the HTTP response can flush before everything
goes down. Commands come from the environment (``.env``), never from user input,
and are only present when the operator opts in (see ``deploy/install.sh
--power-control``).
"""

from __future__ import annotations

import subprocess

# Give the API response time to flush to the client before the command takes the
# process (or the whole box) down.
_DEFAULT_DELAY = 1.0


def run_power_command(
    command: str, *, delay: float = _DEFAULT_DELAY, popen=subprocess.Popen
) -> None:
    """Launch ``command`` detached after ``delay`` seconds.

    Wrapped in ``sh -c 'sleep …; exec …'`` so the wait happens in the child, not
    in the request handler — the endpoint returns right away. ``command`` is an
    operator-configured value (trusted, never user input). Raises if the shell
    can't even be started.
    """
    if not command.strip():
        raise ValueError("empty power command")
    script = f"sleep {delay}; exec {command}"
    popen(
        ["sh", "-c", script],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,  # survive our own termination
    )
