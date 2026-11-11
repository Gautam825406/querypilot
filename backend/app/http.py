"""HTTP helpers: httpx with an explicit egress proxy.

This VM's `no_proxy` list contains bracketed IPv6 entries ([::1]) that httpx
mis-parses ("Invalid port: ':1]'"), breaking every httpx call that relies on
environment proxies. Passing the proxy explicitly — read from the environment
at runtime, never hardcoded — avoids the bug. Where the env var is absent
(ordinary machines), httpx behaves normally.
"""
from __future__ import annotations

import os

import httpx


def _proxy_kwargs() -> dict:
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    return {"proxy": proxy} if proxy else {}


def post(url: str, payload: dict, headers: dict | None = None, timeout: float = 10):
    return httpx.post(url, json=payload, headers=headers, timeout=timeout,
                      **_proxy_kwargs())


def post_json(url: str, payload: dict, timeout: float = 10):
    return post(url, payload, timeout=timeout)


def get(url: str, params: dict | None = None, timeout: float = 30):
    return httpx.get(url, params=params, timeout=timeout, **_proxy_kwargs())


def stream_get(url: str, timeout: float = 30):
    return httpx.stream("GET", url, timeout=timeout, follow_redirects=True,
                        **_proxy_kwargs())
