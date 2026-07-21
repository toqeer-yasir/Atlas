import base64
import streamlit as st
import websockets
import json
import requests
import asyncio
from uuid import uuid4
from email_validator import validate_email, EmailNotValidError

BASE_URL = "http://localhost:8000"
WS_URL = "ws://localhost:8000/ws/chat"

st.set_page_config(
    page_title="Agentic AI Chatbot",
    page_icon="🤖",
    layout="centered",
    initial_sidebar_state="expanded",
)

st.markdown(
    "<h3 style='text-align: center;'>Rag Agent</h3>",
    unsafe_allow_html=True,
)


# session state
if "user" not in st.session_state:
    st.session_state.user = None

if "thread_id" not in st.session_state:
    st.session_state.thread_id = None

if "pending_interrupt" not in st.session_state:
    st.session_state.pending_interrupt = None

if "messages_history" not in st.session_state:
    st.session_state.messages_history = []

if "documents" not in st.session_state:
    st.session_state.documents = {}

if "threads_and_titles" not in st.session_state:
    st.session_state.threads_and_titles = []


# helper functions

def generate_uuid4():
    """to generate uuid4"""
    return str(uuid4())


def new_chat():
    """Start a brand new, empty conversation thread."""
    st.session_state.thread_id = generate_uuid4()
    st.session_state.messages_history = []
    st.session_state.pending_interrupt = None


def fetch_threads_and_titles() -> list[dict]:
    if not st.session_state.user:
        return []
    try:
        response = requests.get(f"{BASE_URL}/users/{st.session_state.user}/chats", timeout=10)
        response.raise_for_status()
        return response.json().get("chats", [])
    except Exception:
        return []


def fetch_messages(thread_id: str) -> list[dict]:
    try:
        response = requests.get(f"{BASE_URL}/chats/{thread_id}/messages", timeout=10)
        response.raise_for_status()
        return response.json().get("messages", [])

    except Exception:
        return []


def push_message(
    user_id: str,
    message_id: str,
    chat_id: str,
    user_content: str,
    assistant_content: str,
):
    try:
        response = requests.post(
            f"{BASE_URL}/messages",
            json={
                "user_id": user_id,
                "message_id": message_id,
                "chat_id": chat_id,
                "user_content": user_content,
                "assistant_content": assistant_content,
            },
            timeout=10,
        )
        response.raise_for_status()
        return response.json()
    except Exception as e:
        st.warning(f"Couldn't save this message to history: {e}")
        return None


def load_thread(thread_id: str):
    """Switch to an existing thread and pull its message history."""
    st.session_state.thread_id = thread_id
    st.session_state.pending_interrupt = None

    raw_messages = fetch_messages(thread_id)
    history = [
        {"role": msg["role"], "content": msg["content"]}
        for msg in raw_messages
    ]
    st.session_state.messages_history = history


def add_document(
    document_id: str,
    user_id: str,
    filename: str,
    file_bytes: bytes,
):
    try:
        file_content_b64 = base64.b64encode(file_bytes).decode("utf-8")

        response = requests.post(
            f"{BASE_URL}/documents",
            json={
                "document_id": document_id,
                "user_id": user_id,
                "filename": filename,
                "file_content": file_content_b64,
            },
            timeout=30,
        )
        response.raise_for_status()
        return response.json()["document_id"]

    except Exception as e:
        st.warning(f"Couldn't add this document to database or rag: {e}")
        return None


def delete_document(user_id: str, document_id: str):
    try:
        response = requests.delete(
            f"{BASE_URL}/users/{user_id}/documents/{document_id}",
            timeout=10,
        )
        response.raise_for_status()
        return True
    except Exception as e:
        st.warning(f"Couldn't delete this document: {e}")
        return False


def fetch_user_documents(user_id: str):
    try:
        response = requests.get(f"{BASE_URL}/users/{user_id}/documents", timeout=10)
        response.raise_for_status()
        docs = {}
        for doc in response.json()["documents"]:
            docs[doc["id"]] = doc["filename"]

        return docs

    except Exception:
        return {}


