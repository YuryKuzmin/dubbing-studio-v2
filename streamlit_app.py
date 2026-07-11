"""
Dubbing Studio V2 — Streamlit version for Streamlit Community Cloud.

Secrets (App settings -> Secrets, TOML):
    ELEVENLABS_API_KEY = "xi-..."
    APP_PASSWORD = "a-long-shared-password"   # optional but recommended

Same credit-efficient flow as the local app:
create -> free source review (with proofread-transcript matching) -> one charged
generation -> edit only changed segments -> one charged regenerate.
"""

import hmac
import json
import os
import time
import urllib.error
import urllib.request
import uuid

import streamlit as st

from app import align_transcript

ELEVEN_BASE = "https://api.elevenlabs.io"

st.set_page_config(page_title="Dubbing Studio V2", page_icon="🎙️", layout="wide")


# ---------------- secrets / config ----------------

def secret(name, default=""):
    try:
        return st.secrets[name]
    except (KeyError, FileNotFoundError):
        return os.environ.get(name, default)


API_KEY = secret("ELEVENLABS_API_KEY")
APP_PASSWORD = secret("APP_PASSWORD")


# ---------------- password gate (no accounts) ----------------

if APP_PASSWORD and not st.session_state.get("authed"):
    st.title("🎙️ Dubbing Studio V2")
    pw = st.text_input("Access password", type="password")
    if pw:
        if hmac.compare_digest(pw, APP_PASSWORD):
            st.session_state.authed = True
            st.rerun()
        else:
            time.sleep(0.5)  # slow down guessing
            st.error("Wrong password.")
    st.stop()

if not API_KEY:
    st.error("ELEVENLABS_API_KEY is missing from Streamlit secrets.")
    st.stop()


# ---------------- ElevenLabs client ----------------

def api(method, path, payload=None):
    req = urllib.request.Request(ELEVEN_BASE + path, method=method)
    req.add_header("xi-api-key", API_KEY)
    data = None
    if payload is not None:
        req.add_header("Content-Type", "application/json")
        data = json.dumps(payload).encode("utf-8")
    try:
        with urllib.request.urlopen(req, data=data, timeout=120) as resp:
            body = resp.read()
            return json.loads(body) if body else None
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        try:
            d = json.loads(detail)
            detail = d.get("detail", {}).get("message") or json.dumps(d)
        except Exception:
            pass
        raise RuntimeError(f"ElevenLabs {e.code}: {detail}") from None


def api_multipart(path, fields):
    boundary = "----dubstudio" + uuid.uuid4().hex
    lines = []
    for k, v in fields.items():
        lines += [f"--{boundary}", f'Content-Disposition: form-data; name="{k}"', "", str(v)]
    lines += [f"--{boundary}--", ""]
    body = "\r\n".join(lines).encode("utf-8")
    req = urllib.request.Request(ELEVEN_BASE + path, data=body, method="POST")
    req.add_header("xi-api-key", API_KEY)
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"ElevenLabs {e.code}: {e.read().decode('utf-8', errors='replace')}") from None


# ---------------- state helpers ----------------
# project/language ids live in the URL query params, so a page refresh or a
# shared link resumes the same project; drafts live in session_state only.

def get_ids():
    return st.query_params.get("project"), st.query_params.get("lang")


def set_ids(project_id=None, language_id=None):
    if project_id:
        st.query_params["project"] = project_id
    if language_id:
        st.query_params["lang"] = language_id


def reset_ids():
    st.query_params.clear()
    for k in list(st.session_state):
        if k.startswith(("draft_", "src_")) or k in ("match_results",):
            del st.session_state[k]


def auto_refresh(seconds=4):
    time.sleep(seconds)
    st.rerun()


def fmt_time(s):
    return f"{int(s // 60)}:{s % 60:04.1f}"


LANGS = ["hi", "ta", "te", "kn", "ml", "bn", "mr", "gu", "pa", "ru",
         "es", "fr", "de", "pt", "zh", "ja", "ar", "en"]

st.title("🎙️ Dubbing Studio V2")
project_id, language_id = get_ids()


# ================= 1. create / resume =================

