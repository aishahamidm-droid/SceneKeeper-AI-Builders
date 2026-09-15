# SceneKeeper

SceneKeeper is an AI script supervisor and continuity agent built for the Agentic Cinema: The Blockbuster Hackathon.

Film productions have a lot of small details that need to remain consistent between scenes: wardrobe, props, injuries, character appearance and object locations. SceneKeeper keeps those production records in ClickHouse and lets an AI agent inspect them for continuity problems.

## Technical Overview

**[Read the SceneKeeper Technical Overview (PDF)](docs/SceneKeeper_Technical_Overview.pdf)**

A six-page guide for hackathon judges covering the current architecture, screenplay ingestion, production isolation, Gemini reasoning, official mcp-clickhouse integration, and implementation scope.

## What SceneKeeper Does

SceneKeeper can compare production information across scenes and flag inconsistencies such as:

- wardrobe changes
- props changing condition or location
- accessories moving between shots
- unexplained changes to a character's physical condition
- other continuity details stored in the production records

Instead of only reporting that something changed, SceneKeeper produces a continuity report with severity levels and an action plan for the production crew.

## Architecture

SceneKeeper uses:

- **Gemini** through the official `google-genai` Python SDK for agent reasoning, tool selection and report generation
- **ClickHouse Cloud** for production continuity data
- **Official `mcp-clickhouse` MCP server** for runtime access to ClickHouse
- **Model Context Protocol (MCP)** over a stdio subprocess connection
- **Python** for the application and agent workflow
- **Streamlit** for the interface
- **Streamlit Community Cloud** for the hosted demo

## How the Agent Works

When a continuity question is submitted:

1. SceneKeeper sends the request to Gemini.
2. Gemini determines what production information it needs.
3. SceneKeeper exposes the available ClickHouse MCP tools to Gemini.
4. Gemini selects the appropriate tool and query.
5. The official `mcp-clickhouse` server executes the request against ClickHouse Cloud.
6. The result is returned to Gemini.
7. Gemini can request additional information when necessary.
8. SceneKeeper generates the final continuity report and action plan.

The application also displays live MCP execution logs so the runtime interaction with the ClickHouse MCP server can be inspected during the demo.

## Current Demo Data

The demonstration contains production records across multiple scenes, including character state, wardrobe and prop information. Some records deliberately contain continuity differences so the agent can identify and explain them.

## Run Locally

### 1. Clone the repository

```bash
git clone https://github.com/aishahamidm-droid/scenekeeper.git
cd scenekeeper
```

### 2. Create a virtual environment

```bash
python -m venv .venv
```

Windows:

```powershell
.venv\Scripts\Activate.ps1
```

Linux/macOS:

```bash
source .venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment variables

Create a `.env` file in the project root.

```env
GEMINI_API_KEY=your_gemini_api_key
CLICKHOUSE_HOST=your_clickhouse_host
CLICKHOUSE_USER=your_clickhouse_user
CLICKHOUSE_PASSWORD=your_clickhouse_password
CLICKHOUSE_PORT=8443
CLICKHOUSE_SECURE=true
```

Do not commit the `.env` file. It is excluded through `.gitignore`.

### 5. Run SceneKeeper

```bash
streamlit run app.py
```

## ClickHouse MCP Integration

SceneKeeper uses the official `mcp-clickhouse` server during the agent workflow rather than connecting it only as a demonstration or static integration.

The MCP server runs as a subprocess using stdio transport. SceneKeeper discovers the server's tools and allows Gemini to invoke the appropriate ClickHouse operations while reasoning about a continuity request.

Runtime MCP activity can be viewed from the **Live MCP Execution Logs** section of the application.

## Live Application

https://scenekeeper.streamlit.app

## Source Code

https://github.com/aishahamidm-droid/scenekeeper

## Hackathon

Built as a new project for **Agentic Cinema: The Blockbuster Hackathon**, submitted to the **ClickHouse partner track**.

## License

SceneKeeper is released under the MIT License. See `LICENSE`.