#usr authentication
if st.session_state.user is None:

    st.title("Welcome 👋")

    signin_tab, signup_tab = st.tabs(["🔐 Sign In", "📝 Sign Up"])

    #sign-in
    with signin_tab:

        with st.form("signin_form"):

            email = st.text_input("Email")
            password = st.text_input(
                "Password",
                type="password",
            )

            signin = st.form_submit_button(
                "Sign In",
                use_container_width=True,
            )

        if signin:

            if not email or not password:
                st.warning("Please fill in all fields.")
                st.stop()

            try:

                email = validate_email(
                    email,
                    check_deliverability=True,
                ).normalized

            except EmailNotValidError as e:
                st.error(str(e))
                st.stop()

            try:

                response = requests.post(
                    f"{BASE_URL}/auth/login",
                    json={
                        "email": email,
                        "password": password,
                    },
                    timeout=10,
                )

                response.raise_for_status()

                data = response.json()

                st.session_state.user = data["user"]["id"]

                st.session_state.documents = fetch_user_documents(
                    st.session_state.user
                )

                new_chat()

                st.rerun()

            except requests.HTTPError:

                if response.status_code == 401:
                    st.error("Invalid email or password.")
                elif response.status_code == 404:
                    st.error("No account exists with this email.")
                else:
                    st.error("Login failed.")

            except Exception as e:
                st.error(f"Backend error: {e}")

    # sing-up
    with signup_tab:

        with st.form("signup_form"):

            name = st.text_input("Name")

            email = st.text_input("Email")

            password = st.text_input(
                "Password",
                type="password",
            )

            confirm_password = st.text_input(
                "Confirm Password",
                type="password",
            )

            signup = st.form_submit_button(
                "Create Account",
                use_container_width=True,
            )

        if signup:

            if not name or not email or not password or not confirm_password:
                st.warning("Please fill in all fields.")
                st.stop()

            if password != confirm_password:
                st.error("Passwords do not match.")
                st.stop()

            try:

                email = validate_email(
                    email,
                    check_deliverability=True,
                ).normalized

            except EmailNotValidError as e:
                st.error(str(e))
                st.stop()

            try:

                response = requests.post(
                    f"{BASE_URL}/users",
                    json={
                        "user_id": generate_uuid4(),
                        "user_name": name,
                        "user_email": email,
                        "password": password,
                    },
                    timeout=10,
                )

                response.raise_for_status()

                data = response.json()

                st.session_state.user = data["user"]["id"]

                st.session_state.documents = fetch_user_documents(
                    st.session_state.user
                )

                new_chat()

                st.success("Account created successfully!")

                st.rerun()

            except requests.HTTPError:

                if response.status_code == 409:
                    st.error("An account with this email already exists.")
                else:
                    st.error("Registration failed.")

            except Exception as e:
                st.error(f"Backend error: {e}")

    st.stop()


USER_ID = st.session_state.user

if st.session_state.thread_id is None:
    new_chat()

THREAD_ID = st.session_state.thread_id


# sidebar: chat history
with st.sidebar:
    st.markdown("### Chats")

    if st.button("➕ New chat", use_container_width=False):
        new_chat()
        st.rerun()

    st.session_state.threads_and_titles = fetch_threads_and_titles()

    # rag documents
    st.markdown("### Upload a document")

    uploaded_file = st.file_uploader(
        "Choose a file",
        type=["pdf", "txt", "docx", "csv", "doc", "md"],
        key="rag_file_uploader",
    )

    add_to_rag_clicked = st.button(
        "➕ Add to RAG",
        use_container_width=True,
        disabled=uploaded_file is None,
    )

    if add_to_rag_clicked and uploaded_file is not None:
        doc_id = add_document(
            generate_uuid4(),
            st.session_state.user,
            uploaded_file.name,
            uploaded_file.getvalue(),
        )
        if doc_id:
            st.session_state.documents[doc_id] = uploaded_file.name
            st.rerun()

    for d_id, d_name in list(st.session_state.documents.items()):
        col1, col2 = st.columns([4, 1])
        col1.write(d_name)
        if col2.button("🗑️", key=f"delete_{d_id}"):
            if delete_document(st.session_state.user, d_id):
                del st.session_state.documents[d_id]
                st.rerun()

    #-------------------------------------------

    st.divider()
    for thread in st.session_state.threads_and_titles:
        tid = thread.get("id")
        title = thread.get("title") or "Untitled chat"
        is_active = tid == st.session_state.thread_id
        if st.button(
            ("👉 " if is_active else "") + title,
            key=f"thread_{tid}",
            use_container_width=True,
        ):
            load_thread(tid)
            st.rerun()


