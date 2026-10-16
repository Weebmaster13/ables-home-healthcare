"""The cPanel requirements file must cover every third-party import."""
import ast
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

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


def _pins(path):
    pins = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        pins[line.split("==")[0].strip().lower()] = line
    return pins


def test_cpanel_pins_match_main_requirements():
    """Alternative runtime sets must not drift on packages they share.

    Both files install the same application code, so a package pinned
    differently in each (e.g. Werkzeug) means cPanel and the container path
    run different versions of a shared dependency.
    """
    main = _pins(ROOT / "requirements.txt")
    cpanel = _pins(ROOT / "requirements-cpanel.txt")
    drifted = {name: (main[name], cpanel[name]) for name in set(main) & set(cpanel) if main[name] != cpanel[name]}
    assert not drifted, f"requirements-cpanel.txt pins drift from requirements.txt: {drifted}"


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


def _run_preflight(secret_key, extra_args=(), extra_env=None):
    env = dict(os.environ)
    env["ENV_FILE"] = os.devnull
    env["FLASK_ENV"] = "production"
    env["SECRET_KEY"] = secret_key
    env["DATABASE_URL"] = "mysql+pymysql://u:p@127.0.0.1:3306/db"
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, "preflight_cpanel.py", *extra_args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=env,
    )


def _run_preflight_reachable(extra_args=(), extra_env=None):
    """Run preflight against a throwaway SQLite DB so it reaches the upload check."""
    tmp = tempfile.mkdtemp(prefix="preflight-")
    uploads = pathlib.Path(tmp) / "uploads"
    uploads.mkdir()
    env = {
        "ENV_FILE": os.devnull,
        "SECRET_KEY": "c" * 48,
        "DATABASE_URL": f"sqlite:///{tmp}/preflight.sqlite",
        "UPLOAD_FOLDER": str(uploads),
        "LOG_TO_FILE": "false",
    }
    env.update(extra_env or {})
    result = subprocess.run(
        [sys.executable, "preflight_cpanel.py", *extra_args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env={**os.environ, **env},
    )
    return result, pathlib.Path(tmp), uploads


def test_preflight_rejects_development_secret_key():
    """A 33-char development key must FAIL, not pass on length alone."""
    result = _run_preflight("dev-change-this-before-production")
    assert "[FAIL] SECRET_KEY missing, shorter than 32 chars, or a development value." in result.stdout


def test_preflight_accepts_strong_secret_key():
    result = _run_preflight("c" * 48)
    assert "[PASS] SECRET_KEY present (48 chars, value hidden)." in result.stdout


def test_preflight_default_creates_no_files():
    """The documented default must be genuinely read-only."""
    # LOG_TO_FILE=true is deliberately passed in: the preflight must override it
    # so the read-only check still writes nothing.
    result, tmp, uploads = _run_preflight_reachable(extra_env={"LOG_TO_FILE": "true"})
    try:
        assert "no write performed" in result.stdout, result.stdout + result.stderr
        assert not (uploads / ".preflight").exists()
        assert list(uploads.iterdir()) == [], "default preflight wrote into the upload folder"
        assert not (pathlib.Path(tmp) / "logs").exists()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_preflight_write_test_flag_is_opt_in_and_reports_result():
    """--write-test is the only path that writes; it must be reported clearly."""
    result, tmp, uploads = _run_preflight_reachable(extra_args=("--write-test",))
    try:
        assert "Upload folder write test passed" in result.stdout, result.stdout + result.stderr
        # The probe file is removed again, leaving the folder empty.
        assert list(uploads.iterdir()) == []
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_preflight_reports_missing_upload_folder_without_creating_it():
    """A missing upload folder is a FAIL, and the check must not create it."""
    base = pathlib.Path(tempfile.mkdtemp(prefix="preflight-missing-"))
    missing = base / "does-not-exist"
    result, tmp, _ = _run_preflight_reachable(extra_env={"UPLOAD_FOLDER": str(missing)})
    try:
        assert "Upload folder does not exist" in result.stdout, result.stdout + result.stderr
        assert not missing.exists(), "read-only preflight created the upload folder"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(base, ignore_errors=True)


def test_log_to_file_disabled_skips_local_log_file(tmp_path):
    """LOG_TO_FILE=false must skip the local rotating log file entirely."""
    from flask import Flask
    from app import configure_logging

    def make_app(instance):
        test_app = Flask("logtest")
        test_app.instance_path = str(instance)
        return test_app

    off_instance = tmp_path / "off"
    disabled = make_app(off_instance)
    disabled.config["LOG_TO_FILE"] = False
    configure_logging(disabled)
    assert not (off_instance / "logs").exists()

    on_instance = tmp_path / "on"
    enabled = make_app(on_instance)
    enabled.config["LOG_TO_FILE"] = True
    configure_logging(enabled)
    assert (on_instance / "logs" / "app.log").exists()
