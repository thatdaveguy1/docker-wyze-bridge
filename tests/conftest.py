import os

_original_env = os.environ.copy()


def pytest_runtest_setup(item):
    """Restore os.environ before each test.

    Several test files insert .ha_live_addon/app into sys.path, which
    triggers build_config.py → load_dotenv(.ha_live_addon/app/.env).
    That leaks dev-only env vars (WHEP_PROXY_PORT, TUTK_NATIVE_LOG, …)
    into os.environ for the rest of the process.  Snapshotting and
    restoring prevents those vars from affecting later tests.
    """
    os.environ.clear()
    os.environ.update(_original_env)
