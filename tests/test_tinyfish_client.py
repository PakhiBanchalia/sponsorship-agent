from app.services.tinyfish_client import interpret_run


def test_interpret_run_completed_success():
    run = {"status": "COMPLETED", "result": {"profiles": []}, "streaming_url": "https://example.com"}
    result = interpret_run(run)
    assert result["infrastructure_ok"] is True
    assert result["goal_succeeded"] is True
    assert result["data"] == {"profiles": []}


def test_interpret_run_completed_goal_failure():
    run = {"status": "COMPLETED", "result": {"status": "failure", "reason": "Blocked"}}
    result = interpret_run(run)
    assert result["infrastructure_ok"] is True
    assert result["goal_succeeded"] is False
    assert result["retryable"] is True


def test_interpret_run_failed():
    run = {"status": "FAILED", "error": {"message": "Infrastructure failure"}}
    result = interpret_run(run)
    assert result["infrastructure_ok"] is False
    assert result["goal_succeeded"] is False
    assert result["error"] == "Infrastructure failure"


def test_interpret_run_pending():
    run = {"status": "PENDING"}
    result = interpret_run(run)
    assert result["pending"] is True
    assert result["goal_succeeded"] is None
