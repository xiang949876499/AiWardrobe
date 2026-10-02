"""Isolated checked-in configuration fixtures; never read private .env or start Docker."""
from pathlib import Path
import re
import secrets

import pytest
import yaml
from pydantic import ValidationError
from sqlalchemy.engine import make_url

from app.config import Settings

ROOT = Path(__file__).resolve().parents[2]


class IsolatedSettings(Settings):
    @classmethod
    def settings_customise_sources(cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings):
        return (init_settings,)


def resolved_config(values):
    def replace(match):
        key, operator, fallback = match.groups()
        if values.get(key):
            return values[key]
        if operator == ':?':
            raise ValueError('Required deployment setting missing')
        return fallback
    # Exercise only the interpolation forms used in this checked-in template.
    def expand(value):
        if isinstance(value, dict):
            return {k: expand(v) for k, v in value.items()}
        if isinstance(value, list):
            return [expand(v) for v in value]
        if isinstance(value, str):
            return re.sub(r'\$\{([A-Z_][A-Z0-9_]*)(:\?|:-)([^}]*)\}', replace, value)
        return value
    return expand(yaml.safe_load((ROOT / 'docker-compose.yml').read_text()))


def credentials():
    return dict(JWT_SECRET=secrets.token_urlsafe(48), POSTGRES_PASSWORD=secrets.token_urlsafe(48),
                S3_ACCESS_KEY_ID='synthetic-operator', S3_SECRET_ACCESS_KEY=secrets.token_urlsafe(48))


@pytest.mark.parametrize('missing', ['JWT_SECRET', 'POSTGRES_PASSWORD', 'S3_ACCESS_KEY_ID', 'S3_SECRET_ACCESS_KEY'])
@pytest.mark.parametrize('empty', [False, True])
def test_production_template_requires_every_credential(missing, empty):
    values = credentials()
    if empty:
        values[missing] = ''
    else:
        del values[missing]
    with pytest.raises(ValueError, match='Required deployment'):
        resolved_config(values)


@pytest.mark.parametrize('field,secret', [('postgres_password', 'aiwardrobe'), ('postgres_password', ''),
                                       ('s3_secret_access_key', 'minioadmin'), ('s3_secret_access_key', '')])
def test_production_validator_rejects_public_infrastructure_passwords(field, secret):
    values = credentials()
    values[field.upper()] = secret
    args = dict(environment='production', testing=False, storage_driver='s3',
                database_url='postgresql+psycopg://aiwardrobe@postgres:5432/aiwardrobe',
                **{k.lower(): v for k, v in values.items()})
    with pytest.raises(ValidationError):
        IsolatedSettings(**args)


def test_supplied_credentials_reach_all_consumers_and_password_is_url_encoded():
    values = credentials()
    values['POSTGRES_PASSWORD'] += '@:/?#%&'
    services = resolved_config(values)['services']
    for name in ('validate-config', 'migrate', 'backend', 'worker', 'wedding-worker'):
        env = services[name]['environment']
        assert env['POSTGRES_PASSWORD'] == services['postgres']['environment']['POSTGRES_PASSWORD'] == values['POSTGRES_PASSWORD']
        assert env['S3_ACCESS_KEY_ID'] == services['minio']['environment']['MINIO_ROOT_USER'] == values['S3_ACCESS_KEY_ID']
        assert env['S3_SECRET_ACCESS_KEY'] == services['minio']['environment']['MINIO_ROOT_PASSWORD'] == values['S3_SECRET_ACCESS_KEY']
        settings = IsolatedSettings(**{k.lower(): v for k, v in env.items()})
        assert make_url(settings.database_url).password == values['POSTGRES_PASSWORD']
        assert make_url(settings.database_url).host == 'postgres'
    health = services['minio']['healthcheck']['test'][-1]
    assert '$$MINIO_ROOT_USER' in health and '$$MINIO_ROOT_PASSWORD' in health
    init = services['minio-init']
    assert init['environment']['S3_SECRET_ACCESS_KEY'] == values['S3_SECRET_ACCESS_KEY']
    assert init['environment']['S3_ACCESS_KEY_ID'] == values['S3_ACCESS_KEY_ID']
    assert '$$S3_ACCESS_KEY_ID' in init['entrypoint'][2] and '$$S3_SECRET_ACCESS_KEY' in init['entrypoint'][2]
    assert init['entrypoint'][2].count('anonymous set none') == 2
    for name in ('postgres', 'minio'):
        assert 'ports' not in services[name]
        assert services[name]['depends_on']['validate-config']['condition'] == 'service_completed_successfully'
    local = yaml.safe_load((ROOT / 'docker-compose.local.yml').read_text())
    assert all(port.startswith('127.0.0.1:') for config in local['services'].values() for port in config.get('ports', []))


def test_empty_sample_password_does_not_add_credentials_to_local_sqlite():
    from sqlalchemy import create_engine
    settings = IsolatedSettings(environment='development', database_url='sqlite:///:memory:', postgres_password='')
    engine = create_engine(settings.database_url)
    with engine.connect() as connection:
        assert connection.exec_driver_sql('SELECT 1').scalar() == 1
    engine.dispose()
