# mcp-linkedin

An [MCP (Model Context Protocol)](https://modelcontextprotocol.io) server to **search and read LinkedIn job offers without a LinkedIn account**.

It lets an AI assistant (Claude, or any MCP client) search for jobs, internships and work-study positions, and turn the Job IDs it finds (for example in LinkedIn email alerts) into complete offer details: title, company, location, seniority level, contract type, number of applicants, direct apply link and full description in Markdown.

![Python](https://img.shields.io/badge/python-3.12%2B-blue)
![MCP](https://img.shields.io/badge/MCP-FastMCP-purple)
![Docker](https://img.shields.io/badge/docker-ready-2496ED)

---

## Features

- **No account, no API key**: uses LinkedIn's public guest endpoints only.
- **Job search** powered by [JobSpy](https://github.com/speedyapply/JobSpy), with filters for location, radius, contract type, remote and age of the offer.
- **Offer details** from a Job ID or any LinkedIn job URL (`/jobs/view/...`, `?currentJobId=...`).
- **Batch lookup** of up to 25 offers in one call, with deduplication and randomized pauses to avoid rate limiting.
- **Built-in resilience**: retries with backoff on HTTP 429, in-memory LRU cache (512 offers), clear error messages for expired or missing offers.
- **Token-friendly output**: empty fields are dropped and descriptions are truncated to a configurable length.
- **Several transports**: `streamable-http` (default), `sse` or `stdio`.
- **Docker-ready**, runs as a non-root user, with a `/health` endpoint.

## How it works

```
┌──────────────┐   MCP (HTTP / stdio)   ┌──────────────────┐   HTTPS (guest)   ┌──────────────┐
│  MCP client  │ ─────────────────────▶ │   mcp-linkedin   │ ────────────────▶ │   LinkedIn   │
│ (Claude ...) │ ◀───────────────────── │ FastMCP + JobSpy │ ◀──────────────── │ public pages │
└──────────────┘      JSON results      └──────────────────┘   HTML parsing    └──────────────┘
```

- `search_jobs` delegates the search to JobSpy.
- `get_linkedin_job(s)` fetches `linkedin.com/jobs-guest/jobs/api/jobPosting/{id}` and parses the HTML with BeautifulSoup.

## Tools

| Tool | Description |
|------|-------------|
| `search_jobs` | Search LinkedIn job offers by keywords and filters. |
| `get_linkedin_job` | Full details of one offer, from a Job ID or a LinkedIn URL. |
| `get_linkedin_jobs` | Full details of several offers at once (max 25). |

### `search_jobs`

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `search_term` | `str` | required | Keywords, e.g. `"python developer internship"`. |
| `location` | `str` | `"Paris, France"` | Search location. |
| `job_type` | `"internship" \| "fulltime" \| "parttime" \| "contract" \| null` | `null` | Contract type. |
| `hours_old` | `int \| null` | `24` | Maximum age of the offers, in hours. |
| `results_wanted` | `int` | `20` | Number of results, clamped between 1 and 50 (about 5 s per page of 10). |
| `fetch_description` | `bool` | `false` | Fetch the full description (one extra request per offer, slower). |
| `is_remote` | `bool` | `false` | Remote offers only. |
| `distance_km` | `int` | `40` | Search radius in kilometers. |
| `max_description_chars` | `int` | `1500` | Description length limit when `fetch_description` is enabled. |

> **Tip:** work-study ("alternance") is not a LinkedIn job type. Put the word `alternance` in `search_term` instead.

Returns `{ "count": int, "jobs": [...] }`. Each job contains `id`, `site`, `title`, `company`, `location`, `date_posted`, `job_type`, `job_level`, `is_remote`, `url`, `url_direct` and, if requested, `description`.

### `get_linkedin_job`

| Parameter | Type | Description |
|-----------|------|-------------|
| `job` | `str` | Job ID (`"4461745138"`) or any LinkedIn URL containing it. |

Returns `job_id`, `url`, `title`, `company`, `location`, `posted`, `applicants`, `seniority_level`, `employment_type`, `job_function`, `industry`, `apply_url_direct` and `description` (Markdown). English and French LinkedIn labels are both supported.

### `get_linkedin_jobs`

| Parameter | Type | Description |
|-----------|------|-------------|
| `jobs` | `list[str]` | Up to 25 Job IDs or URLs. Duplicates are ignored. |

Returns `{ "count": int, "jobs": [...], "invalid": [...] | null }`, where `invalid` lists the inputs in which no Job ID could be found.

## Getting started

### Requirements

- Python **3.12+** and [uv](https://docs.astral.sh/uv/), **or**
- Docker and Docker Compose

### Run locally

```bash
git clone https://github.com/ynsz-66/mcp-linkedin.git
cd mcp-linkedin
uv sync
HOST=127.0.0.1 uv run server.py
```

The MCP endpoint is then available at `http://127.0.0.1:8080/mcp-linkedin`.

### Run with Docker

```bash
cp .env.example .env
docker compose up -d --build
curl http://127.0.0.1:8080/health   # {"status":"ok"}
```

The port is bound to `127.0.0.1` only. Put a reverse proxy in front of it if you want to expose the server.

## Configuration

All settings are environment variables (see [`.env.example`](.env.example)).

| Variable | Default | Description |
|----------|---------|-------------|
| `MCP_TRANSPORT` | `streamable-http` | `streamable-http`, `sse` or `stdio`. |
| `MCP_PATH` | `/mcp-linkedin` | HTTP path of the MCP endpoint. |
| `HOST` | `0.0.0.0` | Bind address (`0.0.0.0` in Docker, `127.0.0.1` locally). |
| `PORT` | `8080` | HTTP port. |
| `MAX_DESCRIPTION_CHARS` | `6000` | Description length limit for `get_linkedin_job(s)`. |
| `LOG_LEVEL` | `INFO` | Python logging level. |

## Connecting an MCP client

### Claude Code (HTTP)

```bash
claude mcp add --transport http linkedin http://127.0.0.1:8080/mcp-linkedin
```

### Claude Desktop or any stdio client

```json
{
  "mcpServers": {
    "linkedin": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/mcp-linkedin", "server.py"],
      "env": { "MCP_TRANSPORT": "stdio" }
    }
  }
}
```

### Example prompts

- *"Find Python developer internships posted in Paris in the last 24 hours."*
- *"Here are the job IDs from my LinkedIn alert email, give me the details and the direct apply links."*
- *"Search for remote AI work-study positions in France and summarize the requirements."*

## Limitations

- The server relies on LinkedIn's **public, undocumented** pages. If LinkedIn changes its HTML, some fields may come back empty. In that case the response contains an explicit `error` field.
- LinkedIn rate limits anonymous traffic. Keep `results_wanted` and batch sizes reasonable.
- The cache lives in memory and is cleared when the server restarts.

## Disclaimer

This project is not affiliated with, endorsed by or sponsored by LinkedIn. It only reads publicly accessible pages. You are responsible for using it in accordance with [LinkedIn's User Agreement](https://www.linkedin.com/legal/user-agreement) and the laws that apply to you. Use it for personal job searching, at a moderate request rate.

## Acknowledgements

- [JobSpy](https://github.com/speedyapply/JobSpy) for the job scraping engine.
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) for FastMCP.
