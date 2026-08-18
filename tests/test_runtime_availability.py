"""Runtime-entrypoint regressions for bridge availability defaults."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUN_FILES = [
    ROOT / "app" / "run",
    ROOT / "home_assistant" / "app" / "run",
    ROOT / ".ha_live_addon" / "app" / "run",
]


def test_runtime_entrypoints_validate_optional_env_before_sourcing():
    for run_path in RUN_FILES:
        text = run_path.read_text(encoding="utf-8")
        with_env = "if sh -n /app/.env 2>/tmp/app_env_syntax.log; then"
        source_env = ". /app/.env"
        assert with_env in text
        assert source_env in text
        assert text.index(with_env) < text.index(source_env)
        assert "ignoring invalid /app/.env" in text


def test_runtime_entrypoints_do_not_restart_healthy_streams_by_default():
    for run_path in RUN_FILES:
        text = run_path.read_text(encoding="utf-8")
        assert "GO2RTC_PROACTIVE_TUTK_REFRESH" in text
        assert "GO2RTC_SESSION_REFRESH_INTERVAL" in text
        assert "proactive TUTK refresh disabled by default; media health drives recovery" in text
        assert "GO2RTC_WIFI_RESTART" in text
        assert "restart-on-ping disabled by default; media health drives recovery" in text
        assert "start_go2rtc_session_refresh_loop()" in text
        assert "start_wifi_health_monitor()" in text


def test_proactive_tutk_refresh_requires_explicit_opt_in():
    text = (ROOT / "app" / "run").read_text(encoding="utf-8")
    assert '_run_truthy "${GO2RTC_PROACTIVE_TUTK_REFRESH:-}"' in text
    assert '[ "${GO2RTC_SESSION_REFRESH_INTERVAL}" -gt 0 ]' in text
    assert 'if [ "${_refresh_opt_in}" != "1" ]; then' in text


def test_wifi_restart_requires_explicit_opt_in():
    text = (ROOT / "app" / "run").read_text(encoding="utf-8")
    assert 'if ! _run_truthy "${GO2RTC_WIFI_RESTART:-}"; then' in text
