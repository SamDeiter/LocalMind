# Google Workspace OAuth Setup

This guide walks a self-hosting LocalMind user through wiring up Google
Workspace OAuth end-to-end: creating the Google Cloud project, enabling the
correct APIs, configuring the consent screen, creating an OAuth Client ID,
downloading `credentials.json`, placing it where LocalMind expects, and
completing the first-time auth flow.

---

## Prerequisites

- A Google account (personal Gmail or Workspace account).
- LocalMind installed and able to start (`./start.ps1` → backend on
  `http://localhost:8000`).
- Browser with cookies / pop-ups allowed for `accounts.google.com` and
  `localhost:8000`.
- Roughly 15 minutes. Google's UI changes occasionally; exact button labels
  may differ slightly from the steps below.

---

## 1. Create a Google Cloud Project

1. Open <https://console.cloud.google.com>.
2. In the top bar, click the project dropdown (left of the search box).
3. Click **New Project**.
4. Name it something like `LocalMind-Personal`. Leave the Organization as-is
   (No organization is fine for personal accounts).
5. Click **Create**.
6. Wait for the "Creating project..." notification to finish, then select the
   new project from the top-bar project picker.

---

## 2. Enable the Required APIs

LocalMind talks to six Google APIs. Enable each one from the APIs & Services
library.

1. In the left navigation, go to **APIs & Services → Library**.
2. For each of the APIs below, search by name, click the result, then click
   **Enable**:

   | API to enable              | Used by                                 |
   |----------------------------|-----------------------------------------|
   | Google Drive API           | File read/write, document tools         |
   | Gmail API                  | Read, send, and modify mail             |
   | Google Docs API            | Document creation and editing           |
   | Google Sheets API          | Spreadsheet tools                       |
   | Google Slides API          | Presentation / pptx tools               |
   | Google Calendar API        | Calendar read access                    |

3. After each API, Google returns to the library page. Confirm the button now
   reads **Manage** (not **Enable**) before moving to the next.

> Enabling an API does not grant the app access on its own — the OAuth scopes
> you request at runtime still have to match what the user consents to in the
> next steps.

---

## 3. Configure the OAuth Consent Screen

Google will not let you create an OAuth client until the consent screen is
set up.

1. Go to **APIs & Services → OAuth consent screen**.
2. User type: choose **External** (required for personal Gmail accounts; a
   Workspace account may offer **Internal**, which is also fine and skips
   verification).
3. Click **Create**.
4. Fill in the **App information** page:
   - App name: `LocalMind`
   - User support email: your email
   - App logo: optional
   - Developer contact email: your email
5. Click **Save and Continue**.
6. On the **Scopes** page you can either add scopes now, or skip — LocalMind
   requests scopes dynamically at sign-in time. **Skip is fine.**
7. On the **Test users** page, click **Add Users** and add your own email.
   (While the app is in "Testing" mode, only listed test users can sign in.)
8. Click **Save and Continue**, then **Back to Dashboard**.

> You can stay in Testing mode indefinitely for personal use. Publishing the
> app would trigger Google's verification review; you do not need it.

---

## 4. Create the OAuth Client ID (Desktop app)

1. Go to **APIs & Services → Credentials**.
2. Click **+ Create Credentials** at the top, then **OAuth client ID**.
3. Application type: **Desktop app**.
4. Name: `LocalMind Desktop`.
5. Click **Create**.
6. A dialog appears with the new client ID and secret. Click **Download JSON**.
   The file is named something like
   `client_secret_1234-abcd.apps.googleusercontent.com.json`.

> Even though LocalMind's redirect URI is `http://localhost:8000/api/google/callback`,
> choose **Desktop app** — the redirect is handled locally and Google treats
> desktop apps as "installed" clients. LocalMind's loader handles both the
> `installed` and `web` JSON layouts.

---

## 5. Place the Credentials File

LocalMind looks for the credentials file at a fixed location.

