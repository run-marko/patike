"""Sintaksna provera JavaScript-a u dashboard šablonu. Preskače se ako Node nije instaliran."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

TEMPLATE = Path(__file__).resolve().parent.parent / "dashboard" / "template.html"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node nije instaliran")
def test_template_javascript_parses(tmp_path):
    script = re.search(r"<script>(.*)</script>", TEMPLATE.read_text(encoding="utf-8"), re.S).group(1)
    path = tmp_path / "dashboard.js"
    path.write_text(script, encoding="utf-8")
    result = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stderr
