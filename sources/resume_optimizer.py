"""
Resume Optimizer module for AgenticSeek.

Provides utilities to:
- Fetch a job description from a URL
- Analyze the job description with an LLM (ATS keywords, skills, culture signals)
- Build a tailored resume from a master resume using LLM analysis
"""

import ipaddress
import json
import re
import socket
from typing import Optional
from urllib.parse import urlparse

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

# HTML tag pattern — length-capped to prevent catastrophic backtracking
_HTML_TAG_RE = re.compile(r"<[^>]{0,1000}>")


def _validate_url(url: str) -> Optional[str]:
    """
    Validate that ``url`` is a safe, public HTTP/HTTPS URL.

    Returns an error message string if the URL is invalid or points to a
    private/reserved network address, or None if the URL is acceptable.
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return "Malformed URL."

    if parsed.scheme not in ("http", "https"):
        return "Only http:// and https:// URLs are supported."

    hostname = parsed.hostname
    if not hostname:
        return "URL has no hostname."

    # Resolve to IP and check for private/reserved ranges (SSRF prevention)
    try:
        ip_str = socket.gethostbyname(hostname)
        ip = ipaddress.ip_address(ip_str)
    except (socket.gaierror, ValueError):
        # Could not resolve — let the downstream request fail naturally
        return None

    if ip.is_private or ip.is_loopback or ip.is_reserved or ip.is_link_local:
        logger.warning(f"Blocked request to private/reserved address: {ip_str} ({hostname})")
        return "Requests to private or reserved network addresses are not allowed."

    return None


def fetch_job_description(url: str) -> str:
    """
    Fetch and return the plain text of a job description from a URL.

    Validates the URL to prevent SSRF before making the request.
    Falls back gracefully with a descriptive error string if the page cannot
    be retrieved or parsed so that callers can surface the message to the user
    instead of raising an unhandled exception.

    Args:
        url: A fully-qualified HTTP/HTTPS URL pointing to a job posting.

    Returns:
        The extracted plain text of the page, or an error message string
        prefixed with "ERROR: ".
    """
    url_error = _validate_url(url)
    if url_error:
        logger.warning(f"URL validation failed for '{url}': {url_error}")
        return f"ERROR: {url_error}"

    try:
        response = requests.get(url, headers=_REQUEST_HEADERS, timeout=_REQUEST_TIMEOUT)
        response.raise_for_status()
    except requests.exceptions.Timeout:
        msg = f"Request timed out after {_REQUEST_TIMEOUT}s while fetching the job posting."
        logger.warning(msg)
        return f"ERROR: {msg}"
    except requests.exceptions.HTTPError as exc:
        msg = f"HTTP {exc.response.status_code} error while fetching the job posting."
        logger.warning(f"HTTPError for {url}: {exc}")
        return f"ERROR: {msg}"
    except requests.exceptions.RequestException as exc:
        msg = "Failed to fetch the job posting URL."
        logger.warning(f"RequestException for {url}: {exc}")
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
        logger.info(f"Fetched {len(text)} chars from job posting URL")
        return text
    except ImportError:
        # BeautifulSoup not available — strip HTML tags with the pre-compiled regex
        logger.warning("beautifulsoup4 not available, falling back to regex HTML stripping")
        text = _HTML_TAG_RE.sub(" ", response.text)
        text = re.sub(r"\s{2,}", " ", text).strip()
        return text
    except (AttributeError, TypeError, ValueError) as exc:
        msg = "Failed to parse the job posting page content."
        logger.error(f"Parse error for {url}: {exc}")
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
    except (ConnectionError, NotImplementedError, ModuleNotFoundError) as exc:
        logger.error(f"Provider error during JD analysis: {exc}")
        return {"error": "The LLM provider failed to respond. Check your provider configuration."}
    except Exception as exc:  # noqa: BLE001
        logger.error(f"Unexpected error during JD analysis: {exc}")
        return {"error": "An unexpected error occurred while analyzing the job description."}

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
    except (ConnectionError, NotImplementedError, ModuleNotFoundError) as exc:
        logger.error(f"Provider error during resume building: {exc}")
        return {
            "tailored_resume": "",
            "missing_from_master": [],
            "ats_score_estimate": "unknown",
            "error": "The LLM provider failed to respond. Check your provider configuration.",
        }
    except Exception as exc:  # noqa: BLE001
        logger.error(f"Unexpected error during resume building: {exc}")
        return {
            "tailored_resume": "",
            "missing_from_master": [],
            "ats_score_estimate": "unknown",
            "error": "An unexpected error occurred while generating the resume.",
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
    except (ValueError, KeyError, AttributeError) as exc:
        logger.error(f"PDF parsing error: {exc}")
        return "ERROR: Failed to extract text from the PDF. The file may be corrupted or encrypted."
    except Exception as exc:  # noqa: BLE001
        logger.error(f"Unexpected PDF extraction error: {exc}")
        return "ERROR: Failed to extract text from the PDF."
