# Portable storage isolation

The portable profile selected by `portable.flag` retains its existing layout:
`settings.json`, `config/`, `data/`, `cache/` and `logs/` beside the launcher.
An explicit `--profile-root` continues to take precedence and keeps its own
existing layout. This change does not migrate or discard saved settings.

When a selected portable directory is unavailable, path resolution now fails
instead of silently selecting host temporary storage. Existing symbolic links
that resolve outside the selected root are rejected before directory creation
or settings access, including optional component and download directories.
This is a path-selection boundary, not protection against a concurrent actor
replacing directories after validation or removal of the USB device mid-write.

## Packaged launch locations

Storage discovery is separate from executable and resource lookup:

| Launch form | Location of `portable.flag` and portable storage |
| --- | --- |
| Source launcher or frozen onedir | Beside the launcher/executable |
| Mounted type 2 AppImage | Beside the external `.AppImage` file |
| Extracted AppDir launched through `AppRun` | Beside `AppRun` |
| Standard macOS `.app/Contents/MacOS` executable | Beside the `.app` bundle |

Explicit profiles and inherited portable identity take precedence, including
after mutation-runtime re-execution. AppImage metadata is checked against the
running executable and its AppRun layout. Invalid supplied metadata, escaped
paths, and symbolic-link or non-regular markers fail before native fallback.
These are layout checks, not artifact authentication. A valid ordinary package
without an external flag keeps native profile behavior.

## Toolkit and library state

Qt's file dialog saves `QtProject` settings even on cancellation. Its native
settings constructor ignores `setDefaultFormat`; redirecting `NativeFormat`
paths does not work on Windows/macOS. Isolated profiles therefore use a
read-only application-owned browser without QFileDialog or QSettings. Ordinary
profiles keep the native picker. Both file and directory selection support
typed paths, navigation and cancellation; file selection enforces its filter.

Before application imports, isolated startup selects XDG, APPDATA/LOCALAPPDATA,
temporary-file and supported shader-cache environment paths under the profile.
Python's cached temporary directory is reset as well. Qt shader disk caching is
disabled before constructing the application. These changes affect only the
application process and descendants. They do not change the user's desktop
environment or override every platform API used by an operating system.

The GUI and reader/mutation workers keep the same profile identity. Existing
logging/settings layouts and explicit-profile precedence are preserved.
Opt-in readiness receipts also accept the selected portable profile; ordinary
profiles still refuse them. Receipt paths must remain inside the selected root,
and exclusive same-directory staging avoids following a pre-existing temporary
file link. Like the other path checks, this does not prevent a concurrent actor
replacing parent directories between validation and writing.

## Verification

Run the dependency-free storage regression suite from the repository root:

```sh
PYTHONPATH=src QT_QPA_PLATFORM=offscreen python3 -m unittest discover -s tests -p test_portable_isolation.py -v
```

The broader portable suites include actual subprocess launches and Qt dialogs:

```sh
PYTHONPATH=src QT_QPA_PLATFORM=offscreen python3 -m unittest discover -s tests -p 'test_portable*.py' -v
PYTHONPATH=src QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_packaging.py -k 'appdir or apprun'
```

Tests seed and compare disposable host-profile files around open, cancel,
directory-navigation and filter-rejection journeys. The AppRun fixture executes
the generated shell launcher with a simulated frozen executable. Native IPC
support is required for the existing mutation-service subprocess test.

The packaging-marked test builds the frozen application and checks decoded-image
readiness and graceful shutdown through explicit and portable onedir/bundle launches.
On Linux it also launches the real extracted AppRun without `--profile-root`.
These journeys compare the embedded source identity and disposable host-profile
files. They use Qt's offscreen backend and do not mount an actual AppImage.

`build_desktop.py --portable` additionally produces a fresh native folder and
archive, with a regular `portable.flag` and exact runtime/source manifest.
Windows ZIP rejects runtime symlinks; Linux/macOS tar.gz preserves internal
relative runtime symlinks and refuses escaping links. Existing output/profile
state is never silently repackaged. The macOS spec builds an actual BUNDLE with
its marker outside `Contents`; its ad-hoc signature must succeed, without
credentials or a claim of notarization. Native Windows/macOS execution is still
required to validate those recipes.

The portable build embeds `hooks/rthook_portable_profile.py` before application
imports. Its process-local policy makes a missing marker an error instead of
selecting native paths; no removable policy file or environment variable is
needed to enforce it. Explicit and validated inherited profiles retain priority.
This also refuses a macOS bundle relocated without its external marker; it does
not try to detect translocation or bypass Gatekeeper. Ordinary builds/source
launches are unaffected. The real Linux frozen test covers missing-marker
refusal before host-profile writes, followed by explicit and portable success.
Windows/macOS refusal-dialog behavior remains a native interactive gate.

