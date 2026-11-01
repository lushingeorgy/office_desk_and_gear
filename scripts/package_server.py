"""Create a server-only deployment archive; never include local secrets/data."""
from pathlib import Path
import hashlib
import json
import tarfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    files = [ROOT / name for name in (
        "Dockerfile", ".dockerignore", "requirements-server.txt", "alembic.ini", "run_app.py",
        "deploy/compose.shared.yaml", "deploy/nginx.shared.conf.example", "deploy/Caddyfile.shared.snippet",
        "deploy/.env.server.example", "deploy/README.md",
    )]
    for directory in ("server", "migrations"):
        files.extend(path for path in (ROOT / directory).rglob("*.py")
                     if "__pycache__" not in path.parts and "data" not in path.relative_to(ROOT).parts)
    manifest = {path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in sorted(files)}
    revision = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()[:16]
    output = ROOT / "bin" / "server" / f"office-desk-gear-server-{revision}.tar.gz"
    output.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(output, "w:gz") as archive:
        for path in sorted(files):
            archive.add(path, arcname=path.relative_to(ROOT).as_posix(), recursive=False)
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(".gz.sha256").write_text(f"{digest}  {output.name}\n", encoding="ascii")
    output.with_suffix(".manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps({"archive": str(output), "sha256": digest, "files": len(files)}, indent=2))


if __name__ == "__main__":
    main()
