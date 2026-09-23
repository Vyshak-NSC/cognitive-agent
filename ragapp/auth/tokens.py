"""JWT access token issuance and verification."""

from datetime import datetime, timedelta, timezone

import jwt

from ragapp import settings


def create_access_token(username):
    """Create a JWT access token for an authenticated user."""

    expires_at = (
        datetime.now(timezone.utc)
        + timedelta(hours=settings.TOKEN_EXPIRE_HOURS)
    )

    payload = {
        "sub": username,
        "exp": expires_at,
    }

    return jwt.encode(
        payload,
        settings.JWT_SECRET,
        algorithm=settings.JWT_ALGORITHM,
    )


def verify_access_token(token):
    """Validate a JWT and return the username."""

    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET,
            algorithms=[settings.JWT_ALGORITHM],
        )

        username = payload.get("sub")

        if not username:
            return None

        return username

    except jwt.InvalidTokenError:
        return None
