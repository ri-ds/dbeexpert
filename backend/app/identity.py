"""
identity.py

Who is making this request.

The app sits behind an Apache SimpleSAMLphp service provider that authenticates
every request before it reaches us. That gate is solid, but it does not currently
tell us WHO the user is: the only cookie present is the SP's own opaque
`ssoSessionID`, which only the SP can decode. Verified by inspecting the headers
that actually arrive.

So identity depends on the proxy forwarding one header. `AUTH_USER_HEADER` names
it, defaulting to `X-Forwarded-User`, and several common alternatives are also
accepted so a different choice by IT needs no code change.

Trust model, which matters:

  * The header is trusted ONLY because the proxy is the sole route in. The
    containers bind to 127.0.0.1, so nothing on the network reaches the backend
    directly.
  * The proxy MUST set this header from the authenticated session rather than
    pass through whatever the browser sent, otherwise a user could send
    `X-Forwarded-User: someone.else` and become them. That is a configuration
    requirement on their side, not something this file can enforce.
  * Nothing here ever reads a username from a request body or a query string.

Until the header exists, `current_user` returns None and every caller falls back
to the behaviour the app has today. Nothing breaks while we wait.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from fastapi import Request

from .settings import settings

log = logging.getLogger(__name__)

# Checked in order after the configured header.
#
# The CCHMC service provider sends sso-uid, sso-email, sso-fname and sso-lname,
# confirmed by inspecting the live headers, so those come first. The rest cover
# the usual nginx auth_request, oauth2-proxy, and Shibboleth conventions so a
# change upstream does not need a redeploy.
_FALLBACK_HEADERS = (
    # CCHMC SimpleSAMLphp service provider. sso-uid is the account name and is the
    # most stable key, so it is preferred over the email, which can change.
    "sso-uid",
    "sso-email",
    "x-forwarded-user",
    "x-forwarded-email",
    "x-forwarded-preferred-username",
    "x-auth-request-user",
    "x-auth-request-email",
    "x-auth-request-preferred-username",
    "x-remote-user",
    "remote-user",
    "x-authenticated-user",
    "x-shib-eppn",
    "eppn",
    "x-shib-mail",
    "mail",
    "x-uid",
    "uid",
)

# A username should be an account name or an email. Anything else is rejected
# rather than stored, so a malformed or injected value cannot become a user id.
_VALID = re.compile(r"^[A-Za-z0-9._%+\-]+(@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})?$")

MAX_USERNAME = 200


# A display name is free text, so it needs a looser rule than an account name:
# real names carry spaces, hyphens, apostrophes and periods. Control characters
# are excluded so nothing can be smuggled into a log line or a stored record.
_NAME_VALID = re.compile(r"^[^\x00-\x1f\x7f<>]{1,120}$")

MAX_NAME = 120


@dataclass(frozen=True)
class User:
    """An authenticated principal."""

    # Stable key used for storage. Lower cased so the same person cannot end up
    # with two accounts through casing differences.
    id: str
    # What the identity header actually said, before lower casing.
    raw: str
    # Real name when the service provider supplies one, otherwise None.
    given_name: str | None = None
    family_name: str | None = None
    email: str | None = None

    @property
    def display_name(self) -> str:
        """
        A human readable name.

        Prefers the real first and last name from the service provider, which the
        CCHMC SP does send as sso-fname and sso-lname. Falls back to deriving one
        from the account when it does not, so a proxy that only forwards a
        username still gets something readable rather than an email address.
        """
        if self.given_name and self.family_name:
            return f"{self.given_name} {self.family_name}"
        if self.given_name:
            return self.given_name

        local = self.raw.split("@", 1)[0]
        if "." in local:
            return " ".join(part.capitalize() for part in local.split(".") if part)
        return local


def _clean(value: str | None) -> str | None:
    if not value:
        return None
    candidate = value.strip()
    # Some proxies forward multiple values as a comma separated list.
    if "," in candidate:
        candidate = candidate.split(",", 1)[0].strip()
    if not candidate or len(candidate) > MAX_USERNAME:
        return None
    if not _VALID.match(candidate):
        log.warning("Rejecting malformed identity header value")
        return None
    return candidate


def _clean_name(value: str | None) -> str | None:
    """Validate a human name, which follows looser rules than an account name."""
    if not value:
        return None
    candidate = " ".join(value.split())[:MAX_NAME]
    if not candidate or not _NAME_VALID.match(candidate):
        return None
    return candidate


def current_user(request: Request) -> User | None:
    """
    Resolve the signed in user from the proxy headers, or None.

    Returning None is a supported state, not an error. It means the reverse proxy
    authenticated the request but did not say who it was, and callers degrade to
    the anonymous behaviour the app already had.
    """
    headers = request.headers
    configured = settings.auth_user_header.lower()

    identifier: str | None = None
    for name in (configured, *_FALLBACK_HEADERS):
        identifier = _clean(headers.get(name))
        if identifier:
            break

    if not identifier:
        return None

    return User(
        id=identifier.lower(),
        raw=identifier,
        given_name=_clean_name(headers.get("sso-fname") or headers.get("givenname")),
        family_name=_clean_name(headers.get("sso-lname") or headers.get("sn")),
        email=_clean(headers.get("sso-email") or headers.get("mail")),
    )