1. Create the config directory if it does not exist:

   - Windows: `C:\Users\<you>\.localmind\`
   - macOS / Linux: `~/.localmind/`

2. Rename the downloaded file to exactly `credentials.json`.

3. Move it into the `.localmind` directory. Final path:

   - Windows: `C:\Users\<you>\.localmind\credentials.json`
   - macOS / Linux: `~/.localmind/credentials.json`

**Alternative:** instead of the file, you can set environment variables before
starting LocalMind:

```powershell
$env:GOOGLE_CLIENT_ID     = '1234-abcd.apps.googleusercontent.com'
$env:GOOGLE_CLIENT_SECRET = 'GOCSPX-xxxxxxxxxxxxxxxxxxxx'
```

If both the file and the env vars exist, env vars win.

---

## 6. OAuth Scopes LocalMind Requests

When you sign in, Google will ask you to approve these scopes. They come from
the central list in `backend/routes/google_auth.py`:

| Scope                                                   | What it grants                                                |
|---------------------------------------------------------|---------------------------------------------------------------|
| `https://www.googleapis.com/auth/drive.file`            | Read / write only files LocalMind creates or you open in it   |
| `https://www.googleapis.com/auth/presentations`         | Full access to Google Slides                                  |
| `https://www.googleapis.com/auth/spreadsheets`          | Full access to Google Sheets                                  |
| `https://www.googleapis.com/auth/gmail.readonly`        | Read Gmail messages and labels                                |
| `https://www.googleapis.com/auth/gmail.send`            | Send mail on your behalf                                      |
| `https://www.googleapis.com/auth/gmail.modify`          | Modify, archive, label Gmail messages (no permanent delete)   |

> **Calendar note:** the Google Calendar tool
> (`backend/tools/google_calendar_tool.py`) requires
> `https://www.googleapis.com/auth/calendar.readonly`, but that scope is not
> yet part of the central `SCOPES` list. Calendar reads will fail with a scope
> error until that entry is added and you re-consent. Enabling the Calendar
> API (step 2) is still correct — just expect Calendar-tool errors if you
> haven't patched the scopes list.

Google only grants what you approve in the consent screen, so the on-screen
prompt is the authoritative source at sign-in time.

---

## 7. First-Time Auth Flow

1. Start LocalMind: `./start.ps1` (or `LocalMind.bat`).
2. Open the LocalMind UI at <http://localhost:8000>.
3. Go to **Settings → Integrations → Google** and click **Connect Google**.
   (If your build does not expose that button, browse directly to
   <http://localhost:8000/api/google/auth>.)
4. Your browser is redirected to Google's consent screen. Because the app is
   in Testing mode, you will see a "Google hasn't verified this app" warning —
   click **Advanced → Go to LocalMind (unsafe)**. This is expected for
   personal-use apps.
5. Approve each scope. You must approve all of them or LocalMind will prompt
   for re-auth on the first tool call that needs the missing scope.
6. Google redirects back to
   `http://localhost:8000/api/google/callback`. You should land on a
   **Google Connected** confirmation page.
7. LocalMind writes the token to `~/.localmind/google_token.json` and also
   into the encrypted provider DB.
8. Verify with:

   ```bash
   curl http://localhost:8000/api/google/status
   ```

   You should see `"authenticated": true` and the granted scopes.

---

## 8. Troubleshooting

**"No Google credentials found"**
→ `credentials.json` is missing from `~/.localmind/`, or the env vars are
unset. Re-do step 5.

**"Error 400: redirect_uri_mismatch"**
→ You chose **Web application** instead of **Desktop app**, or you edited the
redirect URI. Delete the client and recreate it as a Desktop app (step 4), or
edit the existing client and add `http://localhost:8000/api/google/callback`
to its authorized redirect URIs.

**"Access blocked: LocalMind has not completed the Google verification process"**
→ Your email is not in the Test users list. Go back to the OAuth consent
screen and add yourself under **Test users** (step 3.7).

**`needs_reauth: true` in `/api/google/status`**
→ You consented to fewer scopes than LocalMind requires, or the SCOPES list
has grown since your last auth. Click **Revoke** in Settings, then
**Connect Google** again and approve all requested scopes.

**Token expired / refresh failing**
→ Delete `~/.localmind/google_token.json` and re-run the connect flow.
Alternatively, hit `POST http://localhost:8000/api/google/refresh` to force a
refresh while the refresh token is still valid.

**Tool fails with "insufficient authentication scopes" or
`invalid_scope`**
→ The tool you invoked needs a scope that's not in the central SCOPES list
(Calendar is the known case). Add the scope to the list in
`backend/routes/google_auth.py`, restart LocalMind, and re-run the Connect
flow to grant it.

**Google quota errors (429)**
→ The free Gmail / Drive API quotas are generous but finite. Wait a minute,
or check quota usage under **APIs & Services → Quotas**.

---

## 9. Revoking Access

Two options:

1. In LocalMind: **Settings → Integrations → Google → Disconnect** (or
   `POST /api/google/revoke`). This deletes the local token and calls
   Google's revoke endpoint.
2. In your Google account: <https://myaccount.google.com/permissions>, find
   **LocalMind**, click it, then **Remove access**.

Doing both is safe. After revocation, restart the Connect flow from step 7
whenever you want LocalMind to access Google again.
