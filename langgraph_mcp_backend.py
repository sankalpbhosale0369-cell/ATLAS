import asyncio
import logging
import os
import sys
import threading

import aiosqlite
import requests
from dotenv import load_dotenv
from langchain_community.tools import DuckDuckGoSearchRun
from langchain_core.messages import BaseMessage
from langchain_core.tools import BaseTool, tool
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from typing import Annotated, TypedDict

load_dotenv()

logger = logging.getLogger(__name__)

# -----------------------------------------------------------
# Dedicated async loop for backend tasks
# -----------------------------------------------------------
_ASYNC_LOOP = asyncio.new_event_loop()
_ASYNC_THREAD = threading.Thread(target=_ASYNC_LOOP.run_forever, daemon=True)
_ASYNC_THREAD.start()


def _submit_async(coro):
    return asyncio.run_coroutine_threadsafe(coro, _ASYNC_LOOP)


def run_async(coro):
    return _submit_async(coro).result()


def submit_async_task(coro):
    """Schedule a coroutine on the backend event loop."""
    return _submit_async(coro)


# -------------------
# 1. LLM
# -------------------
llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", google_api_key=os.getenv("GOOGLE_API_KEY"))

# -------------------
# 2. Tools
# -------------------
search_tool = DuckDuckGoSearchRun(region="us-en")


@tool
def get_stock_price(symbol: str) -> dict:
    """
    Fetch latest stock price for a given symbol (e.g. 'AAPL', 'TSLA')
    using Alpha Vantage.
    """
    api_key = os.getenv("ALPHA_VANTAGE_API_KEY", "")
    if not api_key:
        return {"error": "ALPHA_VANTAGE_API_KEY environment variable is not set"}
    url = (
        "https://www.alphavantage.co/query"
        f"?function=GLOBAL_QUOTE&symbol={symbol}&apikey={api_key}"
    )
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        return r.json()
    except requests.RequestException as e:
        logger.exception("Alpha Vantage request failed for symbol=%s", symbol)
        return {"error": str(e)}


# --- MCP servers (configurable via env vars) ---
_python_exe = os.getenv("PYTHON_EXECUTABLE", sys.executable)
_mcp_math_script = os.getenv("MCP_MATH_SERVER_PATH", "")
_mcp_expense_url = os.getenv(
    "MCP_EXPENSE_SERVER_URL", "https://splendid-gold-dingo.fastmcp.app/mcp"
)

_mcp_servers: dict = {}
if _mcp_math_script:
    _mcp_servers["arith"] = {
        "transport": "stdio",
        "command": _python_exe,
        "args": [_mcp_math_script],
    }
if _mcp_expense_url:
    _mcp_servers["expense"] = {
        "transport": "streamable_http",
        "url": _mcp_expense_url,
    }

client = MultiServerMCPClient(_mcp_servers) if _mcp_servers else None


def load_mcp_tools() -> list[BaseTool]:
    if client is None:
        logger.info("No MCP servers configured — skipping MCP tool loading")
        return []
    try:
        return run_async(client.get_tools())
    except Exception:
        logger.exception("Failed to load MCP tools")
        return []


mcp_tools = load_mcp_tools()

tools = [search_tool, get_stock_price, *mcp_tools]
llm_with_tools = llm.bind_tools(tools) if tools else llm

# -------------------
# 3. State
# -------------------
class ChatState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]


# -------------------
# 4. Nodes
# -------------------
async def chat_node(state: ChatState):
    """LLM node that may answer or request a tool call."""
    messages = state["messages"]
    try:
        response = await llm_with_tools.ainvoke(messages)
    except Exception:
        logger.exception("LLM invocation failed")
        raise
    return {"messages": [response]}


tool_node = ToolNode(tools) if tools else None

# -------------------
# 5. Checkpointer
# -------------------
async def _init_checkpointer():
    conn = await aiosqlite.connect(database="chatbot.db")
    return AsyncSqliteSaver(conn)


checkpointer = run_async(_init_checkpointer())

# -------------------
# 6. Graph
# -------------------
graph = StateGraph(ChatState)
graph.add_node("chat_node", chat_node)
graph.add_edge(START, "chat_node")

if tool_node:
    graph.add_node("tools", tool_node)
    graph.add_conditional_edges("chat_node", tools_condition)
    graph.add_edge("tools", "chat_node")
else:
    graph.add_edge("chat_node", END)

chatbot = graph.compile(checkpointer=checkpointer)

# -------------------
# 7. Helper
# -------------------
async def _alist_threads():
    all_threads = set()
    async for checkpoint in checkpointer.alist(None):
        all_threads.add(checkpoint.config["configurable"]["thread_id"])
    return list(all_threads)


def retrieve_all_threads():
    """Return a list of every thread_id stored in the checkpointer."""
    return run_async(_alist_threads())
