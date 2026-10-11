"""
Customer Support AI Agent — Starter Code
==========================================
Your task is to complete this file by implementing all sections marked
with # TODO comments.

Reference the project instructions and rubric for guidance.
Work through each section yourself.

Run locally (after filling in config values):
  uv run main.py '{"prompt": "Hello", "customer_id": "CUST-123", "session_id": "s1"}'

Deploy to AgentCore:
  agentcore deploy

Invoke deployed agent:
  agentcore invoke '{"prompt": "Hello", "customer_id": "CUST-123", "session_id": "s1"}'
"""

# ── Imports ───────────────────────────────────────────────────────────────────
# These imports are provided. Do not remove them.
from strands import Agent, tool
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from bedrock_agentcore.memory import MemoryClient
from strands.models import BedrockModel
from strands.tools.mcp.mcp_client import MCPClient
from mcp.client.streamable_http import streamable_http_client
import argparse, json
import os, asyncio, boto3
from strands.hooks import (
    HookProvider, AfterInvocationEvent, HookRegistry, MessageAddedEvent,
)
import logging
import uuid
from typing import Dict
from bedrock_agentcore.tools.code_interpreter_client import code_session
from strands_tools.browser import AgentCoreBrowser


logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("CSAI_Agent")
logger.setLevel(logging.INFO)

# ── TODO 1 — App Initialisation ───────────────────────────────────────────────
# Create a BedrockAgentCoreApp instance.
# This registers the ASGI server for AgentCore deployment.
# There must be exactly one instance per deployment.
#
# Hint: app = BedrockAgentCoreApp()

# TODO: Create the BedrockAgentCoreApp instance
app = BedrockAgentCoreApp()  # Replace this line


# Suppress interactive tool-consent prompts (required in headless deployments).
os.environ["BYPASS_TOOL_CONSENT"] = "true"


# ── TODO 2 — Configuration ────────────────────────────────────────────────────
# Replace the placeholder strings with your actual AWS resource values.
# You collected these in the infrastructure setup section of the project instructions.
#
# GATEWAY_URL format: https://<alias>.gateway.bedrock-agentcore.<region>.amazonaws.com/mcp
# This starter uses an unsigned MCP connection and therefore assumes the
# project Gateway is configured with the NONE authorizer.
# KB_ID       format: 10-character alphanumeric string from the KB console
# REGION:     your AWS region, e.g. "us-east-1"
# MEMORY_ID   format: shown in the AgentCore Memory console

GATEWAY_URL = "https://<your-gateway-id>.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp"   # TODO: Replace with your Gateway URL
KB_ID       = "<your-knowledge-base-id>"          # TODO: Replace with your Knowledge Base ID
REGION      = "us-east-1"       # TODO: Replace with your AWS region
MEMORY_ID   = "<your-memory-id>"     # TODO: Replace with your Memory ID


# ── TODO 3 — Model and Clients ────────────────────────────────────────────────
# Create:
#   1. A BedrockModel using model_id "global.amazon.nova-2-lite-v1:0"
#   2. A MemoryClient with region_name=REGION
#   3. A boto3 client for the "bedrock-agent-runtime" service in REGION
#
# Hint: model = BedrockModel(model_id=model_id)

model_id = "global.amazon.nova-2-lite-v1:0"

# TODO: Create the BedrockModel instance
model = BedrockModel(model_id=model_id)  # Replace this line

# TODO: Create the MemoryClient instance
memory_client = MemoryClient(region_name=REGION)  # Replace this line

# TODO: Create the boto3 bedrock-agent-runtime client
_bedrock_runtime = boto3.client("bedrock-agent-runtime", region_name=REGION)  # Replace this line


# ── TODO 4 — Namespace Helper ─────────────────────────────────────────────────
# Implement get_namespaces() to return a dict mapping strategy type to
# namespace template string.
#
# Steps:
#   1. Call mem_client.get_memory_strategies(memory_id) to get strategy list
#   2. Read the namespace from strategy["namespaceTemplates"][0].
#      For compatibility with older AgentCore responses, fall back to
#      strategy["namespaces"][0] when namespaceTemplates is absent.
#
# Example output:
#   { "SEMANTIC": "cs_agent/{actorId}/facts",
#     "USER_PREFERENCE": "cs_agent/{actorId}/preferences" }

