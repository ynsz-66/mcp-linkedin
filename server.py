from __future__ import annotations

import logging
import math
import os
import random
import re
import time
from functools import lru_cache
from typing import Any, Literal

import anyio
import pandas as pd
import requests
from bs4 import BeautifulSoup, Tag
from jobspy import scrape_jobs
from jobspy.linkedin.constant import headers as LINKEDIN_HEADERS
from jobspy.util import markdown_converter
from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

MCP_PATH: str = os.environ.get("MCP_PATH", "/mcp-linkedin")
PORT: int = int(os.environ.get("PORT", "8080"))
MAX_DESCRIPTION_CHARS: int = int(os.environ.get("MAX_DESCRIPTION_CHARS", "6000"))
LOG_LEVEL: str = os.environ.get("LOG_LEVEL", "INFO")
HOST: str = os.environ.get("HOST", "0.0.0.0")
MCP_TRANSPORT: str = os.environ.get("MCP_TRANSPORT", "streamable-http")

logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("mcp-linkedin")

mcp = FastMCP(
    "linkedin-jobs",
    instructions=(
        "Read and search LinkedIn job offers (no account). "
        "Use get_linkedin_jobs to enrich Job IDs found in email alerts for example, "
        "search_jobs to search offers directly on LinkedIn."
    ),
    host=HOST,
    port=PORT,
    streamable_http_path=MCP_PATH,
    stateless_http=True,
    json_response=True,
)

linkedin_session = requests.Session()
linkedin_session.headers.update(LINKEDIN_HEADERS)
JOB_ID_PATTERN = re.compile(r"(\d{8,12})")


def extract_job_id(job_reference: str) -> str:
    """Accept an ID ('4461745138') or a LinkedIn URL (/jobs/view/..., currentJobId=...)."""
    job_reference = job_reference.strip()
    match = re.search(r"currentJobId=(\d+)", job_reference) or re.search(
        r"/jobs/view/(?:[^/?]*-)?(\d+)", job_reference
    )
    if match:
        return match.group(1)
    match = JOB_ID_PATTERN.search(job_reference)
    if not match:
        raise ValueError(f"No LinkedIn Job ID found in: {job_reference!r}")
    return match.group(1)


def element_text(element: Tag | None) -> str | None:
    return element.get_text(" ", strip=True) if element else None


def truncate_text(text: str | None, max_chars: int = MAX_DESCRIPTION_CHARS) -> str | None:
    if text and len(text) > max_chars:
        return text[:max_chars].rstrip() + "\n\n[…description truncated]"
    return text


@lru_cache(maxsize=512)
def fetch_linkedin_job(job_id: str) -> dict[str, Any]:
    url = f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"
    for attempt in range(3):
        response: requests.Response = linkedin_session.get(url, timeout=15)
        if response.status_code == 429:
            wait_seconds = 5 * (attempt + 1)
            logger.warning("LinkedIn 429 on %s, waiting %ss", job_id, wait_seconds)
            time.sleep(wait_seconds)
            continue
        break
    if response.status_code == 404:
        return {"job_id": job_id, "error": "Job offer not found or expired (404)."}
    if response.status_code != 200:
        return {"job_id": job_id, "error": f"LinkedIn responded {response.status_code}."}

    page = BeautifulSoup(response.text, "html.parser")
    description_element = page.find(
        "div", class_=lambda css_class: css_class and "show-more-less-html__markup" in css_class
    )
    description = markdown_converter(str(description_element)).strip() if description_element else None

    job_criteria: dict[str, str] = {}
    for criteria_item in page.select("li.description__job-criteria-item"):
        criteria_name = element_text(criteria_item.find("h3"))
        criteria_value = element_text(criteria_item.find("span", class_="description__job-criteria-text"))
        if criteria_name and criteria_value:
            job_criteria[criteria_name] = criteria_value

    direct_apply_url: str | None = None
    apply_url_element = page.find("code", id="applyUrl")
    if apply_url_element and apply_url_element.string:
        match = re.search(r'\?url=([^"]+)', apply_url_element.string)
        if match:
            direct_apply_url = requests.utils.unquote(match.group(1))

    def find_criteria(*criteria_names: str) -> str | None:
        for criteria_name in criteria_names:
            if criteria_name in job_criteria:
                return job_criteria[criteria_name]
        return None

    job_details: dict[str, Any] = {
        "job_id": job_id,
        "url": f"https://www.linkedin.com/jobs/view/{job_id}",
        "title": element_text(page.find(class_="top-card-layout__title")),
        "company": element_text(page.find(class_="topcard__org-name-link"))
        or element_text(page.find(class_="topcard__flavor")),
        "location": element_text(page.select_one(".topcard__flavor--bullet")),
        "posted": element_text(page.find(class_="posted-time-ago__text")),
        "applicants": element_text(page.find(class_="num-applicants__caption"))
        or element_text(page.find(class_="num-applicants__figure")),
        "seniority_level": find_criteria("Seniority level", "Niveau hiérarchique"),
        "employment_type": find_criteria("Employment type", "Type d’emploi", "Type d'emploi"),
        "job_function": find_criteria("Job function", "Fonction"),
        "industry": find_criteria("Industries", "Secteurs"),
        "apply_url_direct": direct_apply_url,
        "description": truncate_text(description),
    }
    if not description and not job_details["title"]:
        job_details["error"] = "Page received but empty (LinkedIn may have changed its HTML or is rate limiting)."
    return {key: value for key, value in job_details.items() if value not in (None, "")}


