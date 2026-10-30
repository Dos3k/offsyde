from __future__ import annotations

import json

import pytest

from offsyde.cli import main


@pytest.fixture
def env(monkeypatch, loaded_engine):
    monkeypatch.setenv("OFFSYDE_DB_URL", str(loaded_engine.url))
    monkeypatch.setenv("OLLAMA_HOST", "http://127.0.0.1:9")  # nothing listens here


def test_check_passes_on_healthy_data(env, capsys):
    assert main(["check"]) == 0
    assert "all checks passed" in capsys.readouterr().out


def test_oracle_eval_via_cli_writes_report(env, capsys, tmp_path):
    out = tmp_path / "report.json"
    code = main(["eval", "--oracle", "--limit", "5", "--out", str(out)])
    assert code == 0
    assert "execution accuracy:      100.0%" in capsys.readouterr().out
    assert json.loads(out.read_text())["metrics"]["n"] == 5


def test_unreachable_ollama_is_a_clean_error_not_a_traceback(env, capsys):
    assert main(["ask", "How many goals did Leverkusen score?"]) == 2
    err = capsys.readouterr().err
    assert err.startswith("error: cannot reach Ollama") and "ollama serve" in err
    assert "Traceback" not in err


def test_eval_without_ollama_fails_cleanly(env, capsys):
    assert main(["eval", "--limit", "1"]) == 2
    assert "cannot reach Ollama" in capsys.readouterr().err
