import argparse
import os
import pickle

from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import AuthorizedSession

from .config import GMAIL_TOKEN, GOOGLE_ACCOUNT, GOOGLE_CLIENT, GOOGLE_TOKEN, IDENTITY_SCOPES, SCOPES


def verified_account(creds, gmail=False):
    url = "https://gmail.googleapis.com/gmail/v1/users/me/profile" if gmail else "https://openidconnect.googleapis.com/v1/userinfo"
    with AuthorizedSession(creds) as session:
        response = session.get(url, timeout=20)
        response.raise_for_status()
        profile = response.json()
    email = profile.get("emailAddress" if gmail else "email", "").casefold()
    if email != GOOGLE_ACCOUNT or not gmail and profile.get("email_verified") is not True:
        raise RuntimeError(f"Authorization must use {GOOGLE_ACCOUNT}; the selected account was not accepted. Existing credentials are unchanged.")
    return creds.with_account(email)


def authorize(scopes):
    if not GOOGLE_ACCOUNT:
        raise RuntimeError('Google settings missing; configure control_center/google.local.json before authorization')
    print(f"Authorize only {GOOGLE_ACCOUNT}. Complete the Google consent screen within 10 minutes.", flush=True)
    flow = InstalledAppFlow.from_client_secrets_file(str(GOOGLE_CLIENT), scopes)
    return flow.run_local_server(port=0, timeout_seconds=600, login_hint=GOOGLE_ACCOUNT, prompt="select_account consent", access_type="offline")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reauthorize", action="store_true")
    parser.add_argument("--gmail", action="store_true")
    args = parser.parse_args()
    if args.gmail:
        creds = verified_account(authorize(["https://www.googleapis.com/auth/gmail.readonly"]), gmail=True)
        token = GMAIL_TOKEN
        token.parent.mkdir(parents=True, exist_ok=True)
        with token.open("wb") as handle:
            pickle.dump(creds, handle)
        token.chmod(0o600)
        print(f"MetaProp Gmail read-only authorization saved for {creds.account}.")
        return
    if GOOGLE_TOKEN.exists() and not args.reauthorize:
        print("Token exists. Use --reauthorize to choose a different Google account.")
        return
    print("Gmail authorization is unchanged.", flush=True)
    creds = verified_account(authorize(SCOPES + IDENTITY_SCOPES))
    if not creds.has_scopes(SCOPES):
        raise RuntimeError("Sheets and Docs permissions were not granted")
    GOOGLE_TOKEN.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(GOOGLE_TOKEN, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(creds.to_json())
    GOOGLE_TOKEN.chmod(0o600)
    print(f"Google write authorization saved for {creds.account}.")


if __name__ == "__main__":
    main()
