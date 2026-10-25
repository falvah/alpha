"""Configuration migrations must not silently disable HTTPS."""
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest


class ConfigurationTests(unittest.TestCase):
    def settings(self, values):
        with TemporaryDirectory() as directory:
            env = os.environ.copy()
            env.pop('SANDBOX_MODE', None)
            env.pop('DEMO_MODE', None)
            env.update(DATA_DIR=directory, SECRET_KEY='configuration-test-key-' * 4, PYTHONDONTWRITEBYTECODE='1')
            env.update(values)
            code = 'import json; from config import settings as s; print(json.dumps([s.SANDBOX_MODE, s.SECURE_SSL_REDIRECT, s.SESSION_COOKIE_SECURE]))'
            result = subprocess.run([sys.executable, '-c', code], cwd=Path(__file__).resolve().parents[1], env=env, capture_output=True, text=True, check=True)
            return json.loads(result.stdout)

    def test_legacy_production_configuration_keeps_https(self):
        self.assertEqual(self.settings({'DEMO_MODE': 'false'}), [False, True, True])

    def test_explicit_sandbox_setting_takes_priority(self):
        self.assertEqual(self.settings({'DEMO_MODE': 'true', 'SANDBOX_MODE': 'false'}), [False, True, True])
