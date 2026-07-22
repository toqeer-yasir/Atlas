import os

from dotenv import load_dotenv
load_dotenv()

from langchain_mcp_adapters.client import MultiServerMCPClient

from langgraph.prebuilt import ToolRuntime

from langchain_tavily import TavilySearch

from langchain_core.tools import tool

from uuid import uuid4, UUID
from datetime import datetime, timezone

from langgraph.runtime import get_runtime
from dateutil import parser as date_parser

from db import tasks as tasks_db

import smtplib
from email.message import EmailMessage
from email.utils import make_msgid

GMAIL_ADDRESS = os.getenv("GMAIL_ADDRESS")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")


async def load_mcp_tools():
    """Load MCP tools asynchronously."""
    try:
        mcp_client = MultiServerMCPClient({   
            'System info.': {
                'transport': 'stdio',
                'command': 'python',
                'args': ["/home/toqeer-yasir/Documents/repos/ai-agents-with-langgraph/agentic_chatbot/local_mcp_servers/system_info_mcp_server.py"]
            },
            'GitHub': {
                'transport': 'stdio',
                'command': 'python',
                'args': ["/home/toqeer-yasir/Documents/repos/ai-agents-with-langgraph/agentic_chatbot/local_mcp_servers/github_mcp_server.py"]
            },
            'Shell': {
                'transport': 'stdio',
                'command': 'python',
                'args': ["/home/toqeer-yasir/Documents/repos/ai-agents-with-langgraph/agentic_chatbot/local_mcp_servers/shell_mcp_server.py"]
            }
        })
        return await mcp_client.get_tools(), mcp_client
    except Exception as e:
        raise RuntimeError(f"Error loading MCP tools: {e}")


@tool
async def rag_search(query: str, runtime: ToolRuntime):
    """ Search documents using the RAG retriever. """
    user_id = runtime.context["user_id"]
    rag = runtime.context["rag"]
    retriever = rag.get_retriever(user_id= user_id)
    docs = await retriever.ainvoke(query)
    return str(docs)


online_search = TavilySearch(
    max_results=3,
    include_answer=True,
    search_depth="advanced",
    tavily_api_key=os.getenv("TAVILY_API_KEY")
)




@tool
async def schedule_task(
    task_type: str,
    date_time: str,
    task_description: str,
    task_payload: dict | None = None,
) -> str:
    """
    Schedule a task to run at a specific future date and time for the current chat.

    Args:
        task_type: A short machine-readable label for what kind of task this is
            (e.g. "reminder", "follow_up_message").
        date_time: Target trigger time in ISO 8601 format, including timezone offset
            (e.g. "2026-07-22T09:00:00+05:00"). Must be in the future.
        task_description: Short human-readable description of what this task does.
        task_payload: Optional structured data the worker will need to execute this
            task later (e.g. {"message": "..."}).

    Returns:
        Confirmation string with the scheduled task id and time.
    """
    runtime = get_runtime()
    pool = runtime.context["pool"]
    user_id = runtime.context["user_id"]
    thread_id = runtime.context["thread_id"]

    try:
        target_time = date_parser.isoparse(date_time)
    except ValueError:
        return f"Invalid date_time format: {date_time!r}. Provide an ISO 8601 timestamp."

    if target_time.tzinfo is None:
        target_time = target_time.replace(tzinfo=timezone.utc)

    if target_time <= datetime.now(timezone.utc):
        return "The scheduled time must be in the future."

    task_id = uuid4()

    await tasks_db.create_task(
        pool=pool,
        task_id=task_id,
        user_id=UUID(user_id),
        chat_id=UUID(thread_id),
        task_type=task_type,
        date_time=target_time,
        task_description=task_description,
        task_payload=task_payload,
    )

    return f"Task scheduled (id={task_id}) for {target_time.isoformat()}."




@tool
async def list_scheduled_tasks() -> str:
    """List all pending scheduled tasks for the current chat."""
    runtime = get_runtime()
    pool = runtime.context["pool"]
    thread_id = runtime.context["thread_id"]

    rows = await tasks_db.get_user_tasks(pool=pool, user_id=UUID(runtime.context["user_id"]), chat_id=UUID(thread_id))
    pending = [r for r in rows if r["status"] == "pending"]

    if not pending:
        return "No pending scheduled tasks."

    return "\n".join(
        f"- {r['task_id']}: {r['task_description']} at {r['date_time']}"
        for r in pending
    )


@tool
async def cancel_scheduled_task(task_id: str) -> str:
    """Cancel a pending scheduled task by its task_id."""
    runtime = get_runtime()
    pool = runtime.context["pool"]
    user_id = runtime.context["user_id"]

    result = await tasks_db.cancel_task(pool=pool, task_id=UUID(task_id), user_id=UUID(user_id))
    return f"Task {task_id} cancelled." if result else f"No pending task found with id {task_id}."



@tool
def send_email(
    to_email: str,
    subject: str,
    body: str,
    attachment_content: str | None = None,
    attachment_filename: str | None = None,
) -> str:
    """
    Send an email to a specified recipient, optionally with a text attachment.

    Args:
        to_email: The recipient's email address. Always ask the user for this
            explicitly if they haven't specified who to send it to.
        subject: The email subject line.
        body: Plain text only — no Markdown (no **, ##, backticks, * bullets),
            since the email client won't render it. Use line breaks, dashes,
            and capitalization for structure instead.
        attachment_content: Optional plain-text content to attach as a file
            (e.g. a generated summary). Do not use for binary files.
        attachment_filename: Filename for the attachment, required if
            attachment_content is provided (e.g. "self_attention_summary.txt").

    Returns:
        Confirmation message indicating whether the email was sent successfully.
    """
    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
        return "Email sending is not configured on the server."

    msg = EmailMessage()
    msg["From"] = GMAIL_ADDRESS
    msg["To"] = to_email
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid()
    msg.set_content(body)

    if attachment_content and attachment_filename:
        msg.add_attachment(
            attachment_content.encode("utf-8"),
            maintype="text",
            subtype="plain",
            filename=attachment_filename,
        )

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
            smtp.send_message(msg)
        return f"Email sent successfully to {to_email}."
    except smtplib.SMTPRecipientsRefused:
        return f"Failed to send: '{to_email}' was refused as an invalid recipient."
    except Exception as e:
        return f"Failed to send email: {str(e)}"


async def get_tools():
    mcp_tools, mcp_client = await load_mcp_tools()
    return list(mcp_tools) + [rag_search, online_search, schedule_task, cancel_scheduled_task, list_scheduled_tasks, send_email], mcp_client