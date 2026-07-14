import logging
import os
import sqlite3

from dotenv import load_dotenv
from langchain_core.messages import BaseMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from typing import Annotated, TypedDict

load_dotenv()

logger = logging.getLogger(__name__)

# -------------------
# 1. LLM
# -------------------
llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", google_api_key=os.getenv("GOOGLE_API_KEY"))


# -------------------
# 2. State
# -------------------
class ChatState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]


# -------------------
# 3. Nodes
# -------------------
def chat_node(state: ChatState):
    """Send the conversation history to the LLM and return its reply."""
    messages = state["messages"]
    try:
        response = llm.invoke(messages)
    except Exception:
        logger.exception("LLM invocation failed")
        raise
    return {"messages": [response]}


# -------------------
# 4. Checkpointer
# -------------------
conn = sqlite3.connect(database="chatbot.db", check_same_thread=False)
checkpointer = SqliteSaver(conn=conn)

# -------------------
# 5. Graph
# -------------------
graph = StateGraph(ChatState)
graph.add_node("chat_node", chat_node)
graph.add_edge(START, "chat_node")
graph.add_edge("chat_node", END)

chatbot = graph.compile(checkpointer=checkpointer)


# -------------------
# 6. Helpers
# -------------------
def retrieve_all_threads():
    """Return a list of every thread_id stored in the checkpointer."""
    all_threads = set()
    for checkpoint in checkpointer.list(None):
        all_threads.add(checkpoint.config["configurable"]["thread_id"])
    return list(all_threads)
