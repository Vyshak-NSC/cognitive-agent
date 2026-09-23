"""Streamlit authentication with persistent browser login."""

import streamlit as st

from ragapp.auth.users import authenticate_user, create_user
from ragapp.auth.tokens import (
    create_access_token,
    verify_access_token,
)


COOKIE_NAME = "ragapp_auth_token"


def _get_cookie_controller():
    """
    Return a browser cookie controller.

    Uses streamlit-cookies-controller when installed.
    """
    try:
        from streamlit_cookies_controller import (
            CookieController,
        )
    except ImportError:
        return None

    if "_cookie_controller" not in st.session_state:
        st.session_state["_cookie_controller"] = CookieController()

    return st.session_state["_cookie_controller"]


def _get_token():
    controller = _get_cookie_controller()

    if controller is None:
        return None

    try:
        return controller.get(COOKIE_NAME)
    except Exception:
        return None


def _set_token(token):
    controller = _get_cookie_controller()

    if controller is None:
        return False

    try:
        controller.set(
            COOKIE_NAME,
            token,
            max_age=60 * 60 * 24 * 30,
            path="/",
        )
        return True
    except Exception:
        return False


def _delete_token():
    controller = _get_cookie_controller()

    if controller is None:
        return

    try:
        controller.remove(COOKIE_NAME)
    except Exception:
        pass


def _start(username):
    st.session_state.authenticated = True
    st.session_state.username = username
    st.session_state.messages = []


def _restore_login():
    """
    Restore authentication from the persistent browser JWT.

    Returns True when a valid token restored the session.
    """
    if st.session_state.get("authenticated"):
        return True

    token = _get_token()

    if not token:
        return False

    username = verify_access_token(token)

    if not username:
        _delete_token()
        return False

    _start(username)
    return True


def logout():
    """Log the user out of this browser."""
    _delete_token()

    for key in (
        "authenticated",
        "username",
        "messages",
        "chat_session_id",
        "project_id",
    ):
        st.session_state.pop(key, None)

    st.rerun()


def login():
    """
    Authenticate the user.

    A valid browser JWT restores the session automatically, so restarting
    Streamlit does not require logging in again.
    """

    # --------------------------------------------------------------
    # Restore persistent login first.
    # --------------------------------------------------------------

    if _restore_login():
        return True

    # --------------------------------------------------------------
    # Login UI
    # --------------------------------------------------------------

    st.title("Cognitive Persistence Agent")

    login_tab, register_tab = st.tabs(
        ["Login", "Register"]
    )

    with login_tab:

        u = st.text_input(
            "Username",
            key="login_u",
        )

        p = st.text_input(
            "Password",
            type="password",
            key="login_p",
        )

        if st.button(
            "Login",
            type="primary",
        ):
            user = authenticate_user(u, p)

            if user:
                token = create_access_token(user)

                if not _set_token(token):
                    st.warning(
                        "Login succeeded, but persistent browser "
                        "sessions are unavailable. Install "
                        "streamlit-cookies-controller."
                    )

                _start(user)
                st.rerun()

            else:
                st.error(
                    "Invalid username or password."
                )

    # --------------------------------------------------------------
    # Registration
    # --------------------------------------------------------------

    with register_tab:

        u = st.text_input(
            "Username",
            key="reg_u",
        )

        p = st.text_input(
            "Password",
            type="password",
            key="reg_p",
        )

        q = st.text_input(
            "Confirm password",
            type="password",
            key="reg_q",
        )

        if st.button("Create account"):

            if len(p) < 8:
                st.error(
                    "Password must contain at least 8 characters."
                )

            elif p != q:
                st.error(
                    "Passwords do not match."
                )

            else:
                try:
                    create_user(u, p)

                    token = create_access_token(u)

                    if not _set_token(token):
                        st.warning(
                            "Account created, but persistent browser "
                            "sessions are unavailable. Install "
                            "streamlit-cookies-controller."
                        )

                    _start(u)
                    st.rerun()

                except ValueError as exc:
                    st.error(str(exc))

    return False