def get_namespaces(mem_client: MemoryClient, memory_id: str) -> Dict:
    """Return a dict mapping strategy type → namespace template string."""
    # TODO: Implement this function
    strategies = mem_client.get_memory_strategies(memory_id)
    namespaces = {}
    for strategy in strategies:
        if "namespaceTemplates" in strategy:
            namespaces[strategy["type"]] = strategy["namespaceTemplates"][0]
        elif "namespaces" in strategy:
            namespaces[strategy["type"]] = strategy["namespaces"][0]
    return namespaces


# ── TODO 5 — Memory Hook ──────────────────────────────────────────────────────
# Implement MemoryHook, a HookProvider subclass that adds long-term memory.
#
# The class needs:
#   __init__(self, actor_id, session_id, memory_client, memory_id)
#     — store all four as instance attributes
#     — call get_namespaces() and store the result as self.namespaces
#
#   retrieve_customer_context(self, event: MessageAddedEvent)
#     — only runs for plain-text user messages (not tool results)
#     — for each strategy namespace, call memory_client.retrieve_memories(
#          memory_id, namespace (formatted with actorId), query, top_k=5)
#     — collect non-empty memory texts tagged with their strategy type
#     — if any memories found, prepend them to the user message as:
#          "Customer Context:\n<memories>\n\n<original_message>"
#
#   save_support_interaction(self, event: AfterInvocationEvent)
#     — walk the message list backwards to find the last plain-text user
#       query and the last assistant response
#     — call memory_client.create_event(memory_id, actor_id, session_id,
#          messages=[(customer_query, "USER"), (agent_response, "ASSISTANT")])
#
#   register_hooks(self, registry: HookRegistry)
#     — register retrieve_customer_context on MessageAddedEvent
#     — register save_support_interaction on AfterInvocationEvent

class MemoryHook(HookProvider):
    """Long-term memory hook for the customer support agent."""

    def __init__(
        self,
        actor_id: str,
        session_id: str,
        memory_client: MemoryClient,
        memory_id: str,
    ):
        # TODO: Store actor_id, session_id, memory_id, memory_client as attributes
        self.actor_id = actor_id
        self.session_id = session_id
        self.memory_client = memory_client
        self.memory_id = memory_id

        # TODO: Call get_namespaces() and store the result as self.namespaces
        self.namespaces = get_namespaces(memory_client, memory_id)
        

    def retrieve_customer_context(self, event: MessageAddedEvent):
        """Retrieve relevant memories and prepend them to the user message."""
        # Steps:
        #   1. Get the last message from event.agent.messages
        last_message = event.agent.messages[-1]
        if last_message.get("role", "").lower() != "user":
            return
        #   3. Extract the user query text
        content_blocks = last_message.get("content", []) or []
        if any(isinstance(b, dict) and "toolResult" in b for b in content_blocks):
            return
        if not content_blocks or "text" not in content_blocks[0]:
            return
        user_query = content_blocks[0]["text"]
        #   4. For each namespace in self.namespaces, call retrieve_memories()
        relevant_memories = []
        for namespace, template in self.namespaces.items():
            formatted_namespace = template.format(actorId=self.actor_id)
            logger.warning("MEMORY_DEBUG: querying namespace: %s", formatted_namespace)
            try:
                memories = self.memory_client.retrieve_memories(
                    memory_id=self.memory_id,
                    namespace=formatted_namespace,
                    query=user_query,
                    top_k=5
                )
            except Exception as e:
                logger.warning("Memory retrieval failed for %s: %s", formatted_namespace, e)
                continue
            #   5. Collect non-empty memory texts with strategy type tags
            for memory in memories:
                content = memory.get("content", {})
                text = content.get("text") if isinstance(content, dict) else None
                if text:
                    relevant_memories.append(f"[{namespace}] {text}")
        logger.warning("MEMORY_DEBUG: retrieved %d memory blocks for query: %s", len(relevant_memories), user_query[:50])
        if relevant_memories:
            last_message["content"][0]["text"] = (
                "Customer Context:\n" + "\n".join(relevant_memories) + "\n\n" + user_query
            )

    def save_support_interaction(self, event: AfterInvocationEvent):
        """Save the completed turn to memory after the agent responds."""
        user_query = response = None
        for message in reversed(event.agent.messages):
            role = message.get("role", "").lower()
            content = message.get("content", [])
            text = content[0].get("text") if content and isinstance(content[0], dict) else None
            if not text:
                continue
            if role == "assistant" and response is None:
                response = text
            elif role == "user" and user_query is None:
                user_query = text
            if user_query and response:
                break
        if user_query and response:
            try:
                self.memory_client.create_event(
                    self.memory_id, self.actor_id, self.session_id,
                    messages=[(user_query, "USER"), (response, "ASSISTANT")],
                )
                logger.warning("MEMORY_DEBUG: saved interaction - query=%s response=%s", user_query[:50], response[:50])
            except Exception as e:
                logger.warning("Failed to save support interaction: %s", e)

    def register_hooks(self, registry: HookRegistry) -> None:  # type: ignore
        """Register both memory callbacks."""
        registry.add_callback(MessageAddedEvent, self.retrieve_customer_context)
        registry.add_callback(AfterInvocationEvent, self.save_support_interaction)


