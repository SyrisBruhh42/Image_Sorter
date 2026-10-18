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

## Verification

Run the dependency-free storage regression suite from the repository root:

```sh
PYTHONPATH=src QT_QPA_PLATFORM=offscreen python3 -m unittest discover -s tests -p test_portable_isolation.py -v
```

The suite uses disposable files, isolated environment paths and actual
filesystem symlinks. Platform selectors exercise path decisions; they do not
constitute native Windows or macOS qualification.

## Remaining portable-release work

- Preserve one resolved portable identity through source workers and the
  frozen mutation service's pinned-runtime relaunch.
- Define writable external profiles for AppImage and signed macOS app bundles.
- Qualify Windows locking, process communication, durable mutations and Undo;
  the existing Windows mutation restriction remains in force.
- Distinguish portable persistent state from necessary OS-managed transient
  resources such as local sockets; audit all worker and optional-component writes.
- Build and run OS/architecture-specific packages on real supported platforms,
  including removable-media disconnection, read-only media, drive-letter changes,
  concurrent launches and recovery of interrupted operations.

No packaged, GUI, physical USB or native Windows/macOS acceptance is established
by this bounded source repair. AI tagging, animated images, RAW/HEIF and duplicate
handling remain part of the full product scope.
