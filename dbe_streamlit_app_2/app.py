import streamlit as st
import asyncio
import requests
import html
import re
from uuid import uuid4
import streamlit.components.v1 as components

from config import driver, embedding, ensure_indexes
from retrievers import get_retriever
from agents import run_agentic_query


# ------------------------------------------------------------------
# Feedback -> Google Form (unchanged from the original app)
# ------------------------------------------------------------------
def submit_feedback_to_google_form(question, response, expected, name="Anonymous"):
    try:
        form_url = "https://docs.google.com/forms/d/e/1FAIpQLSfEtI-JSufC8LeU3fDliRQjqicIuf__Bb_T-ONlorI7h-zjmQ/formResponse"
        clean_name = name.strip() if name and name.strip() else "Anonymous"
        form_data = {
            'entry.984811739': question,
            'entry.1057753121': response,
            'entry.876338256': expected,
            'entry.649720090': f"{clean_name}",
        }
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate',
            'Referer': 'https://docs.google.com/forms/d/e/1FAIpQLSfEtI-JSufC8LeU3fDliRQjqicIuf__Bb_T-ONlorI7h-zjmQ/viewform',
        }
        result = requests.post(form_url, data=form_data, headers=headers, timeout=20, allow_redirects=True)
        return result.status_code in [200, 302] or 'Thank you' in result.text
    except requests.exceptions.Timeout:
        return True
    except Exception:
        return False


st.set_page_config(page_title="Faculty RAG QA", layout="wide")
st.title("🔎  DBE faculty expertise and scientific interests")

# Create indexes once (idempotent, cached for the session)
@st.cache_resource
def _init_indexes():
    ensure_indexes()
    return True
_init_indexes()


st.markdown(
    """
    <style>
      .main .block-container { max-width: none; padding-top: 1rem; padding-left: 1rem; padding-right: 1rem; }
      .stButton > button { height: 38px; padding: 0 12px; border-radius: 6px; }
      div[data-baseweb="select"] { min-height: 38px; }
      div[data-baseweb="select"] > div { min-height: 38px; }
      .control-spacer { height: 28px; }
      .report-box { border-left: 3px solid #ff9800; background: #fff8e1; padding: 1rem; margin: 0.5rem 0; border-radius: 4px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
      .msg-actions { display: flex; justify-content: flex-end; gap: 0.5rem; margin-top: 0.35rem; opacity: 0.85; }
    </style>
    """,
    unsafe_allow_html=True,
)


# ------------------------------------------------------------------
# Utilities
# ------------------------------------------------------------------
def _md_basic_to_html(text: str) -> str:
    if text is None:
        return ""
    escaped = html.escape(text)
    escaped = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2" target="_blank" rel="noopener noreferrer">\1</a>', escaped)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"__(.+?)__", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"(?<!_)_(.+?)_(?!_)", r"<em>\1</em>", escaped)
    escaped = escaped.replace("\n", "<br>")
    return escaped


def format_pipeline_results(agent_name, results):
    """Turn the pipeline's [{faculty_name, information:[...]}] into a chat answer."""
    if not results:
        return "No matching faculty were found for that question."
    lines = []
    for item in results:
        name = item.get("faculty_name", "Unknown")
        info = item.get("information", [])
        lines.append(f"**{name}**")
        if isinstance(info, list):
            for point in info:
                lines.append(f"- {point}")
        else:
            lines.append(f"- {info}")
        lines.append("")
    return "\n".join(lines).strip()


# ------------------------------------------------------------------
# Persistence across refresh using URL-bound client id
# ------------------------------------------------------------------
@st.cache_resource
def message_store():
    return {}

if 'client_id' not in st.session_state:
    client_id = None
    try:
        qp = getattr(st, 'query_params', {})
        existing = qp.get('cid') if isinstance(qp, dict) else None
        if isinstance(existing, list):
            existing = existing[0] if existing else None
        if existing:
            client_id = existing
        else:
            client_id = str(uuid4())
            try:
                st.query_params['cid'] = client_id
            except Exception:
                pass
    except Exception:
        client_id = str(uuid4())
    st.session_state.client_id = client_id

store = message_store()

if 'messages' not in st.session_state:
    st.session_state.messages = list(store.get(st.session_state.client_id, [])) or []


# ------------------------------------------------------------------
# Header controls — Hybrid (option 1) first, Vector (option 2) second
# ------------------------------------------------------------------
left_col, right_col = st.columns([0.75, 0.25])
with left_col:
    st.markdown("**Choose Retriever Method**")
    retriever_choice = st.selectbox(
        "Choose Retriever Method",
        ["Hybrid", "Vector"],   # index 0 = Hybrid (option 1), index 1 = Vector (option 2)
        index=0,
        label_visibility="collapsed",
    )
with right_col:
    st.markdown("<div class='control-spacer'></div>", unsafe_allow_html=True)
    if st.button("Clear Chat", use_container_width=True):
        st.session_state.messages = []
        store[st.session_state.client_id] = []
        st.session_state.feedback_query = ""
        st.session_state.feedback_response = ""
        st.session_state.feedback_ready = False
        st.session_state.pop('reporting_message_idx', None)
        st.rerun()


