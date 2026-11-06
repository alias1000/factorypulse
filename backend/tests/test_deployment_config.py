"""v0.3.1 regression: static checks of the shipped deployment configuration (compose files, nginx)."""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
yaml = pytest.importorskip("yaml")


def _compose(name):
    class Loader(yaml.SafeLoader):
        pass
    Loader.add_constructor("!override", lambda loader, node: loader.construct_sequence(node))
    return yaml.load((ROOT / name).read_text(), Loader=Loader)


def test_compose_production_defaults():
    c = _compose("docker-compose.yml")
    env = c["services"]["backend"]["environment"]
    assert env["FP_ENVIRONMENT"] == "${FP_ENVIRONMENT:-production}"
    assert env["FP_SEED_DEMO_ON_STARTUP"] == "${FP_SEED_DEMO_ON_STARTUP:-}"      # automatic → never in production
    assert env["FP_ENABLE_DEMO_TOOLS"] == "${FP_ENABLE_DEMO_TOOLS:-}"
    assert env["FP_COOKIE_SECURE"] == "${FP_COOKIE_SECURE:-true}"
    for svc in ("db", "backend", "worker", "clamav"):
        assert "ports" not in c["services"][svc], f"{svc} must not be published"


def test_compose_worker_service():
    w = _compose("docker-compose.yml")["services"]["worker"]
    assert w["command"] == ["python", "-m", "app.worker"]
    assert w["environment"]["FP_RUN_MIGRATIONS"] == "false"            # only the API container migrates
    assert w["depends_on"]["backend"]["condition"] == "service_healthy"


def test_lan_override_is_https_only():
    lan = _compose("docker-compose.lan.yml")["services"]
    fe = lan["frontend"]
    assert fe["environment"]["FP_TLS"] == "true"
    assert all(":443" in p or ":80" in p for p in fe["ports"]) and not any("8080" in p for p in fe["ports"])
    assert lan["backend"]["environment"]["FP_COOKIE_SECURE"] == "true"


def test_nginx_overwrites_forwarded_for_and_hardens_tls():
    app = (ROOT / "frontend/nginx/app.conf").read_text()
    assert "proxy_set_header X-Forwarded-For $remote_addr;" in app
    assert "$proxy_add_x_forwarded_for" not in app                    # would trust client-supplied values
    tls = (ROOT / "frontend/nginx/tls.conf").read_text()
    assert "ssl_protocols TLSv1.2 TLSv1.3;" in tls and "return 301" in tls
    assert 'max-age=31536000' in tls
    http = (ROOT / "frontend/nginx/http.conf").read_text()
    assert 'map "" $fp_hsts { default ""; }' in http                  # no HSTS on plain HTTP
    hdr = (ROOT / "frontend/nginx/security-headers.conf").read_text()
    assert "frame-ancestors 'none'" in hdr and "Strict-Transport-Security $fp_hsts" in hdr


def test_entrypoint_refuses_tls_without_certificate():
    ep = (ROOT / "frontend/nginx/entrypoint.sh").read_text()
    assert "exit 1" in ep and "/etc/nginx/tls/server.key" in ep
    assert "FP_PUBLIC_URL must look like https://" in ep
