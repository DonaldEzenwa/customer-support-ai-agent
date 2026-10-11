# Customer Support AI Agent

An intelligent customer support agent built on Amazon Bedrock AgentCore and the Strands SDK. The agent handles order tracking, refund processing, product and policy questions, loyalty discount calculations, and live web browsing through a single conversational interface, with memory that persists across sessions.

This project was built as part of the Udacity AWS Future Agent Engineer Nanodegree Program.

## What It Does

Given a natural language request, the agent can:

- Track an order and report its shipping status, carrier, and tracking number
- Initiate a refund, check a refund's status, or generate a return label
- Answer product and policy questions grounded in a Knowledge Base (Retrieval Augmented Generation)
- Remember a customer's name and stated preferences across separate sessions
- Calculate exact loyalty point redemptions and tier discounts using a secure sandboxed code interpreter
- Browse a live web page and report back what it finds

## Architecture

```
                         ┌─────────────────────────┐
                         │   Bedrock AgentCore      │
                         │   Runtime (main.py)      │
                         │                           │
  agentcore invoke  ───▶ │  Strands Agent            │
                         │  + MemoryHook             │
                         │  + system prompt          │
                         └───────────┬───────────────┘
                                     │
          ┌──────────────┬──────────┼──────────────┬──────────────┐
          ▼              ▼          ▼              ▼              ▼
   ┌─────────────┐ ┌───────────┐ ┌─────────┐ ┌───────────┐ ┌─────────────┐
   │ AgentCore   │ │ Knowledge │ │ AgentCore│ │ Code      │ │ AgentCore   │
   │ Gateway     │ │ Base (RAG)│ │ Memory   │ │ Interpreter│ │ Browser     │
   │ (MCP)       │ │           │ │          │ │ (sandbox)  │ │             │
   └──────┬──────┘ └───────────┘ └──────────┘ └───────────┘ └─────────────┘
          │
   ┌──────┴───────┐
   ▼              ▼
┌─────────┐  ┌──────────────┐
│ Lambda  │  │ API Gateway  │
│ (refund-│  │ REST API ──▶ │
│ processor)│ │ Lambda       │
└─────────┘  │ (order-      │
             │  tracker)    │
             └──────────────┘
```

Two Gateway targets expose backend logic as Model Context Protocol (MCP) tools:

- **order-tracker**: a Lambda function behind an API Gateway REST API, exposing `get_order`, `get_customer`, and `get_customer_orders`
- **refund-processor**: a Lambda function invoked directly by the Gateway, exposing `initiate_refund`, `check_refund_status`, and `get_return_label`

## Tech Stack

- **Amazon Bedrock AgentCore**: Runtime, Gateway, Memory, Code Interpreter, Browser
- **Strands Agents SDK**: agent orchestration, tool decorators, hooks
- **Amazon Bedrock Knowledge Bases**: managed vector store for Retrieval Augmented Generation
- **AWS Lambda** and **Amazon API Gateway**: backend tool implementations
- **Amazon Nova Lite** (`global.amazon.nova-2-lite-v1:0`): the underlying model
- **uv**: Python dependency management and packaging

## Project Structure

```
starter/
├── main.py                  # Agent entrypoint, tools, and memory hook
├── setup_permissions.py     # Grants the deployed runtime access to KB, Memory, and Browser
├── pyproject.toml           # Dependencies, including a platform override for pywin32
├── product_catalog.txt      # Source document for the Knowledge Base
└── lambda/
    ├── order_tracker.py     # API Gateway proxy Lambda: order and customer lookups
    ├── refund_processor.py  # Direct Gateway target Lambda: refund operations
    └── lambda_schema        # Tool schema for the refund_processor target
```

## Setup

### Prerequisites

