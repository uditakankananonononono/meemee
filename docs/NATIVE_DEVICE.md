# Native client: Windows primary target, Linux/X11 verified locally

This is real bounded native window observation and title control, not simulator
state arithmetic. It does not provide arbitrary computer operation. OSAdapter
is an open interface; WindowsAdapter uses Win32, X11Adapter uses xdotool and
ImageMagick. Mac and Wayland adapters are absent. Dell Intel Core i5 describes
hardware, not an OS; do not infer the owner's OS from it.

## Install and safe use

Install the package in Python 3.12. Linux needs X11, xdotool and ImageMagick import;
run inside the signed-in graphical session, not as root. Windows needs a normal
interactive Windows session. Window screenshots additionally require Pillow
(`pip install Pillow`); only a target window/rectangle is captured. Windows
screenshots may include occluding content. Do not target a window with secrets.
Do not grant broad admin/accessibility/desktop privileges to make a denial pass.

Use DeviceRegistry to create a one-use pairing for the owner, then pair with
NativeClient.manifest() and a unique device ID. Save the returned secret in an
owner-only file (0600 on Linux, owner ACL on Windows). Never print/commit it.
DeviceRegistry.issue(owner, device_id, capability, arguments) produces the signed
version-1 command after the owner approves the exact target and action. The
local CLI does not supply an approval UI and registry issuance itself is not
proof of approval. Capability manifest names are `window.observe` with {} and
`window.set_title` with {"title":"printable text"}. No shell capability exists.

`meemee native-device-execute OWNER DEVICE PLATFORM WINDOW_ID secret.txt envelope.json`
executes one command. PLATFORM is windows or x11. Pin WINDOW_ID to the intended
window using the OS's window inspector (xdotool on X11). IDs can be reused by the
OS after a window closes; verify the target immediately before each command.
This v1 does not persist process identity or prevent handle reuse, so do not run
unattended against a long-lived numeric handle. Commands cannot choose a new
window. The registry is a local SQLite trust boundary, not a network service.
There is no remote client polling, API device transport or cloud desktop access.

The client verifies signature, exact device, current registry command, declared
capability, 5-minute freshness, arguments and revocation. A durable journal
claims command ID and nonce before the OS operation. Duplicate attempts after
restart are refused. A crash after the effect leaves started/uncertain, never an
automatic retry: inspect the actual window and reconcile manually. The action
is not transactionally atomic with an OS call and exactly-once completion cannot
be guaranteed. Failed OS calls retain uncertainty; no success is fabricated.

## Acceptance and recovery

The Linux test boots Xvfb, creates a real xmessage window, pairs, observes its
title/geometry and screenshot, changes its title through an issued command,
reads the changed title, checks durable registry audit, rejects replay after
restart and refuses a revoked pairing without changing the window. Screenshot
is inspected directly. This proves a bounded test-app action on Linux/X11,
not Windows, the owner's Dell, a polished native app or whole-computer agency.
WindowsAdapter is UNVERIFIED and fails closed: it refuses to construct (even on
Windows) unless MEEMEE_WINDOWS_ADAPTER_UNVERIFIED_OK=1 is set, so nothing runs
there by accident. Window ids must be positive integers (a bool is rejected).
If the owner's Dell turns out to be Windows, what a real Windows run must show
before the opt-in can be dropped: (1) pair a device and run window.observe on a
Notepad window, matching title and rect; (2) window.set_title on Notepad with
readback; (3) set_title on a protected/elevated window is refused and reported,
not silently ignored; (4) a screenshot with Pillow captures the right rectangle;
(5) replay refusal after restart; (6) revoked pairing refuses. None of this has been done.
The Windows ctypes source compiles on Linux and fails explicitly off Windows;
actual Windows execution, permission denial and screenshot behavior remain
UNVERIFIED until tested on Windows. Windows SetWindowTextW may refuse cross-
process application windows; the client reports denial/readback mismatch.

Stop invoking commands to uninstall. Revoke the device in DeviceRegistry,
remove the secret file, and remove native-device.sqlite3 and devices.sqlite3
only after preserving necessary audit records. Window capture output is local
and has no automatic retention/deletion policy. No secrets are placed in command
arguments or audit results by this adapter, but titles themselves may be private.
Back up registry and journal together with workers stopped. There is no PG
migration or automatic account-delete integration for these local stores.
