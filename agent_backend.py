import os
import json
from contextlib import asynccontextmanager
from typing import Annotated

from pydantic import BaseModel, EmailStr

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from uuid import UUID, uuid4

from pwdlib import PasswordHash

from langchain_core.messages import BaseMessage, AIMessage, HumanMessage, ToolMessage, SystemMessage
from langchain_openrouter import ChatOpenRouter

from langgraph.graph import START, END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition

from langgraph.types import interrupt, Command
import shlex

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from typing_extensions import TypedDict

from tools import get_tools

from rag_pipeline import Rag
import base64

from datetime import datetime, timezone, timedelta

PKT = timezone(timedelta(hours=5))


from db import (
    users,
    chats,
    messages,
    documents
)

from error_handling import classify_error

password_hash = PasswordHash.recommended()

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")


class ChatState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]


class Context(TypedDict):
    user_id: str
    thread_id: str
    rag: Rag
    pool: AsyncConnectionPool



def build_datetime_system_message() -> SystemMessage:
    now_pkt = datetime.now(PKT)
    return SystemMessage(
        content=(
            f"Current date and time (Pakistan Standard Time, UTC+5): "
            f"{now_pkt.strftime('%Y-%m-%d %H:%M:%S')} ({now_pkt.strftime('%A')}).\n"
            "All users are in Pakistan. When the user asks to schedule something using "
            "relative or local time (e.g. 'in 2 hours', 'tomorrow at 9am'), interpret it "
            "as Pakistan Standard Time (UTC+5) and convert it into an absolute ISO 8601 "
            "timestamp with the +05:00 offset before passing it to the scheduling tool."
        )
    )


def create_chat_node(model_with_tools):

    async def chat_node(state: ChatState):
        system_message = build_datetime_system_message()
        messages_to_send = [system_message, *state["messages"]]

        response = await model_with_tools.ainvoke(messages_to_send)

        return {"messages": [response]}

    return chat_node


def create_approval_node(RISK_BY_COMMAND, RISK_BY_FLAG):

    def analyze_command(command_str: str):
        try:
            tokens = shlex.split(command_str)
        except ValueError:
            tokens = command_str.split()

        expanded_tokens = []
        for token in tokens:
            if token.startswith("-") and not token.startswith("--") and token[1:].isalpha():
                expanded_tokens.extend(f"-{char}" for char in token[1:])
            else:
                expanded_tokens.append(token)

        detected_risks = set()
        for token in expanded_tokens:
            risk_info = RISK_BY_COMMAND.get(token) or RISK_BY_FLAG.get(token)
            if risk_info:
                detected_risks.add(tuple(risk_info.items()))

        return [dict(t) for t in detected_risks]

    async def approval_node(state: ChatState):
        message = state["messages"][-1]
        tool_calls = getattr(message, "tool_calls", []) or []

        if not tool_calls:
            return Command(goto="tools")

        risky_calls = []
        for tool in tool_calls:
            if tool["name"] != "shell":
                continue

            command = tool.get("args", {}).get("command", "")
            risks = analyze_command(command)

            if risks:
                risky_calls.append({
                    "id": tool["id"],
                    "name": tool["name"],
                    "command": command,
                    "risks": risks,
                })

        if not risky_calls:
            return Command(goto="tools")

        payload = {
            "tools": [
                {
                    "id": rc["id"],
                    "name": rc["name"],
                    "command": rc["command"],
                    "risk_levels": [r["risk"] for r in rc["risks"]],
                    "descriptions": [r["description"] for r in rc["risks"]],
                }
                for rc in risky_calls
            ],
        }

        response = interrupt(payload)
        rejected_ids = set(response["rejected_tool_ids"])

        rejected = [rc for rc in risky_calls if rc["id"] in rejected_ids]

        if not rejected:
            return Command(goto="tools")

        remaining_tool_calls = [tc for tc in tool_calls if tc["id"] not in rejected_ids]

        rejected_messages = [
            ToolMessage(
                tool_call_id=rc["id"],
                name=rc["name"],
                content=(
                    "The user denied permission to execute this tool. "
                    "Do not retry or request execution of the same tool unless the user explicitly changes their decision. "
                    "If a safe alternative exists that does not require this tool, use it. "
                    "Otherwise, explain what could not be completed because of the denial and ask the user how they would like to proceed. "
                    "You may ask why they declined only if that information would help provide a better alternative."
                ),
            )
            for rc in rejected
        ]

        if not remaining_tool_calls:
            return Command(goto="chatnode", update={"messages": rejected_messages})

        updated_message = message.model_copy(
            update={"id": message.id, "tool_calls": remaining_tool_calls}
        )

        return Command(
            goto="tools",
            update={"messages": [updated_message, *rejected_messages]},
        )

    return approval_node


