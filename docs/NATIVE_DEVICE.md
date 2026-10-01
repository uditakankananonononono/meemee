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
migration for these local stores.

Account export and deletion: `export_account` adds a `local_devices` section
(pairings, devices including `secret_hex`, issued commands with envelopes and results,
and the NativeClient journal rows) inside the checksummed payload. The importer does
not restore it. The checksum is a plain unkeyed sha256: it detects accidental damage
only and does not authenticate the file against someone who can rewrite it.

Account deletion (`local_devices` step) works in this order, each part re-runnable:
revoke the owner's devices and record a progress row of hashed keys; write a purge flag
per device in the journal (a live NativeClient inserts a journal row only when no flag
newer than its device pairing exists, decided inside the same SQL statement, so a
command cannot be journaled after the purge); delete the journal rows through the
owner's device ids; delete the registry rows; sweep the journal again (also rows whose
device is no longer in the registry but was flagged by this owner's purge); then
`VACUUM` both files and truncate the WAL. Journal rows with a device id that no purge
ever flagged are not attributable to an owner and are left alone. Window ids are
bounded to 1..4294967295.

Old free pages: databases created before this change had `secure_delete` off, so rows
deleted earlier by other means can still sit in free pages of the file. They stay
there until the file is vacuumed; the purge's `VACUUM` rewrites both files and so
removes them. Copies you made yourself (backups, window screenshots) are not touched.
If a live reader blocks the vacuum or WAL truncation the step fails and is retried on
resume; it is not reported as done.

The journal keeps one small flag row per purged device: a sha256 of owner and device id
and a timestamp (no names, secrets, or command content). It is what stops late writes.

Deliberately retained after deletion: `account-deletions.sqlite3` keeps the deleted
principal id, who requested it and when (proof of deletion, crash resume), and the
tamper-evident audit log is retained. Neither holds account content, but the principal
id remains identifying.
