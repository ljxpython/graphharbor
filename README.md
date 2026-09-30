<h1 align="center">GraphHarbor</h1>

<p align="center">
  <strong>Enterprise-Grade, Open-Source LangGraph Agent Server.</strong><br/>
  Production Postgres Checkpoints + Redis Distributed Workers. Same SDK. Same Studio. Zero Code Changes.
</p>

<p align="center">
  <a href="README.zh-CN.md"><strong>简体中文</strong></a> · <strong>English</strong>
</p>

<p align="center">
  <a href="https://github.com/ljxpython/graphharbor/stargazers"><img src="https://img.shields.io/github/stars/ljxpython/graphharbor?style=social" alt="GitHub stars"></a>
  &nbsp;
  <a href="https://github.com/ljxpython/graphharbor/actions/workflows/ci.yml"><img src="https://github.com/ljxpython/graphharbor/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  &nbsp;
  <a href="https://pypi.org/project/graphharbor/"><img src="https://img.shields.io/pypi/v/graphharbor" alt="PyPI"></a>
  &nbsp;
  <a href="https://pypi.org/project/graphharbor-runtime/"><img src="https://img.shields.io/pypi/v/graphharbor-runtime?label=graphharbor-runtime" alt="runtime PyPI"></a>
  &nbsp;
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-≥3.11-blue" alt="Python"></a>
  &nbsp;
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green" alt="MIT"></a>
  &nbsp;
  <a href="https://opensource.org/licenses/MIT"><img src="https://img.shields.io/badge/100%25-open%20source-brightgreen" alt="100% open source"></a>
</p>

<p align="center">
  <em>If GraphHarbor powers your agent infrastructure, <a href="https://github.com/ljxpython/graphharbor">⭐ star the repo</a> — it keeps this open-source effort thriving.</em>
</p>

---

## What is GraphHarbor?

**GraphHarbor** is an enterprise-grade, open-source **LangGraph Agent Server** engineered for scalable, resilient production workloads. 

Built on top of a PostgreSQL durable checkpoint state machine and Redis distributed worker queue, it delivers **automatic failure recovery, full subagent execution traceability (`checkpoint_ns`), robust SSE stream resumption, and zero vendor lock-in** — all while maintaining 100% protocol and API compatibility with official LangGraph tooling.

