"""Session setup shared by every test module.

Uploads are written under FILES_BASE_PATH. The production default is /files, a root-owned path that does not exist on a CI runner, so
the suite points it at a temporary directory before any module reads the settings. An explicit value from the environment still wins.
"""
import os
import tempfile

os.environ.setdefault("FILES_BASE_PATH", tempfile.mkdtemp(prefix="cv_files_"))
