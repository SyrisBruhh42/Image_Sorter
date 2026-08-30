import sys
from pathlib import Path

# Ensure 'src' directory is in sys.path for source development / uninstalled execution
root_dir = Path(__file__).resolve().parent
src_dir = root_dir / "src"
if src_dir.exists():
    src_str = str(src_dir)
    if src_str not in sys.path:
        sys.path.insert(0, src_str)

from imagesorter.main import main

if __name__ == "__main__":
    main()