Compatible with:
**[LangSmith Studio](https://docs.langchain.com/langsmith/studio)** · **[langgraph-sdk](https://pypi.org/project/langgraph-sdk/)** · **[Agent Protocol](https://docs.langchain.com/langsmith/server-api-ref)** · **[Agent Chat UI](https://github.com/langchain-ai/agent-chat-ui)**

---

## Why GraphHarbor for Production?

While official `langgraph dev` provides an exceptional local developer experience, production deployments require true horizontal scalability, robust fault tolerance, and multi-agent auditability. GraphHarbor bridges this gap without proprietary runtimes or license barriers.

| Capability | [`langgraph dev`](https://docs.langchain.com/oss/python/langgraph/local-server) | [LangSmith Deployments](https://docs.langchain.com/langsmith/deployment) | [Aegra](https://github.com/aegra/aegra) | **GraphHarbor** |
|:---|:---|:---|:---|:---|
| **Target Use Case** | Fast local prototyping | Managed cloud / Licensed self-host | Self-hosted FastAPI alternative | **Enterprise self-hosted production** |
| **Persistence Engine** | In-memory + local SQLite | Postgres + Redis (Proprietary) | Postgres + Redis | **Postgres + Redis (Open MIT Engine)** |
| **Subagent Traceability** | Basic run trees | Cloud-managed LangSmith UI | Limited | **Native `checkpoint_ns` persistence & replay** |
| **Worker Fault Tolerance** | Single process (none) | Proprietary orchestration | Process-based | **Postgres Lease locks + Redis Auto-Reaper** |
| **Streaming Resilience** | Local stream | Proprietary stream | Standard SSE | **15s Gateway Heartbeats + `Last-Event-ID`** |
| **Core Protocol Surface** | Full official surface | Full official surface | Core Agent Protocol | **Full Core Protocol (assistants, threads, runs, crons, HITL)** |
| **Studio & SDK Drop-in** | Yes | Yes | Yes | **Yes (Zero code changes)** |
| **License / License Key** | Elastic-2.0 / None | Commercial / Key Required | Apache-2.0 / None | **MIT (100% Open Source) / None** |

---

## Core Production Superpowers

- 🔍 **Full Subagent Traceability (`checkpoint_ns`)**: Unlike standard setups that drop nested agent tool executions, GraphHarbor provides first-class support for `checkpoint_ns` routing and `/state/checkpoint`, guaranteeing 100% auditability and state replay for hierarchical multi-agent teams.
- ⚡ **Distributed Lease & Auto-Reaper**: Multi-worker job execution protected by PostgreSQL transactional row-level leases. If a worker process crashes, its lease expires automatically and the reaper worker re-queues the run with zero state corruption.
- 🌊 **Resilient SSE Streaming & Heartbeats**: Built-in 15s streaming heartbeats eliminate gateway timeout disconnections (e.g., HTTP 504 / proxy drops), paired with precise `Last-Event-ID` replay for seamless client reconnections.
- 🛡️ **Pure Generic Agent Server**: Strictly isolated from vendor-specific LLMs, proprietary prompts, or private telemetry formats. GraphHarbor provides a pure runtime surface; your business logic lives entirely in your graphs.
- 🔌 **Seamless Ecosystem Drop-in**: Keep your existing `langgraph.json` and graph factory. Works instantly with LangSmith Studio, LangGraph Python/JS SDK, and open-source Chat UIs.

---

## Architecture & How It Fits Together

GraphHarbor is architected around two high-performance packages working in lockstep:

```text
       Studio / langgraph-sdk / Agent Chat UI
                         │
                         ▼
┌──────────────────────────────────────────────────┐
│ libs/langhost (CLI: graphharbor serve)           │
│ - ASGI Protocol Gateway (HTTP / SSE / Crons)     │
│ - Request Authentication & Route Dispatch        │
│ - Heartbeat Injection & Last-Event-ID Resumption │
└────────────────────────┬─────────────────────────┘
                         │
                         ▼
┌──────────────────────────────────────────────────┐
│ libs/langgraph-runtime-pg (graphharbor-runtime)  │
│ - PostgreSQL Checkpoint State Machine            │
│ - Transactional Lease Management & Auto-Reaper   │
│ - Redis Distributed Queues & Pub/Sub Dispatch    │
└────────────────────────┬─────────────────────────┘
                         │
            ┌────────────┴────────────┐
            ▼                         ▼
   PostgreSQL (State/Runs)      Redis (Queues/PubSub)
```

- **`graphharbor` (`libs/langhost/`)**: The ASGI gateway and command-line interface you run (`graphharbor serve`).
- **`graphharbor-runtime` (`libs/langgraph-runtime-pg/`)**: The persistence and execution backbone powering durability and concurrency.

---

## Quick Start

### 1. Scaffold or Bring Your LangGraph Project

```bash
# Bring your existing project, or scaffold a new one:
uvx --from langgraph-cli@latest langgraph new my-agent
cd my-agent
uv sync
```

### 2. Install GraphHarbor

```bash
uv add graphharbor
```

*(This automatically pulls in the matched version of `graphharbor-runtime`)*

### 3. Configure Database & Redis

Create or update `.env` in your project root:

```bash
DATABASE_URI=postgresql+asyncpg://postgres:postgres@localhost:5432/langgraph?sslmode=disable
REDIS_URI=redis://localhost:6379/0
```

### 4. Run Migrations & Launch Server

```bash
# Run schema migration once before starting
uv run graphharbor migrate upgrade

# Start in development mode (with hot reload)
uv run graphharbor serve --reload

# Start in production mode (with multi-worker concurrency)
uv run graphharbor serve --host 0.0.0.0 --port 31296 --workers 4
```

Default port is **31296**. You will see live endpoints in the terminal banner:
- **API:** `http://127.0.0.1:31296`
- **LangSmith Studio:** `https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:31296`
- **Swagger Docs:** `http://127.0.0.1:31296/docs`

### 5. Call with Official SDK

```python
import asyncio
from langgraph_sdk import get_client

client = get_client(url="http://127.0.0.1:31296")

async def main():
    # Stream runs identically to official Agent Server
    async for chunk in client.runs.stream(
        None,  # threadless run
        "agent",  # assistant name from langgraph.json
        input={"messages": [{"role": "human", "content": "Hello GraphHarbor!"}]},
    ):
        print(chunk.event, chunk.data)

asyncio.run(main())
```

---

## 📚 Documentation Hub

GraphHarbor features a comprehensive, multi-layered documentation system under [`docs/`](docs/):

- 🧭 **[Documentation Hub (`docs/README.md`)](docs/README.md)** — Start here for full architecture, operational runbooks, and guides.
- 📋 **[Compatibility Profile (`docs/compatibility/profile.md`)](docs/compatibility/profile.md)** — Detailed capability assessment against official LangGraph specs.
- 🚦 **[Standards & Release Process (`docs/standards/release-process.md`)](docs/standards/release-process.md)** — Release governance, gate checks, and package lockstep policies.
- 🛠 **[Developer & Testing Guides (`docs/guides/README.md`)](docs/guides/README.md)** — Local development workflow, testing guidelines, and E2E scripts.
- 🚑 **[Incident Recovery Runbook (`docs/runbooks/incident-recovery.md`)](docs/runbooks/incident-recovery.md)** — Production troubleshooting, database recovery, and rollback procedures.
- 📜 **[Changelog (`docs/CHANGELOG.md`)](docs/CHANGELOG.md)** — Track full release notes across versions.

---

## Monorepo Layout

```text
libs/
├── langhost/                  # graphharbor: CLI & ASGI HTTP/SSE gateway
└── langgraph-runtime-pg/      # graphharbor-runtime: PostgreSQL + Redis execution engine
docs/                          # Central documentation hub & compatibility matrix
scripts/                       # Local CI & test automation scripts
tests/                         # End-to-end acceptance test suites
```

---

## Heritage & License

This project is licensed under the [MIT License](LICENSE).

GraphHarbor originated as an independent open-source fork inspired by early community explorations around self-hosted LangGraph runtimes. Today, it has evolved into a self-governed, enterprise-grade architecture maintaining its own independent development lifecycle, production resilience mechanisms, and multi-agent persistence capabilities.

Not affiliated with, sponsored by, or endorsed by LangChain, Inc. LangGraph and LangSmith are trademarks of LangChain, Inc.
