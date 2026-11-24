import os
from pathlib import Path
import pytest

FAKES = Path(__file__).parent / "fakes"


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch, tmp_path_factory):
    """Nothing in a test may reach the real machine: a fake `security` first on PATH, and HOME, the Kaggle config dir,
    the launcher home and every *_BIN pinned to temp dirs / fakes (a test that forgets to set one hits a fake, never a live CLI)."""
    h = tmp_path_factory.mktemp("hermetic")
    monkeypatch.setenv("PATH", str(FAKES) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("FAKE_SECURITY_RC", "0")
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("KAGGLE_CONFIG_DIR", str(h / ".kaggle"))
    monkeypatch.setenv("VISOSWAP_HOME", str(h / ".visoswap"))
    monkeypatch.setenv("FAKE_DIR", str(h))
    monkeypatch.setenv("KAGGLE_BIN", str(FAKES / "fake_kaggle.py"))
    monkeypatch.setenv("COLAB_BIN", str(FAKES / "fake_colab.py"))
    monkeypatch.setenv("CLOUDFLARED_BIN", str(FAKES / "fake_cloudflared.py"))
    for k in ("KAGGLE_API_TOKEN", "KAGGLE_USERNAME", "KAGGLE_KEY", "CF_API_TOKEN"):
        monkeypatch.delenv(k, raising=False)