def build_graph(model_with_tools, tools, checkpointer, RISK_BY_COMMAND, RISK_BY_FLAG):
    builder = StateGraph(
        ChatState,
        context_schema=Context,
    )

    chat_node = create_chat_node(model_with_tools)
    approval_node = create_approval_node(RISK_BY_COMMAND, RISK_BY_FLAG)

    builder.add_node("chatnode", chat_node)
    builder.add_node("approvalnode", approval_node, destinations=("tools", "chatnode"))
    builder.add_node("tools", ToolNode(tools))

    builder.add_edge(START, "chatnode")

    builder.add_conditional_edges(
        "chatnode",
        tools_condition,
        {"tools": "approvalnode", END: END},
    )

    builder.add_edge("tools", "chatnode")

    return builder.compile(checkpointer=checkpointer)


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("Starting server...")

    RISK_BY_COMMAND = json.load(open("./approval_actions.json"))
    RISK_BY_FLAG = json.load(open("./dangerous_flags.json"))

    pool = AsyncConnectionPool(
        DATABASE_URL,
        min_size=4,
        max_size=10,
        open=False,
        kwargs={
            "autocommit": True,
            "prepare_threshold": 0,
            "row_factory": dict_row,
        },
    )

    await pool.open()

    checkpointer = AsyncPostgresSaver(pool)
    await checkpointer.setup()

    model = ChatOpenRouter(
        model="nvidia/nemotron-3-nano-30b-a3b:free",
        api_key=OPENROUTER_API_KEY,
        streaming=True,
        temperature=0.7,
    )

    tools, mcp_client = await get_tools()
    print(f"Tools Received: {len(tools)}")

    model_with_tools = model.bind_tools(tools)

    graph = build_graph(
        model_with_tools=model_with_tools,
        tools=tools,
        checkpointer=checkpointer,
        RISK_BY_COMMAND=RISK_BY_COMMAND,
        RISK_BY_FLAG=RISK_BY_FLAG,
    )

    rag = Rag()

    app.state.pool = pool
    app.state.graph = graph
    app.state.model = model
    app.state.tools = tools
    app.state.mcp_client = mcp_client
    app.state.rag = rag

    yield

    print("Stopping server...")
    await pool.close()

    if hasattr(app.state, "mcp_client"):
        await app.state.mcp_client.aclose()