# ------------------------------------------------------------------
# Chat window
# ------------------------------------------------------------------
chat_container = st.container()
with chat_container:
    chat_html = """
    <style>
      :root { color-scheme: light dark; --chat-text-color: #111111; }
      @media (prefers-color-scheme: dark) { :root { --chat-text-color: #ffffff; } }
      body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
      .chat-container { background: transparent; border: 1px solid #262730; border-radius: 6px; padding: 0.75rem; height: 600px; overflow-y: auto; }
      .chat-message { margin-bottom: 0.75rem; padding: 0.1rem 0; background: transparent; color: var(--chat-text-color); font-size: 1.05rem; }
      .chat-message.user { text-align: right; }
      .chat-message.assistant { text-align: left; }
      .message-role { font-weight: 600; margin-bottom: 0.25rem; font-size: 0.9rem; opacity: 0.9; color: var(--chat-text-color); }
      .message-content { line-height: 1.55; color: var(--chat-text-color); font-size: 1.05rem; white-space: pre-wrap; }
    </style>
    <div id='chat-box' class='chat-container'>
    """
    if not st.session_state.messages:
        chat_html += "<p><strong>Welcome!</strong> Ask me anything about DBE faculty expertise and research interests.</p>"
    else:
        for message in st.session_state.messages:
            role_class = "user" if message["role"] == "user" else "assistant"
            role_display = "You" if message["role"] == "user" else "Assistant"
            safe_content = _md_basic_to_html(message['content'])
            chat_html += (
                f"<div class='chat-message {role_class}'>"
                f"  <div class='message-role'>{role_display}</div>"
                f"  <div class='message-content'>{safe_content}</div>"
                f"</div>"
            )
    chat_html += "</div><script>const el=document.getElementById('chat-box'); if(el){el.scrollTop=el.scrollHeight;}</script>"
    components.html(chat_html, height=650, scrolling=False)

    # "Report an issue" for the latest assistant response
    latest_assistant_idx = None
    for i in range(len(st.session_state.messages) - 1, -1, -1):
        if st.session_state.messages[i]["role"] == "assistant":
            latest_assistant_idx = i
            break
    if latest_assistant_idx is not None:
        st.markdown("<div class='msg-actions'>", unsafe_allow_html=True)
        if st.button("Report an issue", key="report_btn_latest"):
            st.session_state.reporting_message_idx = latest_assistant_idx
        st.markdown("</div>", unsafe_allow_html=True)

        if st.session_state.get('reporting_message_idx') == latest_assistant_idx:
            st.markdown("<div class='report-box'>", unsafe_allow_html=True)
            st.markdown("**Report an issue with this response:**")
            report_text = st.text_area("What's wrong with this answer?", key="report_text_latest", height=100,
                                       placeholder="Describe the issue or what you expected instead...")
            name_input = st.text_input("Your Name (Optional)", key="report_name_latest", placeholder="Anonymous")
            col1, col2, _ = st.columns([1, 1, 3])
            with col1:
                submit_pressed = st.button("Submit", key="report_submit_latest")
            with col2:
                cancel_pressed = st.button("Cancel", key="report_cancel_latest")
            if cancel_pressed:
                st.session_state.pop('reporting_message_idx', None)
                st.rerun()
            if submit_pressed:
                if report_text and report_text.strip():
                    user_question = ""
                    if latest_assistant_idx > 0 and st.session_state.messages[latest_assistant_idx - 1]["role"] == "user":
                        user_question = st.session_state.messages[latest_assistant_idx - 1]["content"]
                    success = submit_feedback_to_google_form(
                        question=user_question or st.session_state.get('feedback_query', ''),
                        response=st.session_state.messages[latest_assistant_idx]["content"],
                        expected=report_text,
                        name=(name_input or 'Anonymous'),
                    )
                    if success:
                        st.success("Report submitted successfully!")
                        st.session_state.pop('reporting_message_idx', None)
                        st.rerun()
                    else:
                        st.error("Unable to submit report. Please try again.")
                else:
                    st.error("Please describe the issue before submitting.")
            st.markdown("</div>", unsafe_allow_html=True)


# Feedback state
st.session_state.setdefault('feedback_query', "")
st.session_state.setdefault('feedback_response', "")
st.session_state.setdefault('feedback_ready', False)


# ------------------------------------------------------------------
# Chat input -> agentic pipeline
# ------------------------------------------------------------------
user_prompt = st.chat_input("Ask me anything about DBE faculty expertise and research...")

if user_prompt:
    st.session_state.messages.append({"role": "user", "content": user_prompt})
    store[st.session_state.client_id] = st.session_state.messages

    with st.spinner("Running query..."):
        retriever, choice = get_retriever(retriever_choice, driver, embedding)
        # Match the notebook: pass the raw question straight to the pipeline
        # (the notebook's run_query does NOT Lucene-escape). Escaping here would
        # change the Hybrid fulltext search input and diverge from the notebook.
        query_for_search = user_prompt

        # Run each query on its own fresh event loop. asyncio.run creates and
        # closes a new loop per call, so the per-loop semaphore in llm_utils is
        # always bound to the loop it's used on (fixes the "bound to a different
        # event loop" error on the 2nd question).
        try:
            agent_name, results = asyncio.run(
                run_agentic_query(query_for_search, retriever, choice=choice)
            )
            final_answer = format_pipeline_results(agent_name, results)
        except Exception as e:
            final_answer = f"Error while running the query: {e}"

    st.session_state.messages.append({"role": "assistant", "content": final_answer})
    store[st.session_state.client_id] = st.session_state.messages
    st.session_state.feedback_query = user_prompt
    st.session_state.feedback_response = final_answer
    st.session_state.feedback_ready = True
    st.rerun()
