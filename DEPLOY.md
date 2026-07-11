# Hosting Dubbing Studio V2 online

## Option A (recommended for you): Streamlit Community Cloud

`streamlit_app.py` is a full Streamlit port of the app — same flow, same
credit-efficiency rules. Files needed in the repo: `streamlit_app.py`,
`app.py` (imported for the transcript-matching logic), `requirements.txt`.

1. Push those three files to a GitHub repo (private works with Streamlit Cloud).
2. On https://share.streamlit.io → New app → pick the repo, main file
   `streamlit_app.py`.
3. App settings → Secrets:
   ```toml
   ELEVENLABS_API_KEY = "xi-..."
   APP_PASSWORD = "a-long-shared-password"
   ```
4. Deploy and share the URL + password.

Notes:
- The project/language ids are kept in the URL, so a page refresh or a shared
  link resumes the same project. Unsaved right-column drafts are per-session
  and lost on refresh — everything saved lives in your ElevenLabs workspace.
- Community Cloud apps sleep after inactivity; the first visit wakes them in
  ~30 s. Dubbing jobs keep running on ElevenLabs' side regardless.

## Option B: any Python host (Render, Railway, VPS) with the original UI

The app is a single Python file (stdlib only — no requirements.txt needed) that
serves the UI and keeps the ElevenLabs API key server-side. Access is gated by
a shared password: anyone with the link must enter it before any API call works.

## Configuration

Set these environment variables on the host (or locally in `secrets.json`,
see `secrets.json.example`):

| Variable | Purpose |
|---|---|
| `ELEVENLABS_API_KEY` | Your ElevenLabs key. Never reaches the browser. |
| `APP_PASSWORD` | Shared access password. Pick something long. |
| `PORT` | Set automatically by most hosts. Presence of PORT also switches binding to 0.0.0.0. |

With `ELEVENLABS_API_KEY` set, the UI hides the API-key field entirely.
With `APP_PASSWORD` set, the UI shows an "Access password" field; every API
route returns 401 without the correct password. Without either (local use),
everything behaves as before.

## Deploy to Render (free tier, HTTPS included)

1. Push `app.py`, `index.html`, and `DEPLOY.md` to a **private** GitHub repo
   (do NOT push `secrets.json` if you created one).
2. On https://render.com → New → Web Service → connect the repo.
3. Settings:
   - Runtime: Python
   - Build command: *(leave empty — no dependencies)*
   - Start command: `python app.py`
4. Environment → add `ELEVENLABS_API_KEY` and `APP_PASSWORD`.
5. Deploy. Share the `https://<name>.onrender.com` URL + password with your team.

Note: Render's free tier spins the service down after ~15 min of inactivity;
the first request after idle takes ~30–60 s to wake it. This is fine for this
tool — dubbing jobs run on ElevenLabs' side, and all project state lives in
your ElevenLabs workspace (plus per-browser drafts), not on the server.

Railway, Fly.io, or any VPS with Python 3 work the same way — set the two env
vars and run `python app.py`.

## Security notes

- Always use a host that serves HTTPS (Render/Railway do by default); the
  password travels in a request header.
- Wrong-password attempts are delayed 0.5 s to blunt brute-forcing.
- The password grants use of YOUR ElevenLabs credits — treat it accordingly
  and rotate it (redeploy with a new `APP_PASSWORD`) when someone leaves.