# ── TODO 6 — Knowledge Base Tool ─────────────────────────────────────────────
# Implement search_knowledge_base(query) using the @tool decorator.
#
# Steps:
#   1. Guard: if KB_ID is empty return "Knowledge base not configured."
#   2. Call _bedrock_runtime.retrieve(
#          knowledgeBaseId=KB_ID,
#          retrievalQuery={"text": query}
#      )
#   3. Extract resp["retrievalResults"]; return a message if empty
#   4. Join the text chunks with "\n---\n" and return the result
#
# The docstring is the tool description — the model uses it to decide when
# to call this tool, so keep it clear and accurate.

@tool
def search_knowledge_base(query: str) -> str:
    """
    Search the Amazon product catalog and support knowledge base.
    Use this for product specifications, return policies, warranty
    information, loyalty program details, and order status definitions.

    Args:
        query: The question or topic to search for

    Returns:
        Relevant information retrieved from the knowledge base
    """
    if not KB_ID:
        return "Knowledge base not configured."
    response = _bedrock_runtime.retrieve(
        knowledgeBaseId=KB_ID, retrievalQuery={"text": query}
    )
    results = response.get("retrievalResults", [])
    if not results:
        return "No relevant information found."
    return "\n---\n".join(r.get("content", {}).get("text", "") for r in results if r.get("content", {}).get("text"))


# ── TODO 7 — Loyalty Discount Tool (Code Interpreter) ────────────────────────
# Implement calculate_loyalty_discount() using the @tool decorator.
#
# The tool must:
#   1. Build a self-contained Python code string that:
#        • Defines earn_rates: {"standard": 1, "device": 2, "fresh": 5}
#        • Defines tier_rates: {"Silver": 0.00, "Gold": 0.10, "Platinum": 0.15}
#        • Calculates points_redeemed (floor to nearest 500, cap at 50% of order)
#        • Calculates tier_discount (applied to subtotal after points)
#        • Calculates final_total, total_savings, points_earned, remaining_points
#        • Prints a JSON result dict
#   2. Execute the code with code_session(REGION).invoke("executeCode", {...})
#      using language="python" and clearContext=True
#   3. Return the first result event as a JSON string
#   4. Include a fallback that computes only the tier discount if the
#      Code Interpreter is unavailable

@tool
def calculate_loyalty_discount(
    loyalty_points: int,
    tier: str,
    order_total: float,
    product_category: str = "standard",
) -> str:
    """
    Calculate the loyalty discount for a customer order using the
    AgentCore Code Interpreter. Runs exact arithmetic in a secure sandbox.

    Args:
        loyalty_points:   Customer's current points balance
        tier:             Customer tier — Silver, Gold, or Platinum
        order_total:      Order total in USD
        product_category: standard, device, or fresh

    Returns:
        Full discount breakdown and final price
    """
    import json as _json
    code = f'''import json, math
earn_rates = {{"standard": 1, "device": 2, "fresh": 5}}
tier_rates = {{"Silver": 0.00, "Gold": 0.10, "Platinum": 0.15}}
points = {int(loyalty_points)}
subtotal = {float(order_total)}
tier_pct = tier_rates.get({tier!r}, 0.0)
redeemed = min((points // 500) * 500, math.floor(subtotal * 0.5 / 0.01))
after_points = subtotal - redeemed * 0.01
final_total = round(after_points - after_points * tier_pct, 2)
points_earned = math.floor(final_total * earn_rates.get({product_category!r}, 1))
result = {{
    "points_redeemed": redeemed,
    "tier_discount_pct": round(tier_pct * 100, 2),
    "final_total": final_total,
    "total_savings": round(subtotal - final_total, 2),
    "points_earned": points_earned,
    "remaining_points": points - redeemed + points_earned,
}}
print(json.dumps(result))'''

    try:
        with code_session(REGION) as session:
            result = session.execute_code(code, language="python", clear_context=True)

        stdout = ""
        stderr = ""
        exit_code = 1
        for event in result.get("stream", []):
            event_result = event.get("result")
            if isinstance(event_result, dict):
                structured = event_result.get("structuredContent", {})
                stdout = structured.get("stdout", "")
                stderr = structured.get("stderr", "")
                exit_code = structured.get("exitCode", 1)
                break

        if exit_code != 0 or not stdout:
            raise RuntimeError(f"Code Interpreter error: {stderr or 'no stdout'}")

        return stdout.strip()

    except Exception as e:
        logger.warning("Code Interpreter unavailable, using fallback: %s", e)
        rate = {"Silver": 0.0, "Gold": 0.10, "Platinum": 0.15}.get(tier, 0.0)
        final_total = round(order_total * (1 - rate), 2)
        return _json.dumps({
            "points_redeemed": 0,
            "tier_discount_pct": round(rate * 100, 2),
            "final_total": final_total,
            "remaining_points": loyalty_points,
            "note": "Code Interpreter unavailable; tier discount only, points not applied.",
        })


