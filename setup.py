"""Build hooks only; project metadata remains in pyproject.toml."""
import sys
from pathlib import Path

from setuptools import setup

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_support import AttestedBuildPy, AttestedSdist

setup(cmdclass={"build_py": AttestedBuildPy, "sdist": AttestedSdist})
