"""Coherence de la configuration du projet."""
import os
import re
import tomllib

from packaging.requirements import Requirement
from packaging.version import Version

from conftest import ROOT
from market_intelligence.config import load_settings, read_env_file


def _pins(name):
    pins = {}
    for line in (ROOT / name).read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip()
        if line and not line.startswith("-r"):
            req = Requirement(line)
            (spec,) = req.specifier
            assert spec.operator == "==", f"{name} : {line} n'est pas epingle"
            pins[req.name.lower()] = Version(spec.version)
    return pins


def test_pinned_versions_satisfy_pyproject_ranges():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    runtime, dev = _pins("requirements.txt"), _pins("requirements-dev.txt")
    for text in project["dependencies"]:
        req = Requirement(text)
        assert req.name.lower() in runtime, f"{req.name} absent de requirements.txt"
        assert runtime[req.name.lower()] in req.specifier
    everything = {**runtime, **dev}
    for extra in ("dev", "wgc"):
        for text in project["optional-dependencies"][extra]:
            req = Requirement(text)
            assert everything[req.name.lower()] in req.specifier


def test_env_example_contains_no_secret_and_lists_every_variable():
    example = read_env_file(ROOT / ".env.example")
    assert set(example) == {"BLS_API_KEY", "FRED_API_KEY", "AMB_DATA_DIR", "AMB_TEMPORAL_POLICY"}
    assert all(value == "" for value in example.values())
    source = (ROOT / "src" / "market_intelligence" / "config.py").read_text(encoding="utf-8")
    assert set(re.findall(r'environ\.get\("([A-Z_]+)"\)', source)) == set(example)


def test_env_file_is_read_but_never_overrides_the_environment(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text('# commentaire\nBLS_API_KEY="from-file"\nFRED_API_KEY=\nAUTRE=ignore\n', encoding="utf-8")
    # Copie isolee de l'environnement : rien ne fuit vers les autres tests.
    monkeypatch.setattr(os, "environ", {k: v for k, v in os.environ.items()
                                        if k not in ("BLS_API_KEY", "FRED_API_KEY")})
    settings = load_settings(tmp_path, env_file=env)
    assert settings.bls_api_key == "from-file" and settings.fred_api_key is None
    assert "AUTRE" not in os.environ
    monkeypatch.setenv("BLS_API_KEY", "from-environment")
    assert load_settings(tmp_path, env_file=env).bls_api_key == "from-environment"


def test_gitignore_protects_secrets_and_data():
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").split()
    assert {"data/", ".env", "!.env.example"} <= set(ignored)
