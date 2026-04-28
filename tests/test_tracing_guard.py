from __future__ import annotations
import pytest
from slopmortem.tracing import init_tracing, TracingGuardError

def test_loopback_allowed(monkeypatch):
    init_tracing(base_url="http://127.0.0.1:8000", allow_remote=False)

def test_remote_refused_without_flag():
    with pytest.raises(TracingGuardError):
        init_tracing(base_url="http://attacker.example", allow_remote=False)

def test_localhost_attacker_subdomain_refused():
    with pytest.raises(TracingGuardError):
        init_tracing(base_url="http://localhost.attacker.example", allow_remote=False)

def test_remote_allowed_with_flag(monkeypatch):
    init_tracing(base_url="http://attacker.example", allow_remote=True)
