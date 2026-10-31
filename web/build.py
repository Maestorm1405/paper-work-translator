"""Builds the web version (for GitHub Pages) into _site: the app's page plus its Python code, run in the browser."""

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "paperwork_translator"
SITE = ROOT / "_site"

shutil.rmtree(SITE, ignore_errors=True)
shutil.copytree(PACKAGE / "static", SITE)
code = SITE / "py" / "paperwork_translator"
(code / "samples").mkdir(parents=True)
for name in ("__init__.py", "app.py", "translator.py", "checks.py", "local_reader.py"):
    shutil.copy(PACKAGE / name, code / name)
for sample in (PACKAGE / "samples").glob("*.json"):
    shutil.copy(sample, code / "samples" / sample.name)
shutil.copy(ROOT / "web" / "web_api.py", SITE / "py" / "web_api.py")
shutil.copy(ROOT / "web" / "web-api.js", SITE / "web-api.js")

page = SITE / "index.html"
html = page.read_text(encoding="utf-8")
marker = '<script type="module" src="app.js"></script>'
assert marker in html
page.write_text(html.replace(marker, '<script src="web-api.js"></script>\n  ' + marker), encoding="utf-8")
(SITE / ".nojekyll").write_text("")
print(f"Built {SITE}")