- An AWS account with Bedrock AgentCore access, in `us-east-1`
- Python 3.13+ and [uv](https://docs.astral.sh/uv/)
- A Linux build environment (see **A Note on Deployment Platform** below)

### Infrastructure

1. Deploy the two Lambda functions (`order-tracker`, `refund-processor`) with an execution role that has basic Lambda and CloudWatch Logs permissions.
2. Create a REST API in API Gateway with three `GET` routes (`/orders/{order_id}`, `/customers/{customer_id}`, `/customers/{customer_id}/orders`) proxying to `order-tracker`, deployed to a `prod` stage. Make sure every method declares both a `200` and a `404` response, an undeclared response code will cause the Gateway target to fail.
3. Create an AgentCore Gateway with the **NONE** authorizer. Add two targets:
   - `order-tracker`, type API Gateway, pointing at the REST API and stage above, with explicit `operationId` overrides (`get_order`, `get_customer`, `get_customer_orders`) since the exported API lacked operation IDs
   - `refund-processor`, type Lambda ARN, using `lambda_schema` as its tool schema
4. Create an AgentCore Memory resource with two strategies: a Semantic strategy (`customer_facts`) and a User Preference strategy (`customer_preferences`).
5. Create a Managed Knowledge Base backed by an S3 data source containing `product_catalog.txt`, and sync it.

### Configuration

Fill in the four values at the top of `main.py`:

```python
GATEWAY_URL = "https://<your-gateway>.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp"
KB_ID       = "<your-knowledge-base-id>"
REGION      = "us-east-1"
MEMORY_ID   = "<your-memory-id>"
```

### Deploy

```bash
cd starter
uv sync --python 3.13
uv run agentcore configure --entrypoint main.py --name customer_support_agent --deployment-type direct_code_deploy --runtime PYTHON_3_13 --disable-memory
uv run agentcore deploy
uv run python setup_permissions.py
```

`setup_permissions.py` reads `main.py` as text (never imports it) to validate the three resource IDs, then attaches an inline policy granting the deployed runtime access to the Knowledge Base, Memory, and Browser.

### A Note on Deployment Platform

**Build and deploy from a genuine Linux environment, not Windows.** This agent uses the AgentCore Browser tool, which relies on a bundled Playwright Node.js driver binary. Packaging a deployment zip from an NTFS filesystem (plain Windows, or WSL's `/mnt/c/` mount) strips the Unix executable bit from that binary, causing a `PermissionError` at runtime that only surfaces after deployment. Build from WSL's native filesystem (`~/`, not `/mnt/c/`), a Linux CI runner, or a genuine Linux machine.

Separately, this project's dependency tree pulls in `pywin32` as a Windows-only transitive dependency with no Linux wheel. `pyproject.toml` includes an override to fix cross-platform resolution:

```toml
[tool.uv]
override-dependencies = ["pywin32; sys_platform == 'win32'"]
```

## Testing

Each test below should run in a fresh session (a new UUID), since `runtimeSessionId` requires at least 33 characters:

```bash
uv run agentcore invoke --session-id "$(python3 -c 'import uuid; print(uuid.uuid4())')" \
  '{"prompt": "What is the status of order ORD-001?", "customer_id": "CUST-123"}'
```

| Test | Prompt | Expected |
|---|---|---|
| Order Tracking | "What is the status of order ORD-001?" | Shipping status, tracking number, carrier |
| Refund Processing | "I want to return my Kindle Paperwhite (ORD-002)." | Refund ID, APPROVED status |
| Knowledge Base (RAG) | "What are the benefits of the Platinum loyalty tier?" | Answer grounded in the product catalog |
| Long-Term Memory | Two sessions: introduce yourself, then ask if the agent remembers | Correct recall in a new session |
| Loyalty Discount | "I am a Gold member with 4250 points. Calculate my discount on a $150 order." | Points redeemed, tier discount, correct final total |
| Browser Tool | "Go to https://www.udacity.com and tell me the page title." | The live page title |

Pull logs for any invocation with:

```bash
aws logs filter-log-events \
  --log-group-name "/aws/bedrock-agentcore/runtimes/<runtime-id>-DEFAULT" \
  --start-time $(( ($(date +%s) - 300) * 1000 )) \
  --region us-east-1
```

## Lessons Learned

A few non-obvious issues came up during development that are worth documenting for anyone building on AgentCore:

- **Gateway target names reject underscores.** The console's validation pattern only allows letters, digits, and hyphens, despite some AgentCore surfaces elsewhere expecting underscores. Use hyphens for target names (`order-tracker`, not `order_tracker`).
- **`invoke_async` returns a `Result` object, not a dict.** The final text lives at `result.message["content"][0]["text"]`, not a subscriptable top-level key.
- **`retrieve_memories` nests content under `record["content"]["text"]`**, not a top-level `text` key. This silently returns zero usable results if the nesting is missed, despite the underlying API call succeeding.
- **`execute_code`'s result is an event stream**, not a flat dict. The real `stdout`/`stderr`/`exitCode` live under `event["result"]["structuredContent"]` inside each streamed event.
- **Strands delivers tool results with `role: "user"` too.** A memory hook that filters only on role will misfire on every tool return; also check for the absence of a `toolResult` content block.
- **Gateway tool loading needs explicit error handling.** Wrap `list_tools_sync()` in a try/except that logs and continues, so a Gateway outage degrades the agent's capabilities gracefully instead of crashing or failing silently.

## Cleanup

Delete resources in this order to avoid orphaned billing, the Knowledge Base's OpenSearch Serverless collection is the most expensive component and should go first:

```bash
# Console: delete the Knowledge Base, then its OpenSearch Serverless collection
# Console: delete the Gateway targets, then the Gateway
# Console: delete the Memory resource
aws lambda delete-function --function-name order-tracker --region us-east-1
aws lambda delete-function --function-name refund-processor --region us-east-1
# Console: delete the REST API
# Console: delete the Runtime, or: uv run agentcore destroy
```

## License

Built for educational purposes as part of the Udacity AWS Future Agent Engineer Nanodegree Program.