if not project_id:
    st.subheader("1 · New dubbing project")
    with st.form("create"):
        source_url = st.text_input("Source video URL (YouTube Shorts / long-form)")
        c1, c2, c3 = st.columns(3)
        source_lang = c1.text_input("Source language (blank = auto-detect)", placeholder="en")
        target_lang = c2.selectbox("Target language", LANGS, index=None, placeholder="choose…",
                                   accept_new_options=True)
        reference = c3.text_input("Reference label (optional)")
        submitted = st.form_submit_button("Create project & transcribe", type="primary")
    st.caption("Transcription itself is not a dub generation — the paid step comes later.")

    if submitted:
        if not source_url.strip():
            st.error("Enter a source video URL.")
        elif not target_lang:
            st.error("Choose a target language.")
        else:
            fields = {"source_url": source_url.strip(), "model_id": "dubbing_v2"}
            if source_lang.strip():
                fields["source_language"] = source_lang.strip()
            if reference.strip():
                fields["reference"] = reference.strip()
            try:
                proj = api_multipart("/v1/dubbing/project", fields)
                st.session_state["target_lang"] = target_lang
                set_ids(project_id=proj["project_id"])
                st.rerun()
            except RuntimeError as e:
                st.error(str(e))

    st.divider()
    st.subheader("…or resume an existing project")
    rc1, rc2 = st.columns([3, 1])
    rid = rc1.text_input("Project id", placeholder="proj_...", label_visibility="collapsed")
    if rc2.button("Resume") and rid.strip():
        set_ids(project_id=rid.strip())
        st.rerun()
    st.stop()


# ================= 2. wait for project ready =================

try:
    project = api("GET", f"/v1/dubbing/project/{project_id}")
except RuntimeError as e:
    st.error(str(e))
    if st.button("Start over"):
        reset_ids()
        st.rerun()
    st.stop()

st.caption(f"Project `{project_id}` · {project.get('reference') or 'no label'}")
if st.button("← Different project"):
    reset_ids()
    st.rerun()

if project["status"] in ("queued", "preparing"):
    with st.status(f"Preparing project — {project['status']}…", expanded=True):
        st.write("Fetching and transcribing the source. This can take a few minutes.")
    auto_refresh()
elif project["status"] == "failed":
    st.error("Project preparation failed — the source could not be fetched or decoded. "
             "Check that the URL is publicly reachable, then create a new project.")
    st.stop()

# find this project's language (we use one target language per project here)
if not language_id:
    langs = api("GET", f"/v1/dubbing/project/{project_id}/language").get("languages", [])
    if langs:
        language_id = langs[0]["language_id"]
        set_ids(language_id=language_id)


# ================= 3. source review (free) =================

if not language_id:
    st.subheader("2 · Review the source transcript  🟢 free to edit")
    st.info("Fix transcription mistakes **now**, before dubbing. Source edits cost nothing; "
            "fixing them after the dub exists forces another paid regeneration.")

    tr = api("GET", f"/v1/dubbing/project/{project_id}/transcript")
    segments = tr.get("segments", [])

    with st.expander("Have a proofread transcript? Match it to their segmentation", expanded=True):
        proof = st.text_area("Paste the full proofread transcript",
                             height=160, key="proof_text",
                             placeholder="It will be redistributed into ElevenLabs' timing-aligned "
                                         "segments; only differing segments get updated.")
        if st.button("Match against segmentation") and proof.strip():
            results = align_transcript(
                [{"id": s["id"], "text": s["text"]} for s in segments], proof.strip())
            st.session_state["match_results"] = {r["id"]: r for r in results if r["changed"]}
            if not st.session_state["match_results"]:
                st.success("Transcript already matches — nothing to change.")
            else:
                # pre-fill the editors below with the matched text
                for r in st.session_state["match_results"].values():
                    st.session_state[f"src_{r['id']}"] = r["new_text"]
        matches = st.session_state.get("match_results") or {}
        if matches:
            n_flag = sum(1 for r in matches.values() if r["flagged"])
            st.warning(f"{len(matches)} of {len(segments)} segments differ"
                       + (f" — {n_flag} marked ⚠ need a manual look (a correction sits on a "
                          f"segment boundary)" if n_flag else "")
                       + ". Review below, then save. Nothing is sent until you save.")

    st.write("")
    for seg in segments:
        c1, c2, c3 = st.columns([2, 10, 1])
        c1.caption(f"{fmt_time(seg['start_s'])} – {fmt_time(seg['end_s'])}")
        c2.text_area("text", value=seg["text"], key=f"src_{seg['id']}",
                     label_visibility="collapsed", height=68)
        m = (st.session_state.get("match_results") or {}).get(seg["id"])
        if m and m["flagged"]:
            c3.markdown("⚠")

    edited = [s for s in segments
              if st.session_state.get(f"src_{s['id']}", s["text"]).strip() != s["text"]]
    b1, b2 = st.columns([1, 2])
    if edited and b1.button(f"Save {len(edited)} edited segment(s) — free", type="secondary"):
        prog = st.progress(0.0)
        try:
            for i, seg in enumerate(edited):
                api("PATCH", f"/v1/dubbing/project/{project_id}/transcript/segment/{seg['id']}",
                    {"text": st.session_state[f"src_{seg['id']}"].strip()})
                prog.progress((i + 1) / len(edited))
            st.session_state.pop("match_results", None)
            st.success(f"Saved {len(edited)} segments. No generation was triggered.")
            time.sleep(1)
            st.rerun()
        except RuntimeError as e:
            st.error(str(e))

    st.divider()
    st.subheader("3 · Generate the dub")
    target = st.selectbox("Target language", LANGS,
                          index=LANGS.index(st.session_state.get("target_lang"))
                          if st.session_state.get("target_lang") in LANGS else None,
                          placeholder="choose…", accept_new_options=True, key="target_pick")
    if edited:
        st.warning("You have unsaved source edits above — save them first, or they won't be dubbed.")
    if st.button("Source looks right — translate & generate dub  ⚠ first charged generation",
                 type="primary", disabled=not target or bool(edited)):
        try:
            lang = api("POST", f"/v1/dubbing/project/{project_id}/language",
                       {"target_language": target})
            set_ids(language_id=lang["language_id"])
            st.rerun()
        except RuntimeError as e:
            st.error(str(e))
    st.stop()


