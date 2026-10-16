"""The cPanel requirements file must cover every third-party import."""
import ast
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

# module -> distribution name where they differ
ALIASES = {
    "dotenv": "python-dotenv",
    "dns": "dnspython",
}


def is_first_party(mod):
    if mod in ("extensions", "models", "config"):
        return True
    if (ROOT / f"{mod}.py").exists():
        return True
    return (ROOT / mod).is_dir()


def provided_distributions():
    dists = set()
    for line in (ROOT / "requirements-cpanel.txt").read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        dists.add(line.split("==")[0].split(">=")[0].split(";")[0].strip().lower())
    return dists


def test_cpanel_requirements_cover_all_runtime_imports():
    provided = provided_distributions()
    assert "gunicorn" not in provided and "psycopg2-binary" not in provided
    missing = set()
    for path in list(ROOT.glob("*.py")) + list((ROOT / "auth").glob("*.py")):
        if path.name.startswith("test_") or path.name.startswith("_"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                mods = [node.module.split(".")[0]]
            else:
                continue
            for mod in mods:
                if mod in sys.stdlib_module_names or is_first_party(mod):
                    continue
                dist = ALIASES.get(mod, mod.replace("_", "-")).lower()
                if dist not in provided:
                    missing.add(f"{path.name}:{mod}->{dist}")
    assert not missing, f"imports missing from requirements-cpanel.txt: {sorted(missing)}"


def _run_preflight(secret_key):
    env = dict(os.environ)
    env["ENV_FILE"] = os.devnull
    env["FLASK_ENV"] = "production"
    env["SECRET_KEY"] = secret_key
    env["DATABASE_URL"] = "mysql+pymysql://u:p@127.0.0.1:3306/db"
    return subprocess.run(
        [sys.executable, "preflight_cpanel.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=env,
    )


def test_preflight_rejects_development_secret_key():
    """A 33-char development key must FAIL, not pass on length alone."""
    result = _run_preflight("dev-change-this-before-production")
    assert "[FAIL] SECRET_KEY missing, shorter than 32 chars, or a development value." in result.stdout


def test_preflight_accepts_strong_secret_key():
    result = _run_preflight("c" * 48)
    assert "[PASS] SECRET_KEY present (48 chars, value hidden)." in result.stdout
