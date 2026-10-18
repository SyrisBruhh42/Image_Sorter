"""Baked into portable builds before application imports or profile selection."""
import sys

# A missing external marker must not silently turn a portable package into a
# native-profile launch (for example, when macOS relocates the .app itself).
# Explicit and validated inherited profile identities retain their precedence.
sys._imagesorter_portable_required = True
