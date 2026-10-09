"""Worker protocol (P0 §5): round trip, script errors, timeout kill."""
import pytest

from harness.run import FREECADCMD, run_request

pytestmark = pytest.mark.skipif(not FREECADCMD.exists(), reason="freecadcmd not installed")


def test_ping_round_trip(tmp_path):
    res = run_request({"op": "ping", "timeout_s": 60}, tmp_path)
    assert res.ok, res.signals
    assert res.freecad_version == "1.1.4"
    assert res.result["gui_up"] is False
    assert (tmp_path / "req_0.json").exists() and (tmp_path / "res_0.json").exists()


def test_run_script_returns_result(tmp_path):
    script = tmp_path / "s.py"
    script.write_text("import Part\nresult['volume'] = Part.makeBox(1, 2, 3).Volume\n")
    res = run_request({"op": "run_script", "script": str(script)}, tmp_path)
    assert res.ok, res.signals
    assert res.result["volume"] == pytest.approx(6.0)


def test_script_exception_becomes_failure_signal(tmp_path):
    script = tmp_path / "bad.py"
    script.write_text("raise ValueError('boom')\n")
    res = run_request({"op": "run_script", "script": str(script)}, tmp_path)
    assert not res.ok
    assert res.signals[0].level == "failure" and "boom" in res.signals[0].cause


def test_unknown_op(tmp_path):
    res = run_request({"op": "nope"}, tmp_path)
    assert not res.ok and "unknown op" in res.signals[0].cause


def test_timeout_kills_worker(tmp_path):
    script = tmp_path / "hang.py"
    script.write_text("while True:\n    pass\n")
    res = run_request({"op": "run_script", "script": str(script)}, tmp_path, timeout_s=8)
    assert not res.ok
    assert res.signals[0].gate == "harness" and "timeout" in res.signals[0].cause
    assert not (tmp_path / "res_0.json").exists()
