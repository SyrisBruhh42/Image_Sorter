# Reader containment and authorization boundary

The application, base sorting, codecs, animation, CPU inference and optional GPU
inference run without administrator authorization. The optional privileged native
trace is an acceptance observer, not an application prerequisite. Declining it
does not disable these features. The tradeoff is missing first-launch trace
evidence for a normally mounted AppImage; an unobserved launch is not a passing
network-isolation test. The earlier authorized observer attempt did not reach an
application launch and remains incomplete.

## Enforced Linux leaf policy

`reader_sandbox.py` runs only in a fresh, disposable reader after trusted snapshot
and lease preparation, before native parsers/model imports. It rejects an existing
thread or unexpected writable/socket/device descriptor. The supervisor, component
installer and durable mutation owner do not enter the reader policy.

- Landlock ABI3+ restricts file writes to the private scratch directory. Base CPU
  operation has no privilege fallback. NVIDIA needs ABI6+ for device-ioctl control
  and abstract UNIX socket scoping; incompatible GPU selection has one explicit
  CPU fallback. The Components page reports the requirement.
- Metadata-changing calls, external signaling, process creation, process-group
  escape, foreign scheduling changes, SysV IPC, networking and mutation-service
  communication are denied.
  Native threads inherit the policy. File descriptor operations are restricted to
  ordinary flags and duplication; async signal ownership, locks and leases are
  denied. Existing output channels remain broker-owned and bounded.
- NVIDIA may write/ioctl only the exact NVIDIA character devices and name its own
  newly created threads under its own proc task hierarchy. CUDA may create and
  bind/listen on a local sequence-packet socket; connect, accept and all message
  exchange remain denied. Scope alone does not block an unconfined external peer
  from connecting inward, so the message/accept denials are essential.
- Core dumps are disabled. The inherited per-file hard limit is at most 600 MiB;
  protocol headers, pixels, batch sizes, snapshots and deadlines have additional
  independent bounds. The supervisor rejects symbolic links, FIFOs, changing
  output identity and oversized logs before accepting a result.

The broker validates the required policy version and minimum ABI in every
successful helper reply. Native observations preserve the reply, fixture digest,
component version and exact archive digest. Historical executables lacking the
contract are not enabled or acquired, but their files are not silently deleted.
Catalog and source hash checks prevent pre-repair packs from satisfying final
qualification even if they report the same provisional protocol version.

This is file/IPC authority containment, not a comprehensive host resource sandbox.
Kernel/GPU-driver defects, malicious unconfined same-user host processes, and
complete memory/thread/disk-allocation quotas are outside the claim. No arbitrary
address-space ceiling is imposed on CUDA's large virtual mappings. Supported
filesystem and external-writer limitations also remain in the transaction design.

## Adversarial evidence and limits

Actual CPU/GPU-mode child tests preserve collection bytes and attributes while
rejecting destructive calls, proc aliases and foreign thread writes. Original r6
policy allowed `fcntl` async ownership of an inherited pipe to deliver SIGTERM to
a disposable same-user target without a kill syscall. The revised whitelist
rejects all three controls; the target survives. This is a confirmed defect and
repair, not merely a theoretical hardening recommendation.

A second disposable-target probe confirmed that unfiltered CPU/IO priority calls
could permanently slow another same-user process. Both scheduling mutation calls
are now denied, independently of filesystem restrictions.

The repaired source also passed actual CUDA matrix computation. r6 packs and
their format/UI evidence predate the descriptor-authority repair and are
**not publishable**. Final packs, full inference/fallback, native cases and final
artifact evidence must be rebuilt and repeated against the final exact source.
Passing unit probes do not establish the complete release gate.

Primary kernel contracts: [Landlock](https://docs.kernel.org/userspace-api/landlock.html)
and [seccomp filtering](https://docs.kernel.org/userspace-api/seccomp_filter.html).
