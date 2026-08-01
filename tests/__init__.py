"""Test package bootstrap.

Importing any test module enables test mode for the whole process. In test
mode app/services/write_guard refuses writes that target the repository's
real config files (API/.env, data/api_providers.json, ...), so a test that
forgets to redirect API_ENV_FILE / API_PROVIDERS_FILE fails loudly instead
of silently overwriting the user's API keys.
"""
import os

os.environ.setdefault('FIREFLY_TEST_MODE', '1')