`scripts/qualify_portable_package.py` verifies a pristine package against an
independently supplied build identity, copies it into a new evidence directory,
then launches before and after moving the folder. It retains actual decoded
frame/readiness receipts, process logs, graceful exit, original image/runtime
hashes and disposable host-file comparisons. It refuses a mismatched OS or
architecture. Native shell launch, registry/OS-managed state, network observation
and actual removable-media behavior are explicit separate gates. Linux tests
that construct macOS/Windows layouts and archives are source-level simulations.
Named package/output paths and existing ancestors are checked for symlinks and
junctions before canonicalization, creation or launch. Concurrent replacement
by the same user is outside this trusted developer-tool boundary.

Windows and macOS both refuse mutations centrally before admission, metadata
writes, runtime pinning or retention cleanup. Existing history and uncertain
operations are retained. macOS had previously been admitted on the presence of
AF_UNIX even though `.app` pinning and metadata/durability were unqualified;
the explicit refusal removes reliance on an incidental bundle-path failure.
Image review/settings are an intermediate platform capability, not completion
of the portable sorting product.

The suite uses disposable files, isolated environment paths and actual
filesystem symlinks. Platform selectors exercise path decisions; they do not
constitute native Windows or macOS qualification.

## Remaining portable-release work

- Run the Windows/macOS native build and frozen launch checks; neither a build
  recipe nor a Linux archive-layout fixture clears this gate. Qualify signing,
  distribution, native shell launch and the intended OS/architecture matrix.
- Qualify supported native-library caches and OS-managed state; source path
  selectors and a Linux offscreen dialog do not prove native OS isolation.
- Implement and qualify Windows locking, process communication, durable
  mutations and Undo before removing its mutation restriction.
- Implement bundle-aware macOS mutation-runtime pinning, metadata preservation
  and power-interruption durability before removing its mutation restriction.
- Distinguish portable persistent state from necessary OS-managed transient
  resources such as local sockets; audit all worker and optional-component writes.
- Build and run OS/architecture-specific packages on real supported platforms,
  including removable-media disconnection, read-only media, drive-letter changes,
  concurrent launches and recovery of interrupted operations.

The Linux fixture and offscreen checks do not establish physical USB, native
desktop, mounted AppImage or native Windows/macOS acceptance. AI tagging,
animated images, RAW/HEIF and duplicate handling remain part of the full product
scope.

## Filesystem prerequisites before source claims

The Linux mutation backend now checks each operation's actual participating
parents before renaming an original. Linux or USB transport alone does not
establish filesystem support. Move, configured trash, Undo, metadata, explicit
recovery and explicit retention cleanup use the same prerequisite boundary.
Copy checks its destination and journal parent without requiring writes in the
read-only source parent. No filesystem capability result is cached between
requests or inferred from another directory on the same device.

Only constant disposable bytes enter the initial probe. It checks exclusive
creation, observed owner/private modes, file and directory synchronization,
rename, hardlink publication, and refusal to replace an occupied name. Every
required parent is observed; identities are rechecked after preparation and
before source claims. Actual image/sidecar staging and exact visible-attribute,
mode and nanosecond timestamp roundtrips complete before original claims.
The direct metadata helper likewise prepares the whole file set in a verified
private directory before replacing originals. Its existing rollback backup
retention remains mandatory if restoration fails.

Missing extended-attribute APIs, unsupported enumeration, failed reads and a
successful empty enumeration are distinct. Only the last establishes an empty
set of attributes visible to this process. Unknown metadata causes refusal;
it is never silently exported as an empty set. SQLite connections must report
actual WAL activation and FULL synchronization; requesting those settings alone
is insufficient. Existing journal files must round-trip their private mode.
Windows/macOS restrictions still precede every Linux mutation probe; inspecting
existing history does not authorize mutations on either platform.

A predictable prerequisite failure leaves original names, inodes and bytes in
place. Later races, permission changes or device failure can still interrupt a
transaction after its first claim; existing verified rollback and recovery
rules remain in force. Recovery preflight runs before the new request takes
ownership of any old manifest, so refusal cannot trigger automatic rollback of
previously retained material. Retention cleanup remains explicitly requested
and refuses unsupported storage before deleting retained originals.

Successful probes remove only their verified owned entries. An unknown entry,
changed identity, failed cleanup or synchronization leaves the remaining probe
inspectable at the reported local path and refuses the operation. There is no
recursive cleanup, startup deletion of probes, filesystem reformatting, silent
metadata reduction, or retry through a weaker backend.

These are observations of current kernel calls, not power-loss or device-flush
proof. They do not qualify exFAT, NFS/SMB/FUSE, every USB filesystem, another
owner's inode policy, inaccessible security namespaces, ownership/group changes,
immutable flags, multi-host locking, unplug/remount behavior or malicious
concurrent directory replacement. Visible Linux xattrs/ACL representation,
mode and mtime are the observed metadata scope. Physical removable-media tests
and qualified Windows/macOS mutation backends remain separate release gates.