# ================= 4. wait for dub =================

lang = api("GET", f"/v1/dubbing/project/{project_id}/language/{language_id}")

if lang["status"] in ("queued", "processing"):
    with st.status(f"Generating dub ({lang['target_language']}) — {lang['status']}…", expanded=True):
        st.write("Translating and generating audio. This can take several minutes.")
    auto_refresh(5)
elif lang["status"] == "failed":
    st.error("Dub generation failed. Regenerate below to retry, or contact ElevenLabs if it persists.")


# ================= 5. review translation, edit, regenerate =================

tr = api("GET", f"/v1/dubbing/project/{project_id}/language/{language_id}/transcript")
segments = tr.get("segments", [])

badge = {"completed": "🟢", "stale": "🟠", "failed": "🔴"}.get(lang["status"], "⚪")
st.subheader(f"3 · Refine the translation  {badge} {lang['status']}")
st.caption(f"{tr.get('source_language', '?')} → {tr['target_language']} · "
           f"transcript revision {tr['revision']}, audio from revision {lang.get('output_revision', '—')}")

audio_url = (lang.get("outputs") or {}).get("lossless_audio")
if audio_url:
    st.audio(audio_url)
    st.link_button("Download audio (link valid ~1 h — refresh the page for a fresh one)", audio_url)
if lang["status"] == "stale":
    st.warning("The transcript changed after this audio was generated — regenerate to update it.")


def copy_translation(seg_id, text):
    st.session_state[f"draft_{seg_id}"] = text


def changed_segments():
    out = []
    for seg in segments:
        d = st.session_state.get(f"draft_{seg['id']}", "").strip()
        if d and d != (seg.get("translation") or "").strip():
            out.append((seg, d))
    return out


def do_regenerate():
    changed = changed_segments()
    prog = st.progress(0.0, text="Sending edited segments (free)…")
    try:
        for i, (seg, draft) in enumerate(changed):
            api("PATCH",
                f"/v1/dubbing/project/{project_id}/language/{language_id}/transcript/segment/{seg['id']}",
                {"translation": draft})
            prog.progress((i + 1) / max(1, len(changed)))
        api("POST", f"/v1/dubbing/project/{project_id}/language/{language_id}/transcript/regenerate")
        for seg, _ in changed:
            st.session_state.pop(f"draft_{seg['id']}", None)
        st.rerun()
    except RuntimeError as e:
        st.error(str(e))


n_changed = len(changed_segments())
st.write("")
top_label = (f"Apply {n_changed} change(s) & regenerate  ⚠ charged"
             if n_changed else "Regenerate (no changes yet)  ⚠ charged")
if st.button(top_label, type="primary", key="regen_top"):
    do_regenerate()
st.caption("Only filled-in segments on the right are sent. Regeneration is charged like a full "
           "generation — batch all edits, then regenerate **once**.")

h1, h2, h3 = st.columns([10, 1, 10])
h1.markdown("**ElevenLabs translation**")
h3.markdown("**Your changes**")
for seg in segments:
    c1, c2, c3 = st.columns([10, 1, 10], vertical_alignment="center")
    with c1:
        st.caption(seg["source_text"])
        st.write(seg.get("translation") or "*(not translated yet)*")
        st.caption(f"{fmt_time(seg['start_s'])} – {fmt_time(seg['end_s'])} · {seg['speaker_id']}")
    c2.button("→", key=f"copy_{seg['id']}", on_click=copy_translation,
              args=(seg["id"], seg.get("translation") or ""),
              help="Copy the translation into the editing column")
    c3.text_area("draft", key=f"draft_{seg['id']}", label_visibility="collapsed", height=100,
                 placeholder="Leave empty to keep the translation on the left")

n_changed = len(changed_segments())
if st.button(f"Apply {n_changed} change(s) & regenerate  ⚠ charged" if n_changed
             else "Regenerate (no changes yet)  ⚠ charged",
             type="primary", key="regen_bottom"):
    do_regenerate()
