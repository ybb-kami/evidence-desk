"""Build the workbench from a clean context containing only runtime source files."""

import argparse
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory


HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parents[1]
ROOT_FILES = (
    "app.py", "arxiv_tracking.py", "byline.py", "discover.py", "engine.py", "graph.py",
    "local_review.py", "ocr_local.py", "provider.py", "reader.py", "revisions.py",
    "csv-stats.js", "index.html", "reader.css", "reader.js", "review-panel.js", "sample.pdf",
    "tracking.js", "writing.js",
)
PACKAGES = {
    "scanpdf": (".py", ".html", ".css", ".js"),
    "scanpdf/bilingual": (".py", ".html", ".css", ".js"),
    "scanpdf/discovery": (".py", ".html", ".js"),
    "scanpdf/local_llm": (".py",),
    "scanpdf/preload": (".py",),
    "scanpdf/reproducibility": (".py", ".html", ".js"),
    "scanpdf/qa_reader": (".py",),
    "scanpdf/scanpdf": (".py",),
    "scanpdf/semantic": (".py",),
    "scanpdf/tools": (".py",),
}
REQUIREMENTS = (
    "scanpdf/requirements.txt", "scanpdf/requirements-rag.txt", "scanpdf/bilingual/requirements.txt",
    "scanpdf/qa_reader/requirements.txt", "scanpdf/semantic/requirements.txt",
    "scanpdf/container/formula-requirements.txt",
)


def stage(destination):
    sources = list(ROOT_FILES + REQUIREMENTS)
    for directory, suffixes in PACKAGES.items():
        sources.extend(str(item.relative_to(REPOSITORY)) for item in (REPOSITORY / directory).iterdir()
                       if not item.name.startswith((".", "test_")) and item.suffix in suffixes)
    for name in sorted(sources):
        source = REPOSITORY / name
        if source.resolve() != source or not source.is_file():
            raise ValueError(f"Expected a regular runtime file: {name}")
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        target.chmod(0o644)
    for directory in destination.rglob("*"):
        if directory.is_dir():
            directory.chmod(0o755)
    shutil.copyfile(HERE / "Dockerfile", destination / "Dockerfile")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", default="evidence-desk-rag:local")
    parser.add_argument("--host", help="Optional Docker endpoint, for example unix:///var/run/docker.sock")
    parser.add_argument("--ca-bundle", type=Path, help="HTTPS trust bundle to supply as a build-only secret")
    parser.add_argument("--formula", action="store_true", help="Install the optional RapidLaTeXOCR runtime")
    args = parser.parse_args()
    command = ["docker"]
    if args.host:
        command.extend(["--host", args.host])
    command.extend(["build", "--progress", "plain", "--platform", "linux/amd64", "--tag", args.tag])
    if args.ca_bundle:
        ca_bundle = args.ca_bundle.resolve()
        if not ca_bundle.is_file():
            parser.error("The CA bundle must be a regular file")
        command.extend(["--secret", f"id=proxy_ca,src={ca_bundle}"])
    if args.formula:
        command.extend(["--build-arg", "WITH_FORMULA=1"])
    with TemporaryDirectory(prefix="evidence-container-") as folder:
        stage(Path(folder))
        return subprocess.run([*command, folder], check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