# ── TODO 8 — Agent Entrypoint ─────────────────────────────────────────────────
# Implement the invoke() function decorated with @app.entrypoint.
#
# Steps:
#   1. Extract user_input, actor_id, and session_id from the payload
#      (generate a UUID if session_id is missing)
#   2. Instantiate MemoryHook for this actor/session
#   3. Instantiate AgentCoreBrowser(region=REGION)
#   4. Build the tools list: [search_knowledge_base, calculate_loyalty_discount,
#                              agent_core_browser.browser]
#   5. Connect to the Gateway via MCPClient, load gateway_tools, extend tools list
#   6. Create and invoke the Agent with all tools, hooks, and system_prompt
#   7. Return the text from the first content block of the response
#   8. Handle exceptions gracefully

@app.entrypoint
async def invoke(payload, context=None):
    """
    Main handler called by AgentCore for every incoming request.

    Expected payload keys:
      prompt      (str, required) — the customer's message
      customer_id (str, optional) — unique customer identifier
      session_id  (str, optional) — session identifier; generated if absent
    """
    user_input = payload.get("prompt", "")
    actor_id = payload.get("customer_id", "anonymous")
    session_id = payload.get("session_id") or str(uuid.uuid4())
    hooks = MemoryHook(actor_id, session_id, memory_client, MEMORY_ID)
    browser = AgentCoreBrowser(region=REGION)
    tools = [search_knowledge_base, calculate_loyalty_discount, browser.browser]
    try:
        with MCPClient(lambda: streamable_http_client(GATEWAY_URL)) as client:
            try:
                gateway_tools = client.list_tools_sync()
                tools.extend(gateway_tools)
                logger.info(
                    "Gateway connected successfully. Loaded %d tools.",
                    len(gateway_tools),
                )
            except TimeoutError:
                logger.exception("Gateway tool loading timed out")
            except ConnectionError:
                logger.exception("Gateway connection failed")
            except Exception as exc:
                logger.exception("Gateway tool loading failed: %s", exc)
            agent = Agent(model=model, tools=tools, hooks=[hooks], system_prompt=(
                "You are a helpful customer support agent. "
                "You must always use the calculate_loyalty_discount tool for any loyalty points, "
                "tier discount, or order total calculation involving a customer's points balance, "
                "never compute these numbers yourself. "
                "Use available tools when useful, be accurate, concise, and never invent "
                "customer or order information."
            ))
            response = await agent.invoke_async(user_input)
            content_blocks = response.message.get("content", []) if response.message else []
            for block in content_blocks:
                if isinstance(block, dict) and "text" in block:
                    return block["text"]
            return str(response.message)
    except Exception as exc:
        logger.exception("Agent invocation failed")
        return f"Unable to process the request: {exc}"


# ── CLI entry point (do not modify) ──────────────────────────────────────────
def main():
    """Run one invocation from the command line for local testing."""
    parser = argparse.ArgumentParser()
    parser.add_argument("payload", type=str)
    args = parser.parse_args()
    response = asyncio.run(invoke(json.loads(args.payload)))
    print(response)


if __name__ == "__main__":
    app.run()
    # Uncomment the line below and comment app.run() for local CLI testing:
    # main()
