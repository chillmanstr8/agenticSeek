"""
Resume Optimizer module for AgenticSeek.

Provides utilities to:
- Fetch a job description from a URL
- Analyze the job description with an LLM (ATS keywords, skills, culture signals)
- Build a tailored resume from a master resume using LLM analysis
"""

import json
import re
import os
from typing import Optional

import requests

from sources.logger import Logger

logger = Logger("resume_optimizer.log")

_REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

_REQUEST_TIMEOUT = 15  # seconds


def fetch_job_description(url: str) -> str:
    """
    Fetch and return the plain text of a job description from a URL.

    Falls back gracefully with a descriptive error string if the page cannot
    be retrieved or parsed so that callers can surface the message to the user
    instead of raising an unhandled exception.

    Args:
        url: A fully-qualified HTTP/HTTPS URL pointing to a job posting.

    Returns:
        The extracted plain text of the page, or an error message string.
    """
    try:
        response = requests.get(url, headers=_REQUEST_HEADERS, timeout=_REQUEST_TIMEOUT)
        response.raise_for_status()
    except requests.exceptions.Timeout:
        msg = f"Request timed out after {_REQUEST_TIMEOUT}s while fetching: {url}"
        logger.warning(msg)
        return f"ERROR: {msg}"
    except requests.exceptions.HTTPError as exc:
        msg = f"HTTP {exc.response.status_code} error fetching: {url}"
        logger.warning(msg)
        return f"ERROR: {msg}"
    except requests.exceptions.RequestException as exc:
        msg = f"Failed to fetch URL ({exc}): {url}"
        logger.warning(msg)
        return f"ERROR: {msg}"

    try:
        from bs4 import BeautifulSoup  # optional but preferred
        soup = BeautifulSoup(response.text, "lxml")
        # Remove noisy script/style tags
        for tag in soup(["script", "style", "noscript", "header", "footer", "nav"]):
            tag.decompose()
        text = soup.get_text(separator="\n")
        # Collapse excessive blank lines
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        logger.info(f"Fetched {len(text)} chars from {url}")
        return text
    except ImportError:
        # BeautifulSoup not available — strip HTML tags with a simple regex
        logger.warning("beautifulsoup4 not available, falling back to regex HTML stripping")
        text = re.sub(r"<[^>]+>", " ", response.text)
        text = re.sub(r"\s{2,}", " ", text).strip()
        return text
    except Exception as exc:  # noqa: BLE001
        msg = f"Failed to parse page content: {exc}"
        logger.error(msg)
        return f"ERROR: {msg}"


def _load_prompt(prompt_path: str) -> str:
    """Load a prompt file relative to the project root."""
    with open(prompt_path, "r", encoding="utf-8") as fh:
        return fh.read()


def analyze_job_description(jd_text: str, provider) -> dict:
    """
    Send the job description to the LLM and return a structured analysis dict.

    The returned dict always contains these keys (with empty fallbacks on error):
        required_skills, implied_skills, ats_keywords, culture_signals,
        seniority_level, top_priorities, analysis_summary

    Args:
        jd_text:  The raw text of the job description.
        provider: An initialised Provider instance from sources.llm_provider.

    Returns:
        A dict with the analysis, or a dict with an "error" key on failure.
    """
    system_prompt = _load_prompt("prompts/base/resume_analyzer.txt")
    user_message = (
        "Analyze the following job description and return the JSON analysis as instructed.\n\n"
        f"--- JOB DESCRIPTION START ---\n{jd_text}\n--- JOB DESCRIPTION END ---"
    )
    history = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]

    try:
        raw = provider.respond(history, verbose=False)
    except Exception as exc:  # noqa: BLE001
        logger.error(f"LLM error during JD analysis: {exc}")
        return {"error": str(exc)}

    # Strip any accidental markdown fences the model may have added
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-z]*\n?", "", cleaned)
        cleaned = re.sub(r"\n?```$", "", cleaned.rstrip())

    try:
        result = json.loads(cleaned)
    except json.JSONDecodeError:
        # Try to find the first JSON object inside the response
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            try:
                result = json.loads(match.group())
            except json.JSONDecodeError:
                logger.error(f"Could not parse JSON from LLM response: {cleaned[:500]}")
                result = {"error": "Could not parse JSON from model response", "raw": cleaned}
        else:
            logger.error(f"No JSON found in LLM response: {cleaned[:500]}")
            result = {"error": "No JSON found in model response", "raw": cleaned}

    # Ensure all expected keys exist with safe defaults
    defaults = {
        "required_skills": [],
        "implied_skills": [],
        "ats_keywords": [],
        "culture_signals": [],
        "seniority_level": "unknown",
        "top_priorities": [],
        "analysis_summary": "",
    }
    for key, default in defaults.items():
        result.setdefault(key, default)

    logger.info(f"JD analysis complete: {len(result.get('ats_keywords', []))} ATS keywords found")
    return result


