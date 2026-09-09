"""Power-cut a disposable Ubuntu VM; never mount a host collection in it.

Requires local qemu-system-x86_64, KVM access, xorriso, ssh/scp and a Python
environment containing the application's piexif/Pillow dependencies. Downloads
one pinned Ubuntu cloud disk into the requested evidence directory. No packages,
daemons or host settings are installed/modified. Runtime files are generated
evidence; transaction code is archived byte-for-byte with per-file hashes.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import io
import json
import selectors
import shlex
import shutil
import socket
import subprocess
import tarfile
import tempfile
import time
import urllib.request
from pathlib import Path

IMAGE_URL = "https://cloud-images.ubuntu.com/noble/20260826/noble-server-cloudimg-amd64.img"
IMAGE_SHA256 = "d0fe84bb5f80853425fa6be28e2c106f30104c3cfe8611933f2e65c9b63f0e30"
CASES = ("move-intent", "move-claim", "move-staged", "move-publish", "move-filesystem", "move-receipt",
         "move-attributes", "undo-claim", "metadata-publish", "metadata-receipt", "cross-publish", "cross-filesystem")
MODULES = ("__init__", "operation_engine", "operation_journal", "operation_contracts", "file_safety",
           "profile_lock", "metadata_io", "model_assets", "paths", "logger")
HARNESS_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
DATA_DEVICE = "/dev/disk/by-id/virtio-isort-q-data"
DATA_MOUNT = "/mnt/imagesorter-qualification"


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024**2), b""):
            value.update(chunk)
    return value.hexdigest()


def run(args, **kwargs):
    return subprocess.run(args, check=True, text=True, capture_output=True, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--cases", nargs="+", choices=CASES, default=list(CASES))
    options = parser.parse_args()
    repository = Path(__file__).resolve().parents[1]
    evidence_root = options.evidence_root.resolve()
    if not evidence_root.is_dir() or evidence_root.is_symlink():
        raise ValueError("Evidence root must already exist and not be a symlink")
    for executable in ("qemu-system-x86_64", "xorriso", "ssh-keygen", "ssh", "scp"):
        if not shutil.which(executable):
            raise RuntimeError(f"Missing executable: {executable}")
    with open("/dev/kvm", "rb+"):
        pass
    directory = Path(tempfile.mkdtemp(prefix="vm-power-loss-", dir=evidence_root))
    directory.chmod(0o700)
    print(f"Evidence directory: {directory}", flush=True)
    base = evidence_root / "ubuntu-noble-20260826-amd64.img"
    if not base.exists():
        partial = directory / "ubuntu-download.partial"
        print("Downloading pinned disposable guest image", flush=True)
        with urllib.request.urlopen(IMAGE_URL, timeout=60) as response, partial.open("xb") as output:
            shutil.copyfileobj(response, output, length=1024**2)
        if digest(partial) != IMAGE_SHA256:
            raise RuntimeError("Ubuntu cloud image checksum mismatch; partial evidence retained")
        partial.rename(base)
    if base.is_symlink() or digest(base) != IMAGE_SHA256:
        raise RuntimeError("Cached Ubuntu cloud image is not the pinned artifact")
    disk = directory / "disposable-guest.qcow2"
    run(["cp", "--reflink=auto", str(base), str(disk)])
    data_disk = directory / "disposable-data.raw"
    with data_disk.open("xb") as output:
        output.truncate(1024**3)
    key = directory / "guest-only-key"
    run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)])
    public_key = key.with_suffix(".pub").read_text().strip()
    seed = directory / "seed"
    seed.mkdir()
    (seed / "meta-data").write_text("instance-id: imagesorter-power-loss\nlocal-hostname: imagesorter-test\n")
    (seed / "user-data").write_text("#cloud-config\nssh_pwauth: false\ndisable_root: true\n"
        "package_update: false\npackage_upgrade: false\nusers:\n  - name: ubuntu\n"
        "    groups: [sudo]\n    shell: /bin/bash\n    sudo: ALL=(ALL) NOPASSWD:ALL\n"
        "    lock_passwd: true\n    ssh_authorized_keys:\n      - " + public_key + "\n")
    iso = directory / "seed.iso"
    run(["xorriso", "-as", "mkisofs", "-quiet", "-output", str(iso), "-volid", "cidata", "-joliet", "-rock",
         str(seed / "user-data"), str(seed / "meta-data")])
    import piexif
    from PIL import Image
    payload = directory / "payload.tar"
    source_hashes = {}
    with tarfile.open(payload, "w") as archive:
        def add_bytes(relative, blob):
            info = tarfile.TarInfo(relative)
            info.size = len(blob)
            archive.addfile(info, io.BytesIO(blob))
            source_hashes[relative] = hashlib.sha256(blob).hexdigest()

        for module in MODULES:
            source = repository / "src" / "imagesorter" / (module + ".py")
            relative = "src/imagesorter/" + source.name
            add_bytes(relative, source.read_bytes())
        guest = repository / "scripts" / "vm_power_loss_guest.py"
        add_bytes("guest.py", guest.read_bytes())
        vendor = Path(piexif.__file__).parent
        for source in sorted(vendor.glob("*.py")):
            relative = "vendor/piexif/" + source.name
            add_bytes(relative, source.read_bytes())
        fixture = io.BytesIO()
        Image.new("RGB", (64, 48), "#204080").save(fixture, format="JPEG", quality=95)
        add_bytes("fixture.jpg", fixture.getvalue())
        inventory = json.dumps(source_hashes, sort_keys=True).encode()
        info = tarfile.TarInfo("payload-inventory.json")
        info.size = len(inventory)
        archive.addfile(info, io.BytesIO(inventory))
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    ssh_options = ["-i", str(key), "-o", "BatchMode=yes", "-o", "ConnectTimeout=3", "-o",
                   f"UserKnownHostsFile={directory / 'known-hosts'}", "-o", "StrictHostKeyChecking=accept-new"]
    ssh = ["ssh", *ssh_options, "-p", str(port), "ubuntu@127.0.0.1"]
    process = None
    volume_ready = False
    report = {"kind": "whole-guest-vm-power-interruption", "image_url": IMAGE_URL, "image_sha256": IMAGE_SHA256,
        "selected_cases": options.cases, "full_matrix": set(options.cases) == set(CASES),
        "harness_sha256": HARNESS_SHA256, "started_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "source_sha256": source_hashes, "host_mounts": [], "memory_mib": 1536, "vcpus": 2,
        "disposable_data_disk_bytes": 1024**3,
        "limitations": "Virtual guest/kernel RAM loss and virtual block flush semantics; not physical host/controller power loss",
        "qemu_version": run(["qemu-system-x86_64", "--version"]).stdout.splitlines()[0],
        "disk_cache": "none", "power_cut": "SIGKILL to QEMU VMM; no guest shutdown or post-boundary sync", "cases": []}

    def boot():
        nonlocal process
        serial = (directory / "guest-serial.log").open("ab")
        command = ["qemu-system-x86_64", "-enable-kvm", "-cpu", "host", "-smp", "2", "-m", "1536", "-display", "none",
            "-serial", "stdio", "-monitor", "none", "-drive", f"file={disk},if=none,id=qualification-root,format=qcow2,cache=none",
            "-device", "virtio-blk-pci,drive=qualification-root,bootindex=1",
            "-drive", f"file={data_disk},if=none,id=qualification-data,format=raw,cache=none",
            "-device", "virtio-blk-pci,drive=qualification-data,serial=isort-q-data",
            "-drive", f"file={iso},media=cdrom,readonly=on", "-netdev", f"user,id=net0,restrict=on,hostfwd=tcp:127.0.0.1:{port}-:22",
            "-device", "virtio-net-pci,netdev=net0", "-no-reboot"]
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=serial, stderr=serial)
        serial.close()
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"Guest boot failed; see {directory / 'guest-serial.log'}")
            response = subprocess.run([*ssh, "true"], text=True, capture_output=True)
            if response.returncode == 0:
                if volume_ready:
                    run([*ssh, f"sudo mount -o nosuid,nodev,noexec {DATA_DEVICE} {DATA_MOUNT}"], timeout=30)
                return
            time.sleep(1)
        raise TimeoutError("Disposable guest did not reach SSH within 180 seconds")

    def power_off():
        nonlocal process
        assert process is not None and process.poll() is None
        process.kill()
        process.wait(timeout=15)
        process = None

    def guest_command(mode, case):
        return "cd qualification && python3 guest.py " + shlex.quote(mode) + " " + shlex.quote(case)

    try:
        boot()
        run([*ssh, "cloud-init status --wait"], timeout=180)
        if run([*ssh, f"sudo blockdev --getsize64 {DATA_DEVICE}"]).stdout.strip() != str(1024**3):
            raise RuntimeError("Disposable data disk size did not match the newly created guest device")
        if run([*ssh, f"lsblk -dn -o SERIAL {DATA_DEVICE}"]).stdout.strip() != "isort-q-data":
            raise RuntimeError("Disposable data disk serial did not match; refusing to format")
        run([*ssh, f"sudo mkfs.ext4 -q {DATA_DEVICE}"], timeout=60)
        run([*ssh, f"sudo mkdir -p {DATA_MOUNT}"], timeout=30)
        run([*ssh, f"sudo mount -o nosuid,nodev,noexec {DATA_DEVICE} {DATA_MOUNT}"], timeout=30)
        run([*ssh, f"sudo chown ubuntu:ubuntu {DATA_MOUNT}"], timeout=30)
        volume_ready = True
        run(["scp", *ssh_options, "-P", str(port), str(payload), "ubuntu@127.0.0.1:payload.tar"], timeout=60)
        run([*ssh, "mkdir qualification && tar -xf payload.tar -C qualification"], timeout=30)
        report["guest_platform"] = run([*ssh, f"uname -a; findmnt -no FSTYPE,OPTIONS /; findmnt -no SOURCE,FSTYPE,OPTIONS {DATA_MOUNT}; python3 --version"]).stdout
        for case in options.cases:
            print(f"Arming VM power cut: {case}", flush=True)
            run([*ssh, guest_command("prepare", case)], timeout=60)
            worker = subprocess.Popen([*ssh, guest_command("run", case)], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            selector = selectors.DefaultSelector()
            selector.register(worker.stdout, selectors.EVENT_READ)
            deadline, cut_event = time.monotonic() + 60, None
            while time.monotonic() < deadline:
                if selector.select(timeout=1):
                    line = worker.stdout.readline()
                    if not line:
                        raise RuntimeError("Guest probe exited before boundary: " + worker.stderr.read())
                    if '"power_cut_ready"' in line:
                        cut_event = json.loads(line)
                        break
            selector.close()
            if cut_event is None:
                raise TimeoutError(f"Guest did not reach requested power boundary: {case}")
            power_off()
            worker.communicate(timeout=15)
            case_report = {"case": case, "cut_event": cut_event, "restarts": []}
            for restart in (1, 2):
                boot()
                output = run([*ssh, guest_command("verify", case)], timeout=60).stdout
                audit = json.loads(output.strip().splitlines()[-1])
                assert audit["restart"] == restart
                case_report["restarts"].append(audit)
                if restart == 1:
                    # This second whole-VM loss also verifies durability of
                    # the just-written recovery receipt, not a warm reread.
                    power_off()
            report["cases"].append(case_report)
            (directory / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
            print(f"Verified two cold restarts: {case}", flush=True)
        shutdown = subprocess.run([*ssh, "sudo poweroff"], text=True, capture_output=True, timeout=30)
        if shutdown.returncode not in (0, 255):
            raise RuntimeError("Disposable guest shutdown failed: " + shutdown.stderr)
        if process is not None:
            process.wait(timeout=45)
            process = None
        report["passed"] = True
        report["completed_at_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait(timeout=15)
        (directory / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(f"Qualification passed: {directory / 'report.json'}", flush=True)


if __name__ == "__main__":
    main()
