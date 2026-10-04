import re
import subprocess
import sys


def test_runtime_logs_include_utc_timestamps_for_server_access_and_provider():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import logging
from uvicorn import Config

Config('cueweaver.product:create_product_app_from_env',
       log_config='cueweaver/logging.json')
logging.getLogger('uvicorn.error').info('Synthetic startup')
logging.getLogger('uvicorn.access').info('%s - "%s %s HTTP/%s" %d',
                                      '127.0.0.1:1234', 'GET', '/api/jobs/example', '1.1', 200)
logging.warning('Synthetic provider retry')
""",
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    lines = (result.stdout + result.stderr).splitlines()
    assert len(lines) == 3
    assert all(
        re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}\+00:00 ", line)
        for line in lines
    )
    assert any('GET /api/jobs/example HTTP/1.1" 200' in line for line in lines)
    assert any("WARNING root Synthetic provider retry" in line for line in lines)
