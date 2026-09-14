"""Download exact Ubuntu source sets using an evidence-only APT configuration.

No install, upgrade, build-dependency installation or host configuration changes.
The input inventory must identify OS packages whose bytes are actually shipped.
Default selection conservatively acquires sources when preserved copyright text
mentions copyleft; that is not an assertion that every such licence is applicable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

RUNNING_RECIPE = Path(__file__).read_bytes()


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def selected_sources(
    inventory: dict, root: Path, requested: list[str] | None
) -> dict[str, str]:
    result = {}
    for row in inventory.get("shipped_os_packages", []):
        name, version = row["source_package"], row["source_version"]
        if not re.fullmatch(r"[a-z0-9][a-z0-9+.-]*", name) or not re.fullmatch(
            r"[A-Za-z0-9.+:~_-]+", version
        ):
            raise ValueError("Invalid exact source package identity")
        if requested is not None:
            include = name in requested
        else:
            notice = row.get("notice")
            if not notice:
                continue
            path = root / notice["path"]
            if (
                not path.resolve().is_relative_to(root.resolve())
                or sha(path) != notice["sha256"]
            ):
                raise ValueError("Input copyright inventory hash/path mismatch")
            include = bool(
                re.search(
                    r"(?:[AL]?GPL|General Public License|Mozilla Public License)",
                    path.read_text(errors="replace"),
                    re.IGNORECASE,
                )
            )
        if include:
            if name in result and result[name] != version:
                raise ValueError(
                    f"Multiple shipped source versions require separate handling: {name}"
                )
            result[name] = version
    if requested is not None and set(requested) - set(result):
        raise ValueError(
            "Requested package is not represented in the shipped native inventory"
        )
    return dict(sorted(result.items()))


def verify_source_set(directory: Path, package: str, version: str) -> int:
    descriptors = list(directory.glob("*.dsc"))
    if len(descriptors) != 1 or descriptors[0].is_symlink():
        raise ValueError("Exact source set requires one source descriptor")
    text = descriptors[0].read_text()
    source = re.search(r"^Source: (\S+)\s*$", text, re.MULTILINE)
    release = re.search(r"^Version: (\S+)\s*$", text, re.MULTILINE)
    checksums = re.search(r"^Checksums-Sha256:\n((?:[ \t].*\n)+)", text, re.MULTILINE)
    if (
        not source
        or source.group(1) != package
        or not release
        or release.group(1) != version
        or not checksums
    ):
        raise ValueError(
            "Source descriptor does not match the requested exact identity"
        )
    count = 0
    seen = set()
    for line in checksums.group(1).splitlines():
        expected, size, name = line.split()
        if (
            Path(name).name != name
            or name in (".", "..")
            or "\\" in name
            or name in seen
            or not re.fullmatch(r"[0-9a-f]{64}", expected)
            or not size.isdecimal()
        ):
            raise ValueError("Unsafe source descriptor filename")
        seen.add(name)
        path = directory / name
        if (
            not path.is_file()
            or path.is_symlink()
            or path.stat().st_size != int(size)
            or sha(path) != expected
        ):
            raise ValueError(f"Source descriptor checksum/size mismatch: {name}")
        count += 1
    if not count:
        raise ValueError("Source descriptor has no corresponding archives")
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-package", action="append")
    args = parser.parse_args()
    inventory = json.loads(args.inventory.read_text())
    packages = selected_sources(inventory, args.inventory.parent, args.source_package)
    if not packages:
        raise SystemExit("No shipped source packages selected")
    output = args.output.absolute()
    if any(token in str(output) for token in ('"', "\\", "\n")):
        raise SystemExit("APT evidence path contains unsupported quoting characters")
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    output = output.resolve()
    (output / "acquire_distro_sources.py").write_bytes(RUNNING_RECIPE)
    for directory in (
        "etc/parts",
        "etc/sourceparts",
        "state/lists/partial",
        "cache/archives/partial",
        "logs",
        "tmp",
        "sources",
    ):
        (output / directory).mkdir(parents=True)
    (output / "etc/empty.conf").write_text("")
    (output / "state/status").write_text("")
    key = output / "etc/ubuntu-archive-keyring.gpg"
    shutil.copyfile("/usr/share/keyrings/ubuntu-archive-keyring.gpg", key)
    sources = output / "etc/sources.list"
    sources.write_text(
        "\n".join(
            f"deb-src [signed-by={key}] https://{host}/ubuntu {suite} main universe restricted multiverse"
            for host, suite in (
                ("archive.ubuntu.com", "noble"),
                ("archive.ubuntu.com", "noble-updates"),
                ("security.ubuntu.com", "noble-security"),
            )
        )
        + "\n"
    )
    config = output / "apt.conf"
    config.write_text(
        "\n".join(
            [
                f'Dir::Etc::parts "{output / "etc/parts"}";',
                f'Dir::Etc::main "{output / "etc/empty.conf"}";',
                f'Dir::Etc::sourcelist "{sources}";',
                f'Dir::Etc::sourceparts "{output / "etc/sourceparts"}";',
                f'Dir::State "{output / "state"}";',
                f'Dir::State::status "{output / "state/status"}";',
                f'Dir::State::lists "{output / "state/lists"}";',
                f'Dir::Cache "{output / "cache"}";',
                f'Dir::Log "{output / "logs"}";',
                'Acquire::Languages "none";',
                'Acquire::Retries "2";',
                'Acquire::AllowInsecureRepositories "false";',
                'Acquire::AllowDowngradeToInsecureRepositories "false";',
                'APT::Get::AllowUnauthenticated "false";',
            ]
        )
        + "\n"
    )
    env = dict(os.environ)
    env.update(APT_CONFIG=str(config), TMPDIR=str(output / "tmp"), LC_ALL="C.UTF-8")
    report = {
        "schema_version": 1,
        "kind": "exact-shipped-ubuntu-source-acquisition",
        "input_inventory_sha256": sha(args.inventory),
        "keyring_sha256": sha(key),
        "recipe_sha256": hashlib.sha256(RUNNING_RECIPE).hexdigest(),
        "selection": "explicit"
        if args.source_package
        else "conservative-copyright-scan-not-license-adjudication",
        "packages": [],
        "complete": False,
    }

    def command(arguments: list[str], cwd: Path, log: Path) -> int:
        with log.open("ab") as stream:
            stream.write((json.dumps(arguments) + "\n").encode())
            stream.flush()
            return subprocess.run(
                arguments, cwd=cwd, env=env, stdout=stream, stderr=subprocess.STDOUT
            ).returncode

    if command(
        ["apt-get", "update", "--error-on=any"], output, output / "logs/update.log"
    ):
        raise SystemExit(
            f"Signed source-index acquisition failed; retained {output / 'logs/update.log'}"
        )
    report["signed_source_indexes"] = [
        {"path": str(path.relative_to(output)), "sha256": sha(path)}
        for path in sorted((output / "state/lists").iterdir())
        if path.is_file() and not path.name.endswith("lock")
    ]
    for name, version in packages.items():
        directory = output / "sources" / name
        directory.mkdir()
        print(f"Acquiring exact source: {name}={version}", flush=True)
        code = command(
            [
                "apt-get",
                "--download-only",
                "--only-source",
                "source",
                f"{name}={version}",
            ],
            directory,
            output / "logs" / f"{name}.log",
        )
        files = [
            {
                "path": str(path.relative_to(output)),
                "sha256": sha(path),
                "size": path.stat().st_size,
            }
            for path in sorted(directory.iterdir())
            if path.is_file()
        ]
        validation_error = None
        verified_archives = 0
        if code == 0:
            try:
                verified_archives = verify_source_set(directory, name, version)
            except (OSError, ValueError) as exc:
                validation_error = str(exc)
        record = {
            "source_package": name,
            "source_version": version,
            "download_returncode": code,
            "files": files,
            "complete": code == 0
            and verified_archives > 0
            and validation_error is None,
            "descriptor_verified_archives": verified_archives,
            "validation_error": validation_error,
        }
        report["packages"].append(record)
        (output / "source-acquisition.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n"
        )
    report["complete"] = all(row["complete"] for row in report["packages"])
    (output / "source-acquisition.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n"
    )
    print(
        json.dumps(
            {
                "receipt": str(output / "source-acquisition.json"),
                "complete": report["complete"],
                "missing": [
                    r["source_package"] for r in report["packages"] if not r["complete"]
                ],
            },
            indent=2,
        )
    )
    if not report["complete"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