# websocket streaming
async def stream_turn(payload: dict, placeholder):
    assistant_response = ""

    async with websockets.connect(WS_URL) as websocket:
        await websocket.send(json.dumps(payload))

        async for raw in websocket:
            data = json.loads(raw)

            if data["type"] == "content":
                assistant_response += data["content"]
                placeholder.markdown(assistant_response + "┃")

            elif data["type"] == "interrupt":
                placeholder.markdown(assistant_response or "_Waiting for your approval..._")
                return assistant_response, data["content"], None

            elif data["type"] == "completed":
                placeholder.markdown(assistant_response)
                return assistant_response, None, None

            elif data["type"] == "error":
                placeholder.markdown(f"⚠️ {data['content']}")
                error_info = {
                    "content": data["content"],
                    "debug": data.get("debug"),
                    "category": data.get("category"),
                }
                return assistant_response, None, error_info

    return assistant_response, None, None


def run_turn_and_store(payload: dict, user_content: str | None = None):
    try:
        with st.chat_message("assistant"):
            placeholder = st.empty()
            placeholder.markdown("_Thinking..._")
            assistant_response, interrupt_content, error_info = asyncio.run(
                stream_turn(payload, placeholder)
            )
    except Exception as e:
        st.error(f"Connection error: {e}")
        return

    if assistant_response:
        st.session_state.messages_history.append(
            {"role": "assistant", "content": assistant_response}
        )

    if error_info:
        st.session_state.messages_history.append(
            {"role": "error", "content": error_info["content"], "debug": error_info.get("debug"), "category": error_info.get("category")}
        )

    if not interrupt_content and not error_info and assistant_response and user_content:
        push_message(
            user_id=USER_ID,
            message_id=generate_uuid4(),
            chat_id=THREAD_ID,
            user_content=user_content,
            assistant_content=assistant_response,
        )

    st.session_state.pending_interrupt = interrupt_content
    st.rerun()

#rendering existing conversation
for turn in st.session_state.messages_history:
    if turn["role"] == "error":
        with st.chat_message("assistant"):
            st.markdown(f"⚠️ {turn['content']}")
            if turn.get("debug"):
                with st.expander(f"🔧 Debug details ({turn.get('category')})"):
                    st.code(turn["debug"], language="text")
    else:
        with st.chat_message(turn["role"]):
            st.write(turn["content"])


# pending tool approval UI
if st.session_state.pending_interrupt:
    tools = st.session_state.pending_interrupt.get("tools", [])

    with st.chat_message("assistant"):
        st.warning("Approval required before I continue!")

        rejected_by_id = {}
        for tc in tools:
            st.write(f"**Tool:** {tc['name']}")
            st.code(tc["command"], language="bash")
            for risk, desc in zip(tc["risk_levels"], tc["descriptions"]):
                st.write(f"🔴 **{risk.upper()}** — {desc}")

            approved = st.checkbox(
                "Approve this command",
                value=False,
                key=f"approve_{tc['id']}",
            )
            rejected_by_id[tc["id"]] = not approved

        if st.button("Submit decision"):
            rejected_ids = [tid for tid, rejected in rejected_by_id.items() if rejected]
            payload = {
                "type": "approval_response",
                "thread_id": THREAD_ID,
                "user_id": USER_ID,
                "rejected_tool_ids": rejected_ids,
            }

            run_turn_and_store(payload, user_content=None)


# chat input
message = st.chat_input(
    placeholder="Type here . . .",
    disabled=bool(st.session_state.pending_interrupt),
)

if message:
    st.session_state.messages_history.append({"role": "user", "content": message})
    with st.chat_message("user"):
        st.write(message)

    payload = {
        "message": message,
        "thread_id": THREAD_ID,
        "user_id": USER_ID,
    }
    run_turn_and_store(payload, user_content=message)