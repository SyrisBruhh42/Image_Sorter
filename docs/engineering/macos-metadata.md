# macOS metadata candidate — mutation admission remains held

This slice supplies bounded Darwin metadata primitives to the existing file
transaction architecture. It does **not** enable sorting, Undo, trash or direct
metadata writing on macOS. `platform_capabilities.py` still refuses those paths
before claims or writes. Existing Linux behavior and legacy Linux provenance are
unchanged. Native fixture success is a separate result from a usable native
mutation service, portable distribution, or physical-device durability.

## Supported snapshot and restoration policy

`file_safety.fingerprint` adds `native_metadata` only on Darwin:

- `schema: 1`, `backend: darwin-acl-ext-v1` identify the exact representation.
- `acl` is base64 of Apple's **portable-endian** `acl_copy_ext` image. The adapter
  checks the magic, zero owner/group header, exact length, bounded entry count,
  supported flags and rights before `acl_copy_int`, whose API has no length
  parameter. ACL entries retain order and UUIDs; no account-name lookup or text
  normalization occurs. The fd-only API's documented source implementation
  returns ENOENT for an absent FILESEC_ACL; the descriptor's filesystem must
  first report extended-security support through `fpathconf`, so unsupported
  filesystem security is not treated as an empty ACL; that specific result is recorded as
  the portable NOACL sentinel, distinct from an empty zero-entry ACL. Restoration
  uses the documented `_FILESEC_REMOVE_ACL` sentinel. Other errors or unset errno
  remain unknown/refusal. Native serialize/deserialize and restore/readback must
  agree exactly. Deferred-inheritance and unknown ACL semantics are refused.
- `flags` supports only `UF_HIDDEN` and `UF_NODUMP`. Immutable, append-only,
  compressed, dataless, restricted and unknown flags refuse **before content
  reads**. This code never clears a source flag, hydrates cloud data or requests
  elevation. Native tests clear only flags they set on their own synthetic files.
- `uid` must be the current effective user. `gid` must be a current process group;
  restoration changes only a private stage's group if necessary and verifies it.
  Special permission bits are refused. Other-owner files are outside this slice.
- `ownership: same-user-group-preserved` records that policy explicitly.
- `creation_time: observed-not-restored` is intentional. `birthtime` is recorded
  separately at Python's available precision and checked for source identity;
  a newly staged inode gets its own creation time. Existing promises cover mode,
  modification time, bytes and observable extended attributes; this increment
  makes **no promise to preserve every native timestamp**, creation time,
  ownership identity on another machine, or hidden filesystem metadata.

`xattrs` remains the existing name-to-base64-value dictionary. Darwin uses typed
`flistxattr`, `fgetxattr`, `fsetxattr` and `fremovexattr` descriptor APIs. Names
must be valid UTF-8 and round-trip exactly; duplicate/malformed names, unavailable
observation, native errors, oversize or changing values refuse. Limits are 256
attributes, 64 KiB name-list storage, 16 MiB per value and 32 MiB total per snapshot;
these are explicit supported-case limits, not lossy truncation. Size/read retries
are bounded to three. Zero-length values have a separate non-NULL read check;
missing observation never becomes an empty dictionary. Only caller-observable
attributes can be promised by the OS API; an inaccessible attribute is a refusal,
not permission to drop it. Resource forks, FinderInfo and quarantine are retained
and verified. `com.apple.system.Security` is refused through the xattr channel;
ACLs use their separate native API.

Apple `copyfile` was considered. `COPYFILE_METADATA` includes permissions and
other stat data, and `COPYFILE_UNPACK` restores categories regardless of requested
flags. Category-specific copying from a live source does not implement restoration
from an already recorded bounded snapshot. This implementation therefore uses
native ACL serialization and bounded explicit attribute restoration, without
`COPYFILE_ALL`, broad stat copying, or source removal flags.

## Private staging, refusal and interruption

A Darwin private directory must be current-user-owned, mode 0700 and have an
observed absent/empty ACL with no inheritance flags. Nonempty inherited ACLs refuse and
leave the newly created empty probe inspectable; the user parent's ACL is never
changed. Before copying content, a newly exclusive stage must be mode 0600 with an
absent/empty ACL. Restoring source ACL/mode occurs only under a validated private parent
and on a single-link regular stage, followed by exact readback. The adapter is an
internal primitive, not an authorization boundary against arbitrary Python code;
all existing application write entry points retain their platform admission guard.

