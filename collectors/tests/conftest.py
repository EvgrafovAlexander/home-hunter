import pytest


@pytest.fixture(autouse=True)
def isolated_avito_session(settings, tmp_path):
    settings.AVITO_STATE_DIR = tmp_path / 'avito'
    settings.AVITO_PAGE_DELAY_SECONDS = 0
    settings.AVITO_BLOCK_COOLDOWN_SECONDS = 3600
    settings.AVITO_FULL_SCAN_ENABLED = True
