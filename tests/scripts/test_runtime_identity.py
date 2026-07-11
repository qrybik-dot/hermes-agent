import json

from scripts.runtime_identity import git_identity, parse_unit_text


def test_parse_unit_text_extracts_only_runtime_references():
    parsed = parse_unit_text("""[Service]\nWorkingDirectory=/srv/runtime\nExecStart=/srv/runtime/venv/bin/python -m hermes_cli.memory_search --reindex\nEnvironmentFile=/secret.env\n""")
    assert parsed == {
        "working_directory": "/srv/runtime",
        "exec_start": "/srv/runtime/venv/bin/python -m hermes_cli.memory_search --reindex",
    }


def test_parse_unit_text_ignores_environment_values():
    parsed = parse_unit_text("Environment=TOKEN=should-not-be-emitted\n")
    assert parsed == {"working_directory": None, "exec_start": None}


def test_git_identity_reads_immutable_release_manifest(tmp_path):
    (tmp_path / ".hermes-release.json").write_text(json.dumps({
        "commit": "a" * 40,
        "tree": "b" * 40,
    }))

    assert git_identity(tmp_path) == {
        "root": str(tmp_path),
        "branch": "release",
        "head": "a" * 40,
        "tree": "b" * 40,
        "dirty": False,
    }