The native descriptor remains in `attribute_target`, fingerprints, retained
original provenance and Undo records. Missing or foreign native descriptors refuse
staging/restoration. Old Linux records retain their existing behavior. If native
attribute restoration is interrupted, cleanup does not infer that a mixture of
old/new attributes is owned and disposable: it retains that stage and reports it
for review. Incomplete native stages without completed provenance are also kept.
No broad cleanup, data-only fallback or silent metadata loss is introduced.

As with the existing transaction primitives, ordinary concurrent changes are
checked through descriptor/name identity, hashing and stat/readback. This does not
provide an immutable snapshot against a same-user process retaining a writable
file descriptor or atomically exclude arbitrary parent-namespace replacement.

## Qualification and remaining product work

`tests/test_darwin_metadata.py` separates synthetic binding/error tests from six
actual Darwin fixtures. Native CI compiles a small SDK assertion in a disposable
directory and checks constants, ACL framing and xattr limits; native fixture tests
exercise real Unicode/empty xattrs, resource fork, quarantine, FinderInfo,
nontrivial ACL, supported flags, private staging, restoration, inherited ACL
refusal, immutable refusal before reads, public-stage refusal and failed restore
retention. No native APIs or fixture requirements are skipped into success on the
macOS runner. A bounded receipt contains OS/architecture, synthetic hashes and
capability outcomes, never fixture paths, account IDs, ACL contents or xattr values.
The JUnit file preserves failures; the receipt alone is not the overall CI gate.
Linux runs skip the six actual-native cases and do not qualify them. Native fixture
runs remain unqualified until the exact candidate is run successfully on macOS.

Still held separately: service peer credentials/startup, journal and directory
barrier qualification, frozen modern `.app` runtime pinning, native mutation and
Undo journeys, signing/distribution, and physical USB removal/power-loss trials.
An observed `fsync` or SQLite transaction is evidence for its kernel/API contract,
not a universal promise about every drive's volatile cache. Each filesystem must
be evaluated under that distinction rather than an impossible blanket guarantee.

The product's no-install USB goal remains full scope. Executable medium, profile
medium, source medium and destination medium are separate capabilities: an app
running from exFAT does not imply a private profile or hardlink publication works
there. Current hardlink/private-mode prerequisites fail closed for unsupported
media; they are **not a permanent exFAT exclusion**. Later work must design a
no-hardlink transaction with exclusive destination reservation, verified copied
bytes, durable phase records, retained originals and bounded restart ambiguity,
plus an explicit profile privacy policy. That work must not weaken current Linux
protection or fabricate ACLs, hardlinks or directory durability on exFAT.

## Primary API references

- [Apple xattr descriptor API](https://developer.apple.com/library/archive/documentation/System/Conceptual/ManPages_iPhoneOS/man2/getxattr.2.html)
- [Apple ACL retrieval](https://developer.apple.com/library/archive/documentation/System/Conceptual/ManPages_iPhoneOS/man3/acl_get_file.3.html)
- [Apple ACL declarations](https://github.com/apple-oss-distributions/Libc/blob/main/include/sys/acl.h)
- [Apple portable ACL serialization implementation](https://github.com/apple-oss-distributions/Libc/blob/main/posix1e/acl_translate.c)
- [Apple ACL framing](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/sys/kauth.h)
- [Apple file flags](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/sys/stat.h)
- [Apple copyfile categories and unpack behavior](https://developer.apple.com/library/archive/documentation/System/Conceptual/ManPages_iPhoneOS/man3/copyfile.3.html)
- [Apple fsync contract](https://developer.apple.com/library/archive/documentation/System/Conceptual/ManPages_iPhoneOS/man2/fsync.2.html)
- [Microsoft filesystem comparison, including exFAT](https://learn.microsoft.com/en-us/windows/win32/fileio/filesystem-functionality-comparison)

- [Apple fd ACL absence implementation](https://github.com/apple-oss-distributions/Libc/blob/main/posix1e/acl_file.c)
- [Apple FILESEC_ACL absence distinction](https://github.com/apple-oss-distributions/Libc/blob/main/gen/filesec.c)
- [Apple native stat ACL population](https://github.com/apple-oss-distributions/Libc/blob/main/sys/statx_np.c)

- [Apple filesystem security capability](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/vfs/vfs_vnops.c)
