import subprocess
import sys
import os
import argparse
from pathlib import Path

def build_executable():
    parser = argparse.ArgumentParser(description="Build the Image Sorter Executable")
    parser.add_argument(
        "--standalone", "--onefile",
        action="store_true",
        dest="is_standalone",
        help="Build a standalone single-file executable"
    )
    args = parser.parse_args()

    # Determine paths relative to this script
    script_dir = os.path.dirname(os.path.abspath(__file__))
    src_main = os.path.join(script_dir, "src", "main.py")
    spec_file = os.path.join(script_dir, "ImageSorter.spec")
    models_dir = os.path.join(script_dir, "models")
    gitkeep_file = os.path.join(models_dir, ".gitkeep")
    version_file = os.path.join(script_dir, "version_info.txt")

    print(f"Starting build process from: {script_dir}")

    if not os.path.exists(src_main):
        print(f"Error: src/main.py not found at {src_main}.")
        sys.exit(1)

    if not os.path.exists(spec_file):
        print(f"Error: {spec_file} not found.")
        sys.exit(1)

    # Ensure models folder and .gitkeep exist
    os.makedirs(models_dir, exist_ok=True)
    Path(gitkeep_file).touch(exist_ok=True)

    # Determine build type and set environment variable
    build_type = "onefile" if args.is_standalone else "onedir"
    os.environ["BUILD_TYPE"] = build_type

    print(f"Build type: {build_type}")

    # Output and work directories should be local to the build script
    dist_dir = os.path.join(script_dir, "dist")
    work_dir = os.path.join(script_dir, "build")

    # PyInstaller command using the spec file
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--distpath", dist_dir,
        "--workpath", work_dir,
        spec_file
    ]

    print(f"Running: {' '.join(cmd)}")

    try:
        subprocess.run(cmd, check=True)
        print(f"\nBuild successful! Executable is located in: {dist_dir}")
    except subprocess.CalledProcessError as e:
        print(f"\nBuild failed with error code {e.returncode}")
        sys.exit(e.returncode)

if __name__ == "__main__":
    build_executable()