def build_tailored_resume(master_resume: str, analysis: dict, provider) -> dict:
    """
    Generate a tailored resume Markdown string and extract gap/score metadata.

    Args:
        master_resume: Full text of the candidate's master resume.
        analysis:      The dict returned by analyze_job_description().
        provider:      An initialised Provider instance.

    Returns:
        A dict with keys:
            tailored_resume  (str)  — Markdown of the optimised resume
            missing_from_master (list) — skills absent from the master resume
            ats_score_estimate  (str)  — e.g. "~78%"
    """
    system_prompt = _load_prompt("prompts/base/resume_builder.txt")

    analysis_text = json.dumps(analysis, indent=2)
    user_message = (
        "Here is the job analysis:\n\n"
        f"```json\n{analysis_text}\n```\n\n"
        "Here is the candidate's master resume:\n\n"
        f"--- MASTER RESUME START ---\n{master_resume}\n--- MASTER RESUME END ---\n\n"
        "Please produce the tailored resume as instructed."
    )
    history = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]

    try:
        raw = provider.respond(history, verbose=False)
    except Exception as exc:  # noqa: BLE001
        logger.error(f"LLM error during resume building: {exc}")
        return {
            "tailored_resume": "",
            "missing_from_master": [],
            "ats_score_estimate": "unknown",
            "error": str(exc),
        }

    # Split the response into the resume body and the metadata sections
    tailored_resume = raw.strip()
    missing_from_master = []
    ats_score_estimate = "unknown"

    # Extract GAPS TO ADDRESS section
    gaps_match = re.search(
        r"##\s*GAPS TO ADDRESS\s*\n(.*?)(?=##\s*ATS MATCH ESTIMATE|$)",
        tailored_resume,
        re.DOTALL | re.IGNORECASE,
    )
    if gaps_match:
        gaps_text = gaps_match.group(1).strip()
        for line in gaps_text.splitlines():
            line = line.lstrip("-• ").strip()
            if line:
                missing_from_master.append(line)

    # Extract ATS MATCH ESTIMATE section
    ats_match = re.search(
        r"##\s*ATS MATCH ESTIMATE\s*\n(.*?)$",
        tailored_resume,
        re.DOTALL | re.IGNORECASE,
    )
    if ats_match:
        ats_block = ats_match.group(1).strip()
        pct_match = re.search(r"~?\d+%", ats_block)
        if pct_match:
            ats_score_estimate = pct_match.group()

    # Strip the metadata sections from the resume body returned to the user
    separator_idx = re.search(
        r"\n---\s*\n##\s*GAPS TO ADDRESS",
        tailored_resume,
        re.IGNORECASE,
    )
    if separator_idx:
        tailored_resume = tailored_resume[: separator_idx.start()].strip()

    logger.info(
        f"Resume built: {len(missing_from_master)} gaps identified, ATS estimate={ats_score_estimate}"
    )
    return {
        "tailored_resume": tailored_resume,
        "missing_from_master": missing_from_master,
        "ats_score_estimate": ats_score_estimate,
    }


def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """
    Extract plain text from a PDF file given as raw bytes.

    Uses pypdf which is already in requirements.txt.
    Returns the extracted text, or an error string on failure.
    """
    try:
        import io
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(pdf_bytes))
        pages = [page.extract_text() or "" for page in reader.pages]
        text = "\n".join(pages).strip()
        logger.info(f"Extracted {len(text)} chars from PDF ({len(reader.pages)} pages)")
        return text
    except ImportError:
        return "ERROR: pypdf is not installed. Install it with: pip install pypdf"
    except Exception as exc:  # noqa: BLE001
        msg = f"Failed to extract PDF text: {exc}"
        logger.error(msg)
        return f"ERROR: {msg}"
