from __future__ import annotations

import logging
import os

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger(__name__)

DEFAULT_UA = "Mozilla/5.0 (compatible; sourcer/0.1; personal job search)"


def session(user_agent: str | None = None) -> requests.Session:
    s = requests.Session()
    retry = Retry(total=2, backoff_factor=1.0, status_forcelist=(429, 500, 502, 503, 504),
                  allowed_methods=("GET",))
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.headers["User-Agent"] = user_agent or DEFAULT_UA
    return s


def get_json(s: requests.Session, url: str, **kw):
    """GET a JSON endpoint; returns None on 404 or network failure (logged)."""
    try:
        r = s.get(url, timeout=kw.pop("timeout", 20), **kw)
    except requests.RequestException as e:
        log.warning("GET %s failed: %s", url, e)
        return None
    if r.status_code == 404:
        return None
    if not r.ok:
        log.warning("GET %s -> %s", url, r.status_code)
        return None
    try:
        return r.json()
    except ValueError:
        return None


def get_text(s: requests.Session, url: str, **kw) -> str | None:
    try:
        r = s.get(url, timeout=kw.pop("timeout", 20), **kw)
    except requests.RequestException as e:
        log.warning("GET %s failed: %s", url, e)
        return None
    if not r.ok:
        log.warning("GET %s -> %s", url, r.status_code)
        return None
    return r.text


def sec_user_agent(configured: str) -> str:
    return os.environ.get("SEC_USER_AGENT") or configured
