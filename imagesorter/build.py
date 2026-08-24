import subprocess
import sys
import os
import shutil

def build_executable():
    print("Starting build process for Windows Executable...")

    if not os.path.exists("src/main.py"):
        print("Error: src/main.py not found. Are you in the project root?")
        sys.exit(1)

    # PyInstaller command
    cmd = [
        "pyinstaller",
        "--noconfirm",
        "--onedir",          # Create a 1-folder bundle containing the executable
        "--windowed",        # Don't open a command prompt (GUI only)
        "--name", "ImageSorter",
        "--add-data", f"models:models", # Package the models directory if it exists
        "src/main.py"
    ]

    # In Linux, path sep for --add-data is ':', in Windows it's ';'
    if sys.platform == "win32":
        cmd[-2] = "models;models"

    print(f"Running: {' '.join(cmd)}")

    try:
        subprocess.run(cmd, check=True)
        print("\nBuild successful! Executable is located in the 'dist' folder.")
    except subprocess.CalledProcessError as e:
        print(f"\nBuild failed with error code {e.returncode}")
        sys.exit(e.returncode)

if __name__ == "__main__":
    # Ensure models folder exists to avoid pyinstaller warning/error
    os.makedirs("models", exist_ok=True)
    build_executable()