app = FastAPI(title="Agentic RAG Backend", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class SaveMessagesRequest(BaseModel):
    user_id : UUID
    message_id: UUID
    chat_id: UUID
    user_content: str
    assistant_content: str


class CreateUserRequest(BaseModel):
    user_name: str
    user_email: EmailStr
    password: str


class AddDocumentRequest(BaseModel):
    document_id: UUID
    user_id: UUID
    filename: str
    file_content: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


async def stream_graph_run(websocket: WebSocket, graph, run_input, config, context):

    async for mode, data in graph.astream(
        run_input,
        config=config,
        context=context,
        stream_mode=["messages", "updates"],
    ):
        if mode == "messages":
            message_chunk, _metadata = data
            if isinstance(message_chunk, AIMessage) and message_chunk.content:
                await websocket.send_json({
                    "type": "content",
                    "content": message_chunk.content,
                })

        elif mode == "updates":
            for node_name, node_update in data.items():
                if node_name == "__interrupt__":
                    interrupt_obj = node_update[0]
                    await websocket.send_json({
                        "type": "interrupt",
                        "content": interrupt_obj.value,
                    })


@app.websocket("/ws/chat")
async def websocket_chat(websocket: WebSocket):
    await websocket.accept()

    try:
        while True:
            data = await websocket.receive_json()
            thread_id = data.get("thread_id")

            if not thread_id:
                await websocket.send_json({
                    "type": "error",
                    "content": "Missing thread_id",
                })
                continue

            config = {"configurable": {"thread_id": thread_id}}

            if data.get("type") == "approval_response":
                rejected_ids = data.get("rejected_tool_ids", [])
                run_input = Command(resume={
                    "rejected_tool_ids": rejected_ids
                })
                context = {
                    "user_id": data.get("user_id"),
                    "thread_id": thread_id,
                    "rag": app.state.rag,
                    "pool": app.state.pool,
                }
            else:
                message = data.get("message")
                if not message:
                    await websocket.send_json({
                        "type": "error",
                        "content": "Missing message",
                    })
                    continue
                run_input = {"messages": [HumanMessage(content=message)]}
                context = {
                    "user_id": data.get("user_id"),
                    "thread_id": thread_id,
                    "rag": app.state.rag,
                    "pool": app.state.pool,
                }
            try:
                await stream_graph_run(websocket, websocket.app.state.graph, run_input, config, context)
                await websocket.send_json({"type": "completed"})

            except (WebSocketDisconnect, RuntimeError) as e:
                print(f"Client disconnected mid-run: {e}")
                return

            except Exception as e:
                            error_info = classify_error(e)
                            print(error_info["traceback"])

                            try:
                                await websocket.send_json({
                                    "type": "error",
                                    "category": error_info["category"],
                                    "content": error_info["message"],
                                    "debug": error_info["debug_detail"],
                                })
                            except Exception:
                                pass

    except WebSocketDisconnect:
            print("Client Disconnected")

    except RuntimeError as e:
        print(f"Client Disconnected (runtime): {e}")


@app.post("/users")
async def create_user(data: CreateUserRequest, request: Request):
    pool = request.app.state.pool
    user_id = uuid4()
    user_name = data.user_name
    user_email = data.user_email

    existing_user = await users.get_user_by_email(
    pool=pool,
    email=user_email,
    )

    if existing_user is not None:
        raise HTTPException(
            status_code=409,
            detail="An account with this email already exists.",
        )

    hashed_password = password_hash.hash(data.password)

    await users.create_user(
        pool=pool,
        user_id=user_id,
        name=user_name,
        email=user_email,
        password_hash=hashed_password,
    )
    return {
        "success": True,
        "user": {
            "id": user_id,
            "name": user_name,
            "email": user_email,
        },
    }


@app.post("/auth/login")
async def login(data: LoginRequest, request: Request):
    pool = request.app.state.pool

    user = await users.get_user_by_email(
        pool=pool,
        email=data.email,
    )

    if user is None:
        raise HTTPException(
            status_code=404,
            detail="No account found with this email.",
        )

    if not password_hash.verify(
        data.password,
        user["password_hash"],
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid password.",
        )
        
    return {
        "success": True,
        "user": {
            "id": user["id"],
            "name": user["name"],
            "email": user["email"],
        },
    }


@app.delete("/users/{email}")
async def delete_user_by_email(email: str, request: Request):
    pool = request.app.state.pool

    count = await users.delete_user(pool=pool, email=email)

    return {
        "success": True,
        "row_count": count
    }

@app.get("/users/{user_id}/chats")
async def get_user_chats(user_id: UUID, request: Request):
    pool = request.app.state.pool

    chats_list = await chats.get_user_chats(pool=pool, user_id=user_id)

    return {
        "success": True,
        "chats": chats_list
    }


@app.get("/chats/{chat_id}/messages")
async def get_chat_messages(chat_id: UUID, request: Request):
    pool = request.app.state.pool

    chat_messages = await messages.get_chat_messages(pool=pool, chat_id=chat_id)

    return {
        "success": True,
        "messages": chat_messages
    }


@app.post("/messages")
async def save_message(data: SaveMessagesRequest, request: Request):
    pool = request.app.state.pool

    user_id = data.user_id
    message_id = data.message_id
    chat_id = data.chat_id
    user_content = data.user_content
    assistant_content = data.assistant_content

    chat = await chats.get_chat(pool=pool, chat_id=chat_id)


    if chat is None:
        tokens = user_content.split()
        title = " ".join(tokens[:4])

        await chats.create_chat(pool= pool, chat_id= chat_id, user_id= user_id, title= title)

        await messages.create_message(pool= pool, message_id=message_id, chat_id=chat_id, user_content=user_content, assistant_content=assistant_content)

        return {
            "success": True,
            "chat_title": title
        }

    await messages.create_message(pool= pool, message_id=message_id, chat_id=chat_id, user_content=user_content, assistant_content=assistant_content)

    return {"success": True}

 
@app.delete("/chats/{chat_id}")
async def delete_user_chat(chat_id: UUID, request: Request):
    pool = request.app.state.pool

    await chats.delete_chat(pool=pool, chat_id=chat_id)

    return {
        "success": True
    }


@app.post("/documents")
async def add_document(data: AddDocumentRequest, request: Request):
    pool = request.app.state.pool
    rag = request.app.state.rag
    
    file_bytes = base64.b64decode(data.file_content)
    
    await documents.add_document(pool, data.document_id, data.user_id, data.filename) 

    status = await rag.ingest_documents(file_content=file_bytes, filename = data.filename, document_id=data.document_id, user_id=data.user_id)

    return {
        "success": True,
        "rag_status": status,
        "document_id": data.document_id
    }


@app.get("/users/{user_id}/documents")
async def get_user_documents(user_id: UUID, request: Request):
    pool = request.app.state.pool

    user_documents = await documents.get_user_documents(pool, user_id=user_id)

    return {
        "success": True,
        "documents": user_documents
    }


@app.delete("/users/{user_id}/documents/{document_id}")
async def delete_document(user_id: UUID, document_id: UUID, request: Request):
    pool = request.app.state.pool
    rag = request.app.state.rag

    await documents.delete_document(pool, document_id= document_id) 

    response = rag.delete_documents(user_id = user_id, document_id= document_id)

    return {
        "success": True,
        "rag_response": response,
    }



if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)