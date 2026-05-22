from django.contrib.auth.forms import AuthenticationForm


class GanpatiAuthenticationForm(AuthenticationForm):
    """Login form with Hinglish error messages so salesmen see a tone
    consistent with the rest of the app."""

    error_messages = {
        "invalid_login": "Username ya password galat hai. Phir se try karein.",
        "inactive": "Aapka account band hai. Owner se baat karein.",
    }
