from reddit_customer_research.cli import main


def test_demo_runs_offline_and_prints_report(capsys, monkeypatch):
    def network_is_forbidden(*args, **kwargs):
        raise AssertionError("demo mode attempted network access")

    monkeypatch.setattr("requests.Session.request", network_is_forbidden)

    assert main(["demo"]) == 0
    output = capsys.readouterr().out
    assert "# Buyer Research Report" in output
    assert "appointment reminder software" in output


def test_demo_refuses_to_overwrite_existing_file(tmp_path):
    output = tmp_path / "existing.md"
    output.write_text("keep me", encoding="utf-8")

    try:
        main(["demo", "--output", str(output)])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("expected overwrite protection to stop the command")
    assert output.read_text(encoding="utf-8") == "keep me"