def row_to_job(row: dict[str, Any], max_description_chars: int) -> dict[str, Any]:
    def clean_value(value: Any) -> Any:
        # pandas represents empty cells as NaN
        if isinstance(value, float) and math.isnan(value):
            return None
        return value

    job_summary: dict[str, Any] = {
        "id": clean_value(row.get("id")),
        "site": clean_value(row.get("site")),
        "title": clean_value(row.get("title")),
        "company": clean_value(row.get("company")),
        "location": clean_value(row.get("location")),
        "date_posted": str(row["date_posted"]) if clean_value(row.get("date_posted")) else None,
        "job_type": clean_value(row.get("job_type")),
        "job_level": clean_value(row.get("job_level")),
        "is_remote": clean_value(row.get("is_remote")),
        "url": clean_value(row.get("job_url")),
        "url_direct": clean_value(row.get("job_url_direct")),
    }
    description = clean_value(row.get("description"))
    if description and max_description_chars > 0:
        job_summary["description"] = truncate_text(description, max_description_chars)
    return {key: value for key, value in job_summary.items() if value not in (None, "", [])}


@mcp.tool()
async def search_jobs(
    search_term: str,
    location: str = "Paris, France",
    job_type: Literal["internship", "fulltime", "parttime", "contract"] | None = None,
    hours_old: int | None = 24,
    results_wanted: int = 20,
    fetch_description: bool = False,
    is_remote: bool = False,
    distance_km: int = 40,
    max_description_chars: int = 1500,
) -> dict[str, Any]:
    """Search job offers on LinkedIn (no account).

    - search_term: keywords, e.g. "stage développeur python", "alternance IA".
      Tip: work-study ("alternance") is not a LinkedIn job type → put "alternance" in search_term.
    - job_type: "internship" for internships.
    - hours_old: max age in hours (24 = today's offers).
    - results_wanted: 1 to 50. The higher, the slower (~5 s per page of 10).
    - fetch_description: fetch the full description (1 extra request per offer, slow).
    """
    results_wanted = max(1, min(results_wanted, 50))

    def run_scraper() -> pd.DataFrame | None:
        return scrape_jobs(
            site_name=["linkedin"],
            search_term=search_term,
            location=location,
            distance=max(1, round(distance_km / 1.609)),  # JobSpy expects miles
            is_remote=is_remote,
            job_type=job_type,
            results_wanted=results_wanted,
            hours_old=hours_old,
            linkedin_fetch_description=fetch_description,
            description_format="markdown",
            verbose=0,
        )

    try:
        jobs_dataframe = await anyio.to_thread.run_sync(run_scraper)
    except Exception as error:
        logger.exception("search_jobs failed")
        return {"count": 0, "jobs": [], "error": str(error)}

    description_limit = max_description_chars if fetch_description else 0
    jobs = (
        [row_to_job(row, description_limit) for row in jobs_dataframe.to_dict(orient="records")]
        if jobs_dataframe is not None
        else []
    )
    return {"count": len(jobs), "jobs": jobs}


@mcp.tool()
async def get_linkedin_job(job: str) -> dict[str, Any]:
    """Full details of a LinkedIn job offer: title, company, location, level, contract type,
    industry, number of applicants, direct apply link and description (markdown).

    - job: Job ID ('4461745138') or any LinkedIn URL containing the ID.
    """
    try:
        job_id = extract_job_id(job)
    except ValueError as error:
        return {"error": str(error)}
    try:
        return await anyio.to_thread.run_sync(fetch_linkedin_job, job_id)
    except Exception as error:
        logger.exception("get_linkedin_job failed")
        return {"job_id": job_id, "error": str(error)}


@mcp.tool()
async def get_linkedin_jobs(jobs: list[str]) -> dict[str, Any]:
    """Details of several LinkedIn job offers at once (max 25), with a short pause between
    requests to avoid getting blocked. Ideal for Job IDs extracted from email alerts.
    """
    job_ids: list[str] = []
    invalid_inputs: list[dict[str, str]] = []
    for job_reference in jobs[:25]:
        try:
            job_id = extract_job_id(job_reference)
            if job_id not in job_ids:
                job_ids.append(job_id)
        except ValueError as error:
            invalid_inputs.append({"input": job_reference, "error": str(error)})

    results: list[dict[str, Any]] = []
    for index, job_id in enumerate(job_ids):
        cache_hits_before = fetch_linkedin_job.cache_info().hits
        try:
            results.append(await anyio.to_thread.run_sync(fetch_linkedin_job, job_id))
        except Exception as error:
            results.append({"job_id": job_id, "error": str(error)})
        # no pause if the offer came from the cache (no request sent)
        is_last_job = index == len(job_ids) - 1
        if not is_last_job and fetch_linkedin_job.cache_info().hits == cache_hits_before:
            await anyio.sleep(random.uniform(1.0, 2.5))
    return {"count": len(results), "jobs": results, "invalid": invalid_inputs or None}


@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


if __name__ == "__main__":
    mcp.run(transport=MCP_TRANSPORT)
