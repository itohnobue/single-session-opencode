#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["scrapling[fetchers]", "ddgs", "trafilatura", "rank-bm25", "httpx", "feedparser", "pypdf"]
# ///
# -*- coding: utf-8 -*-
"""
Web Research Tool - Autonomous Search + Fetch + Report

Unified tool combining search and fetch into a single optimized workflow:
1. Search via DuckDuckGo for maximum coverage
2. Filter and deduplicate URLs during search (early filtering)
3. Fetch content in parallel via Scrapling (TLS fingerprinting, anti-bot bypass)
4. Scrapling text extraction fallback for "Too short" pages
5. Search mode: full filtered text is written to a report file in
   tmp/webresearch/ (run-id.txt); stdout carries a compact technical
   index (FULL REPORT path first + stats + one line per page with
   @line/@hit locators) — the SAME index heads the report file itself,
   so a lost stdout digest is recoverable from the file. The model
   researches from the report file (read/grep by @line), not from stdout.
6. --url direct fetch: ONE URL per invocation. The full page is fetched
   (no output char cap; HTML extraction bounded by MAX_CONTENT_BYTES) and
   saved RAW to its own report file in tmp/webresearch/ (run-id.txt) —
   RAW = quality filters off and full-document DOM extraction (no
   content-area selection): every visible text node in page order,
   nav/boilerplate included. stdout prints ONLY the absolute path.
   JS pages and bot-gated pages are retried with a real browser —
   Google Chrome, driven by browser_fetch.py (the --url-chrome tier) and
   provisioned repo-locally into tmp/browser/chrome/ on first use:
   auto by default (only when the static fetch fails or is gated),
   --no-render to disable. If Chrome is unavailable the static result
   stands — a missing browser never fails the request.
7. Search mode is static-only by design (benchmarked: browser escalation in
   search cost +44% fetch time and rescued ~0-2 pages — the browser's value
   is --url single-page rendering, not bulk search fetching). The browser
   machinery (probe/install/escalation) is used ONLY by --url mode.
8. --since d|w|m|y: optional engine-side time window for the primary search
   only (bonus sources ignore it). Never inferred from the query.
9. --map URL: lists URLs on the URL's host inside its path subtree (sitemap,
   else page links), one per line, fixed cap 100. Lists only, no fetching.
10. --cite SEED: citers + references for one paper (DOI, doi.org/arXiv URL or
   OpenAlex id), 25 per section, OpenAlex with a Semantic Scholar fallback.

Usage:
    python web_research.py "search query"
    python web_research.py "query" --sci | --med | --tech   # Domain bonus sources (arXiv, PubMed, HN/Stack Overflow/GitHub)
    python web_research.py "query" --since w                # Optional engine time window (d/w/m/y)
    python web_research.py --url https://example.com   # Save one URL's full page text to a report file (path printed)
    python web_research.py --map https://example.com/docs/   # List URLs in that path subtree
    python web_research.py --cite 10.1038/s41586-020-2649-2  # Citers + references for a paper

(fixed tuned settings: request up to 30 results, fetch up to 20 pages, plain-text output)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import platform
import random
import re
import shutil
import string
import subprocess
import sys
import threading
import time
import types
import urllib.parse
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from dataclasses import dataclass, field
from html import unescape
from html.parser import HTMLParser
from io import StringIO
from pathlib import Path
from typing import (
    AsyncIterator,
    Dict,
    Iterator,
    List,
    Optional,
    Set,
    Tuple,
)

# =============================================================================
# LOGGING CONFIGURATION
# =============================================================================

# Suppress ALL library logging before any imports touch the root logger.
# Scrapling uses logging.info() (root logger) and named loggers — silence both.
logging.basicConfig(level=logging.CRITICAL, stream=sys.stderr)
logging.getLogger().setLevel(logging.CRITICAL)
for _lib in ("scrapling", "curl_cffi", "httpx", "hpack", "httpcore", "asyncio"):
    logging.getLogger(_lib).setLevel(logging.CRITICAL)

# Our own logger — restored to WARNING after imports
logger = logging.getLogger("web_research")
logger.setLevel(logging.WARNING)
_handler = logging.StreamHandler(sys.stderr)
_handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
logger.addHandler(_handler)
logger.propagate = False

# =============================================================================
# CONSTANTS
# =============================================================================

# Fetch timeout for single-page fetches (seconds)
DEFAULT_TIMEOUT: int = 5

# Whole-run wall-clock timeout (seconds). Bounds the entire research run on ALL
# platforms: search mode wraps the async core in asyncio.wait_for (Windows has
# no SIGALRM), and the Unix SIGALRM watchdog covers the post-processing phase
# outside the async core. Both mechanisms use this same value.
# Env-overridable for ops/testing (read at import time).
def _wall_timeout_default() -> int:
    """Whole-run wall-clock timeout (seconds); env-overridable, fail-soft."""
    raw = os.environ.get("WEB_RESEARCH_TIMEOUT_SECONDS", "")
    if raw.isdigit():
        return int(raw)
    return 300


WALL_TIMEOUT: int = _wall_timeout_default()

# ---------------------------------------------------------------------------
# Search-backend retry
#
# ddgs raises "No results found" both for a genuinely empty query and under
# throttling (observed live: back-to-back calls fail, the same query succeeds
# seconds later). One retry is the bound: it rescues the throttle case at the
# cost of one extra request when the query is truly empty. A partial result set
# is never re-requested.
# ---------------------------------------------------------------------------
SEARCH_MAX_ATTEMPTS: int = 2


def _search_retry_delay_default() -> float:
    """Seconds between search attempts; env-overridable, fail-soft."""
    try:
        return max(0.0, float(os.environ.get("WEB_RESEARCH_SEARCH_RETRY_DELAY", "2.5")))
    except ValueError:
        return 2.5


SEARCH_RETRY_DELAY: float = _search_retry_delay_default()

# ---------------------------------------------------------------------------
# Domain routing
#
# A flat deny list conflated four different problems into one answer. Each
# domain now names the access path that actually works for it:
#   block      — drop at intake (login wall, or no usable content)
#   wayback    — the live page is a paywall teaser; take the archived copy
#   scholarly  — peer-reviewed full text; resolve by PMCID, DOI or PMID
#   transcript — reserved for the video-transcript tier (inert today)
# Matching is exact host or real subdomain, never substring, so `nejm.org`
# cannot match `notnejm.org`.
# Set WEB_RESEARCH_ROUTES=off to restore the old flat drop (kill switch).
# ---------------------------------------------------------------------------
ROUTE_BLOCK = "block"
ROUTE_WAYBACK = "wayback"
ROUTE_SCHOLARLY = "scholarly"
ROUTE_TRANSCRIPT = "transcript"  # reserved — no fetcher is wired to it yet

_ROUTES_ENABLED = os.environ.get("WEB_RESEARCH_ROUTES", "").strip().lower() != "off"

ROUTES: Tuple[Tuple[str, str], ...] = (
    # Peer-reviewed / archived full text — resolved via PMCID, DOI or PMID.
    ("doi.org", ROUTE_SCHOLARLY),
    ("nejm.org", ROUTE_SCHOLARLY),
    ("cell.com", ROUTE_SCHOLARLY),
    ("sciencedirect.com", ROUTE_SCHOLARLY),
    ("onlinelibrary.wiley.com", ROUTE_SCHOLARLY),
    ("dl.acm.org", ROUTE_SCHOLARLY),
    ("amjmed.com", ROUTE_SCHOLARLY),
    ("zenodo.org", ROUTE_SCHOLARLY),
    ("europepmc.org", ROUTE_SCHOLARLY),
    ("pubmed.ncbi.nlm.nih.gov", ROUTE_SCHOLARLY),
    # Paywalled news: the live fetch returns 200 with a teaser, so the archived
    # copy is taken on stub/short content as well as on outright failure.
    ("nytimes.com", ROUTE_WAYBACK),
    ("forbes.com", ROUTE_WAYBACK),
    # Login wall or no usable content — still dropped at intake.
    ("facebook.com", ROUTE_BLOCK),
    ("tiktok.com", ROUTE_BLOCK),
    ("instagram.com", ROUTE_BLOCK),
    ("linkedin.com", ROUTE_BLOCK),
    ("youtube.com", ROUTE_BLOCK),  # transcript route not wired yet
    ("msn.com", ROUTE_BLOCK),  # redirects to stub/privacy pages
    ("percona.com", ROUTE_BLOCK),
    ("mctlaw.com", ROUTE_BLOCK),
    ("edmunds.com", ROUTE_BLOCK),
    ("cars.com", ROUTE_BLOCK),
    ("reddit.com", ROUTE_BLOCK),  # no zero-config path exists; browser probe rejected
    # twitter.com, x.com: unblocked — FxTwitter API for tweet text
    # medium.com: unblocked — full articles extract cleanly
)

SKIP_URL_PATTERNS: Tuple[str, ...] = (
    r"\.jpg$", r"\.png$", r"\.gif$", r"\.svg$", r"\.webp$",
    r"/login", r"/signin", r"/signup", r"/cart", r"/checkout",
    r"/tag/", r"/tags/", r"/category/", r"/categories/",
    r"/archive/", r"/page/\d+",
    r"bing\.com/aclick",  # Bing ad redirects — marketing/booking noise
    r"www\.yahoo\.com/",  # EU privacy consent walls, no usable content
    r"finance\.yahoo\.com/",  # EU privacy consent walls
    r"www\.aol\.com/",  # cookie/privacy consent walls
    # tech.yahoo.com: unblocked — returns actual article content
    # .pdf: handled by the PDF extractor (pdftotext, else the bundled pypdf)
)


# CAPTCHA/blocked page detection markers
BLOCKED_CONTENT_MARKERS: Tuple[str, ...] = (
    "verify you are human",
    "access to this page has been denied",
    "please complete the security check",
    "cloudflare ray id:",
    "checking your browser",
    "enable javascript and cookies",
    "unusual traffic from your computer",
    "are you a robot",
    "captcha",
    "perimeterx",
    "distil networks",
    "blocked by",
)

# =============================================================================
# COMPILED REGEX PATTERNS
# =============================================================================

# URL skip patterns (paths/extensions only — domain routing is separate)
_SKIP_URL_PATTERN = re.compile(
    r'(?:' + '|'.join(SKIP_URL_PATTERNS) + r')',
    re.IGNORECASE
)


def route_for(url: str) -> str:
    """Return the access route for a URL's host.

    Matching is exact host or real subdomain, never a substring, so
    ``nejm.org`` cannot match ``notnejm.org`` or ``nejm.org.evil.test``.
    Returns "" for an unrouted host (the ordinary fetch path). When routing is
    disabled (WEB_RESEARCH_ROUTES=off), every routed input degrades to a drop,
    which reproduces the previous flat deny-list behaviour.
    """
    try:
        host = (urllib.parse.urlparse(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return ROUTE_BLOCK
    if not host:
        return ROUTE_BLOCK
    for domain, route in ROUTES:
        if host == domain or host.endswith("." + domain):
            return route if _ROUTES_ENABLED else ROUTE_BLOCK
    return ""


# HTML extraction - simple fast patterns (optimized for speed)
RE_STRIP_TAGS = re.compile(
    r"<(script|style|nav|footer|header|aside|noscript|iframe|svg|form)[^>]*>.*?</\1>",
    re.DOTALL | re.IGNORECASE,
)
RE_COMMENTS = re.compile(r"<!--.*?-->", re.DOTALL)
RE_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
RE_JSON_LD = re.compile(
    r"<script[^>]*type\s*=\s*[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
    re.DOTALL | re.IGNORECASE,
)
RE_BR = re.compile(r"<br\s*/?>", re.IGNORECASE)
RE_BLOCK_END = re.compile(r"</(p|div|h[1-6]|li|tr|article|section)>", re.IGNORECASE)
RE_LI = re.compile(r"<li[^>]*>", re.IGNORECASE)
RE_ALL_TAGS = re.compile(r"<[^>]+>")
RE_SPACES = re.compile(r"[ \t]+")
RE_LEADING_SPACE = re.compile(r"\n[ \t]+")
RE_MULTI_NEWLINE = re.compile(r"\n{3,}")
RE_WHITESPACE = re.compile(r"\s+")
# Sentence boundary: period/exclamation/question + space + uppercase letter
# Handles common abbreviations by requiring 2+ chars before the period
RE_SENT_SPLIT = re.compile(r'(?<=[.!?])\s+(?=[A-Z])')
# Wikipedia cleanup patterns
RE_WIKI_CITE = re.compile(r'\[\[?\d+\]?\](?:\(#cite_note[^)]*\))?')  # [[20]](#cite_note-22), [21]
RE_WIKI_CITE_NAMED = re.compile(r'\[\[?[a-z]\]?\](?:\(#cite_note[^)]*\))?')  # [[b]](#cite_note-b-13)
RE_WIKI_LINK = re.compile(r'\[([^\]]+)\]\(/wiki/[^)]+\)')  # [Battle](/wiki/Battle) -> Battle
RE_WIKI_REFLIST = re.compile(r'\n(?:\*\s*)?(?:\[?\d+\]?\s*)?(?:\^.*)?(?:ISBN|ISSN|doi:|JSTOR|S2CID|OCLC).*', re.IGNORECASE)
# Forum noise: lines that are pure metadata (likes, timestamps, user roles)
RE_FORUM_NOISE = re.compile(
    r'^\s*(?:'
    r'\d+\s+Likes?\b'               # "1 Like", "2 Likes"
    r'|Like\s*$'                     # standalone "Like"
    r'|\d+\s*(?:yr|mo|hr|min|sec)s?\s+ago\b'  # "2 yr ago"
    r'|(?:Community\s+Expert|Author|Moderator|Admin)\s*$'  # user roles
    r'|(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4}\d*\s*(?:yr|mo)?\s*$'  # "March 19, 20232 yr"
    r'|\d{1,2}\s+(?:hours?|minutes?|days?|weeks?|months?|years?)\s+ago'  # "8 hours ago"
    r'|said:\s*$'                    # "X said:"
    r'|Quote\s*$'                    # standalone "Quote"
    r'|Share\s*$'                    # standalone "Share"
    r'|Reply\s*$'                    # standalone "Reply"
    r'|Report\s*$'                   # standalone "Report"
    r')',
    re.MULTILINE | re.IGNORECASE,
)

# Domains where curl_cffi c-ares DNS resolver fails (Windows).
# Populated at runtime; domains in this set skip straight to httpx fallback.
_CURL_DNS_FAIL_DOMAINS: set = set()

# External tool availability (checked once at import)
PDFTOTEXT_PATH = shutil.which("pdftotext")

# ---------------------------------------------------------------------------
_SYSTEM: str = platform.system().lower()          # "darwin" | "linux" | "windows"
_IS_WINDOWS: bool = _SYSTEM == "windows"
_IS_MACOS: bool = _SYSTEM == "darwin"

# Errors worth escalating to the browser in "auto" mode. A browser cannot fix a
# clean 404 or a DNS failure — those stay static-only (mirrors the old ladder's
# BRIGHTDATA_RETRY_ERRORS). "Too short" is retry-worthy: it often means the
# static fetch got a JS shell the browser would render.
BROWSER_RETRY_ERRORS: frozenset = frozenset({
    "HTTP 403", "HTTP 429", "HTTP 500", "HTTP 502", "HTTP 503",
    "CAPTCHA/blocked", "Timeout", "Too short",
})

# Per-run browser-escalation counter (search mode only; reset at run start).
_BROWSER_ESCALATIONS: int = 0

# =============================================================================
# SMART CONTENT FILTERS (tunable)
# =============================================================================
# These constants tune the four content filters applied during compression:
#   F1 boilerplate filter, F2 fact-density boost, F3 cross-page dedup, F4 sections.
# Values were tuned against the real-search corpus (tmp/bm25-stats/corpus.jsonl).

# F1 - boilerplate sentence patterns. Each pattern matches a junk sentence
# (cookie/privacy banners, subscribe/share invites, copyright, nav fragments,
# date-only lines). Tuned conservatively: only unambiguous junk phrases.
BOILERPLATE_PATTERNS: Tuple[re.Pattern, ...] = (
    # Cookie / privacy / consent banners
    re.compile(
        r"\b(?:we use cookies?|accept(?: all)? cookies?|manage cookies?|"
        r"cookie (?:policy|settings|notice|preferences?|consent)|"
        r"privacy (?:policy|notice)|gdpr)\b",
        re.IGNORECASE,
    ),
    # Subscribe / signup / newsletter invites
    re.compile(
        r"\b(?:subscribe(?: to| now| for)?|sign\s?up(?: for| to| now)?|newsletter|"
        r"join our (?:newsletter|mailing list|community)|get the latest|"
        r"delivered to your inbox)\b",
        re.IGNORECASE,
    ),
    # Follow / share / social invites
    re.compile(
        r"\b(?:follow us(?: on)?|follow @|share (?:this|on|via|the)|tweet this|"
        r"pin it|like us on|add us on|connect with us)\b",
        re.IGNORECASE,
    ),
    # Copyright / rights lines
    re.compile(r"\b(?:copyright|\u00a9|\(c\)|all rights reserved|rights reserved)\b", re.IGNORECASE),
    # Download / app / CTA junk
    re.compile(
        r"\b(?:download (?:our |the )?(?:app|application)|get (?:the )?app|"
        r"available on (?:the )?(?:app store|google play)|read more|continue reading|"
        r"click here|learn more|view all|see all|watch now|shop now|buy now|"
        r"sign in|log in|register (?:now|free)?|create (?:an )?account)\b",
        re.IGNORECASE,
    ),
    # Navigation / breadcrumb / related-content junk
    re.compile(
        r"\b(?:you are here|back to top|back to (?:the )?(?:home|article|top)|"
        r"next (?:article|page|story)|previous (?:article|page|story)|"
        r"related (?:posts|articles|stories)|recommended for you|you might also like|"
        r"more from|top stories|trending now|most (?:read|popular)|"
        r"popular (?:posts|articles|news)|latest (?:news|articles|posts)|"
        r"table of contents|search results)\b",
        re.IGNORECASE,
    ),
    # Date-only lines
    re.compile(r"^\d{1,2}[./-]\d{1,2}[./-]\d{2,4}$"),
    re.compile(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}$"),
    re.compile(r"^[A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{4},?\s*(?:\d{1,2}:\d{2})?\s*(?:AM|PM)?$", re.IGNORECASE),
    re.compile(r"^\d{1,2}\s+[A-Za-z]{3,9}\.?\s+\d{4}$", re.IGNORECASE),
)

# F2 - fact-density boost: score = blend * (1 + alpha * fact_density)
FACT_DENSITY_ALPHA: float = 0.4

# Common capitalized words that are NOT entity-like (F2 fact token exceptions)
_FACT_CAP_EXCEPTIONS = frozenset(
    "the this that these those it its we you they i my me our us your their his her he she "
    "and but or not of in on at to from with for as by than then so there here when where why "
    "how what which who whom while until during between about after before because although "
    "however therefore also more most some such any all both each few other another one two "
    "first second next last recent new many much same only just very too".split()
)

# F4 - heading-aware section selection: drop body sections whose heading matches
# fewer than this fraction of query content words. 0.0 = drop only zero-overlap.
HEADING_MATCH_THRESHOLD: float = 0.0

# F3 - cross-page near-duplicate sentence dedup (batch path)
CROSS_PAGE_JACCARD_THRESHOLD: float = 0.85   # Jaccard above this = near-dup; 0.0 = exact only
CROSS_PAGE_DEDUP_MIN_LEN: int = 30           # only dedup lines at least this long

# Minimum scoring pool size after F4/F1 filtering. Below this the BM25 idf
# collapses (terms in 1 of N docs score ~0 with small N) and the page would
# fall back to full-length output — so filtering gives up instead.
MIN_SCORING_POOL: int = 15

# =============================================================================
# DIGEST + REPORT FILE (search-path output)
# =============================================================================
# Search mode writes the full filtered text to a report file under
# <REPO_ROOT>/tmp/webresearch/ and prints a compact digest to stdout; the
# model researches from the file (read/grep), not from the inline output.

REPORT_DIR_NAME: str = "webresearch"        # subdirectory of <REPO_ROOT>/tmp/
REPORT_PAGE_CEILING: int = 200_000          # per-page text ceiling in the file (memory sanity; digest is a line-per-page index, see _build_digest)
REPORT_MAX_AGE_DAYS: int = 7                # rotation: delete files older than this
REPORT_MAX_FILES: int = 30                  # rotation: keep only the N newest

# F4 - section-level junk headings. Whole sections under these headings are
# dropped when their heading shares no query tokens (see _filter_sections_by_heading).
# Deliberately conservative: generic CONTENT headings (Abstract, Introduction,
# Results, Methods, Ingredients, Instructions, FAQ, Pros/Cons, ...) are NOT listed —
# real pages use creative headings that legitimately lack query words, and the
# naive token-overlap filter was observed to delete real content on the corpus.
SECTION_DROP_HEADING_PATTERNS: Tuple[re.Pattern, ...] = (
    # Comments / discussion
    re.compile(r"\b(?:comments?|commentary|join the discussion|leave a (?:comment|reply)|responses?|replies?)\b", re.IGNORECASE),
    # Related / recommended / popular / latest / trending
    re.compile(
        r"\b(?:related (?:articles?|posts?|stories|reading)|you might also like|recommended (?:for you|reading)|"
        r"more (?:from|to read)|popular (?:posts?|articles?|now)|trending(?: now)?|most (?:read|popular)|"
        r"latest (?:news|articles?|posts?)|top (?:stories|posts?|articles?)|further reading|external links)\b",
        re.IGNORECASE,
    ),
    # Nav / breadcrumb / TOC
    re.compile(r"\b(?:table of contents|contents|search(?: results)?|site ?map|you are here|back to (?:top|home)|breadcrumbs?|next (?:article|post|page)|previous (?:article|post|page))\b", re.IGNORECASE),
    # Author / about / contact boxes
    re.compile(r"\b(?:about (?:the )?author|author bio|written by|byline|about us|contact us|our team|our writers)\b", re.IGNORECASE),
    # Newsletter / subscribe / social
    re.compile(r"\b(?:newsletter|subscribe|sign\s?up|join our (?:newsletter|mailing list)|share (?:this|the (?:article|post))|follow us|social (?:media|sharing))\b", re.IGNORECASE),
    # Academic nav blocks (PubMed/arXiv/EuropePMC chrome)
    re.compile(
        r"\b(?:references|citations?|bibliography|works cited|footnotes?|endnotes?|linkout|full text sources|"
        r"mesh terms|publication types|medical\s*$|miscellaneous|conflict of interest|grants? and funding|"
        r"submission history|current browse context|other literature sources|author information|"
        r"similar articles|related information|figures|supplementary)\b",
        re.IGNORECASE,
    ),
    # Ads / sponsorship / legal
    re.compile(
        r"\b(?:advertisements?|sponsored|paid (?:promotion|post)|affiliate (?:links?|disclosure)|"
        r"footer|cookie (?:policy|settings|notice)|privacy (?:policy|notice)|terms (?:of (?:use|service))?|"
        r"legal|disclaimer)\b",
        re.IGNORECASE,
    ),
    # Tag / category / archive lists
    re.compile(r"\b(?:categories?|tags?|archives?)\b", re.IGNORECASE),
)

# =============================================================================
# QUALITY FILTERS F5, F7 (tunable)
# =============================================================================
# Two quality filters layered on top of F1-F4, tuned against the same corpus
# (tmp/bm25-stats/corpus.jsonl) and the motivating junk reports
# (tmp/webresearch/20260814-*deepseek-harness*). All fail-soft: on any error the
# filter does nothing and the pipeline keeps the unfiltered page set.

# F5 - cross-domain syndication / content-farm detection (batch path, page level)
FARM_PAGE_JACCARD_THRESHOLD: float = 0.85   # page-level shingle Jaccard = near-dup
FARM_GROUP_MIN_PAGES: int = 3               # group must have this many pages
FARM_GROUP_MIN_DOMAINS: int = 3             # ... across this many distinct registrable domains
FARM_MIN_SIGNALS: int = 2                   # signals required to drop redundant copies
FARM_HARD_SIGNALS: int = 3                  # signals required to drop the whole group (all copies)
FARM_SIG_CHARS: int = 4000                  # chars of normalized body used for the page signature

# F5 byline/author patterns: same author repeating across unrelated domains
FARM_BYLINE_PATTERNS: Tuple[re.Pattern, ...] = (
    re.compile(r"\bAbout\s+([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)?)", re.IGNORECASE),
    re.compile(r"\bBy\s+([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)?)", re.IGNORECASE),
    re.compile(r"\bBy\s+(?:Dr|Prof|Mr|Ms|Mrs|Miss|Sir|Dame|Fr|Rev)\.?\s+([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)?)", re.IGNORECASE),
    re.compile(r"\bwritten\s+by\s+([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)?)", re.IGNORECASE),
    re.compile(r"\bI'?m\s+([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)?)\s*[—\-–]", re.IGNORECASE),
    re.compile(r"\bAuthor\s*:\s*([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)?)", re.IGNORECASE),
)

# F5 farm-network boilerplate phrases (maxgrowthagency-style PBN self-promotion)
FARM_NETWORK_PATTERNS: Tuple[re.Pattern, ...] = (
    re.compile(r"\balso on our network\b", re.IGNORECASE),
    re.compile(r"\bkeep reading\b", re.IGNORECASE),
    re.compile(r"\bmore from our network\b", re.IGNORECASE),
    re.compile(r"\bread more at\b", re.IGNORECASE),
    re.compile(r"\bwant my best\b", re.IGNORECASE),
    re.compile(r"\bget access here\b", re.IGNORECASE),
    re.compile(r"\bget inside\b", re.IGNORECASE),
    re.compile(r"\bget my best\b", re.IGNORECASE),
    re.compile(r"\bjoin my (?:private |free )?(?:group|community|course)\b", re.IGNORECASE),
    re.compile(r"\bbook a free strategy session\b", re.IGNORECASE),
    re.compile(r"\bDA\d+\s*[–—-]\s*\$", re.IGNORECASE),      # DA-tier pricing menus
    re.compile(r"\b(?:free )?AI (?:Course|Community)\b", re.IGNORECASE),
)

# F7 - recency filter (search path)
RECENCY_MAX_AGE_DAYS: int = 3 * 365         # drop pages older than this on recency-sensitive queries
RECENCY_UNDATED_MAX_FRACTION: float = 0.5   # ≥50% undated pages → recency non-applicable (fail-soft)
RECENCY_EVERGREEN_WORDS: Tuple[str, ...] = (
    "history", "origins", "why do", "how to", "explained", "ethics",
    "classic", "famous", "evolution", "what is", "tutorial", "guide",
)
RECENCY_SENSITIVE_WORDS: Tuple[str, ...] = (
    "breaking changes", "new features", "release", "launch", "benchmark",
    "update", "updated", "version", "comparison", "vs", "latest", "news",
    "price", "today", "announce", "2026",
)

# Master toggle: WEB_RESEARCH_QUALITY=0 disables F5/F7 entirely (search mode).
QUALITY_FILTERS_ENABLED: bool = os.environ.get("WEB_RESEARCH_QUALITY", "1") != "0"

# Stub-page rule: a page is a stub when its extracted content is small AND holds
# nothing beyond its own title/heading (forum/social homepages, empty product
# listings). Calibrated on the corpus (tmp/bm25-stats/corpus.jsonl, 389 pages):
# the smallest legit page is 641 chars (body 611), so total < 200 with body < 80
# sits 441 chars below the smallest legit page while catching the observed
# title-only stubs (68-103 chars). The AND keeps real small pages with actual
# body text: a 180-char page with a 150-char body is not a stub.
STUB_MAX_TOTAL_CHARS: int = 200
STUB_MAX_BODY_CHARS: int = 80

# =============================================================================
# REQUIRED DEPENDENCIES (managed by uv)
# =============================================================================

from scrapling.fetchers import AsyncFetcher
from ddgs import DDGS

# Scrapling adds its own StreamHandler at INFO — remove it post-import
_scrapling_logger = logging.getLogger("scrapling")
_scrapling_logger.handlers.clear()
_scrapling_logger.setLevel(logging.CRITICAL)

# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class ResearchConfig:
    """Configuration for research workflow."""
    query: str
    fetch_count: int = 20
    max_content_length: int = 20000
    timeout: int = DEFAULT_TIMEOUT
    quiet: bool = False
    min_content_length: int = 600
    max_concurrent: int = 50  # Match default search count
    search_results: int = 30
    scientific: bool = False
    medical: bool = False
    tech: bool = False
    # Optional time window for the primary search only (d/w/m/y, maps to the
    # engine's timelimit). Never inferred: the caller asks for it explicitly.
    since: str = ""
    escalation_budget: int = 3  # max browser escalations in --url auto mode (search is static-only)


@dataclass
class FetchResult:
    """Single fetch result."""
    url: str
    success: bool
    content: str = ""
    title: str = ""
    error: Optional[str] = None
    source: str = "scrapling"
    # Structured record type: "" for an ordinary page, "feed" for an RSS/Atom
    # entry list, "hn" for a Hacker News thread. Structured records are
    # machine-generated, so the F4/F1 boilerplate filters must not treat their
    # entry headings or comments as page chrome.
    kind: str = ""
    # Candidate image URLs found on the page (og:image / twitter:image first,
    # then <img> src/data-src/data-original/srcset). Printed as one `[images]`
    # line under the page's `=== <url> ===` header in the full report.
    images: List[str] = field(default_factory=list)
    # Publication date for the digest (informational only — the recency filter
    # F7 keeps its own parsing and does NOT consume these fields). ISO day.
    date: str = ""
    # Provenance of `date`: "meta" | "jsonld" | "visible" | "url" | "modified".
    date_source: str = ""


@dataclass
class ResearchStats:
    """Statistics for research run."""
    query: str = ""
    urls_searched: int = 0
    urls_fetched: int = 0
    urls_filtered: int = 0
    content_chars: int = 0
    bonus_sources: dict = None  # {source_name: count}


def _quality_fields(results: Optional[List[FetchResult]]) -> dict:
    """Extract quality-related fields from fetch results."""
    if not results:
        return {"short_pages": 0, "domains": []}
    return {
        "short_pages": sum(1 for r in results if r.success and len(r.content) < 200),
        "domains": list({urllib.parse.urlparse(r.url).netloc for r in results if r.success}),
    }


def log_usage(event: dict) -> None:
    """Append one JSONL event to ~/.web-research/usage.jsonl."""
    try:
        log_dir = os.path.join(os.path.expanduser("~"), ".web-research")
        os.makedirs(log_dir, exist_ok=True)
        event["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        with open(os.path.join(log_dir, "usage.jsonl"), "a") as f:
            f.write(json.dumps(event) + "\n")
    except Exception:
        pass


def print_usage_stats(quality: bool = False) -> None:
    """Print usage statistics from ~/.web-research/usage.jsonl."""
    log_path = os.path.join(os.path.expanduser("~"), ".web-research", "usage.jsonl")
    if not os.path.exists(log_path):
        print("No usage data yet", file=sys.stderr)
        sys.exit(0)

    from collections import Counter
    from datetime import datetime, timedelta

    cutoff = datetime.now().astimezone() - timedelta(days=30)
    events = []
    errors: Counter = Counter()
    modes: Counter = Counter()
    days: Counter = Counter()
    domain_ok: Counter = Counter()    # domain → successful fetches

    with open(log_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            try:
                ts = datetime.fromisoformat(ev["ts"])
                if ts < cutoff:
                    continue
            except (KeyError, ValueError):
                continue
            events.append(ev)
            modes[ev.get("mode", "unknown")] += 1
            day = ev["ts"][:10]
            days[day] += 1
            if not ev.get("ok") and ev.get("error"):
                errors[ev["error"]] += 1
            for d in ev.get("domains", []):
                domain_ok[d] += 1

    if not events:
        print("No usage data in last 30 days")
        sys.exit(0)

    total = len(events)
    ok_count = sum(1 for e in events if e.get("ok"))
    avg_ms = sum(e.get("ms", 0) for e in events) / total
    timeouts = sum(1 for e in events if e.get("timeout"))
    avg_fetched = sum(e.get("urls_fetched", 0) for e in events) / total
    avg_chars = sum(e.get("content_chars", 0) for e in events) / total
    total_short = sum(e.get("short_pages", 0) for e in events)
    total_fetched = sum(e.get("urls_fetched", 0) for e in events)
    print(f"Web Research Usage (last 30 days)")
    print(f"{'='*40}")
    print(f"Total searches:    {total}")
    print(f"Success rate:      {ok_count}/{total} ({100*ok_count/total:.0f}%)")
    print(f"Avg latency:       {avg_ms/1000:.1f}s")
    print(f"Timeouts:          {timeouts}")
    print()
    print(f"Mode breakdown:")
    for mode, count in modes.most_common():
        print(f"  {mode:15s} {count:4d} ({100*count/total:.0f}%)")
    print()
    print(f"Fetch efficiency:")
    print(f"  Avg URLs fetched:  {avg_fetched:.1f}")
    print(f"  Avg content chars: {avg_chars:.0f}")

    if quality:
        print()
        print(f"Output quality:")
        print(f"  Short pages (<200 chars): {total_short}/{total_fetched}" +
              (f" ({100*total_short/total_fetched:.0f}%)" if total_fetched else ""))
        print()
        print(f"Top domains (by fetch count):")
        for domain, count in domain_ok.most_common(10):
            print(f"  {count:4d}x {domain}")

    if errors:
        print()
        print(f"Top errors:")
        for err, count in errors.most_common(5):
            print(f"  {count:4d}x {err[:80]}")

    if days:
        print()
        print(f"Busiest days:")
        for day, count in days.most_common(5):
            print(f"  {day}  {count} searches")


# =============================================================================
# PROGRESS REPORTER (Unified)
# =============================================================================

class ProgressReporter:
    """Progress reporting with timing and per-URL diagnostics."""

    def __init__(self, quiet: bool = False, verbose: bool = False):
        self.quiet = quiet
        self.verbose = verbose
        self._last_line_len = 0
        self._phase_start: float = 0
        self._total_start: float = time.monotonic()
        self._ok_count = 0
        self._failures: List[Tuple[str, str, float]] = []  # (url, error, elapsed)

    def message(self, msg: str) -> None:
        if not self.quiet:
            print(msg, file=sys.stderr)

    def phase_start(self, name: str) -> None:
        self._phase_start = time.monotonic()

    def phase_end(self, name: str) -> None:
        elapsed = time.monotonic() - self._phase_start
        if not self.quiet:
            print(f"  [{name}] {elapsed:.1f}s", file=sys.stderr)

    def url_result(self, url: str, success: bool, elapsed: float, error: str = "") -> None:
        if success:
            self._ok_count += 1
            if self.verbose and not self.quiet:
                domain = urllib.parse.urlparse(url).netloc
                print(f"    OK  {elapsed:4.1f}s  {domain}", file=sys.stderr)
        else:
            self._failures.append((url, error, elapsed))
            if self.verbose and not self.quiet:
                domain = urllib.parse.urlparse(url).netloc
                print(f"    --  {elapsed:4.1f}s  {domain} ({error})", file=sys.stderr)

    def update(self, phase: str, current: int, total: int) -> None:
        if self.quiet or self.verbose:
            return
        elapsed = time.monotonic() - self._phase_start
        line = f"\r    {phase}: {current}/{total} ({self._ok_count} ok, {elapsed:.0f}s)"
        padding = max(0, self._last_line_len - len(line))
        print(f"{line}{' ' * padding}", end="", file=sys.stderr)
        self._last_line_len = len(line)

    def newline(self) -> None:
        if not self.quiet and not self.verbose:
            print(file=sys.stderr)
            self._last_line_len = 0

    def summary(self, fetched_ok: int, total: int, chars: int) -> None:
        if self.quiet:
            return
        total_elapsed = time.monotonic() - self._total_start
        rate = (fetched_ok / total * 100) if total > 0 else 0
        rate_indicator = ""
        if rate < 50:
            rate_indicator = " !! LOW"
        elif rate < 70:
            rate_indicator = " !"
        print(f"  Done: {fetched_ok}/{total} ok ({rate:.0f}%{rate_indicator}) -- {chars:,} chars in {total_elapsed:.1f}s", file=sys.stderr)

        if self._failures:
            by_error: dict[str, int] = {}
            slow: List[Tuple[str, float]] = []
            for url, error, elapsed in self._failures:
                by_error[error] = by_error.get(error, 0) + 1
                if elapsed >= 5.0:
                    slow.append((url, elapsed))
            parts = [f"{count} {err}" for err, count in sorted(by_error.items(), key=lambda x: -x[1])]
            print(f"  Skipped: {', '.join(parts)}", file=sys.stderr)
            if slow:
                print(f"  Slow (>5s):", file=sys.stderr)
                for url, elapsed in sorted(slow, key=lambda x: -x[1])[:5]:
                    domain = urllib.parse.urlparse(url).netloc
                    print(f"    {elapsed:4.1f}s  {domain}", file=sys.stderr)


# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

def clean_text(text: str) -> str:
    """Clean HTML entities and normalize whitespace."""
    if not text:
        return ""
    text = unescape(text)
    text = RE_ALL_TAGS.sub("", text)
    text = RE_WHITESPACE.sub(" ", text)
    return text.strip()


def is_blocked_url(url: str) -> bool:
    """True when a URL is dropped at intake.

    Two independent reasons: a path/extension skip pattern, or a domain routed
    to ROUTE_BLOCK. Domains routed to wayback or scholarly are NOT dropped —
    the fetch dispatcher takes their alternate access path instead.
    """
    if _SKIP_URL_PATTERN.search(url):
        return True
    return route_for(url) == ROUTE_BLOCK


def is_valid_url(url: str) -> bool:
    """Validate URL format."""
    try:
        result = urllib.parse.urlparse(url)
        return all([result.scheme in ('http', 'https'), result.netloc])
    except Exception:
        return False


def is_blocked_content(content: str) -> bool:
    """Check if content is a CAPTCHA/blocked page (returns True if blocked)."""
    if not content or len(content) < 50:
        return False
    content_lower = content[:2000].lower()  # Only check first 2KB for speed
    return any(marker in content_lower for marker in BLOCKED_CONTENT_MARKERS)


def _strip_wiki_tables(html: str) -> str:
    """Remove Wikipedia infobox/navbox tables (may contain nested tables)."""
    for css_class in ("infobox", "navbox"):
        pattern = re.compile(
            rf'<table\b[^>]*class="[^"]*{css_class}[^"]*"[^>]*>',
            re.IGNORECASE,
        )
        while True:
            m = pattern.search(html)
            if not m:
                break
            # Find matching </table> accounting for nesting
            depth = 1
            pos = m.end()
            while depth > 0 and pos < len(html):
                next_open = html.find("<table", pos)
                next_close = html.find("</table>", pos)
                if next_close == -1:
                    break
                if next_open != -1 and next_open < next_close:
                    depth += 1
                    pos = next_open + 6
                else:
                    depth -= 1
                    pos = next_close + 8
            html = html[:m.start()] + html[pos:]
    return html

def _extract_with_trafilatura(html: str) -> str:
    """Extract article text using trafilatura (content-area detection + boilerplate removal)."""
    # Strip Wikipedia infobox/navbox tables before extraction (they render as messy pipe-tables)
    html = _strip_wiki_tables(html)
    import trafilatura
    text = trafilatura.extract(
        html,
        include_links=True,
        include_formatting=True,
        include_tables=True,
        include_comments=False,
        output_format="txt",
    )
    return text or ""


def _extract_with_regex(html: str) -> str:
    """Fallback: extract text from HTML using regex (for when trafilatura returns nothing)."""
    # Strip boilerplate tags
    html = RE_STRIP_TAGS.sub("", html)
    html = RE_COMMENTS.sub("", html)

    html = RE_BR.sub("\n", html)
    html = RE_BLOCK_END.sub("\n\n", html)
    html = RE_LI.sub("\u2022 ", html)

    text = RE_ALL_TAGS.sub(" ", html)
    text = unescape(text)
    text = RE_SPACES.sub(" ", text)
    text = RE_LEADING_SPACE.sub("\n", text)
    return RE_MULTI_NEWLINE.sub("\n\n", text)


def extract_text(html: str) -> str:
    """Extract readable text from HTML. Trafilatura primary, regex fallback."""
    text = _extract_with_trafilatura(html)

    if not text or len(text) < 100:
        text = _extract_with_regex(html)

    # Extract title for prepending
    title_match = RE_TITLE.search(html)
    if title_match:
        raw_title = unescape(title_match.group(1).strip())
        title = re.sub(r'\s*[\|\-\u2013\u2014]\s*[^|\-\u2013\u2014]{3,50}$', '', raw_title)
    else:
        title = ""

    text = text.strip()
    # Strip forum noise lines (likes, timestamps, user roles)
    text = RE_FORUM_NOISE.sub("", text)
    # Clean Wikipedia artifacts: citation refs, internal links, reference lists
    text = RE_WIKI_CITE.sub("", text)
    text = RE_WIKI_CITE_NAMED.sub("", text)
    text = RE_WIKI_LINK.sub(r"\1", text)
    text = RE_WIKI_REFLIST.sub("", text)
    text = RE_MULTI_NEWLINE.sub("\n\n", text)
    # Prepend title if not already present
    if title and not text.startswith(f"# {title}"):
        text = f"# {title}\n\n{text}"
    return text


def extract_title_from_content(content: str) -> str:
    """Extract title from markdown-formatted content."""
    if content.startswith("# "):
        newline = content.find("\n")
        if newline > 0:
            return content[2:newline]
    return ""


MAX_CONTENT_BYTES = 2_000_000  # 2MB max content size

def _jsonld_entries(data) -> List[dict]:
    """Flatten a parsed JSON-LD block into a list of entity dicts.

    Handles @graph arrays and nested @type arrays (the tool previously only
    read data[0] of a list, missing Article data nested in @graph).
    """
    if isinstance(data, list):
        out: List[dict] = []
        for item in data:
            out.extend(_jsonld_entries(item))
        return out
    if not isinstance(data, dict):
        return []
    out = [data]
    graph = data.get("@graph")
    if isinstance(graph, list):
        for item in graph:
            out.extend(_jsonld_entries(item))
    return out


def extract_jsonld_metadata(html: str) -> str:
    """Extract only high-value metadata from JSON-LD that page text doesn't provide:
    datePublished (recency signal), dateModified (staleness signal) and FAQPage
    Q&A pairs (hard to parse from DOM)."""
    blocks = RE_JSON_LD.findall(html)
    if not blocks:
        return ""

    for raw in blocks:
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue

        for entry in _jsonld_entries(data):
            if not isinstance(entry, dict):
                continue

            ld_type = entry.get("@type", "")
            if isinstance(ld_type, list):
                ld_type = ld_type[0] if ld_type else ""

            parts = []

            # FAQPage: Q&A pairs are genuinely hard to extract from rendered HTML
            if ld_type == "FAQPage":
                entities = entry.get("mainEntity", [])
                # Flatten nested lists (e.g. AWS uses [[{...}, {...}]])
                if entities and isinstance(entities[0], list):
                    entities = [e for sub in entities for e in sub]
                for entity in entities[:5]:
                    if not isinstance(entity, dict):
                        continue
                    q = entity.get("name", "")
                    a_obj = entity.get("acceptedAnswer", {})
                    a = a_obj.get("text", "") if isinstance(a_obj, dict) else ""
                    if q and a:
                        parts.append(f"Q: {q}")
                        parts.append(f"A: {a[:300]}")

            # datePublished: recency signal not always visible in page text
            date_pub = entry.get("datePublished", "")
            if date_pub:
                if "T" in str(date_pub):
                    date_pub = str(date_pub).split("T")[0]
                parts.append(f"published: {date_pub}")

            # dateModified: staleness signal not always visible in page text
            date_mod = entry.get("dateModified", "")
            if date_mod:
                if "T" in str(date_mod):
                    date_mod = str(date_mod).split("T")[0]
                parts.append(f"updated: {date_mod}")

            if parts:
                return "[meta] " + " | ".join(parts) + "\n\n" if len(parts) == 1 else "[meta]\n" + "\n".join(parts) + "\n[/meta]\n\n"

    return ""


# =============================================================================
# URL FETCHER (Scrapling-based)
# =============================================================================

# =============================================================================
# SMART CONTENT FILTERS
# =============================================================================

def _is_boilerplate(sentence: str) -> bool:
    """Model-free deterministic boilerplate detection (F1).

    True for cookie/privacy banners, subscribe/share invites, copyright lines,
    nav/breadcrumb fragments, date-only lines, and pure-punctuation/emoji/separator
    lines. Applied to sentences BEFORE BM25 scoring so junk cannot steal budget
    via the centrality weight (boilerplate repeats the most, so it scores high).
    """
    s = sentence.strip()
    if not s:
        return True
    # Pure punctuation / emoji / separator lines (no letters or digits in any script)
    if not re.search(r"[^\W_]", s, re.UNICODE):
        return True
    if len(s) <= 2:
        return True
    for pat in BOILERPLATE_PATTERNS:
        if pat.search(s):
            return True
    return False


def _fact_density(sentence: str) -> float:
    """Fraction of fact-like tokens: numbers, percentages, years, currency, entity caps (F2)."""
    tokens = re.findall(r"[A-Za-z0-9$€£¥%]+(?:\.\d+)?|[\u3400-\u9FFF]+", sentence)
    if not tokens:
        return 0.0
    fact = 0
    for t in tokens:
        if re.search(r"\d|[$€£¥%]", t):
            fact += 1
        elif t[0].isupper() and len(t) >= 3 and t.lower() not in _FACT_CAP_EXCEPTIONS:
            fact += 1
    return fact / len(tokens)


def _heading_query_tokens(query: str) -> Set[str]:
    """Content words of the query used for heading matching (F4)."""
    return {
        w for w in re.findall(r"[a-zA-Z0-9\u00C0-\u024F]+", query.lower())
        if len(w) >= 3 and w not in _STOP_WORDS
    }


def _heading_score(heading: str, q_tokens: Set[str]) -> float:
    """Fraction of query content words appearing in a heading."""
    if not q_tokens:
        return 1.0
    words = set(re.findall(r"[a-zA-Z0-9\u00C0-\u024F]+", heading.lower()))
    return sum(1 for t in q_tokens if t in words) / len(q_tokens)


def _is_junk_heading(heading: str) -> bool:
    """True if the heading is a junk/nav section label (comments, related, references, ...)."""
    return any(pat.search(heading) for pat in SECTION_DROP_HEADING_PATTERNS)


def _filter_sections_by_heading(blocks: List[str], query: str, threshold: float) -> Tuple[List[str], int]:
    """Drop body sections under junk headings that share no query tokens with the query (F4).

    Runs before sentence-level selection to remove big irrelevant chunks early.
    Only sections whose heading matches a junk/nav pattern are eligible for
    dropping — generic content headings (Abstract, Introduction, Results, recipe
    steps, ...) are always kept even when they lack query words. A dropped heading
    drops all blocks until the next heading. Returns (kept_blocks, dropped_sections).
    """
    q_tokens = _heading_query_tokens(query)
    if not q_tokens:
        return blocks, 0
    kept: List[str] = []
    section_active = True  # blocks before any heading stay
    dropped = 0
    for block in blocks:
        stripped = block.strip()
        if stripped.startswith("# ") or stripped.startswith("## ") or stripped.startswith("### "):
            if _is_junk_heading(stripped):
                section_active = _heading_score(stripped, q_tokens) > threshold
            else:
                section_active = True
            if not section_active:
                dropped += 1
                continue
            kept.append(block)
            continue
        if section_active:
            kept.append(block)
    return kept, dropped


def _filter_junk_sections(blocks: List[str]) -> Tuple[List[str], int]:
    """Drop body sections under junk headings (F4, no-query variant).

    Same SECTION_DROP_HEADING_PATTERNS as the search path, but without a query
    the overlap gate is vacuous (there is no query to overlap) — junk sections
    are dropped outright. Generic content headings are never dropped, so real
    content survives even when it has no query words. Blocks before the first
    heading stay. Returns (kept_blocks, dropped_sections).
    """
    kept: List[str] = []
    section_active = True  # blocks before any heading stay
    dropped = 0
    for block in blocks:
        stripped = block.strip()
        if stripped.startswith("# ") or stripped.startswith("## ") or stripped.startswith("### "):
            section_active = not _is_junk_heading(stripped)
            if not section_active:
                dropped += 1
            else:
                kept.append(block)
            continue
        if section_active:
            kept.append(block)
    return kept, dropped


def _prepare_sentences(
    content: str,
    query: str,
    *,
    use_sections: bool = True,
    use_boilerplate: bool = True,
    use_fact: bool = True,
    heading_threshold: float = HEADING_MATCH_THRESHOLD,
    fact_alpha: float = FACT_DENSITY_ALPHA,
) -> dict:
    """Parse content into header + scored body sentences (single source of truth).

    Applies the smart filters before BM25 scoring:
      F4 - drop body sections whose heading shares no query tokens
      F1 - drop boilerplate sentences
      F2 - fact-density boost on the 70/30 BM25+centrality blend
    Returns dict with header_parts, sentences, scores, boilerplate_dropped, sections_dropped.
    """
    from rank_bm25 import BM25Okapi

    # Split into paragraphs first to identify headers
    blocks = content.split("\n\n")
    header_parts: List[str] = []
    body_text_parts: List[str] = []
    for block in blocks:
        stripped = block.strip()
        if not stripped:
            continue
        if not body_text_parts and (stripped.startswith("# ") or stripped.startswith("[meta")):
            header_parts.append(stripped)
        else:
            body_text_parts.append(stripped)

    result = {
        "header_parts": header_parts,
        "sentences": [],
        "scores": [],
        "boilerplate_dropped": 0,
        "sections_dropped": 0,
        "pool_fallback": False,
    }
    if not body_text_parts:
        return result

    all_sentences = _split_sentences("\n\n".join(body_text_parts))
    if not all_sentences:
        return result

    candidate = all_sentences
    if use_sections:
        section_blocks, result["sections_dropped"] = _filter_sections_by_heading(
            body_text_parts, query, heading_threshold
        )
        if section_blocks:
            candidate = _split_sentences("\n\n".join(section_blocks))
        else:
            candidate = []
    if use_boilerplate and candidate:
        kept: List[str] = []
        for s in candidate:
            if _is_boilerplate(s):
                result["boilerplate_dropped"] += 1
            else:
                kept.append(s)
        candidate = kept

    # Fail-soft: if filtering leaves too small a pool, BM25 idf collapses and the
    # page would fall back to full-length output — use the unfiltered pool instead.
    if len(candidate) < MIN_SCORING_POOL:
        result["boilerplate_dropped"] = 0
        result["sections_dropped"] = 0
        result["pool_fallback"] = True
        candidate = all_sentences
    sentences = candidate
    if not sentences:
        return result

    # BM25 rank sentences by query relevance
    tokenized = [s.lower().split() for s in sentences]
    bm25 = BM25Okapi(tokenized)
    bm25_scores = bm25.get_scores(query.lower().split())

    # Centrality scoring: sentences similar to many others are "hub" sentences
    # (captures important context that BM25 misses when it lacks query terms)
    # Cap at 200 sentences to keep O(n²) manageable (~40K comparisons max)
    word_sets = [set(t) for t in tokenized]
    n = len(sentences)
    centrality = [0.0] * n
    n_cap = min(n, 200)
    if n_cap > 1:
        for i in range(n_cap):
            if not word_sets[i]:
                continue
            total_sim = 0.0
            for j in range(n_cap):
                if i == j or not word_sets[j]:
                    continue
                # Jaccard similarity
                intersection = len(word_sets[i] & word_sets[j])
                union = len(word_sets[i] | word_sets[j])
                if union:
                    total_sim += intersection / union
            centrality[i] = total_sim / (n_cap - 1)

    # Blend: 70% BM25 relevance + 30% centrality importance
    max_bm25 = max(bm25_scores) if max(bm25_scores) > 0 else 1.0
    max_cent = max(centrality) if max(centrality) > 0 else 1.0
    scores = [
        0.7 * (b / max_bm25) + 0.3 * (c / max_cent)
        for b, c in zip(bm25_scores, centrality)
    ]

    # F2: fact-density boost — factual sentences carry more value per token
    if use_fact:
        scores = [
            s * (1.0 + fact_alpha * _fact_density(sent))
            for s, sent in zip(scores, sentences)
        ]

    result["sentences"] = sentences
    result["scores"] = scores
    return result


def _select_sentences(scores: List[float], sentences: List[str], budget: int) -> Tuple[List[str], int]:
    """Select top-scoring sentences within budget (existing selection rule).

    Returns (selected_texts_in_original_order, chars_used).
    """
    if not sentences:
        return [], 0
    ranked = sorted(zip(scores, range(len(sentences)), sentences), reverse=True)
    selected: List[Tuple[int, str]] = []  # (original_index, text)
    chars = 0
    # Minimum score threshold: at least 10% of max blended score
    min_score = 0.1
    for score, idx, sent in ranked:
        if score < min_score or chars >= budget:
            break
        selected.append((idx, sent))
        chars += len(sent) + 1

    selected.sort(key=lambda x: x[0])
    return [s for _, s in selected], chars


def _split_sentences(text: str) -> List[str]:
    """Split text into sentences. Two-level: split on newlines, then on sentence boundaries."""
    sentences: List[str] = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        # Short lines (headings, list items) stay as-is
        if len(line) < 150:
            sentences.append(line)
        else:
            # Split long lines on sentence boundaries
            parts = RE_SENT_SPLIT.split(line)
            sentences.extend(p.strip() for p in parts if p.strip())
    return sentences


def _compress_with_bm25(
    content: str,
    query: str,
    max_length: int,
    *,
    use_sections: bool = True,
    use_boilerplate: bool = True,
    use_fact: bool = True,
    heading_threshold: float = HEADING_MATCH_THRESHOLD,
    fact_alpha: float = FACT_DENSITY_ALPHA,
) -> str:
    """Query-focused extraction: keep sentences most relevant to query via BM25.

    Smart filters (all default on, fail-soft to unfiltered behavior):
      F4 - drop body sections whose heading matches nothing in the query
      F1 - drop boilerplate sentences before scoring (junk can't steal budget)
      F2 - boost sentences with numbers/currency/entity-like facts
    """
    prep = _prepare_sentences(
        content, query,
        use_sections=use_sections, use_boilerplate=use_boilerplate, use_fact=use_fact,
        heading_threshold=heading_threshold, fact_alpha=fact_alpha,
    )
    header_parts = prep["header_parts"]
    sentences = prep["sentences"]
    scores = prep["scores"]

    if not sentences:
        return content[:max_length]

    budget = max_length - sum(len(h) + 2 for h in header_parts)
    selected_sents, chars = _select_sentences(scores, sentences, budget)

    if not selected_sents:
        return content[:max_length]

    parts = header_parts + selected_sents
    result = "\n".join(parts)
    if chars >= budget:
        result += "\n[Compressed...]"
    return result


def _filter_direct_fetch(content: str) -> str:
    """Order-preserving junk removal for the direct URL-fetch path (no query).

    Applies F4 (junk-section drop) then F1 (boilerplate sentence drop) with the
    SAME pattern sets as the search path; the caller applies the length cap.
    No re-ranking — content stays in original page order (the user asked for the
    page, not a query digest). Fail-soft: never empties the page; if filtering
    leaves fewer than MIN_SCORING_POOL sentences the page is returned unchanged
    (a page that is mostly junk can't be helped, and dropping it risks destroying
    content). On any error falls back to the raw content.
    """
    try:
        blocks = content.split("\n\n")
        header_parts: List[str] = []
        body_text_parts: List[str] = []
        for block in blocks:
            stripped = block.strip()
            if not stripped:
                continue
            if not body_text_parts and (stripped.startswith("# ") or stripped.startswith("[meta")):
                header_parts.append(stripped)
            else:
                body_text_parts.append(stripped)
        if not body_text_parts:
            return content

        # F4: drop body sections under junk headings (no-query variant)
        section_blocks, _ = _filter_junk_sections(body_text_parts)
        if not section_blocks:
            return content

        # F1: drop boilerplate sentences, original order preserved
        sentences = _split_sentences("\n\n".join(section_blocks))
        kept = [s for s in sentences if not _is_boilerplate(s)]
        if len(kept) < MIN_SCORING_POOL:
            return content

        result = "\n".join(header_parts + kept).strip()
        return result if result else content
    except Exception:
        return content


def _filter_page_text(content: str) -> str:
    """Filter-only path for the report file: F4 + F1, original order, no BM25.

    The report file carries a page's full filtered text (no 10k budget cut, no
    re-ranking, no fact-density boost) so grep can find terms the query never
    mentioned. Reuses the direct-fetch F4/F1 filtering; adds a fail-soft guard
    for the report contract: if filtering leaves the page nearly empty
    (< ~500 chars), the original text is returned unchanged — never an empty page.
    """
    filtered = _filter_direct_fetch(content)
    if filtered is not content and filtered and len(filtered) < 500:
        return content
    return filtered


def _create_fetch_result(
    url: str,
    content: Optional[str],
    min_length: int,
    max_length: int,
    query: str = "",
    apply_filters: bool = True,
) -> FetchResult:
    """Create FetchResult from content, applying length checks and truncation."""
    if content and len(content) >= min_length:
        if query:
            if len(content) > max_length:
                content = _compress_with_bm25(content, query, max_length)
        else:
            # Direct fetch (--url): apply_filters=True runs F1+F4 first so the
            # cap holds cleaner content (filters only drop junk, fail-soft to
            # raw); apply_filters=False is raw mode (--url default): the page
            # text is saved exactly as extracted, no F4/F1 cleanup.
            if apply_filters:
                content = _filter_direct_fetch(content)
            if len(content) > max_length:
                content = content[:max_length] + "\n\n[Truncated...]"
        return FetchResult(url=url, success=True, content=content, title=extract_title_from_content(content))
    return FetchResult(url=url, success=False, error="Too short")


def _extract_with_scrapling_fallback(page, min_length: int) -> str:
    """Try Scrapling's get_all_text() when w3m/regex extraction is too short.

    This handles JS-heavy pages where our regex extraction strips too much
    but Scrapling's DOM parser preserves the text content.
    """
    try:
        text = page.get_all_text(separator='\n', strip=True)
        if text and len(text) >= min_length:
            # Add title if available
            title = ""
            title_el = page.css('title')
            if title_el:
                raw_title = title_el[0].text.strip() if hasattr(title_el[0], 'text') else ""
                if raw_title:
                    title = re.sub(r'\s*[\|\-\u2013\u2014]\s*[^|\-\u2013\u2014]{3,50}$', '', raw_title)
            if title:
                return f"# {title}\n\n{text}"
            return text
    except Exception:
        pass
    return ""


def _is_pdf(raw: str, url: str) -> bool:
    """Detect PDF content by magic bytes (not URL — .pdf URLs may return HTML 404)."""
    return "%PDF" in raw[:50]


PDF_MIN_GOOD_CHARS = 600  # below this, pdftotext output is treated as unusable
PDF_PYPDF_MAX_PAGES = 400  # safety bound for the pure-Python fallback
PDF_MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024  # papers routinely exceed MAX_CONTENT_BYTES


def _extract_pdf_pdftotext(raw_bytes: bytes) -> str:
    """pdftotext (poppler). Writes to a temp file since it needs seekable input."""
    if not PDFTOTEXT_PATH:
        return ""
    import tempfile
    tmp_path = None
    try:
        # Use delete=False and manual cleanup: on Windows, NamedTemporaryFile(delete=True)
        # keeps the file locked, preventing pdftotext from reading it
        f = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        tmp_path = f.name
        f.write(raw_bytes)
        f.close()
        result = subprocess.run(
            [PDFTOTEXT_PATH, "-layout", tmp_path, "-"],
            capture_output=True,
            timeout=30,
        )
        if result.returncode == 0:
            return result.stdout.decode("utf-8", errors="replace").strip()
    except (subprocess.TimeoutExpired, OSError):
        pass
    finally:
        if tmp_path:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
    return ""


def _extract_pdf_pypdf(raw_bytes: bytes) -> str:
    """Pure-Python PDF text extraction (pypdf).

    Fallback for machines without poppler, and for PDFs where pdftotext
    returned nothing. Handles text PDFs only — scanned pages need OCR, which
    this tool does not do; they yield an empty string and are reported as such.
    """
    try:
        from io import BytesIO

        from pypdf import PdfReader
    except Exception:
        return ""
    try:
        reader = PdfReader(BytesIO(raw_bytes))
        if getattr(reader, "is_encrypted", False):
            try:
                reader.decrypt("")
            except Exception:
                return ""
        pages: List[str] = []
        for page in reader.pages[:PDF_PYPDF_MAX_PAGES]:
            try:
                text = page.extract_text() or ""
            except Exception:
                continue
            if text.strip():
                pages.append(text.strip())
        return "\n\n".join(pages).strip()
    except Exception:
        return ""


def _extract_pdf(raw_bytes: bytes) -> str:
    """Extract text from a PDF: pdftotext first, pypdf when that is absent or short.

    Keeps the better of the two results, so a machine with poppler behaves
    exactly as before and a machine without it no longer silently loses the page.
    """
    primary = _extract_pdf_pdftotext(raw_bytes)
    if primary and len(primary) >= PDF_MIN_GOOD_CHARS:
        return primary
    fallback = _extract_pdf_pypdf(raw_bytes)
    return fallback if len(fallback) > len(primary) else primary


def _extract_content(raw_html: str) -> Tuple[str, str]:
    """CPU-bound: extract text + JSON-LD from HTML. Runs in process pool."""
    try:
        structured = extract_jsonld_metadata(raw_html)
    except Exception:
        structured = ""
    content = extract_text(raw_html)
    return content, structured


# Image candidates on a page: pure regex over the HTML (no HTML parser, no new
# dependency). The Chrome/escalation path yields extracted text only, not HTML,
# so its FetchResult carries no images (see module notes).
_IMG_META_RE = re.compile(r"<meta\b[^>]*>", re.I)
_IMG_TAG_RE = re.compile(r"<img\b[^>]*>", re.I)
_IMG_ATTR_RE = re.compile(
    r"""([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+))"""
)
# URL path/filename fragments that mark page chrome rather than content.
_IMG_NOISE_TOKENS = (
    "logo", "icon", "sprite", "pixel", "tracker", "spacer", "blank", "favicon",
)
_IMG_MAX_CANDIDATES = 12


def _img_tag_attrs(tag: str) -> Dict[str, str]:
    """Attribute dict for one raw HTML tag (lowercased keys)."""
    attrs: Dict[str, str] = {}
    for m in _IMG_ATTR_RE.finditer(tag):
        val = m.group(2)
        if val is None:
            val = m.group(3)
        if val is None:
            val = m.group(4) or ""
        attrs[m.group(1).lower()] = val
    return attrs


def _img_size_drop(attrs: Dict[str, str]) -> bool:
    """True when explicit width/height attributes mark a sub-100px image."""
    try:
        w = int(float(attrs.get("width") or 0))
        h = int(float(attrs.get("height") or 0))
    except (TypeError, ValueError):
        return False
    return 0 < w < 100 and 0 < h < 100


def _img_candidate_values(attrs: Dict[str, str]) -> List[str]:
    """src / data-src / data-original (lazy) + the FIRST srcset URL."""
    vals: List[str] = []
    for key in ("src", "data-src", "data-original"):
        v = (attrs.get(key) or "").strip()
        if v:
            vals.append(v)
    srcset = attrs.get("srcset") or ""
    if srcset:
        first = srcset.split(",")[0].strip().split()
        if first:
            vals.append(first[0])
    return vals


def _is_noise_image_url(path: str) -> bool:
    low = path.lower()
    if low.endswith(".svg"):
        return True
    return any(tok in low for tok in _IMG_NOISE_TOKENS)


def _dom_img_attrs(page) -> Optional[List[Dict[str, str]]]:
    """Best-effort <img> attribute dicts from the scrapling DOM, or None.

    Returns None when the page object exposes no CSS selector (the httpx
    fallback path) or the DOM API does not behave — the caller then falls back
    to the raw-HTML scan. Kept cheap: one selector pass, no second fetch.
    """
    try:
        css = getattr(page, "css", None)
        if css is None:
            return None
        els = css("img")
        out: List[Dict[str, str]] = []
        for el in els:
            attrib = getattr(el, "attrib", None)
            if attrib is None:
                return None
            out.append({str(k).lower(): v for k, v in dict(attrib).items()})
        return out
    except Exception:
        return None


def _extract_image_urls(
    raw_html: str,
    page_url: str,
    dom_imgs: Optional[List[Dict[str, str]]] = None,
) -> List[str]:
    """Candidate image URLs on a page (pure; runs in the extract pool).

    Order: og:image, then twitter:image, then every <img>'s src/data-src/
    data-original and the FIRST srcset URL (covers lazy-loaded images). Every
    URL is resolved against the page URL; `data:`/`blob:` URLs and obvious
    chrome (logo/icon/sprite/pixel/tracker/spacer/blank/favicon, any `.svg`)
    are dropped; results are deduped preserving order and capped at 12.
    `dom_imgs`, when given and non-empty, is the rendered DOM's <img> attribute
    dicts and takes precedence over the raw-HTML scan.
    """
    if not raw_html and not dom_imgs:
        return []

    metas: List[str] = []
    if raw_html:
        og: List[str] = []
        tw: List[str] = []
        for m in _IMG_META_RE.finditer(raw_html):
            a = _img_tag_attrs(m.group(0))
            key = (a.get("property") or a.get("name") or "").strip().lower()
            content = (a.get("content") or "").strip()
            if not content:
                continue
            if key in ("og:image", "og:image:url", "og:image:secure_url"):
                og.append(content)
            elif key in ("twitter:image", "twitter:image:src"):
                tw.append(content)
        metas = og + tw

    img_vals: List[str] = []
    if dom_imgs:
        for a in dom_imgs:
            if _img_size_drop(a):
                continue
            img_vals.extend(_img_candidate_values(a))
    elif raw_html:
        for m in _IMG_TAG_RE.finditer(raw_html):
            a = _img_tag_attrs(m.group(0))
            if _img_size_drop(a):
                continue
            img_vals.extend(_img_candidate_values(a))

    out: List[str] = []
    seen: Set[str] = set()
    for raw_val in metas + img_vals:
        v = (raw_val or "").strip()
        if not v:
            continue
        low = v.lower()
        if low.startswith("data:") or low.startswith("blob:"):
            continue
        try:
            absu = urllib.parse.urljoin(page_url, v)
        except ValueError:
            continue
        parts = urllib.parse.urlsplit(absu)
        if parts.scheme not in ("http", "https"):
            continue
        if _is_noise_image_url(parts.path):
            continue
        if absu in seen:
            continue
        seen.add(absu)
        out.append(absu)
        if len(out) >= _IMG_MAX_CANDIDATES:
            break
    return out


def _images_line(images: Optional[List[str]]) -> str:
    """The `[images] …` report line for one page, or "" when there is none."""
    if not images:
        return ""
    return "[images] " + " | ".join(images) + "\n"


# Shared process pool for CPU-bound text extraction (avoids blocking event loop)
_extract_pool: Optional[ProcessPoolExecutor] = None


def _get_extract_pool() -> ProcessPoolExecutor:
    global _extract_pool
    if _extract_pool is None:
        _extract_pool = ProcessPoolExecutor(max_workers=4)
    return _extract_pool


def _shutdown_extract_pool() -> None:
    """Shut down process pool to prevent hang on exit."""
    global _extract_pool
    if _extract_pool is not None:
        _extract_pool.shutdown(wait=False, cancel_futures=True)
        _extract_pool = None


import atexit
atexit.register(_shutdown_extract_pool)


RE_WIKIPEDIA_URL = re.compile(r'https?://(\w+)\.wikipedia\.org/wiki/(.+?)(?:#.*)?$')
RE_GITHUB_REPO_URL = re.compile(r'https?://github\.com/([^/]+)/([^/]+?)(?:/?|/tree/[^/]+/?)?$')
RE_ARXIV_URL = re.compile(r'https?://arxiv\.org/(?:abs|pdf)/(\d+\.\d+)')
RE_TWITTER_URL = re.compile(r'https?://(?:twitter\.com|x\.com)/([^/]+)/status/(\d+)')
RE_SEMANTIC_SCHOLAR_URL = re.compile(r'https?://(?:www\.)?semanticscholar\.org/paper/(?:.+/)?([a-f0-9]{40})')

def _fetch_wikipedia_api(lang: str, title: str, max_length: int) -> Optional[Tuple[str, Optional[str]]]:
    """Fetch clean text + lead image from the Wikipedia API (no scraping needed).

    Returns (text, lead_image_url) or None. The lead image comes from the
    `pageimages` prop (pithumbsize=800) and is placed first in the page's
    `[images]` line.
    """
    import urllib.request
    api_url = (
        f"https://{lang}.wikipedia.org/w/api.php?action=query&titles="
        f"{urllib.parse.quote(title)}&prop=extracts|pageimages"
        f"&explaintext=true&pithumbsize=800&format=json"
    )
    try:
        req = urllib.request.Request(api_url, headers={"User-Agent": "web-research-tool/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        pages = data.get("query", {}).get("pages", {})
        for page_data in pages.values():
            text = page_data.get("extract", "")
            if text:
                page_title = page_data.get("title", title)
                thumb = page_data.get("thumbnail")
                image = thumb.get("source") if isinstance(thumb, dict) else None
                return f"# {page_title}\n\n{text[:max_length]}", image
    except Exception:
        pass
    return None

def _fetch_github_readme(owner: str, repo: str, max_length: int) -> Optional[str]:
    """Fetch README from GitHub API as rendered HTML, then extract text."""
    import urllib.request
    api_url = f"https://api.github.com/repos/{owner}/{repo}/readme"
    try:
        req = urllib.request.Request(api_url, headers={
            "Accept": "application/vnd.github.html+json",
            "User-Agent": "web-research-tool/1.0",
        })
        with urllib.request.urlopen(req, timeout=5) as resp:
            html = resp.read().decode("utf-8", errors="replace")
        if not html:
            return None
        # Use our existing text extraction on the rendered HTML
        text = _extract_with_regex(html)
        text = RE_MULTI_NEWLINE.sub("\n\n", text).strip()
        if text:
            return f"# {owner}/{repo}\n\n{text[:max_length]}"
    except Exception:
        pass
    return None

ARXIV_PDF_MAX_CHARS = 60000


def _fetch_arxiv_pdf_fulltext(paper_id: str) -> str:
    """Full text of an arXiv paper via its PDF; "" when unavailable.

    Runs in the same worker thread as the Atom API call. Fail-soft by design:
    a missing or unparseable PDF never costs the abstract.
    """
    pdf_bytes = _http_get_bytes(
        f"https://arxiv.org/pdf/{paper_id}", timeout=12, max_bytes=PDF_MAX_DOWNLOAD_BYTES
    )
    if not pdf_bytes or b"%PDF" not in pdf_bytes[:64]:
        return ""
    text = _extract_pdf(pdf_bytes)
    if len(text) < 400:
        return ""
    return text[:ARXIV_PDF_MAX_CHARS]


def _fetch_arxiv_api(paper_id: str, max_length: int) -> Optional[str]:
    """Fetch ArXiv paper metadata + abstract via Atom API."""
    import urllib.request
    import xml.etree.ElementTree as ET
    api_url = f"http://export.arxiv.org/api/query?id_list={paper_id}"
    try:
        req = urllib.request.Request(api_url, headers={"User-Agent": "web-research-tool/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            xml_data = resp.read().decode("utf-8", errors="replace")
        root = ET.fromstring(xml_data)
        ns = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
        entry = root.find("atom:entry", ns)
        if entry is None:
            return None
        title = (entry.findtext("atom:title", "", ns) or "").strip().replace("\n", " ")
        abstract = (entry.findtext("atom:summary", "", ns) or "").strip()
        authors = [a.findtext("atom:name", "", ns) for a in entry.findall("atom:author", ns)]
        published = (entry.findtext("atom:published", "", ns) or "")[:10]
        categories = [c.get("term", "") for c in entry.findall("atom:category", ns)]

        parts = [f"# {title}\n"]
        if authors:
            parts.append(f"Authors: {', '.join(authors[:10])}")
        if published:
            parts.append(f"Published: {published}")
        if categories:
            parts.append(f"Categories: {', '.join(categories[:5])}")
        parts.append(f"\n## Abstract\n\n{abstract}")

        # The abstract alone is a stub. Append the paper's full text so a --sci
        # hit is evidence, not a teaser. Fail-soft: the abstract still stands.
        full_text = _fetch_arxiv_pdf_fulltext(paper_id)
        if full_text:
            parts.append(f"\n## Full text\n\n{full_text}")

        text = "\n".join(parts)
        return text[:max_length] if text else None
    except Exception:
        pass
    return None

def _fetch_semantic_scholar_api(paper_hash: str, max_length: int) -> Optional[str]:
    """Fetch Semantic Scholar paper metadata + abstract via API (free, no key)."""
    import urllib.request
    api_url = f"https://api.semanticscholar.org/graph/v1/paper/{paper_hash}?fields=title,abstract,authors,year,citationCount,venue"
    try:
        req = urllib.request.Request(api_url, headers={"User-Agent": "web-research-tool/1.0"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        title = data.get("title", "Unknown")
        abstract = data.get("abstract") or ""
        authors = [a.get("name", "") for a in (data.get("authors") or [])]
        year = data.get("year")
        citations = data.get("citationCount")
        venue = data.get("venue") or ""
        parts = [f"# {title}\n"]
        if authors:
            parts.append(f"Authors: {', '.join(authors[:10])}")
        if year:
            parts.append(f"Year: {year}")
        if venue:
            parts.append(f"Venue: {venue}")
        if citations is not None:
            parts.append(f"Citations: {citations}")
        if abstract:
            parts.append(f"\n## Abstract\n\n{abstract}")
        text = "\n".join(parts)
        return text[:max_length] if text else None
    except Exception:
        pass
    return None


def _fetch_twitter_api(screen_name: str, tweet_id: str, max_length: int) -> Optional[str]:
    """Fetch tweet text via FxTwitter API (no auth required)."""
    import urllib.request
    api_url = f"https://api.fxtwitter.com/status/{tweet_id}"
    try:
        req = urllib.request.Request(api_url, headers={"User-Agent": "web-research-tool/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        tweet = data.get("tweet", {})
        author = tweet.get("author", {})
        name = author.get("name", screen_name)
        handle = author.get("screen_name", screen_name)
        text = tweet.get("text", "")
        created = tweet.get("created_at", "")
        likes = tweet.get("likes", 0)
        retweets = tweet.get("retweets", 0)
        replies = tweet.get("replies", 0)
        if not text:
            return None
        parts = [f"# @{handle} ({name})\n"]
        if created:
            parts.append(f"Date: {created}")
        parts.append(f"Likes: {likes} | Retweets: {retweets} | Replies: {replies}\n")
        parts.append(text)
        quote = tweet.get("quote", {})
        if quote and quote.get("text"):
            q_handle = quote.get("author", {}).get("screen_name", "?")
            parts.append(f"\n> Quoting @{q_handle}:\n> {quote['text']}")
        result = "\n".join(parts)
        return result[:max_length]
    except Exception:
        pass
    return None

def _fetch_wayback_fallback(url: str, max_length: int) -> Optional[str]:
    """Try Wayback Machine for a recent cached version of the page."""
    import urllib.request
    api_url = f"https://archive.org/wayback/available?url={urllib.parse.quote(url, safe='')}"
    try:
        req = urllib.request.Request(api_url, headers={"User-Agent": "web-research-tool/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        snapshot = data.get("archived_snapshots", {}).get("closest", {})
        if not snapshot.get("available"):
            return None
        archive_url = snapshot.get("url", "")
        if not archive_url:
            return None
        req = urllib.request.Request(archive_url, headers={"User-Agent": "web-research-tool/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8", errors="replace")
        text = _extract_with_regex(html)
        text = RE_MULTI_NEWLINE.sub("\n\n", text).strip()
        if text and len(text) > 200:
            return text[:max_length]
    except Exception:
        pass
    return None


# --------------------------------------------------- scholarly / feed / HN access
RE_DOI_IN_URL = re.compile(r'10\.\d{4,9}/[^\s"\'<>&?#]+')
RE_PMCID_IN_URL = re.compile(r'/(?:article|articles)/(?:PMC/)?(PMC\d+)', re.IGNORECASE)
RE_PMID_IN_URL = re.compile(
    r'(?:pubmed\.ncbi\.nlm\.nih\.gov/|/article/MED/)(\d+)', re.IGNORECASE
)
RE_HN_ITEM_URL = re.compile(r'https?://news\.ycombinator\.com/item\?id=(\d+)')
RE_XML_BLOCK_END = re.compile(
    r'</(?:p|sec|title|abstract|caption|list-item|ref|table-wrap|fig|'
    r'article-title|journal-title|contrib|aff|journal-meta|article-meta|pub-date)>',
    re.IGNORECASE,
)

# Scholarly resolution is throttled: each miss can cost four upstream calls, and
# the fetch consumer runs up to 50 pages concurrently against free public APIs.
SCHOLARLY_MAX_PER_RUN = 10
SCHOLARLY_CONCURRENCY = 3
_scholarly_sem = threading.BoundedSemaphore(SCHOLARLY_CONCURRENCY)
_scholarly_used = 0
_scholarly_lock = threading.Lock()

FEED_MAX_ENTRIES = 50
FEED_SUMMARY_CHARS = 300
HN_THREADS_PER_QUERY = 2
HN_THREAD_MIN_COMMENTS = 5
HN_MAX_COMMENTS = 40
HN_MAX_DEPTH = 3


def _reset_scholarly_budget() -> None:
    """Reset the per-run scholarly resolution budget (called once per run)."""
    global _scholarly_used
    with _scholarly_lock:
        _scholarly_used = 0


def _scholarly_budget_take() -> bool:
    """Claim one scholarly resolution slot for this run."""
    global _scholarly_used
    with _scholarly_lock:
        if _scholarly_used >= SCHOLARLY_MAX_PER_RUN:
            return False
        _scholarly_used += 1
        return True


def _http_get_bytes(url: str, timeout: int = 5, max_bytes: int = MAX_CONTENT_BYTES) -> Optional[bytes]:
    """GET a URL with a UA header and a byte cap. Fail-soft: None on any error."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "web-research-tool/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read(max_bytes + 1)
    except Exception:
        return None


def _markup_to_text(markup: str) -> str:
    """Strip tags from an XML/HTML fragment while keeping paragraph breaks."""
    if not markup:
        return ""
    markup = RE_BR.sub("\n", markup)
    markup = RE_XML_BLOCK_END.sub("\n\n", markup)
    markup = RE_ALL_TAGS.sub("", markup)
    markup = unescape(markup)
    markup = RE_SPACES.sub(" ", markup)
    return RE_MULTI_NEWLINE.sub("\n\n", markup).strip()


def _pmcid_from_url(url: str) -> Optional[str]:
    match = RE_PMCID_IN_URL.search(url)
    return match.group(1).upper() if match else None


def _pmid_from_url(url: str) -> Optional[str]:
    match = RE_PMID_IN_URL.search(url)
    return match.group(1) if match else None


def _doi_from_url(url: str) -> Optional[str]:
    match = RE_DOI_IN_URL.search(urllib.parse.unquote(url))
    if not match:
        return None
    doi = match.group(0).rstrip(".,;:)]}\"'")
    return doi or None


def _europepmc_pmcid(query: str) -> Optional[str]:
    """Look up a PMCID in Europe PMC by an exact query (DOI / EXT_ID)."""
    encoded = urllib.parse.quote(query, safe="")
    raw = _http_get_bytes(
        "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
        f"?query={encoded}&format=json&pageSize=1&resultType=core",
        timeout=6,
    )
    if not raw:
        return None
    try:
        data = json.loads(raw.decode("utf-8", errors="replace"))
        results = (data.get("resultList") or {}).get("result") or []
        pmcid = results[0].get("pmcid") if results else None
        return pmcid.upper() if pmcid else None
    except Exception:
        return None


def _fetch_europepmc_xml(pmcid: str, max_length: int) -> Optional[str]:
    """Fetch clean full text from Europe PMC's JATS XML endpoint."""
    xml_url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML"
    raw = _http_get_bytes(xml_url, timeout=10)
    if not raw:
        return None
    xml = raw.decode("utf-8", errors="replace")
    title_match = re.search(r'<article-title[^>]*>(.*?)</article-title>', xml, re.S | re.I)
    title = _markup_to_text(title_match.group(1)) if title_match else ""
    body = _markup_to_text(xml)
    if len(body) < 400:
        return None
    header = f"# {title or f'Europe PMC {pmcid}'}\n\nSource: {xml_url}\n\n"
    return (header + body)[:max_length]


def _fetch_openalex_oa_pdf(doi: str, max_length: int) -> Optional[str]:
    """Resolve an open-access PDF for a DOI through OpenAlex, then extract it."""
    work_url = "https://api.openalex.org/works/https://doi.org/" + urllib.parse.quote(doi, safe="/")
    raw = _http_get_bytes(work_url, timeout=6)
    if not raw:
        return None
    try:
        data = json.loads(raw.decode("utf-8", errors="replace"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    pdf_url = (data.get("best_oa_location") or {}).get("pdf_url") or (
        data.get("open_access") or {}
    ).get("oa_url")
    if not pdf_url:
        return None
    pdf_bytes = _http_get_bytes(pdf_url, timeout=12, max_bytes=PDF_MAX_DOWNLOAD_BYTES)
    if not pdf_bytes or b"%PDF" not in pdf_bytes[:64]:
        return None
    text = _extract_pdf(pdf_bytes)
    if len(text) < 400:
        return None
    title = " ".join((data.get("title") or "").split()) or f"OpenAlex OA copy ({doi})"
    return (f"# {title}\n\nSource: {pdf_url}\n\n{text}")[:max_length]


def _fetch_scholarly_fulltext(url: str, max_length: int) -> Optional[str]:
    """Resolve an open-access full text for a scholarly URL.

    Order: PMCID in the URL → Europe PMC by DOI → OpenAlex OA PDF → Europe PMC
    by PMID. Fail-soft: None means no OA copy was found, and the caller falls
    back to the ordinary fetch (publisher landing page, then Wayback).
    """
    with _scholarly_sem:
        pmcid = _pmcid_from_url(url)
        if pmcid:
            text = _fetch_europepmc_xml(pmcid, max_length)
            if text:
                return text
        doi = _doi_from_url(url)
        if doi:
            pmcid = _europepmc_pmcid(f'DOI:"{doi}"')
            if pmcid:
                text = _fetch_europepmc_xml(pmcid, max_length)
                if text:
                    return text
            text = _fetch_openalex_oa_pdf(doi, max_length)
            if text:
                return text
        pmid = _pmid_from_url(url)
        if pmid:
            pmcid = _europepmc_pmcid(f"EXT_ID:{pmid} AND SRC:MED")
            if pmcid:
                text = _fetch_europepmc_xml(pmcid, max_length)
                if text:
                    return text
    return None


def _looks_like_feed(payload) -> bool:
    """Cheap structural sniff for RSS/Atom; never trusts the content type alone."""
    if isinstance(payload, (bytes, bytearray)):
        head = bytes(payload[:4096]).decode("utf-8", errors="replace")
    else:
        head = str(payload or "")[:4096]
    head = head.lstrip("\ufeff \t\r\n").lower()
    if not head.startswith(("<?xml", "<rss", "<feed", "<rdf:rdf")):
        return False
    return any(marker in head for marker in ("<rss", "<feed", "<rdf:rdf"))


def _feed_entry_date(entry) -> str:
    """Publication date as YYYY-MM-DD, or "" when the feed does not carry one."""
    for key in ("published_parsed", "updated_parsed"):
        parsed = entry.get(key)
        if parsed:
            try:
                return time.strftime("%Y-%m-%d", parsed)
            except (TypeError, ValueError):
                continue
    return ""


def _feed_entry_summary(entry) -> str:
    """Plain-text summary from content:encoded when present, else summary."""
    raw = ""
    content = entry.get("content")
    if isinstance(content, list) and content and isinstance(content[0], dict):
        raw = content[0].get("value") or ""
    if not raw:
        raw = entry.get("summary") or entry.get("description") or ""
    if not raw:
        return ""
    raw = RE_BR.sub(" ", raw)
    raw = RE_ALL_TAGS.sub("", raw)
    raw = unescape(raw)
    raw = RE_WHITESPACE.sub(" ", raw).strip()
    return raw[:FEED_SUMMARY_CHARS]


def _render_feed(payload: bytes, url: str, max_chars: int) -> Optional[str]:
    """Render an RSS/Atom payload as ONE structured record.

    Structure is the point: the old path reached the entries only by accident,
    losing every entry URL. One record per feed keeps the request count at one
    and preserves the links a follow-up fetch needs.
    """
    try:
        import feedparser
    except Exception:
        return None
    try:
        parsed = feedparser.parse(payload)
    except Exception:
        return None
    entries = list(getattr(parsed, "entries", None) or [])
    feed_title = " ".join((parsed.feed.get("title") or "").split())
    if not feed_title and not entries:
        return None
    parts = [
        f"# {feed_title or url}",
        "",
        f"Feed: {url}",
        f"Entries: {len(entries)} (newest first)",
        "",
    ]
    used = sum(len(part) + 1 for part in parts)
    shown = 0
    for entry in entries[:FEED_MAX_ENTRIES]:
        title = " ".join((entry.get("title") or "").split()) or "(untitled)"
        link = (entry.get("link") or "").strip()
        date = _feed_entry_date(entry)
        summary = _feed_entry_summary(entry)
        block = [f"## {date + ' — ' if date else ''}{title}"]
        if link:
            block.append(f"URL: {link}")
        if summary:
            block.append(summary)
        block.append("")
        chunk = "\n".join(block)
        if used + len(chunk) > max_chars:
            parts.append(f"[feed truncated: {shown} of {len(entries)} entries shown]")
            break
        parts.append(chunk)
        used += len(chunk)
        shown += 1
    return "\n".join(parts).strip()


def _fetch_hn_thread(item_id: str, max_length: int) -> Optional[str]:
    """Render a Hacker News discussion as one record (Algolia item API)."""
    raw = _http_get_bytes(f"https://hn.algolia.com/api/v1/items/{item_id}", timeout=6)
    if not raw:
        return None
    try:
        data = json.loads(raw.decode("utf-8", errors="replace"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    title = " ".join((data.get("title") or "").split()) or f"Hacker News item {item_id}"
    parts = [f"# {title}", "", f"Points: {data.get('points') or 0}", ""]
    count = 0

    def walk(node: dict, depth: int) -> None:
        nonlocal count
        if count >= HN_MAX_COMMENTS or depth > HN_MAX_DEPTH:
            return
        for child in node.get("children") or []:
            if count >= HN_MAX_COMMENTS:
                return
            if not isinstance(child, dict):
                continue
            body = _markup_to_text(child.get("text") or "")
            if body:
                author = child.get("author") or "unknown"
                parts.append(f"[d{depth}] {author}: {body[:600]}")
                count += 1
            walk(child, depth + 1)

    walk(data, 1)
    if count == 0:
        return None
    joined = "\n".join(parts)
    return joined[:max_length]


# ---------------------------------------------------------------- browser tier
# The browser tier is real Google Chrome, driven by browser_fetch.py (--url-chrome).
# Chromium and Chrome-for-Testing are measurably detected, so no other engine is used.
# The tier sets itself up on first use: the wrapper provisions Chrome repo-locally
# (tmp/browser/chrome), and it does so from whichever path needs it first — an explicit
# --url-chrome, or the automatic --url escalation below, whose preflight runs BEFORE the
# run's wall-clock block so a first provisioning never counts against the fetch budget.
_BROWSER_TIER_READY: Optional[bool] = None   # None = not ensured yet this run
_BROWSER_ENSURE_TIMEOUT: int = 900           # provisioning (download + libs) is one-time


def _chrome_candidates() -> List[Path]:
    """The repo-local Chrome payload (tmp/browser/chrome) — the only browser ever used.

    No system install is consulted on any platform: the wrapper provisions this payload
    (macOS .dmg, Linux .deb, Windows offline installer) so every host behaves the same.
    The Windows payload nests chrome.exe under a versioned directory, hence the glob.
    """
    repo = Path(__file__).resolve().parents[2]
    root = repo / "tmp" / "browser" / "chrome"
    if _IS_WINDOWS:
        try:
            return sorted(root.glob("**/chrome.exe"),
                          key=lambda p: p.stat().st_mtime, reverse=True)
        except OSError:
            return []
    if _IS_MACOS:
        return [root / "Google Chrome.app" / "Contents" / "MacOS" / "Google Chrome"]
    return [root / "opt" / "google" / "chrome" / "chrome"]


def _chrome_present() -> bool:
    """Cheap existence check — never downloads, never launches."""
    try:
        return any(p.exists() for p in _chrome_candidates())
    except OSError:
        return False


def _browser_wrapper() -> Optional[Path]:
    """The tier's wrapper script for this platform (browser_fetch.sh / .bat)."""
    script = Path(__file__).resolve().parent / (
        "browser_fetch.bat" if _IS_WINDOWS else "browser_fetch.sh")
    return script if script.exists() else None


def _ensure_browser_tier() -> bool:
    """Provision the Chrome tier if it is not installed yet (once per run).

    Called by the --url preflight BEFORE the run's wall-clock block: a first
    provisioning (Chrome download plus, on Linux, its system libraries) can take
    minutes and must not count against the fetch budget. Silent and free once the
    payload is provisioned; the wrapper is invoked only when it is missing, and the
    wrapper is what installs it (repo-local — a system browser is never used).
    """
    global _BROWSER_TIER_READY
    if _BROWSER_TIER_READY is not None:
        return bool(_BROWSER_TIER_READY)
    if _chrome_present():
        _BROWSER_TIER_READY = True
        return True
    _BROWSER_TIER_READY = False
    script = _browser_wrapper()
    if script is None:
        return False
    print("Browser tier not installed — provisioning Google Chrome "
          "(one-time, first use only)", file=sys.stderr)
    env = {**os.environ, "BROWSER_FETCH_NO_FALLBACK": "1"}
    try:
        proc = subprocess.run(
            [str(script), "--ensure"], capture_output=True, text=True,
            timeout=_BROWSER_ENSURE_TIMEOUT, encoding="utf-8", errors="replace",
            env=env,
        )
        for line in (proc.stderr or proc.stdout or "").splitlines():
            if line.strip():
                print(f"  {line.strip()}", file=sys.stderr)
    except (OSError, subprocess.SubprocessError):
        pass
    _BROWSER_TIER_READY = _chrome_present()
    if _BROWSER_TIER_READY:
        print("Browser tier ready — Google Chrome provisioned in tmp/browser/chrome",
              file=sys.stderr)
    else:
        print("Browser tier unavailable — the static fetch stands on its own",
              file=sys.stderr)
    return _BROWSER_TIER_READY


def _fetch_with_real_browser(url: str, timeout: int = 180) -> Optional[FetchResult]:
    """Run the Chrome tier (browser_fetch.sh/.bat) as a subprocess and adopt its report.

    Returns None when the tier itself could not run (provisioning/launch failure) —
    distinct from a page that WAS fetched but came back gated (a non-None result with
    success=False). BROWSER_FETCH_NO_FALLBACK=1 stops the wrapper from re-running the
    static --url fetch the caller already holds: on a tier failure it exits 3 instead.
    """
    script = _browser_wrapper()
    if script is None:
        return None
    env = {**os.environ, "BROWSER_FETCH_NO_FALLBACK": "1"}
    try:
        proc = subprocess.run([str(script), url], capture_output=True, text=True,
                              timeout=timeout, env=env,
                              encoding="utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError):
        return None
    out = f"{proc.stdout or ''}\n{proc.stderr or ''}"
    m = re.search(r"^OUTCOME:\s*(\w+)", out, re.M)
    if m is None:
        # No OUTCOME line: the wrapper never got as far as the browser (provisioning,
        # uv or launch failure) — the tier is unusable, nothing was fetched.
        return None
    status = m.group(1)
    path_m = re.search(r"^FULL REPORT:\s*(.+)$", out, re.M)
    if status != "ok" or not path_m:
        return FetchResult(url=url, success=False, error=f"browser {status}",
                           source="chrome")
    try:
        raw = Path(path_m.group(1).strip()).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return FetchResult(url=url, success=False, error="browser report unreadable",
                           source="chrome")
    body = raw.split("\n\n", 1)[1].strip() if "\n\n" in raw else raw.strip()
    title = next((ln[7:].strip() for ln in raw.splitlines()[:12] if ln.startswith("title: ")), "")
    date_iso, date_source = _resolve_page_date("", body, url)
    return FetchResult(url=url, success=True, content=body, title=title, source="chrome",
                       date=date_iso, date_source=date_source)


async def _maybe_escalate(
    url: str,
    result: FetchResult,
    min_content_length: int,
    progress: Optional[ProgressReporter],
    render: str,
    escalation_budget: int,
    elapsed: float,
) -> FetchResult:
    """Escalation ladder: retry a static failure once with the real browser.

    Used by --url mode: retry-worthy failures (HTTP 403/429/5xx, CAPTCHA,
    Timeout and Too short) get one browser retry, budget-capped. Search mode
    never calls this (static-only by design, per module docstring item 7).
    Non-retry-worthy failures (404, DNS, PDF...) return unchanged. The browser
    tier is provisioned on first use by the --url preflight, which runs before the
    wall-clock block — `_BROWSER_TIER_READY` False means that provisioning failed,
    so there is nothing to retry with and the static result stands. The browser
    tier is a subprocess that takes no progress argument and reports nothing to
    this reporter — it reports through its own stderr; the static result is
    reported here (once) when it wins. The budget check-then-increment is race-free: no
    await between the check and the `+= 1` (single-threaded event loop; the
    await for the browser fetch happens after the increment).
    """
    if render == "auto" and _BROWSER_TIER_READY is not False:
        global _BROWSER_ESCALATIONS
        retry_worthy = (
            not result.success and result.error in BROWSER_RETRY_ERRORS
        )
        if retry_worthy and _BROWSER_ESCALATIONS < escalation_budget:
            _BROWSER_ESCALATIONS += 1
            browser_result = await asyncio.get_running_loop().run_in_executor(
                None, _fetch_with_real_browser, url,
            )
            if (browser_result and browser_result.success
                    and len(browser_result.content or "") >= min_content_length):
                return browser_result
    if progress:
        progress.url_result(url, result.success, elapsed, result.error or "")
    return result


async def fetch_single_async(
    url: str,
    timeout: int,
    min_content_length: int,
    max_content_length: int,
    progress: Optional[ProgressReporter] = None,
    query: str = "",
    escalation_budget: int = 3,
    render: str = "off",
    apply_filters: bool = True,
) -> FetchResult:
    """Fetch single URL using Scrapling's AsyncFetcher (TLS fingerprinting).

    render: "off" | "auto". "auto" retries failed fetches with the real-browser
    tier (Google Chrome, provisioned on first use by the --url preflight);
    "off" is static-only (--no-render / search mode).
    escalation_budget: max browser escalations per run in "auto" mode
    (single URL per --url run, so effectively 1).
    apply_filters: False = raw mode (--url default): full-document DOM
    extraction (no content-area detection) and no F4/F1 cleanup — every
    visible text node in page order. True = the default filtered path.
    """
    # The --url full mode passes max_content_length=None (no cap). _create_fetch_result
    # would crash on `len(content) > None`, so a sentinel huge value disables
    # truncation while keeping the static path code unchanged.
    if max_content_length is None:
        max_content_length = 10 ** 12
    t0 = time.monotonic()
    try:
        # API fast-path: use native APIs for sites that produce cleaner output than scraping
        api_content = None
        wiki_image: Optional[str] = None
        loop = asyncio.get_running_loop()
        wiki_match = RE_WIKIPEDIA_URL.match(url)
        if wiki_match:
            lang, title = wiki_match.group(1), wiki_match.group(2)
            wiki_api = await loop.run_in_executor(
                None, _fetch_wikipedia_api, lang, title, max_content_length
            )
            if wiki_api:
                api_content, wiki_image = wiki_api
        gh_match = RE_GITHUB_REPO_URL.match(url) if not api_content else None
        if gh_match:
            owner, repo = gh_match.group(1), gh_match.group(2)
            api_content = await loop.run_in_executor(
                None, _fetch_github_readme, owner, repo, max_content_length
            )
        arxiv_match = RE_ARXIV_URL.match(url) if not api_content else None
        if arxiv_match:
            paper_id = arxiv_match.group(1)
            api_content = await loop.run_in_executor(
                None, _fetch_arxiv_api, paper_id, max_content_length
            )
        # Semantic Scholar: try regex first, then fallback to string extraction
        if not api_content and 'semanticscholar.org/paper/' in url:
            s2_match = RE_SEMANTIC_SCHOLAR_URL.match(url)
            paper_hash = s2_match.group(1) if s2_match else None
            if not paper_hash:
                # Fallback: extract last path segment as paperId
                path = url.split('semanticscholar.org/paper/')[-1].split('?')[0].rstrip('/')
                candidate = path.split('/')[-1]
                if len(candidate) == 40 and all(c in '0123456789abcdef' for c in candidate.lower()):
                    paper_hash = candidate
            if paper_hash:
                api_content = await loop.run_in_executor(
                    None, _fetch_semantic_scholar_api, paper_hash, max_content_length
                )
            if not api_content:
                # S2 website returns HTTP 202 for programmatic access — skip Scrapling
                elapsed = time.monotonic() - t0
                s2_domain = urllib.parse.urlparse(url).netloc
                if progress:
                    progress.message(f"    --  {elapsed:5.1f}s  {s2_domain} (S2 API unavailable)")
                return FetchResult(url=url, success=False, error="S2 API unavailable")
        tw_match = RE_TWITTER_URL.match(url) if not api_content else None
        if tw_match:
            screen_name, tweet_id = tw_match.group(1), tw_match.group(2)
            api_content = await loop.run_in_executor(
                None, _fetch_twitter_api, screen_name, tweet_id, max_content_length
            )
        # Hacker News discussion: structured thread instead of a page scrape.
        hn_match = RE_HN_ITEM_URL.match(url) if not api_content else None
        if hn_match:
            api_content = await loop.run_in_executor(
                None, _fetch_hn_thread, hn_match.group(1), max_content_length
            )
        # For API-routed domains, if API failed, scraping won't help — bail early
        api_only = tw_match
        if api_content:
            elapsed = time.monotonic() - t0
            result = _create_fetch_result(url, api_content, min_content_length, max_content_length, query=query, apply_filters=apply_filters)
            if hn_match:
                result.kind = "hn"
            if wiki_image:
                result.images = [wiki_image]
            if progress:
                progress.url_result(url, result.success, elapsed, result.error or "")
            return result
        if api_only:
            elapsed = time.monotonic() - t0
            result = FetchResult(url=url, success=False, error="API extraction failed")
            if progress:
                progress.url_result(url, False, elapsed, "API failed")
            return result
        # Domain routing: scholarly sources take an open-access resolution path
        # before the ordinary static fetch. Budgeted per run; on a miss (or an
        # exhausted budget) the static path below runs unchanged.
        route = route_for(url)
        if route == ROUTE_SCHOLARLY and _scholarly_budget_take():
            scholarly = await loop.run_in_executor(
                None, _fetch_scholarly_fulltext, url, max_content_length
            )
            if scholarly:
                result = _create_fetch_result(
                    url, scholarly, min_content_length, max_content_length,
                    query=query, apply_filters=apply_filters,
                )
                if result.success:
                    elapsed = time.monotonic() - t0
                    if progress:
                        progress.url_result(url, True, elapsed, "")
                    return result
        _host = urllib.parse.urlparse(url).hostname or ""
        _use_httpx = _host in _CURL_DNS_FAIL_DOMAINS
        # Shared budget across the static attempt AND the httpx fallback: the
        # fallback may never re-spend the full timeout (5+5=10s on DNS-failing
        # domains). httpx gets the remainder of the original timeout, floor 1s.
        _static_deadline = time.monotonic() + timeout
        if not _use_httpx:
            try:
                page = await asyncio.wait_for(
                    AsyncFetcher.get(url, timeout=timeout, stealthy_headers=True),
                    timeout=min(timeout, 5),  # hard cutoff — DNS should resolve in <1s
                )
            except (asyncio.TimeoutError, Exception) as _fetch_err:
                if isinstance(_fetch_err, asyncio.TimeoutError) or "Resolving timed out" in str(_fetch_err):
                    _CURL_DNS_FAIL_DOMAINS.add(_host)
                    _use_httpx = True
                else:
                    raise
        if _use_httpx:
            # curl_cffi c-ares DNS fails for this domain — use httpx (system DNS)
            import httpx
            # Keep the total at ~timeout+1s: scrapling may have burned the whole
            # budget on a slow peer; httpx still gets a short window (floor 1s).
            _remaining = max(1.0, _static_deadline - time.monotonic())
            async with httpx.AsyncClient(
                follow_redirects=True, timeout=min(timeout, _remaining),
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            ) as _hx:
                _resp = await _hx.get(url)
            page = types.SimpleNamespace(
                status=_resp.status_code,
                html_content=_resp.text,
                body=_resp.content,
            )
        elapsed = time.monotonic() - t0

        if page.status != 200:
            return await _maybe_escalate(
                url,
                FetchResult(url=url, success=False, error=f"HTTP {page.status}"),
                min_content_length,
                progress, render, escalation_budget, elapsed,
            )

        try:
            raw_html = page.html_content
        except (UnicodeDecodeError, AttributeError):
            # Scrapling failed to decode — try common encodings on raw bytes
            raw_bytes = page.body if hasattr(page, 'body') else b""
            raw_html = ""
            for enc in ("utf-8", "latin-1", "windows-1252", "iso-8859-1"):
                try:
                    raw_html = raw_bytes.decode(enc, errors="replace")
                    break
                except Exception:
                    continue
            if not raw_html:
                if progress:
                    progress.url_result(url, False, elapsed, "Encoding error")
                return FetchResult(url=url, success=False, error="Encoding error")
        if len(raw_html) > MAX_CONTENT_BYTES:
            # Truncate HTML but still try to extract text
            raw_html = raw_html[:MAX_CONTENT_BYTES]

        if _is_pdf(raw_html, url):
            # PDF: pdftotext when available, pypdf otherwise (both in process pool)
            raw_body = page.body if isinstance(page.body, (bytes, bytearray)) else raw_html.encode("utf-8", errors="replace")
            loop = asyncio.get_running_loop()
            content = await loop.run_in_executor(
                _get_extract_pool(), _extract_pdf, raw_body
            )
            if not content:
                _pdf_err = "PDF extraction failed (no text layer or encrypted)"
                if progress:
                    progress.url_result(url, False, elapsed, _pdf_err)
                return FetchResult(url=url, success=False, error=_pdf_err)
            result = _create_fetch_result(url, content, min_content_length, max_content_length, query=query, apply_filters=apply_filters)
            if result.success:
                # A PDF has no meta tags to read; the date comes from URL/visible text.
                result.date, result.date_source = _resolve_page_date("", result.content, url)
            if progress:
                progress.url_result(url, result.success, elapsed, result.error or "")
            return result

        # RSS/Atom: render structured entries instead of tag-stripped text.
        # Runs in both modes; the record keeps entry URLs, which extraction loses.
        feed_payload = (
            page.body if isinstance(getattr(page, "body", None), (bytes, bytearray))
            else raw_html.encode("utf-8", errors="replace")
        )
        if _looks_like_feed(feed_payload):
            # --url mode normalises an absent cap to 10**12; keep the feed record
            # bounded by entries either way instead of inheriting that sentinel.
            feed_budget = (
                40000 if max_content_length > 10**11
                else max(9000, max_content_length - 1000)
            )
            feed_text = await asyncio.get_running_loop().run_in_executor(
                None, _render_feed, bytes(feed_payload), url, feed_budget
            )
            if feed_text:
                result = _create_fetch_result(
                    url, feed_text, min_content_length, max_content_length,
                    query=query, apply_filters=apply_filters,
                )
                result.kind = "feed"
                if progress:
                    progress.url_result(url, result.success, elapsed, result.error or "")
                return result

        if is_blocked_content(raw_html):
            return await _maybe_escalate(
                url,
                FetchResult(url=url, success=False, error="CAPTCHA/blocked"),
                min_content_length,
                progress, render, escalation_budget, elapsed,
            )

        # Extract text + JSON-LD in process pool (CPU-bound, don't block event loop)
        loop = asyncio.get_running_loop()
        images: List[str] = []
        if apply_filters:
            content, structured = await loop.run_in_executor(
                _get_extract_pool(), _extract_content, raw_html
            )
            # Candidate image URLs on the page (prefer the rendered DOM's <img>
            # attributes when the scrapling page exposes them; otherwise scan the
            # raw HTML). Same pool, so the regex never blocks the event loop.
            dom_imgs = _dom_img_attrs(page)
            try:
                images = await loop.run_in_executor(
                    _get_extract_pool(), _extract_image_urls, raw_html, url, dom_imgs
                )
            except Exception:  # noqa: BLE001 - images are optional; never fail a page
                images = []
            # Fallback: Scrapling's DOM parser when primary extraction is too short
            if len(content) < min_content_length:
                scrapling_content = _extract_with_scrapling_fallback(page, min_content_length)
                if scrapling_content:
                    content = scrapling_content
        else:
            # Raw mode (--url): full-document DOM text — every visible text node
            # in order (nav, lists, boilerplate included), no content-area
            # detection, no F4/F1 cleanup. min_length=0 disables the gate.
            content = _extract_with_scrapling_fallback(page, 0)
            structured = ""

        # Prepend structured data to content
        if structured:
            content = structured + content

        result = _create_fetch_result(url, content, min_content_length, max_content_length, query=query, apply_filters=apply_filters)
        # Wayback Machine fallback. Ordinary pages use it on failure only. For a
        # WAYBACK-route domain (paywalled news) the live fetch returns 200 with a
        # teaser, which is a "success" — so a stub or short body also triggers it.
        _wb_needed = not result.success
        if not _wb_needed and route == ROUTE_WAYBACK:
            _wb_needed = (
                not result.content
                or _is_stub_page(result.content)
                or len(result.content) < 3 * min_content_length
            )
        if _wb_needed:
            wb_content = await loop.run_in_executor(
                None, _fetch_wayback_fallback, url, max_content_length
            )
            if wb_content:
                wb_result = _create_fetch_result(url, wb_content, min_content_length, max_content_length, query=query, apply_filters=apply_filters)
                if wb_result.success and (
                    not result.success or len(wb_result.content) > len(result.content)
                ):
                    result = wb_result
        result.images = images
        # Page date for the digest (informational). Computed after the Wayback
        # fallback so the marker describes the content actually delivered; the
        # recency filter F7 does not consume these fields.
        if result.success:
            try:
                result.date, result.date_source = await loop.run_in_executor(
                    _get_extract_pool(), _resolve_page_date, raw_html, result.content, url
                )
            except Exception:
                pass
        # Escalation ladder: retry-worthy failures (HTTP 403/429/5xx, CAPTCHA,
        # Timeout, Too short) get one retry with real Google Chrome (the same tier
        # as --url-chrome, provisioned on first use by the preflight above),
        # budget-capped in "auto" mode. The static result is reported once by
        # the helper when it wins.
        return await _maybe_escalate(
            url,
            result,
            min_content_length,
            progress, render, escalation_budget, elapsed,
        )

    except asyncio.TimeoutError:
        elapsed = time.monotonic() - t0
        return await _maybe_escalate(
            url,
            FetchResult(url=url, success=False, error="Timeout"),
            min_content_length,
            progress, render, escalation_budget, elapsed,
        )
    except Exception as e:
        elapsed = time.monotonic() - t0
        error_msg = str(e)[:50] if str(e) else type(e).__name__
        logger.debug(f"Fetch error for {url}: {e}")
        return await _maybe_escalate(
            url,
            FetchResult(url=url, success=False, error=error_msg),
            min_content_length,
            progress, render, escalation_budget, elapsed,
        )


# =============================================================================
# SEARCH BACKENDS
# =============================================================================


_RE_HTML_TAGS = re.compile(r"<[^>]+>")

def _snippet_relevance(query: str, title: str, snippet: str) -> float:
    """Score snippet relevance to query by substring match. Returns 0.0-1.0.

    Uses substring matching instead of word-set intersection because DDG's
    ddgs library strips <b> tags without adding spaces, concatenating adjacent
    words (e.g. "theCRISPRsicklecelltherapy"). Substring match handles this.
    """
    query_words = set(query.lower().split())
    text = _RE_HTML_TAGS.sub(" ", (title + " " + snippet)).lower()
    if not query_words:
        return 1.0
    return sum(1 for w in query_words if w in text) / len(query_words)


_ACADEMIC_STRONG = (
    "paper", "papers", "preprint", "arxiv", "pubmed", "doi:",
    "meta-analysis", "systematic review", "clinical trial",
    "literature review", "peer-review", "journal article",
)
_ACADEMIC_WEAK = (
    "research", "study", "studies", "algorithm", "neural",
    "genome", "protein", "quantum", "theorem", "benchmark",
    "dataset", "experiment", "hypothesis", "molecular",
    "computational", "optimization", "evaluation", "survey",
    "simulation", "methodology", "technique",
    "prediction", "detection", "classification",
    "learning", "training", "computing",
    "correction", "encoding", "decoding", "synthesis",
    "imaging", "catalyst", "receptor", "enzyme",
    "biodiversity", "ecosystem", "acidification",
    "emission", "photovoltaic", "semiconductor",
    "neuroscience", "cortex", "cognitive",
    "clinical", "therapeutic", "diagnostic",
    "mechanism", "architecture", "model",
    "reinforcement", "robotics", "autonomous",
    "generative", "diffusion", "transformer",
)


def _is_academic_query(query: str) -> bool:
    """Heuristic: does this query likely seek academic/scientific content?
    Strong signals (any 1): paper, arxiv, clinical trial, etc.
    Weak signals (need 2+): research, algorithm, neural, model, etc."""
    q = query.lower()
    if any(s in q for s in _ACADEMIC_STRONG):
        return True
    return sum(1 for w in _ACADEMIC_WEAK if w in q) >= 2


def _detect_ddg_region(query: str) -> Optional[str]:
    """Detect DDG region from query script (Unicode ranges). Returns None for Latin."""
    scripts = {"ja": 0, "zh": 0, "ko": 0}
    for ch in query:
        cp = ord(ch)
        if 0x3040 <= cp <= 0x30FF or 0x31F0 <= cp <= 0x31FF:  # Hiragana + Katakana
            scripts["ja"] += 1
        elif 0x4E00 <= cp <= 0x9FFF or 0x3400 <= cp <= 0x4DBF:  # CJK Unified
            scripts["zh"] += 1  # tentative — overridden by ja if kana present
        elif 0xAC00 <= cp <= 0xD7AF or 0x1100 <= cp <= 0x11FF:  # Hangul
            scripts["ko"] += 1
    # If kana detected, CJK chars are also Japanese
    if scripts["ja"] > 0:
        scripts["ja"] += scripts["zh"]
        scripts["zh"] = 0
    top = max(scripts, key=scripts.get)
    if scripts[top] == 0:
        return None
    region_map = {"ja": "jp-jp", "zh": "zh-cn", "ko": "kr-kr"}
    return region_map.get(top)


class DuckDuckGoSearch:
    """DuckDuckGo search with early URL filtering."""

    def search(
        self,
        query: str,
        num_results: int = 50,
        region: Optional[str] = None,
        timelimit: Optional[str] = None,
    ) -> Iterator[Tuple[str, str, str]]:
        """Search DuckDuckGo and yield (url, title, snippet) tuples.

        timelimit: "d" | "w" | "m" | "y" — engine-side time window (ddgs maps
        it per backend). Only set when the caller explicitly asked for it.
        """
        seen_urls: Set[str] = set()
        count = 0

        ddg = DDGS(verify=False)
        ddg_kwargs = {}
        if region:
            ddg_kwargs["region"] = region
        if timelimit:
            ddg_kwargs["timelimit"] = timelimit
        # Request exactly `num_results` (not 2x): paginating a doubled count costs
        # ~2-3s and most extra hits are duplicates/blocked anyway — the count
        # loop already caps the yield.
        for r in ddg.text(query, max_results=num_results, **ddg_kwargs):
            url = r.get("href", "")
            if url and url not in seen_urls and is_valid_url(url) and not is_blocked_url(url):
                seen_urls.add(url)
                yield url, r.get("title", ""), r.get("body", "")
                count += 1
                if count >= num_results:
                    return


class MultiSearch:
    """DuckDuckGo search — the only search backend.

    Exceptions are NOT swallowed here: the caller classifies them (backend
    throttle vs. a genuinely empty query) and decides whether to retry.
    """

    def search(
        self,
        query: str,
        num_results: int = 20,
        timelimit: Optional[str] = None,
    ) -> Iterator[Tuple[str, str, str]]:
        """Search DuckDuckGo and yield (url, title, snippet) tuples."""
        seen_urls: Set[str] = set()
        region = _detect_ddg_region(query)

        ddg = DuckDuckGoSearch()
        for url, title, snippet in ddg.search(
            query, num_results, region=region, timelimit=timelimit
        ):
            if url not in seen_urls:
                seen_urls.add(url)
                yield url, title, snippet


# =============================================================================
# RESEARCH WORKFLOW
# =============================================================================

async def run_research_async(
    config: ResearchConfig,
    progress: ProgressReporter,
    global_seen_urls: Optional[Set[str]] = None,
    status: Optional[dict] = None,
) -> AsyncIterator[FetchResult]:
    """
    Async streaming research workflow.
    Yields FetchResult objects as they complete.
    Pass global_seen_urls to dedup across multiple parallel queries.
    status: optional dict filled with search diagnostics (attempts, error,
    URL count) so the caller can print an accurate failure reason.
    """
    status = {} if status is None else status
    progress.message(f'Researching: "{config.query}"')

    urls: List[str] = []
    fetch_queue: asyncio.Queue[Optional[str]] = asyncio.Queue()
    result_queue: asyncio.Queue[Optional[FetchResult]] = asyncio.Queue()
    stats = ResearchStats(query=config.query)

    async def search_producer() -> None:
        loop = asyncio.get_running_loop()
        searcher = MultiSearch()
        t0 = time.monotonic()

        skipped = 0

        def search_and_stream():
            nonlocal skipped
            enqueued = 0
            seen_in_search: Set[str] = set()

            def consume_primary(timelimit: Optional[str]) -> None:
                # Streams into the fetch queue as it iterates, so fetching still
                # overlaps the search. `enqueued` is the zero-result signal.
                nonlocal enqueued, skipped
                for url, title, snippet in searcher.search(
                    config.query, config.search_results, timelimit=timelimit
                ):
                    if global_seen_urls is not None:
                        if url in global_seen_urls:
                            continue
                        global_seen_urls.add(url)
                    seen_in_search.add(url)
                    urls.append(url)
                    stats.urls_searched = len(urls)
                    # Snippet relevance gate: skip URLs with zero query word overlap
                    # Always enqueue at least 5 URLs (safety net for edge cases)
                    relevance = _snippet_relevance(config.query, title, snippet)
                    if relevance == 0 and enqueued >= 5:
                        if progress.verbose:
                            _host = urllib.parse.urlparse(url).hostname or url
                            print(f"  [skip] {_host} relevance=0 | {title[:50]}", flush=True)
                        skipped += 1
                        continue
                    loop.call_soon_threadsafe(fetch_queue.put_nowait, url)
                    enqueued += 1

            attempts = 0
            while attempts < SEARCH_MAX_ATTEMPTS:
                attempts += 1
                before = enqueued
                try:
                    consume_primary(config.since or None)
                except Exception as e:
                    # ddgs raises for both throttling and a genuinely empty query;
                    # the class name goes into the run status for the final report.
                    status["search_error"] = type(e).__name__
                    logger.debug(f"DDG search failed: {e}")
                    if progress.verbose:
                        print(f"  DDG search failed ({type(e).__name__})", file=sys.stderr)
                # Retry only when the attempt produced NOTHING. A partial set is
                # never re-requested.
                if enqueued > before or attempts >= SEARCH_MAX_ATTEMPTS:
                    break
                progress.message(
                    f"  [search] 0 URLs; retrying in {SEARCH_RETRY_DELAY:.1f}s"
                )
                time.sleep(SEARCH_RETRY_DELAY)
            status["search_attempts"] = attempts

            # Supplement with topic-specific bonus sources (not in web search)
            stats.bonus_sources = {}

            def _enqueue_bonus(url: str, source: str = "") -> None:
                nonlocal enqueued
                if url in seen_in_search or not is_valid_url(url) or is_blocked_url(url):
                    return
                if global_seen_urls is not None:
                    if url in global_seen_urls:
                        return
                    global_seen_urls.add(url)
                seen_in_search.add(url)
                urls.append(url)
                stats.urls_searched = len(urls)
                if source:
                    stats.bonus_sources[source] = stats.bonus_sources.get(source, 0) + 1
                loop.call_soon_threadsafe(fetch_queue.put_nowait, url)
                enqueued += 1

            # Run bonus searches in parallel (topic-specific sources).
            # NOTE: DDG is hit exactly ONCE per run (the primary text search).
            # The former DDG news bonus was removed: a second hit on the same
            # hostname per run doubles the chance of DDG IP rate-limiting and
            # contributed ~1 URL per run (often failing with DDGSException).
            # An academic site: supplement was tried and removed for the same
            # reason: a fixed delay before it did not improve reliability, and
            # it raised the per-run call count. Publisher coverage stays a
            # manual second query (site: operators pass through unchanged).
            def _bonus_arxiv():
                """Search arXiv API, fallback to Semantic Scholar if arXiv fails."""
                arxiv_ok = False
                try:
                    import urllib.request
                    import xml.etree.ElementTree as ET
                    # Strip dates/filler, use AND between core terms for precision
                    _arxiv_skip = {"recent", "latest", "new", "advances", "applications",
                                   "current", "overview", "update", "the", "and", "for", "with", "from"}
                    words = [w for w in config.query.split()
                             if not re.match(r'^\d{4}$', w) and w.lower() not in _arxiv_skip]
                    core = words[:4]  # max 4 key terms
                    arxiv_query = " AND ".join(f"all:{w}" for w in core) if core else config.query
                    encoded = urllib.parse.quote_plus(arxiv_query)
                    api_url = f"http://export.arxiv.org/api/query?search_query={encoded}&start=0&max_results=5&sortBy=relevance"
                    req = urllib.request.Request(api_url, headers={"User-Agent": "web-research-tool/1.0"})
                    with urllib.request.urlopen(req, timeout=3) as resp:
                        xml_data = resp.read().decode("utf-8", errors="replace")
                    root = ET.fromstring(xml_data)
                    ns = {"atom": "http://www.w3.org/2005/Atom"}
                    for entry in root.findall("atom:entry", ns):
                        for link in entry.findall("atom:link", ns):
                            href = link.get("href", "")
                            if "arxiv.org/abs/" in href:
                                _enqueue_bonus(href, "arxiv")
                                arxiv_ok = True
                                break
                except Exception:
                    pass
                # Fallback: Semantic Scholar if arXiv returned nothing
                if not arxiv_ok:
                    try:
                        import urllib.request
                        encoded = urllib.parse.quote_plus(config.query)
                        api_url = f"https://api.semanticscholar.org/graph/v1/paper/search?query={encoded}&limit=10&fields=url,externalIds"
                        req = urllib.request.Request(api_url, headers={"User-Agent": "web-research-tool/1.0"})
                        with urllib.request.urlopen(req, timeout=3) as resp:
                            data = json.loads(resp.read().decode("utf-8", errors="replace"))
                        for paper in (data.get("data") or []):
                            ext_ids = paper.get("externalIds") or {}
                            arxiv_id = ext_ids.get("ArXiv")
                            if arxiv_id:
                                _enqueue_bonus(f"https://arxiv.org/abs/{arxiv_id}", "scholar")
                            else:
                                paper_id = paper.get("paperId", "")
                                if paper_id:
                                    _enqueue_bonus(f"https://www.semanticscholar.org/paper/{paper_id}", "scholar")
                    except Exception:
                        pass

            def _bonus_pubmed():
                """Search PubMed via NCBI E-utilities (free, no key)."""
                try:
                    import urllib.request
                    encoded = urllib.parse.quote_plus(config.query)
                    api_url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=pubmed&term={encoded}&retmax=5&retmode=json&sort=relevance"
                    req = urllib.request.Request(api_url, headers={"User-Agent": "web-research-tool/1.0"})
                    with urllib.request.urlopen(req, timeout=3) as resp:
                        data = json.loads(resp.read().decode("utf-8", errors="replace"))
                    for pmid in (data.get("esearchresult", {}).get("idlist") or []):
                        _enqueue_bonus(f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/", "pubmed")
                except Exception:
                    pass

            def _bonus_openalex():
                """Search OpenAlex for papers (free, no key for basic search)."""
                try:
                    import urllib.request
                    encoded = urllib.parse.quote_plus(config.query)
                    api_url = f"https://api.openalex.org/works?search={encoded}&per_page=5&mailto=web-research-tool@example.com"
                    req = urllib.request.Request(api_url, headers={"User-Agent": "web-research-tool/1.0"})
                    with urllib.request.urlopen(req, timeout=3) as resp:
                        data = json.loads(resp.read().decode("utf-8", errors="replace"))
                    for work in (data.get("results") or []):
                        # Prefer open access URL, then DOI, then landing page
                        oa = work.get("open_access") or {}
                        url = oa.get("oa_url")
                        if not url:
                            doi = work.get("doi")
                            if doi:
                                url = doi  # DOI URLs like https://doi.org/10.1234/...
                        if not url:
                            loc = work.get("primary_location") or {}
                            url = loc.get("landing_page_url")
                        if url:
                            _enqueue_bonus(url, "openalex")
                except Exception:
                    pass

            def _bonus_europepmc():
                """Search Europe PMC for papers (free, no key, more OA full-text than PubMed)."""
                try:
                    import urllib.request
                    encoded = urllib.parse.quote_plus(config.query)
                    api_url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/search?query={encoded}&format=json&pageSize=5&sort=CITED%20desc"
                    req = urllib.request.Request(api_url, headers={"User-Agent": "web-research-tool/1.0"})
                    with urllib.request.urlopen(req, timeout=3) as resp:
                        data = json.loads(resp.read().decode("utf-8", errors="replace"))
                    for result in (data.get("resultList", {}).get("result") or []):
                        # PMCID first: the scholarly route turns it into clean
                        # full text. A DOI resolves into the publisher wall this
                        # source exists to bypass, so it is the fallback.
                        url = None
                        pmcid = result.get("pmcid")
                        if pmcid:
                            url = f"https://europepmc.org/article/PMC/{pmcid}"
                        if not url:
                            doi = result.get("doi")
                            if doi:
                                url = f"https://doi.org/{doi}"
                            else:
                                pmid = result.get("pmid")
                                if pmid:
                                    url = f"https://europepmc.org/article/MED/{pmid}"
                        if url:
                            _enqueue_bonus(url, "europepmc")
                except Exception:
                    pass

            def _bonus_hackernews():
                """Search Hacker News via Algolia API (free, no key, 10K/hr)."""
                try:
                    import urllib.request
                    # Use top 3 key terms to avoid zero-result long queries
                    _hn_skip = {"best", "practices", "latest", "recent", "new", "how", "what",
                                "the", "and", "for", "with", "from", "using", "guide", "tutorial"}
                    words = [w for w in config.query.split() if w.lower() not in _hn_skip][:3]
                    hn_query = " ".join(words) if words else config.query
                    encoded = urllib.parse.quote_plus(hn_query)
                    api_url = f"https://hn.algolia.com/api/v1/search?query={encoded}&tags=story&hitsPerPage=5"
                    req = urllib.request.Request(api_url, headers={"User-Agent": "web-research-tool/1.0"})
                    with urllib.request.urlopen(req, timeout=3) as resp:
                        data = json.loads(resp.read().decode("utf-8", errors="replace"))
                    threads_added = 0
                    for hit in (data.get("hits") or []):
                        story_id = hit.get("objectID")
                        url = hit.get("url")
                        if url:
                            _enqueue_bonus(url, "hackernews")
                            # The discussion is where an article gets corrected.
                            # Bounded: two threads per query and enough comments
                            # to be worth a page, so the 20-page budget holds.
                            if (
                                story_id
                                and threads_added < HN_THREADS_PER_QUERY
                                and (hit.get("num_comments") or 0) >= HN_THREAD_MIN_COMMENTS
                            ):
                                _enqueue_bonus(
                                    f"https://news.ycombinator.com/item?id={story_id}",
                                    "hackernews-comments",
                                )
                                threads_added += 1
                        else:
                            # Ask HN / Show HN posts without external URL — link to HN discussion
                            if story_id:
                                _enqueue_bonus(f"https://news.ycombinator.com/item?id={story_id}", "hackernews")
                except Exception:
                    pass

            def _bonus_stackoverflow():
                """Search Stack Overflow API (free, no key, 300/day unauth)."""
                try:
                    import urllib.request
                    encoded = urllib.parse.quote_plus(config.query)
                    api_url = f"https://api.stackexchange.com/2.3/search/excerpts?order=desc&sort=relevance&q={encoded}&site=stackoverflow&pagesize=5&filter=default"
                    req = urllib.request.Request(api_url, headers={
                        "User-Agent": "web-research-tool/1.0",
                        "Accept-Encoding": "gzip",
                    })
                    with urllib.request.urlopen(req, timeout=3) as resp:
                        # SO API always returns gzip
                        raw = resp.read()
                        if resp.headers.get("Content-Encoding") == "gzip":
                            import gzip
                            raw = gzip.decompress(raw)
                        data = json.loads(raw.decode("utf-8", errors="replace"))
                    for item in (data.get("items") or []):
                        qid = item.get("question_id")
                        if qid:
                            _enqueue_bonus(f"https://stackoverflow.com/questions/{qid}", "stackoverflow")
                except Exception:
                    pass

            def _bonus_devto():
                """Search Dev.to API for articles (free, no key)."""
                try:
                    import urllib.request
                    encoded = urllib.parse.quote_plus(config.query)
                    # Dev.to doesn't have keyword search in API, but per_page+tag works
                    # Use page=1&per_page=5 with the query as tag approximation
                    # Actually, the /articles endpoint does support a hidden 'q' param via Forem
                    api_url = f"https://dev.to/api/articles?per_page=5&top=365"
                    # Try tag-based search with first keyword
                    words = config.query.split()
                    if words:
                        tag = re.sub(r'[^a-zA-Z0-9]', '', words[0]).lower()
                        if tag:
                            api_url += f"&tag={tag}"
                    req = urllib.request.Request(api_url, headers={"User-Agent": "web-research-tool/1.0"})
                    with urllib.request.urlopen(req, timeout=3) as resp:
                        data = json.loads(resp.read().decode("utf-8", errors="replace"))
                    for article in (data if isinstance(data, list) else []):
                        url = article.get("url")
                        if url:
                            _enqueue_bonus(url, "devto")
                except Exception:
                    pass

            def _bonus_github_repos():
                """Search GitHub repositories API (free, no key, 10/min unauth)."""
                try:
                    import urllib.request
                    encoded = urllib.parse.quote_plus(config.query)
                    api_url = f"https://api.github.com/search/repositories?q={encoded}&sort=stars&per_page=5"
                    req = urllib.request.Request(api_url, headers={
                        "User-Agent": "web-research-tool/1.0",
                        "Accept": "application/vnd.github.v3+json",
                    })
                    with urllib.request.urlopen(req, timeout=3) as resp:
                        data = json.loads(resp.read().decode("utf-8", errors="replace"))
                    for repo in (data.get("items") or []):
                        url = repo.get("html_url")
                        if url:
                            _enqueue_bonus(url, "github")
                except Exception:
                    pass

            bonus_fns = []
            if config.scientific:
                bonus_fns.extend([_bonus_arxiv, _bonus_openalex])
            if config.medical:
                bonus_fns.extend([_bonus_pubmed, _bonus_europepmc])
                if not config.scientific:
                    bonus_fns.append(_bonus_openalex)
            if config.tech:
                bonus_fns.extend([_bonus_hackernews, _bonus_stackoverflow, _bonus_devto, _bonus_github_repos])
            if bonus_fns:
                with ThreadPoolExecutor(max_workers=len(bonus_fns)) as bonus_pool:
                    list(bonus_pool.map(lambda f: f(), bonus_fns))
            status["search_urls"] = stats.urls_searched

        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                await loop.run_in_executor(executor, search_and_stream)

            search_elapsed = time.monotonic() - t0
            source_info = f"{stats.urls_searched} URLs (DDG)"
            if stats.bonus_sources:
                bonus_parts = [f"{v} {k}" for k, v in sorted(stats.bonus_sources.items())]
                source_info += f" + bonus: {', '.join(bonus_parts)}"
            if skipped:
                source_info += f", {skipped} filtered"
            progress.message(f"  [search] {source_info} in {search_elapsed:.1f}s")
        except Exception as e:
            search_elapsed = time.monotonic() - t0
            progress.message(f"  [search] failed after {search_elapsed:.1f}s: {e}")
        finally:
            await fetch_queue.put(None)

    async def fetch_consumer() -> None:
        semaphore = asyncio.Semaphore(config.max_concurrent)
        pending: List[asyncio.Task] = []
        fetch_limit = config.fetch_count

        async def fetch_one(url: str) -> None:
            async with semaphore:
                result = await fetch_single_async(
                    url, config.timeout,
                    config.min_content_length, config.max_content_length,
                    progress=progress, query=config.query,
                )
                await result_queue.put(result)

        while True:
            url = await fetch_queue.get()
            if url is None:
                break
            if fetch_limit == 0 or len(pending) < fetch_limit:
                pending.append(asyncio.create_task(fetch_one(url)))

        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        await result_queue.put(None)

    progress.phase_start("fetch")
    # Fresh per-run escalation budget (module counter shared across fetch tasks)
    global _BROWSER_ESCALATIONS
    _BROWSER_ESCALATIONS = 0
    asyncio.create_task(search_producer())
    asyncio.create_task(fetch_consumer())

    fetched = 0
    while True:
        result = await result_queue.get()
        if result is None:
            break
        fetched += 1
        if result.success:
            stats.urls_fetched += 1
            stats.content_chars += len(result.content)
        progress.update("fetch", fetched, stats.urls_searched or fetched)
        yield result

    progress.newline()
    progress.summary(stats.urls_fetched, stats.urls_searched, stats.content_chars)


# =============================================================================
# CROSS-PAGE DEDUPLICATION
# =============================================================================

# Common English stop words for fuzzy dedup signatures
_STOP_WORDS = frozenset(
    "a an the and or but in on at to for of is it its be by as was were are been "
    "has have had do does did will would shall should can could may might this that "
    "these those with from not no nor so if then than too also very just about above "
    "after before between each few more most other some such only own same through "
    "during until while into over under again further once here there when where why "
    "how all any both each which what who whom".split()
)


def _normalize_sentence(s: str) -> str:
    """Normalize a sentence for exact dedup: lowercase, strip punctuation, collapse whitespace."""
    s = s.lower().strip()
    s = re.sub(r'[^\w\s]', '', s)
    return RE_WHITESPACE.sub(' ', s)


def _content_signature(s: str) -> str:
    """Content-word signature for fuzzy dedup. Strips stop words, sorts remaining → key.
    Two sentences with the same content words in any order match."""
    words = sorted(w for w in s.lower().split() if w not in _STOP_WORDS and len(w) > 2)
    return " ".join(words)


@dataclass
class DedupStats:
    """Track dedup savings."""
    chars_before: int = 0
    chars_after: int = 0
    exact_dupes: int = 0
    fuzzy_dupes: int = 0
    pages_dropped: int = 0


def _dedup_results(
    results: List[FetchResult],
    seen: Optional[Set[str]] = None,
    seen_fuzzy: Optional[Set[str]] = None,
) -> Tuple[List[FetchResult], DedupStats]:
    """Remove duplicate sentences across pages (exact + fuzzy).
    Pass shared sets to dedup across multiple calls."""
    if seen is None:
        seen = set()
    if seen_fuzzy is None:
        seen_fuzzy = set()
    deduped: List[FetchResult] = []
    stats = DedupStats()

    for r in results:
        if not r.success:
            deduped.append(r)
            continue

        stats.chars_before += len(r.content)
        lines = r.content.split("\n")
        kept: List[str] = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                kept.append(line)
                continue
            # Always keep headers and metadata
            if stripped.startswith("# ") or stripped.startswith("[meta"):
                kept.append(line)
                continue
            # Skip very short lines (not worth deduping)
            if len(stripped) < 40:
                kept.append(line)
                continue
            # Exact dedup (normalized)
            norm = _normalize_sentence(stripped)
            if norm in seen:
                stats.exact_dupes += 1
                continue
            seen.add(norm)
            # Fuzzy dedup (content-word signature)
            sig = _content_signature(norm)
            if sig and len(sig) > 10 and sig in seen_fuzzy:
                stats.fuzzy_dupes += 1
                continue
            if sig and len(sig) > 10:
                seen_fuzzy.add(sig)
            kept.append(line)

        new_content = "\n".join(kept).strip()
        stats.chars_after += len(new_content)
        if len(new_content) < 50:
            stats.pages_dropped += 1
            continue
        deduped.append(FetchResult(
            url=r.url,
            success=True,
            content=new_content,
            title=r.title,
            source=r.source,
            kind=r.kind,
            images=r.images,
            date=r.date,
            date_source=r.date_source,
        ))

    # Log dedup effectiveness at debug level
    if stats.chars_before > 0 and (stats.exact_dupes or stats.fuzzy_dupes):
        saved_pct = 100 * (1 - stats.chars_after / stats.chars_before)
        logger.debug(
            f"Dedup: {stats.chars_before:,} → {stats.chars_after:,} chars "
            f"({saved_pct:.0f}% saved, {stats.exact_dupes} exact + {stats.fuzzy_dupes} fuzzy dupes, "
            f"{stats.pages_dropped} pages dropped)"
        )

    return deduped, stats


def _shingles(s: str, k: int = 3) -> Set[str]:
    """Word k-grams of a normalized sentence; whole sentence as one shingle if shorter."""
    words = s.split()
    if len(words) < k:
        return {s}
    return {" ".join(words[i:i + k]) for i in range(len(words) - k + 1)}


def _dedup_cross_page_sentences(
    results: List[FetchResult],
    jaccard_threshold: float = CROSS_PAGE_JACCARD_THRESHOLD,
    min_len: int = CROSS_PAGE_DEDUP_MIN_LEN,
) -> Tuple[List[FetchResult], dict]:
    """Cross-page near-duplicate sentence removal (F3), applied batch-wide in page order.

    Normalized exact matching always applies; shingle-Jaccard near-dup detection
    applies above jaccard_threshold (0.0 disables it). Only sentences already seen
    on EARLIER pages are dropped — legitimate within-page repeats are kept. Applies
    to verbatim pages too (they may contain cross-page mirrors). Never empties a
    page: at least its first non-header body line survives.
    Returns (results, stats dict).
    """
    seen_norm: Set[str] = set()
    seen_shingles: Dict[str, List[int]] = {}  # shingle -> ids of kept sentences sharing it
    stored_shingles: List[Set[str]] = []      # shingle set per kept sentence (id = index)
    stats = {"chars_before": 0, "chars_after": 0, "exact_dropped": 0, "near_dropped": 0}

    out: List[FetchResult] = []
    for r in results:
        if not r.success:
            out.append(r)
            continue
        lines = r.content.split("\n")
        kept_lines: List[str] = []
        page_norms: Set[str] = set()   # norms added by THIS page (never checked against)
        page_ids: Set[int] = set()     # ids added by THIS page (never checked against)
        stats["chars_before"] += len(r.content)

        for line in lines:
            stripped = line.strip()
            if not stripped:
                kept_lines.append(line)
                continue
            # Headers, metadata and short lines pass through
            if stripped.startswith("# ") or stripped.startswith("[meta"):
                kept_lines.append(line)
                continue
            if len(stripped) < min_len:
                kept_lines.append(line)
                continue
            norm = _normalize_sentence(stripped)
            if not norm:
                kept_lines.append(line)
                continue
            if norm in seen_norm:
                if norm in page_norms:
                    kept_lines.append(line)  # within-page repeat — keep
                else:
                    stats["exact_dropped"] += 1
                continue
            if jaccard_threshold > 0.0:
                sh = _shingles(norm)
                candidates: Set[int] = set()
                for shg in sh:
                    for cid in seen_shingles.get(shg, ()):
                        if cid not in page_ids:
                            candidates.add(cid)
                dup = False
                for cid in candidates:
                    a = stored_shingles[cid]
                    inter = len(sh & a)
                    union = len(sh | a)
                    if union and inter / union > jaccard_threshold:
                        dup = True
                        break
                if dup:
                    stats["near_dropped"] += 1
                    continue
                if sh:
                    sid = len(stored_shingles)
                    for shg in sh:
                        seen_shingles.setdefault(shg, []).append(sid)
                    stored_shingles.append(sh)
                    page_ids.add(sid)
            seen_norm.add(norm)
            page_norms.add(norm)
            kept_lines.append(line)

        # Never empty a page: ensure at least one non-header body line survives
        if not any(l.strip() and not l.strip().startswith(("# ", "[meta")) for l in kept_lines):
            for line in lines:
                stripped = line.strip()
                if stripped and not stripped.startswith(("# ", "[meta")):
                    kept_lines.append(line)
                    break
        new_content = "\n".join(kept_lines).strip()
        stats["chars_after"] += len(new_content)
        if not new_content:
            continue
        out.append(FetchResult(
            url=r.url, success=True, content=new_content,
            title=r.title, source=r.source, kind=r.kind,
            images=r.images, date=r.date, date_source=r.date_source,
        ))

    # Log dedup effectiveness at debug level
    if stats["chars_before"] > 0 and (stats["exact_dropped"] or stats["near_dropped"]):
        saved_pct = 100 * (1 - stats["chars_after"] / stats["chars_before"])
        logger.debug(
            f"Cross-page dedup: {stats['chars_before']:,} → {stats['chars_after']:,} chars "
            f"({saved_pct:.2f}% saved, {stats['exact_dropped']} exact + "
            f"{stats['near_dropped']} near-dup drops)"
        )

    return out, stats


# =============================================================================
# QUALITY FILTERS F5, F7 (batch path)
# =============================================================================
# F5: cross-domain content-farm / syndication detection (page-level).
# F7: recency filter (stale pages on recency-sensitive queries).
# All are conservative, fail-soft (errors -> no filtering), and operate
# ONLY on the batch search path (never on single --url fetches).


def _registrable_domain(netloc: str) -> str:
    """Registrable domain: last two labels (www.example.co.uk -> co.uk is coarse
    but consistent with the tool's existing domain handling)."""
    labels = netloc.lower().split(".")
    if len(labels) >= 2:
        return ".".join(labels[-2:])
    return netloc


def _page_body_signature(content: str, limit: int = FARM_SIG_CHARS) -> str:
    """Normalized body text used for page-level similarity: strips header/meta
    lines, lowercases, removes punctuation, collapses whitespace, caps length."""
    lines = []
    for line in content.split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith("# ") or stripped.startswith("[meta"):
            continue
        lines.append(stripped)
    body = " ".join(lines)[:limit]
    body = _normalize_sentence(body)
    return body


def _shingle_jaccard(a: str, b: str) -> float:
    """Shingle-Jaccard similarity between two normalized texts."""
    if not a or not b:
        return 0.0
    sa = _shingles(a)
    sb = _shingles(b)
    inter = len(sa & sb)
    union = len(sa | sb)
    if not union:
        return 0.0
    return inter / union


def _extract_byline_names(content: str) -> Set[str]:
    """Author names from byline/author-bio patterns ('By X', 'About X',
    'written by X', 'I'm X —', 'By Dr. X'). Lowercased, deduped."""
    names: Set[str] = set()
    for pat in FARM_BYLINE_PATTERNS:
        for m in pat.finditer(content[:4000]):
            raw = m.group(1).strip()
            name = raw.lower()
            # Require a plausible personal name (at least 2 alpha words or 1 long word)
            words = [w for w in re.split(r"\s+", name) if w.isalpha()]
            if not words:
                continue
            if len(words) >= 2 or len(name) >= 5:
                names.add(name)
    return names


def _has_farm_boilerplate(content: str) -> bool:
    """True if the page carries farm-network self-promotion phrases."""
    return any(pat.search(content) for pat in FARM_NETWORK_PATTERNS)


def _page_cross_links(content: str, group_netlocs: List[str]) -> bool:
    """True if the page text mentions another group member's registrable domain
    (the 'read on our network' cross-link pattern)."""
    lower = content.lower()
    for netloc in group_netlocs:
        reg = _registrable_domain(netloc)
        # Match bare domain or a URL containing it
        if reg in lower and reg not in {"com", "net", "org"}:
            return True
    return False


def _dedup_farm_pages(
    results: List[FetchResult],
    jaccard_threshold: float = FARM_PAGE_JACCARD_THRESHOLD,
    min_pages: int = FARM_GROUP_MIN_PAGES,
    min_domains: int = FARM_GROUP_MIN_DOMAINS,
    min_signals: int = FARM_MIN_SIGNALS,
    hard_signals: int = FARM_HARD_SIGNALS,
) -> Tuple[List[FetchResult], dict]:
    """F5: drop cross-domain content-farm / syndication groups (page level).

    Groups pages whose body-text shingle-Jaccard similarity exceeds
    jaccard_threshold AND whose registrable domains differ. A group is a farm
    candidate only when it spans >= min_pages pages across >= min_domains
    distinct domains (near-identical content across unrelated domains). To
    actually DROP pages, the group must carry >= min_signals independent
    signals: (a) multi-domain near-dup (inherent), (b) byline/author
    repetition, (c) farm boilerplate phrases, (d) cross-links between members.

    Canonical-origin rule: with 2+ signals the redundant copies are dropped but
    the first-fetched representative (canonical) is kept — a copy that looks
    like legitimate syndication (same article mirrored on a partner site) is
    never the sole survivor. With >= hard_signals (confirmed PBN: byline +
    boilerplate + cross-links all present) the whole group is dropped.
    When in doubt (fewer than min_signals) nothing is dropped.

    Fail-soft: on any error returns the input unchanged.
    Returns (results, stats dict).
    """
    try:
        if not QUALITY_FILTERS_ENABLED:
            return results, {"farm_dropped": 0, "groups": 0}
        ok = [r for r in results if r.success]
        if len(ok) < min_pages:
            return results, {"farm_dropped": 0, "groups": 0}

        # Signatures for the pages we can compare
        sigs: List[Optional[str]] = []
        netlocs: List[str] = []
        for r in results:
            if r.success:
                sigs.append(_page_body_signature(r.content))
                netlocs.append(urllib.parse.urlparse(r.url).netloc)
            else:
                sigs.append(None)
                netlocs.append("")

        # Union-find over near-dup pairs from DIFFERENT registrable domains
        parent = list(range(len(results)))

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: int, b: int) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        for i in range(len(results)):
            if not results[i].success or not sigs[i]:
                continue
            for j in range(i + 1, len(results)):
                if not results[j].success or not sigs[j]:
                    continue
                if _registrable_domain(netlocs[i]) == _registrable_domain(netlocs[j]):
                    continue
                if _shingle_jaccard(sigs[i], sigs[j]) >= jaccard_threshold:
                    union(i, j)

        # Group members by root
        groups: Dict[int, List[int]] = {}
        for i in range(len(results)):
            if results[i].success:
                groups.setdefault(find(i), []).append(i)

        dropped: Set[int] = set()
        group_count = 0
        for members in groups.values():
            if len(members) < min_pages:
                continue
            domains = {_registrable_domain(netlocs[i]) for i in members}
            if len(domains) < min_domains:
                continue
            group_count += 1

            # Signal (a): multi-domain near-dup — inherent for eligible groups.
            # Signal (b): byline repetition — same author on >= 2 members.
            byline_sets = [_extract_byline_names(results[i].content) for i in members]
            common_bylines: Set[str] = set()
            for s in byline_sets:
                for name in s:
                    if sum(1 for s2 in byline_sets if name in s2) >= 2:
                        common_bylines.add(name)
            signal_b = bool(common_bylines)

            # Signal (c): farm boilerplate on any member.
            signal_c = any(_has_farm_boilerplate(results[i].content) for i in members)

            # Signal (d): cross-links — any member mentions another member's domain.
            member_netlocs = [netlocs[i] for i in members]
            signal_d = any(
                _page_cross_links(results[i].content, [n for n in member_netlocs if n != netlocs[i]])
                for i in members
            )

            signals = (1 if signal_b else 0) + (1 if signal_c else 0) + (1 if signal_d else 0)
            if signals >= hard_signals:
                dropped.update(members)
            elif signals >= min_signals:
                # Keep the first-fetched (canonical) representative; drop copies.
                dropped.update(members[1:])

        if not dropped:
            return results, {"farm_dropped": 0, "groups": group_count}

        out: List[FetchResult] = []
        for i, r in enumerate(results):
            if i in dropped:
                continue
            out.append(r)
        return out, {"farm_dropped": len(dropped), "groups": group_count}
    except Exception:
        return results, {"farm_dropped": 0, "groups": 0}


# --- Stub-page rule (structural) --------------------------------------------

def _is_stub_page(content: str) -> bool:
    """True when a page has essentially no body: the extracted content is small
    AND contains nothing beyond its own title/heading (forum/social
    homepages, empty product listings). Both conditions must hold so a small
    page with real body text is never treated as a stub. Fail-soft: content
    that cannot be measured is kept."""
    if not isinstance(content, str) or not content:
        return False
    total = len(content)
    if total >= STUB_MAX_TOTAL_CHARS:
        return False
    body_chars = 0
    in_meta = False
    for line in content.split("\n"):
        s = line.strip()
        if not s:
            continue
        if s.startswith("[meta"):
            in_meta = True
            continue
        if in_meta:
            if "[/meta]" in s:
                in_meta = False
            continue
        if s.startswith(("# ", "## ", "### ")):
            continue
        body_chars += len(s)
    return body_chars < STUB_MAX_BODY_CHARS


def _drop_stub_pages(
    results: List[FetchResult],
) -> Tuple[List[FetchResult], dict]:
    """Drop stub pages (structural, no model). Gated by QUALITY_FILTERS_ENABLED,
    batch path only. Fail-soft: on any error the page set is unchanged.
    Returns (results, stats dict with 'stub_dropped')."""
    stats: dict = {"stub_dropped": 0}
    try:
        if not QUALITY_FILTERS_ENABLED:
            return results, stats
        drop_ids = {
            id(r) for r in results
            # A structured feed record is machine-generated, not page chrome:
            # a one-entry feed is thin but still content.
            if r.success and r.kind != "feed" and _is_stub_page(r.content)
        }
        if not drop_ids:
            return results, stats
        stats["stub_dropped"] = len(drop_ids)
        return [r for r in results if id(r) not in drop_ids], stats
    except Exception:
        logger.debug("stub filter failed; skipping")
        return results, stats


# --- F7: recency filter -------------------------------------------------------

_MONTH_NAMES = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)
_RE_MONTH = re.compile(
    r"\b(?:January|February|March|April|May|June|July|August|"
    r"September|October|November|December)\b",
    re.IGNORECASE,
)
_RE_ISO_DATE = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")
_RE_MONTH_DAY_YEAR = re.compile(
    r"\b([A-Z][a-z]{2,8})\s+(\d{1,2}),?\s+(\d{4})\b"
)
_RE_DAY_MONTH_YEAR = re.compile(
    r"\b(\d{1,2})\s+([A-Z][a-z]{2,8})\s+(\d{4})\b"
)
_RE_URL_DATE = re.compile(r"/(20\d{2})/(\d{1,2})/(\d{1,2})/")
_RE_QUERY_YEAR = re.compile(r"\b(19|20)\d{2}\b")


def _page_date_with_source(content: str, url: str) -> Tuple[Optional[object], str]:
    """Best-effort publication date plus provenance.

    Priority chain (all conservative, fail-soft): [meta] published/updated
    lines written by the JSON-LD extractor -> visible month-name byline dates
    near the top -> URL path /YYYY/MM/DD. Never trusts HTTP Last-Modified (CDNs
    reset it) and never parses arbitrary YYYY-MM-DD tokens in the body (they
    match version strings, citations, junk). Returns (datetime.date, source)
    where source is "jsonld-pub" | "jsonld-upd" | "visible" | "url"; (None, "")
    when undated. The recency filter F7 consumes only the date; the digest uses
    the source to mark heuristic dates.
    """
    from datetime import datetime
    head = content[:2000]

    # 1. [meta] blocks produced by extract_jsonld_metadata (datePublished/dateModified).
    #    Two emitted shapes: single-part "[meta] published: YYYY-MM-DD" (one part,
    #    no closer) and multi-part "[meta]\npublished: ...\nupdated: ...\n[/meta]"
    #    (parts on separate lines, closed with [/meta]). Scan each whole block so
    #    dates on later lines are found; published keeps priority over updated.
    #    The block boundary ([/meta] closer, or the line end for the single-part
    #    form) keeps body-text dates from matching. They are normally prepended
    #    at the top, but scan the whole content — the marker prefix makes false
    #    matches in body text essentially impossible.
    for label, source in (("published", "jsonld-pub"), ("updated", "jsonld-upd")):
        pat = re.compile(rf"\b{label}:\s*(\d{{4}})-(\d{{1,2}})-(\d{{1,2}})", re.IGNORECASE)
        pos = 0
        while True:
            s = content.find("[meta]", pos)
            if s == -1:
                break
            closer = content.find("[/meta]", s)
            if closer != -1:
                block = content[s:closer + len("[/meta]")]
                pos = closer + len("[/meta]")
            else:
                nl = content.find("\n", s)
                block = content[s:nl if nl != -1 else len(content)]
                pos = nl if nl != -1 else len(content)
            m = pat.search(block)
            if m:
                try:
                    d = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date()
                    return d, source
                except ValueError:
                    pass

    # 2. Visible byline dates near the top ("March 19, 2026", "19 March 2026")
    m = _RE_MONTH_DAY_YEAR.search(head)
    if m and m.group(1).lower()[:3] in {mo[:3].lower() for mo in _MONTH_NAMES}:
        try:
            month = next(i + 1 for i, mo in enumerate(_MONTH_NAMES) if mo.lower()[:3] == m.group(1).lower()[:3])
            d = datetime(int(m.group(3)), month, int(m.group(2))).date()
            return d, "visible"
        except ValueError:
            pass
    m = _RE_DAY_MONTH_YEAR.search(head)
    if m and m.group(2).lower()[:3] in {mo[:3].lower() for mo in _MONTH_NAMES}:
        try:
            month = next(i + 1 for i, mo in enumerate(_MONTH_NAMES) if mo.lower()[:3] == m.group(2).lower()[:3])
            d = datetime(int(m.group(3)), month, int(m.group(1))).date()
            return d, "visible"
        except ValueError:
            pass

    # 3. URL path /YYYY/MM/DD (common on news/blog platforms)
    m = _RE_URL_DATE.search(url)
    if m:
        try:
            d = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date()
            return d, "url"
        except ValueError:
            pass

    return None, ""


def _extract_page_date(content: str, url: str) -> Optional[object]:
    """Date-only wrapper (used by the recency filter F7; behaviour unchanged)."""
    return _page_date_with_source(content, url)[0]


# Meta tags carry publication dates that the JSON-LD extractor never sees.
# Keys are lowercased attribute values of property/name/itemprop.
_META_PUBLISHED_KEYS = frozenset({
    "article:published_time", "og:article:published_time",
    "datepublished", "date", "dc.date", "dc.date.issued", "dcterms.date",
    "dcterms.created", "pubdate", "publishdate", "publication_date",
    "citation_publication_date", "citation_date",
})
_META_UPDATED_KEYS = frozenset({
    "article:modified_time", "og:article:updated_time", "datemodified",
    "dcterms.modified", "last-modified", "revised",
})
_RE_ISO_DAY = re.compile(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})")


def _iso_day(value: str) -> str:
    """ISO day from a date-ish string; "" when absent or implausible."""
    from datetime import datetime as _dt
    m = _RE_ISO_DAY.search(value or "")
    if not m:
        return ""
    try:
        d = _dt(int(m.group(1)), int(m.group(2)), int(m.group(3))).date()
    except ValueError:
        return ""
    # Reject junk years (licence years in footers, placeholder 0000/9999 dates).
    # 1970 keeps dates on classic papers and old docs while still rejecting
    # placeholders; the upper bound tolerates a slightly future-dated page.
    if not (1970 <= d.year <= _dt.now().year + 1):
        return ""
    return d.isoformat()


def _extract_meta_dates(raw_html: str) -> Tuple[str, str]:
    """(published, updated) ISO days from <meta> tags and <time datetime>.

    Scans only the first 16 KB (the head), never the body: body dates match
    version strings and comments. Fail-soft: ("", "") when nothing is found.
    """
    head = (raw_html or "")[:16384]
    pub = ""
    upd = ""
    for tag in re.findall(r"<meta\b[^>]*>", head, re.I):
        attrs = _img_tag_attrs(tag)
        key = (attrs.get("property") or attrs.get("name") or attrs.get("itemprop") or "").strip().lower()
        if not key:
            continue
        day = _iso_day(attrs.get("content") or attrs.get("datetime") or "")
        if not day:
            continue
        if not pub and key in _META_PUBLISHED_KEYS:
            pub = day
        elif not upd and key in _META_UPDATED_KEYS:
            upd = day
    if not pub:
        m = re.search(r"<time\b[^>]*\bdatetime\s*=\s*[\"']([^\"']+)", head, re.I)
        if m:
            pub = _iso_day(m.group(1))
    return pub, upd


def _resolve_page_date(raw_html: str, content: str, url: str) -> Tuple[str, str]:
    """Destination date for the digest: (ISO day, source).

    Priority: published over modified, structured over heuristic —
    meta published > JSON-LD published > visible byline > URL > meta modified >
    JSON-LD modified. Informational only — never fed to the recency filter.
    """
    pub_meta, upd_meta = _extract_meta_dates(raw_html)
    d, source = _page_date_with_source(content, url)
    if pub_meta:
        return pub_meta, "meta"
    if source == "jsonld-pub":
        return d.isoformat(), "jsonld"
    if source in ("visible", "url"):
        return d.isoformat(), source
    if upd_meta:
        return upd_meta, "modified"
    if source == "jsonld-upd":
        return d.isoformat(), "modified"
    return "", ""


def _format_digest_date(iso: str, source: str) -> str:
    """Digest marker: exact, heuristic, or updated-only. "" when undated."""
    if not iso:
        return ""
    if source == "modified":
        return f"[upd {iso}]"
    if source in ("visible", "url"):
        return f"[~{iso}]"
    return f"[{iso}]"


def _is_recency_sensitive_query(query: str) -> bool:
    """True when the query demands fresh content (recency filter applies).

    Evergreen queries (history, why do, how to, explained, ethics, tutorial,
    guide, what is...) never get recency drops. Otherwise a year in the query
    or a time-sensitive word (news, latest, update, release, benchmark,
    breaking changes, vs, ...) makes the query recency-sensitive.
    """
    q = query.lower()
    if any(w in q for w in RECENCY_EVERGREEN_WORDS):
        return False
    if _RE_QUERY_YEAR.search(query):
        return True
    return any(w in q for w in RECENCY_SENSITIVE_WORDS)


def _recency_filter(results: List[FetchResult], query: str) -> Tuple[List[FetchResult], dict]:
    """F7: drop stale pages on recency-sensitive queries.

    Pages with an extractable date older than RECENCY_MAX_AGE_DAYS are dropped
    (or older than the year named in the query when the query names one).
    Pages with NO extractable date are always kept (fail-soft: an undated page
    cannot be proven stale). If >= 50% of the batch is undated, recency is
    non-applicable and nothing is dropped (matches the tool's fail-soft
    philosophy — never empty the digest on missing data).
    Returns (results, stats dict).
    """
    stats: dict = {"stale_dropped": 0, "recency_skipped": False}
    try:
        if not QUALITY_FILTERS_ENABLED or not query:
            return results, stats
        if not _is_recency_sensitive_query(query):
            return results, stats

        pages = [r for r in results if r.success]
        if len(pages) < 2:
            return results, stats

        dates = [_extract_page_date(r.content, r.url) for r in pages]
        undated = sum(1 for d in dates if d is None)
        if undated / len(pages) >= RECENCY_UNDATED_MAX_FRACTION:
            stats["recency_skipped"] = True
            return results, stats

        from datetime import datetime, timedelta
        now = datetime.now()
        cutoff_days = RECENCY_MAX_AGE_DAYS
        q_year = None
        ym = _RE_QUERY_YEAR.search(query)
        if ym:
            q_year = int(ym.group(0))

        drop_idx: Set[int] = set()
        for r, d in zip(pages, dates):
            if d is None:
                continue
            age_days = (now.date() - d).days
            if age_days < 0 or age_days > 60 * 365:  # clamp: future/junk dates
                continue
            if q_year:
                # Older than the year named in the query — with a 1-year grace so
                # a 2025 page on a "2026" query (fresh, months old) is never dropped.
                stale = d.year < q_year - 1
            else:
                stale = age_days > cutoff_days
            if stale:
                drop_idx.add(id(r))

        if not drop_idx:
            return results, stats
        stats["stale_dropped"] = len(drop_idx)
        return [r for r in results if id(r) not in drop_idx], stats
    except Exception:
        logger.debug("recency filter failed; skipping")
        return results, stats


# =============================================================================
# BATCH OUTPUT FORMATTERS (for non-streaming mode)
# =============================================================================

def format_batch_raw(results: List[FetchResult]) -> str:
    """Format all results as raw text (optimized with StringIO)."""
    buffer = StringIO()
    for r in results:
        if r.success:
            buffer.write(f"=== {r.url} ===\n")
            images_line = _images_line(r.images)
            if images_line:
                buffer.write(images_line)
            buffer.write(r.content)
            buffer.write("\n\n")
    return buffer.getvalue()


# =============================================================================
# DIGEST + REPORT FILE OUTPUT (search path)
# =============================================================================

def _report_root() -> Path:
    """Repo root resolved from the script location: .opencode/tools/ -> two dirs up."""
    return Path(__file__).resolve().parent.parent.parent


def _report_dir() -> Path:
    """Report directory: <REPO_ROOT>/tmp/webresearch/."""
    return _report_root() / "tmp" / REPORT_DIR_NAME


def _query_slug(query: str) -> str:
    """Sanitized query for the run-id: lowercase alnum, '-' separators, max ~40 chars."""
    slug = re.sub(r"[^a-z0-9]+", "-", query.lower()).strip("-")
    return slug[:40] or "query"


def _make_run_id(query: str) -> str:
    """YYYYMMDD-HHMMSS-<query-slug>-<rand4>; rand4 = random alnum for parallel-safety."""
    rand4 = "".join(random.choices(string.ascii_lowercase + string.digits, k=4))
    return f"{time.strftime('%Y%m%d-%H%M%S')}-{_query_slug(query)}-{rand4}"


def _rotate_report_files() -> None:
    """Prune <REPO_ROOT>/tmp/webresearch/*.txt: delete files older than 7 days,
    keep only the 30 newest regardless. Fail-soft."""
    try:
        report_dir = _report_dir()
        if not report_dir.is_dir():
            return
        files = sorted(report_dir.glob("*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
        cutoff = time.time() - REPORT_MAX_AGE_DAYS * 86400
        fresh: List[Path] = []
        for f in files:
            try:
                is_old = f.stat().st_mtime < cutoff
            except OSError:
                is_old = False
            if is_old:
                try:
                    f.unlink()
                except OSError:
                    pass
            else:
                fresh.append(f)
        for f in fresh[REPORT_MAX_FILES:]:
            try:
                f.unlink()
            except OSError:
                pass
    except Exception:
        pass


def _ensure_report_dir() -> None:
    """mkdir the report dir at startup + rotate old files. Fail-soft."""
    try:
        _report_dir().mkdir(parents=True, exist_ok=True)
        _rotate_report_files()
    except Exception:
        pass


def _prepare_report_pages(results: List[FetchResult]) -> List[Tuple[FetchResult, str, bool]]:
    """FILTERED FULL TEXT per successful result, in final (deduped) order.

    Only quality-preserving filters: F4 junk-section removal + F1 boilerplate
    sentence removal, original order. No BM25 selection, no 10k budget cut, no
    fact-density re-ranking — the file must be grep-safe for terms the query
    never mentioned. Per-page safety ceiling at REPORT_PAGE_CEILING chars.
    Third tuple element = whether the text was truncated at that ceiling.
    """
    pages: List[Tuple[FetchResult, str, bool]] = []
    for r in results:
        if not r.success:
            continue
        # Structured records (feed entry lists, HN threads) are already clean:
        # F4 would read entry titles as section headings and drop entries whose
        # title or summary contains a word like "comments" or "read more".
        text = r.content if r.kind in ("feed", "hn") else _filter_page_text(r.content)
        if not text:
            continue
        truncated = len(text) > REPORT_PAGE_CEILING
        if truncated:
            text = text[:REPORT_PAGE_CEILING] + "\n[truncated at 200k chars]"
        pages.append((r, text, truncated))
    return pages


def _build_report_file(pages: List[Tuple[FetchResult, str, bool]]) -> Tuple[str, List[int]]:
    """Full report text: `=== <url> ===` + filtered text per page.

    Returns (content, start_lines): start_lines[i] is the 1-based line number
    of the i-th page's `=== <url> ===` header within the returned content.
    Line accounting must be exact — the digest's @line/@hit locators are
    computed from these values (never by the model).
    """
    buffer = StringIO()
    start_lines: List[int] = []
    line = 1
    for r, text, _ in pages:
        start_lines.append(line)
        segment = f"=== {r.url} ===\n" + _images_line(r.images) + text + "\n\n"
        buffer.write(segment)
        # splitlines() counts consumed lines exactly (trailing-newline cases
        # and empty separator lines included) — @line/@hit depend on this.
        line += len(segment.splitlines())
    return buffer.getvalue(), start_lines


def _human_chars(n: int) -> str:
    """Compact char count for the digest: 14200 -> '14.2K', 900 -> '900'."""
    if n >= 1000:
        return f"{n / 1000:.1f}K"
    return str(n)


QUERY_STOPWORDS = frozenset(
    """the a an and or but for in on of to with from by at as is are was were be been
    being do does did can could should would may might will shall not no yes so if
    then than that this these those it its you your we our they their he she him her
    them i me my what which who whom when where why how about into over under between
    through during before after above below again further once here there all any both
    each few more most other some such only own same too very just also has have had""".split()
)


def _query_key_terms(query: str) -> List[str]:
    r"""Up to 2 significant query terms for @hit locators; [] when none.

    `\w` (unicode-aware) keeps non-English queries working (Cyrillic/CJK);
    the 3-char minimum skips particles, and `+`/non-word chars split tokens
    (so "C++" yields no term by itself but "C++ pitfalls" yields "pitfalls").
    """
    terms: List[str] = []
    for word in re.findall(r"\w[\w-]{2,}", query.lower()):
        if word not in QUERY_STOPWORDS and word not in terms:
            terms.append(word)
        if len(terms) == 2:
            break
    return terms


def _find_hit_line(text: str, terms: List[str]) -> Optional[int]:
    """1-based line within `text` of the first line containing any key term."""
    if not terms:
        return None
    for idx, line in enumerate(text.splitlines(), 1):
        low = line.lower()
        if any(t in low for t in terms):
            return idx
    return None


def _build_digest(
    query: str,
    pages: List[Tuple[FetchResult, str, bool]],
    failures: List[FetchResult],
    report_path: Path,
    quality_stats: Optional[dict] = None,
    start_lines: Optional[List[int]] = None,
    terms: Optional[List[str]] = None,
    line_offset: int = 0,
    since: str = "",
) -> str:
    """Technical index digest: FULL REPORT path FIRST and LAST, stats, one
    technical line per page (`N. [size] [date] [trunc] @line L @hit H — Title — URL`,
    the date field omitted when the page is undated).

    start_lines: 1-based `=== <url> ===` header lines relative to the report
    content; line_offset: lines the digest + separator occupy at the top of the
    final file — so @line/@hit values are absolute in the written report file.
    The digest is small by design (~25 lines): nothing to trim.

    quality_stats (from F5/F7) is appended to the stats line as extra
    counters; the base format is unchanged so downstream consumers keep working.
    """
    total_chars = sum(len(text) for _, text, _ in pages)
    reasons = list(dict.fromkeys(r.error for r in failures if r.error))
    reason_part = f" ({', '.join(reasons)})" if reasons else ""
    quality_part = ""
    if quality_stats:
        qparts = []
        if quality_stats.get("farm_dropped"):
            qparts.append(f"farm-dropped: {quality_stats['farm_dropped']}")
        if quality_stats.get("stub_dropped"):
            qparts.append(f"stub-dropped: {quality_stats['stub_dropped']}")
        if quality_stats.get("stale_dropped"):
            qparts.append(f"stale-dropped: {quality_stats['stale_dropped']}")
        # Always visible (even at 0): pages dropped below the 50-char floor by
        # cross-page dedup used to be completely silent; hiding the counter at
        # 0 would recreate that silence in the common case. Appended; existing
        # counters unchanged (backward-compatible).
        qparts.append(f"dedup-dropped: {quality_stats.get('dedup_dropped', 0)}")
        if qparts:
            quality_part = " | " + " | ".join(qparts)
    full_report_line = f"FULL REPORT: {report_path} — grep or read this file for full content"
    since_part = f" | since: {since}" if since else ""
    lines = [
        full_report_line,
        f"RESEARCH: {query}",
        f"Results: {len(pages)} fetched, {len(failures)} failed{reason_part} | {total_chars:,} chars{quality_part}{since_part} | ordered by relevance",
    ]
    for i, (r, text, truncated) in enumerate(pages, 1):
        size = f"[{_human_chars(len(text))}{' trunc' if truncated else ''}]"
        date_part = f" {_format_digest_date(r.date, r.date_source)}" if r.date else ""
        base = start_lines[i - 1] + line_offset if start_lines else 0
        # A `[images] …` line sits between the header and the page text, so a
        # relative text hit shifts by one before it is made absolute.
        img_offset = 1 if r.images else 0
        hit = _find_hit_line(text, terms) if terms else None
        hit_part = f" @hit {base + img_offset + hit}" if hit else ""
        if r.title:
            lines.append(f"{i}. {size}{date_part} @line {base}{hit_part} — {r.title} — {r.url}")
        else:
            lines.append(f"{i}. {size}{date_part} @line {base}{hit_part} — {r.url}")
    lines.append(full_report_line)
    return "\n".join(lines)


def _assemble_report(
    query: str,
    pages: List[Tuple[FetchResult, str, bool]],
    failures: List[FetchResult],
    report_path: Path,
    quality_stats: Optional[dict],
    since: str = "",
) -> Tuple[str, str]:
    """Return (digest, report_content) for one run.

    The report file = the digest (index) + a `---` separator + the page
    content, so the artifact is self-contained: the stdout digest and the file
    head are identical, and @line/@hit values are absolute in the final file.
    The offset (digest lines + separator) is computed from a probe digest —
    line COUNT does not depend on the values, so the second build is exact.
    """
    content, start_lines = _build_report_file(pages)
    terms = _query_key_terms(query)
    probe = _build_digest(query, pages, failures, report_path, quality_stats, start_lines, terms, 0, since)
    offset = len(probe.splitlines()) + 1  # +1 = the `---` separator line
    digest = _build_digest(query, pages, failures, report_path, quality_stats, start_lines, terms, offset, since)
    return digest, digest + "\n---\n" + content


def _write_report_file(path: Path, content: str) -> bool:
    """Write the report file. Returns False on failure (caller falls back to inline raw)."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return True
    except Exception:
        return False


# =============================================================================
# MAIN ENTRY POINTS (modes: search, --map, --cite)
# =============================================================================

# ---------------------------------------------------------------------------
# --map: list URLs on a site/subtree — no page fetching
# ---------------------------------------------------------------------------

# Fixed caps (no user-facing count flags, per the tool's tuned-defaults rule).
MAP_MAX_URLS: int = 100
MAP_MAX_SITEMAPS: int = 5            # sitemap documents fetched per run (index + children)
MAP_MAX_DOC_BYTES: int = 5_000_000   # per fetched document
MAP_MAX_TOTAL_BYTES: int = 10_000_000

_RE_SITEMAP_HINT = re.compile(r"(?:^|[/._-])sitemap", re.IGNORECASE)


def _map_fetch_doc(url: str, budget: List[int]) -> Optional[str]:
    """Fetch one sitemap/robots/page document: gzip-aware, capped, entity-guarded."""
    raw = _http_get_bytes(url, timeout=8, max_bytes=MAP_MAX_DOC_BYTES)
    if not raw:
        return None
    budget[0] -= len(raw)
    if budget[0] < 0:
        return None
    if raw[:2] == b"\x1f\x8b":
        import zlib
        try:
            # Bounded inflate: a small gzip can expand to gigabytes, so cap the
            # OUTPUT (gzip.decompress would allocate it all first).
            raw = zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(
                raw, MAP_MAX_DOC_BYTES + 1
            )
        except zlib.error:
            return None
        if len(raw) > MAP_MAX_DOC_BYTES:
            return None
        budget[0] -= len(raw)
        if budget[0] < 0:
            return None
    # Entity-expansion guard: a sitemap never needs an internal DTD.
    if b"<!ENTITY" in raw[:100_000]:
        return None
    return raw.decode("utf-8", errors="replace")


def _parse_sitemap(text: str) -> Tuple[List[str], List[str]]:
    """(page_urls, child_sitemaps) from a sitemap or sitemap index. ([], []) on error."""
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(text)
    except Exception:
        return [], []

    def locs(node) -> List[str]:
        out: List[str] = []
        for child in node:
            tag = child.tag.rsplit("}", 1)[-1].lower()
            if tag == "loc" and (child.text or "").strip():
                out.append(child.text.strip())
        return out

    urls: List[str] = []
    children: List[str] = []
    for node in root:
        tag = node.tag.rsplit("}", 1)[-1].lower()
        if tag == "sitemap":
            children.extend(locs(node))
        elif tag == "url":
            urls.extend(locs(node))
    return urls, children


def _crawl_scope(path: str) -> Tuple[str, str]:
    """(prefix, exact) for a path — the docs-subtree rule.

    /docs/foo/bar    -> ("/docs/foo/bar/", "/docs/foo/bar")
    /docs/guide.html -> ("/docs/", "")
    /docs/widgets    -> ("/docs/widgets/", "/docs/widgets")
    /                -> ("/", "")
    """
    if not path:
        return "/", ""
    if not path.startswith("/"):
        path = "/" + path
    if path.endswith("/"):
        return path, ""
    last = path.rsplit("/", 1)[-1]
    if "." in last:
        parent = path.rsplit("/", 1)[0]
        return (parent + "/") if parent else "/", ""
    return path + "/", path


def _in_scope(url: str, host: str, prefix: str, exact: str) -> bool:
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        return False
    if parsed.netloc.lower() != host:
        return False
    return parsed.path == exact or parsed.path.startswith(prefix)


class _AnchorParser(HTMLParser):
    """Collect <a href> values without a third-party HTML parser."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hrefs: List[str] = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "a":
            return
        for name, value in attrs:
            if name and name.lower() == "href" and value:
                self.hrefs.append(value.strip())

    handle_startendtag = handle_starttag


def _links_from_html(html: str, base_url: str) -> List[str]:
    """Same-document links, absolutised and fragment-stripped, in page order."""
    parser = _AnchorParser()
    try:
        parser.feed(html or "")
    except Exception:
        pass
    out: List[str] = []
    seen: Set[str] = set()
    for href in parser.hrefs:
        if href.startswith(("#", "mailto:", "javascript:", "tel:")):
            continue
        try:
            abs_url = urllib.parse.urljoin(base_url, href).split("#", 1)[0]
        except ValueError:
            continue
        if abs_url and abs_url not in seen:
            seen.add(abs_url)
            out.append(abs_url)
    return out


def _map_mode(url: str, query: str) -> int:
    """--map entry point. Prints one URL per line; 0 on success, 1 otherwise."""
    if not is_valid_url(url):
        print(f"MAP: invalid URL: {url}", file=sys.stderr)
        return 1
    cap = MAP_MAX_URLS
    parsed = urllib.parse.urlparse(url)
    scheme = parsed.scheme or "https"
    host = (parsed.netloc or "").lower()
    prefix, exact = _crawl_scope(parsed.path)
    budget = [MAP_MAX_TOTAL_BYTES]

    # 1) the URL itself when it is a sitemap; else 2) robots.txt Sitemap: lines;
    # else 3) the two conventional sitemap paths.
    candidates: List[str] = []
    if _RE_SITEMAP_HINT.search(url) or url.lower().split("?", 1)[0].endswith((".xml", ".xml.gz")):
        candidates = [url]
    else:
        robots = _map_fetch_doc(f"{scheme}://{host}/robots.txt", budget) or ""
        for line in robots.splitlines():
            if line.strip().lower().startswith("sitemap:"):
                candidate = line.split(":", 1)[1].strip()
                if candidate:
                    candidates.append(candidate)
        if not candidates:
            candidates = [
                f"{scheme}://{host}/sitemap.xml",
                f"{scheme}://{host}/sitemap_index.xml",
            ]

    found: List[str] = []
    queue = list(candidates)
    seen_sitemaps: Set[str] = set()
    fetched = 0
    while queue and fetched < MAP_MAX_SITEMAPS and budget[0] > 0:
        sitemap_url = queue.pop(0)
        if sitemap_url in seen_sitemaps:
            continue
        seen_sitemaps.add(sitemap_url)
        text = _map_fetch_doc(sitemap_url, budget)
        fetched += 1
        if not text:
            continue
        pages, children = _parse_sitemap(text)
        found.extend(pages)
        queue.extend(c for c in children if c not in seen_sitemaps)

    label = "sitemap"

    def scope_urls(raw_urls: List[str]) -> List[str]:
        scoped_here: List[str] = []
        seen_here: Set[str] = set()
        for found_url in raw_urls:
            found_url = found_url.split("#", 1)[0]
            if found_url in seen_here or not _in_scope(found_url, host, prefix, exact):
                continue
            seen_here.add(found_url)
            scoped_here.append(found_url)
        return scoped_here

    scoped = scope_urls(found)
    if not scoped:
        # 4) no sitemap coverage for THIS subtree (a root sitemap may only list
        # section index pages): same-host links from the page itself.
        label = "page links"
        scoped = scope_urls(_links_from_html(_map_fetch_doc(url, budget) or "", url))

    if query.strip():
        # Keep ASCII tokens longer than 2 chars, plus any non-ASCII token
        # (a CJK term is often one character but still meaningful).
        tokens = [
            t for t in re.findall(r"\w+", query.lower())
            if len(t) > 2 or any(ord(c) > 127 for c in t)
        ]
        if tokens:
            scored = []
            for idx, found_url in enumerate(scoped):
                path = urllib.parse.unquote(
                    urllib.parse.urlparse(found_url).path
                ).lower()
                score = sum(1 for t in tokens if t in path)
                if score:
                    scored.append((-score, idx, found_url))
            scoped = [u for _score, _idx, u in sorted(scored)]

    if not scoped:
        reason = "no URLs matched the query filter" if query.strip() else "no URLs found"
        print(f"MAP: {reason} (scope {exact or prefix}* on {host})", file=sys.stderr)
        return 1

    shown = scoped[:cap]
    print("\n".join(shown))
    suffix = f" (showing {len(shown)} of {len(scoped)})" if len(scoped) > len(shown) else ""
    print(f"MAP: {len(shown)} URLs from {label}{suffix} — scope {exact or prefix}* on {host}",
          file=sys.stderr)
    return 0


# ---------------------------------------------------------------------------
# --cite: citers + references for one paper — no full text
# ---------------------------------------------------------------------------

# Fixed cap for both lists (no user-facing count flag).
CITE_MAX_ITEMS: int = 25

_RE_BARE_DOI = re.compile(r"^10\.\d{4,9}/\S+$")


def _parse_cite_seed(seed: str) -> Tuple[str, str]:
    """(kind, value) for a DOI / arXiv id / OpenAlex id. Raises ValueError."""
    s = (seed or "").strip()
    if not s:
        raise ValueError("empty seed")
    m = re.match(r"(?i)^(?:doi:\s*|https?://(?:dx\.)?doi\.org/)(\S+)$", s)
    if m:
        return "doi", urllib.parse.unquote(m.group(1))
    m = re.match(r"(?i)^https?://(?:www\.)?arxiv\.org/(?:abs|pdf)/([^?#]+?)(?:\.pdf)?$", s)
    if m:
        return "arxiv", m.group(1)
    m = re.match(r"(?i)^arxiv:\s*(\S+)$", s)
    if m:
        return "arxiv", m.group(1)
    m = re.match(r"(?i)^https?://(?:www\.)?openalex\.org/(W\d+)$", s)
    if m:
        return "openalex", m.group(1).upper()
    if re.match(r"(?i)^W\d+$", s):
        return "openalex", s.upper()
    if _RE_BARE_DOI.match(s):
        return "doi", s
    raise ValueError(
        f"unrecognised seed (use a DOI, a doi.org/arXiv URL, or an OpenAlex W-id): {s}"
    )


def _openalex_url(path: str) -> str:
    """OpenAlex API URL; adds the polite-pool mailto only when configured."""
    url = "https://api.openalex.org" + path
    mailto = os.environ.get("WEB_RESEARCH_OPENALEX_MAILTO", "").strip()
    if mailto:
        url += ("&" if "?" in url else "?") + "mailto=" + urllib.parse.quote(mailto)
    return url


def _json_get(url: str, timeout: int = 10, max_bytes: int = 4_000_000):
    """JSON GET with the shared UA/byte cap. None unless the body is an object."""
    raw = _http_get_bytes(url, timeout=timeout, max_bytes=max_bytes)
    if not raw:
        return None
    try:
        data = json.loads(raw.decode("utf-8", errors="replace"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _openalex_work(kind: str, value: str) -> Optional[dict]:
    """OpenAlex work record for a DOI / OpenAlex id / arXiv id (via its DOI)."""
    if kind == "openalex":
        return _json_get(_openalex_url(f"/works/{value}"))
    if kind == "doi":
        return _json_get(
            _openalex_url("/works/https://doi.org/" + urllib.parse.quote(value, safe="/"))
        )
    if kind == "arxiv":
        doi = f"10.48550/arXiv.{value}"
        return _json_get(
            _openalex_url("/works/https://doi.org/" + urllib.parse.quote(doi, safe="/"))
        )
    return None


def _cite_items(works: Optional[List[dict]]) -> List[Tuple[str, str, str, str]]:
    """(title, year, doi, openalex_id) tuples, untitled entries kept."""
    items: List[Tuple[str, str, str, str]] = []
    for work in works or []:
        if not isinstance(work, dict):
            continue
        title = " ".join((work.get("title") or work.get("display_name") or "").split())
        title = title or "(untitled)"
        year = work.get("publication_year") or ""
        doi = (work.get("doi") or "").replace("https://doi.org/", "")
        oid = (work.get("id") or "").rsplit("/", 1)[-1]
        items.append((title, str(year) if year else "", doi, oid))
    return items


def _openalex_citers(work_id: str, limit: int) -> Optional[List[dict]]:
    data = _json_get(_openalex_url(
        f"/works?filter=cites:{work_id}&per-page={min(limit, 200)}"
        f"&sort=cited_by_count:desc&select=title,publication_year,doi,id"
    ))
    if data is None:
        return None
    return data.get("results") or []


def _openalex_references(ref_ids: List[str], limit: int) -> Optional[List[dict]]:
    """Newest referenced works, batched 25 ids per request (OpenAlex pipe-OR).

    Fetches at least 50 ids (so "newest N" is drawn from a real sample, not an
    arbitrary first N) and at most 100 — citation lists can run to hundreds and
    the display is capped anyway. None when every request failed (distinct from
    an empty result).
    """
    out: List[dict] = []
    ids = ref_ids[:min(len(ref_ids), max(limit, 50), 100)]
    answered = False
    for start in range(0, len(ids), 25):
        chunk = ids[start:start + 25]
        data = _json_get(_openalex_url(
            "/works?filter=openalex_id:" + "|".join(chunk)
            + f"&per-page={len(chunk)}&select=title,publication_year,doi,id"
        ))
        if data is not None:
            answered = True
            out.extend(data.get("results") or [])
    if ids and not answered:
        return None
    out.sort(key=lambda w: (w.get("publication_year") or 0), reverse=True)
    return out[:limit]


def _s2_identifier(kind: str, value: str) -> str:
    if kind == "doi":
        return f"DOI:{value}"
    if kind == "arxiv":
        return f"ARXIV:{value}"
    return ""


def _s2_citers(kind: str, value: str, limit: int) -> List[dict]:
    """Semantic Scholar citers as OpenAlex-shaped dicts. One 429 retry."""
    ident = _s2_identifier(kind, value)
    if not ident:
        return []
    url = (
        "https://api.semanticscholar.org/graph/v1/paper/"
        + urllib.parse.quote(ident, safe=":")
        + f"/citations?fields=title,year,externalIds&limit={min(limit, 100)}"
    )
    for attempt in (1, 2):
        data = _json_get(url, timeout=12)
        if data is not None:
            items = []
            for entry in data.get("data") or []:
                paper = (entry or {}).get("citingPaper") or {}
                if not paper:
                    continue
                doi = ((paper.get("externalIds") or {}).get("DOI") or "")
                items.append({
                    "title": paper.get("title") or "(untitled)",
                    "publication_year": paper.get("year") or "",
                    "doi": f"https://doi.org/{doi}" if doi else "",
                    "id": "",
                })
            return items
        if attempt == 1:
            time.sleep(2)
    return []


def _s2_seed(kind: str, value: str) -> Optional[dict]:
    """Seed metadata from Semantic Scholar when OpenAlex has no record."""
    ident = _s2_identifier(kind, value)
    if not ident:
        return None
    data = _json_get(
        "https://api.semanticscholar.org/graph/v1/paper/"
        + urllib.parse.quote(ident, safe=":")
        + "?fields=title,year,externalIds,citationCount"
    )
    return data or None


def _cite_line(title: str, year: str, doi: str, oid: str) -> str:
    link = f"https://doi.org/{doi}" if doi else (f"https://openalex.org/{oid}" if oid else "")
    year_part = f" ({year})" if year else ""
    return f"- {title}{year_part}" + (f" — {link}" if link else "")


def _cite_mode(seed: str) -> int:
    """--cite entry point. Prints citers + references; 0 on success, 1 otherwise."""
    try:
        kind, value = _parse_cite_seed(seed)
    except ValueError as exc:
        print(f"CITE: {exc}", file=sys.stderr)
        return 1
    cap = CITE_MAX_ITEMS

    work = _openalex_work(kind, value)
    if not work:
        # No OpenAlex record: Semantic Scholar can still give seed + citers.
        s2_seed = _s2_seed(kind, value)
        if not s2_seed:
            print(
                "CITE: seed not resolved — no OpenAlex record, and the Semantic "
                f"Scholar lookup failed or returned nothing: {seed}",
                file=sys.stderr,
            )
            return 1
        doi = ((s2_seed.get("externalIds") or {}).get("DOI") or "")
        year = str(s2_seed.get("year") or "")
        title = " ".join((s2_seed.get("title") or "").split()) or "(untitled)"
        citers = _cite_items(_s2_citers(kind, value, cap))
        print(f"SEED: {title}{f' ({year})' if year else ''}"
              + (f" — https://doi.org/{doi}" if doi else ""))
        print(f"CITERS ({len(citers)} of {s2_seed.get('citationCount') or len(citers)}) [Semantic Scholar]")
        for item in citers:
            print(_cite_line(*item))
        print("REFERENCES (0) [unavailable for this seed — no OpenAlex record]")
        return 0

    seed_title = " ".join((work.get("title") or "").split()) or "(untitled)"
    seed_year = str(work.get("publication_year") or "")
    seed_doi = (work.get("doi") or "").replace("https://doi.org/", "")
    work_id = (work.get("id") or "").rsplit("/", 1)[-1]
    total_citers = work.get("cited_by_count") or 0
    ref_ids = [u.rsplit("/", 1)[-1] for u in (work.get("referenced_works") or []) if u]

    citers_raw = _openalex_citers(work_id, cap)
    provider = "OpenAlex"
    citers_failed = citers_raw is None
    if not citers_raw and kind in ("doi", "arxiv"):
        s2_raw = _s2_citers(kind, value, cap)
        if s2_raw:
            citers_raw, provider, citers_failed = s2_raw, "Semantic Scholar", False
    citers = _cite_items(citers_raw)

    print(f"SEED: {seed_title}{f' ({seed_year})' if seed_year else ''}"
          + (f" — https://doi.org/{seed_doi}" if seed_doi else ""))
    citer_label = "request failed" if citers_failed else provider
    print(f"CITERS ({len(citers)} of {total_citers}) [{citer_label}]")
    for item in citers:
        print(_cite_line(*item))

    refs_raw = _openalex_references(ref_ids, cap)
    if refs_raw is None:
        print(f"REFERENCES (0 of {len(ref_ids)}) [OpenAlex — request failed]")
    else:
        refs = _cite_items(refs_raw)
        print(f"REFERENCES ({len(refs)} of {len(ref_ids)}) [OpenAlex]")
        for item in refs:
            print(_cite_line(*item))
    return 0


def run_research(config: ResearchConfig, status: Optional[dict] = None) -> Optional[List[FetchResult]]:
    """Execute research and output results."""
    _reset_scholarly_budget()
    progress = ProgressReporter(quiet=config.quiet)
    status = {} if status is None else status

    # Batch mode: collect all results, then format
    results: List[FetchResult] = []
    _wall_t0 = time.monotonic()

    async def collect_async():
        try:
            async for result in run_research_async(config, progress, status=status):
                results.append(result)
        finally:
            _shutdown_extract_pool()

    # Whole-run bound for the async core on ALL platforms (Windows has no
    # SIGALRM, so the Unix watchdog in main() does not apply there). This
    # covers every documented unbounded vector: hung ProcessPoolExecutor
    # extraction workers, the synchronous Scrapling fallback, and all gather
    # points. On timeout the inner task is cancelled promptly even when a pool
    # worker is stuck; we then os._exit(1) exactly like the SIGALRM watchdog —
    # a plain exit could hang interpreter teardown joining the hung worker.
    try:
        asyncio.run(asyncio.wait_for(collect_async(), timeout=WALL_TIMEOUT))
    except asyncio.TimeoutError:
        log_usage({
            "query": config.query, "mode": "search",
            "urls_searched": 0, "urls_fetched": 0, "content_chars": 0,
            "ok": False, "error": "wall-clock timeout",
            "ms": int((time.monotonic() - _wall_t0) * 1000), "timeout": True,
            "short_pages": 0, "domains": [],
        })
        print(f"\nwall-clock timeout ({WALL_TIMEOUT}s) — exiting", file=sys.stderr)
        os._exit(1)  # kills child processes (ProcessPoolExecutor workers)

    return results


def main() -> None:
    """Main entry point."""
    # Force UTF-8 stdout on Windows (avoids cp1251/charmap encoding errors)
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(
        description="Web Research Tool - Search + Fetch with TLS fingerprinting (Scrapling)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python web_research.py "Mac Studio M3 Ultra LLM performance"
  python web_research.py "rust release notes" --since y   # Optional: last year only
  python web_research.py --url https://example.com   # Fetch one URL: full page saved to a report file, path printed
  python web_research.py --url https://example.com --no-render  # Pure static fetch (no browser)
  python web_research.py --map https://docs.python.org/3/library/  # List URLs in that subtree
  python web_research.py --cite 10.1038/s41586-020-2649-2  # Citers + references for a paper

Search: DuckDuckGo (static, no API key)
Fetch: Scrapling AsyncFetcher (TLS fingerprinting); failed or gated fetches are retried with real Google Chrome (the --url-chrome tier, provisioned into tmp/browser/chrome)
Extract: trafilatura > regex > Scrapling DOM parser (tiered fallback)
Full page saved to tmp/webresearch/, path printed to stdout
Blocked domains: facebook.com, tiktok.com, instagram.com, linkedin.com, youtube.com, msn.com, percona.com, mctlaw.com, edmunds.com, cars.com, reddit.com
Routed access: scholarly publishers and doi.org resolve to open-access full text; nytimes.com and forbes.com fall back to the Wayback copy
Structured sources: RSS/Atom feeds, Hacker News threads, arXiv full text, PDFs (pdftotext or the bundled pypdf extractor)
        """
    )

    parser.add_argument("query", nargs="?", help="Search query (omit if using --url)")
    parser.add_argument("-u", "--url", nargs="+", metavar="URL",
                        help="Fetch one URL directly: raw page text saved to a report file (skip search, no quality filters)")
    parser.add_argument("--no-render", action="store_true",
                        help="Disable browser rendering entirely (pure static path)")
    parser.add_argument("--usage", action="store_true",
                        help="Show usage statistics (last 30 days)")
    parser.add_argument("--sci", action="store_true",
                        help="Enable scientific bonus sources (arXiv, OpenAlex)")
    parser.add_argument("--med", action="store_true",
                        help="Enable medical bonus sources (PubMed, Europe PMC, OpenAlex)")
    parser.add_argument("--tech", action="store_true",
                        help="Enable tech bonus sources (Hacker News, Stack Overflow, Dev.to, GitHub)")
    parser.add_argument("--since", choices=("d", "w", "m", "y"),
                        help="Restrict results to the last day/week/month/year (engine time window; optional, trades recall for recency)")
    parser.add_argument("--map", metavar="URL",
                        help="List URLs on a site or subtree (sitemap, else page links). Optional query filters by path. Lists only — fetch with --url")
    parser.add_argument("--cite", metavar="SEED",
                        help="Citation graph for one paper: citers + references. Seed: DOI, doi.org/arXiv URL, or OpenAlex W-id")
    parser.add_argument("--quality", action="store_true",
                        help="Include output quality analysis (with --usage)")

    args = parser.parse_args()

    if args.usage or args.quality:
        print_usage_stats(quality=args.quality)
        sys.exit(0)

    # Exactly one data mode. --since is a search-mode modifier only.
    if sum(1 for m in (args.url, args.map, args.cite) if m) > 1:
        parser.error("use only one of --url, --map, --cite")
    if args.since and (args.url or args.map or args.cite):
        parser.error("--since applies to search mode only")

    if args.map:
        _t0 = time.monotonic()
        code = _map_mode(args.map, args.query or "")
        log_usage({
            "query": args.map, "mode": "map",
            "urls_fetched": 0, "content_chars": 0,
            "ok": code == 0, "error": None if code == 0 else "failed",
            "ms": int((time.monotonic() - _t0) * 1000), "timeout": False,
        })
        sys.exit(code)
    if args.cite:
        _t0 = time.monotonic()
        code = _cite_mode(args.cite)
        log_usage({
            "query": args.cite, "mode": "cite",
            "urls_fetched": 0, "content_chars": 0,
            "ok": code == 0, "error": None if code == 0 else "failed",
            "ms": int((time.monotonic() - _t0) * 1000), "timeout": False,
        })
        sys.exit(code)

    # URL-fetch mode: ONE URL per invocation. Fetch the page in FULL (no char
    # cap), save it RAW (quality filters OFF, full-document DOM text — every
    # visible text node, nav/boilerplate included), write to its own report
    # file, print ONLY the absolute path to stdout.
    if args.url:
        if len(args.url) > 1:
            parser.error("only one URL per invocation")
        url = args.url[0]

        # Render mode: default auto (browser only when static fetch fails);
        # --no-render disables it entirely. WEB_RESEARCH_NO_BROWSER=1 overrides
        # everything (pure static path).
        if os.environ.get("WEB_RESEARCH_NO_BROWSER") == "1":
            render_mode = "off"
        elif args.no_render:
            render_mode = "off"
        else:
            render_mode = "auto"

        _ensure_report_dir()

        # Browser-tier preflight, OUTSIDE the wall-clock block below: the tier sets
        # itself up on first use (real Google Chrome, repo-local, like uv) — a first
        # provisioning takes minutes and must not count against the fetch budget.
        # No-op when the browser is disabled (--no-render / WEB_RESEARCH_NO_BROWSER=1).
        if render_mode != "off":
            _ensure_browser_tier()

        global _BROWSER_ESCALATIONS
        _BROWSER_ESCALATIONS = 0
        _reset_scholarly_budget()
        progress = ProgressReporter()

        async def _fetch_url_single(u: str) -> FetchResult:
            return await fetch_single_async(
                u, DEFAULT_TIMEOUT, 100, None, progress=progress, render=render_mode,
                apply_filters=False,  # --url raw mode: page text as extracted, no F4/F1 cleanup
            )

        t0 = time.monotonic()
        try:
            # Whole-run bound: --url shares the exact extraction vectors of
            # search mode (hung pool worker / stuck Scrapling parse), and this
            # branch exits before the SIGALRM watchdog below is reached, so no
            # other wall-clock bound applies to it.
            result = asyncio.run(asyncio.wait_for(_fetch_url_single(url), timeout=WALL_TIMEOUT))
        except asyncio.TimeoutError:
            log_usage({
                "query": "", "mode": "url-fetch",
                "urls_searched": 0, "urls_fetched": 0,
                "content_chars": 0, "ok": False,
                "error": "wall-clock timeout",
                "ms": int((time.monotonic() - t0) * 1000), "timeout": True,
            })
            print(f"\nwall-clock timeout ({WALL_TIMEOUT}s) — exiting", file=sys.stderr)
            os._exit(1)  # kills child processes (ProcessPoolExecutor workers)
        except KeyboardInterrupt:
            sys.exit(130)

        if not result.success:
            log_usage({
                "query": "", "mode": "url-fetch", "urls_searched": 0,
                "urls_fetched": 0, "content_chars": 0,
                "ok": False, "error": result.error,
                "ms": int((time.monotonic() - t0) * 1000), "timeout": False,
                **_quality_fields([result]),
            })
            err = str(result.error)
            print(f"Failed to fetch {url}: {err}", file=sys.stderr)
            # Hint when the page looks bot-gated, whether or not the automatic retry
            # covered the failure: that retry only handles BROWSER_RETRY_ERRORS, so other
            # refusals — a WAF's own non-standard status code — deserve
            # the pointer too. The wording depends on whether the tier actually ran: only
            # then is repeating the page with --scroll/--wait the right advice.
            # A clean "gone / forbidden for a reason" code is NOT bot-gating — a browser
            # cannot fix it (see the module docs on 404/DNS): only the retry-worthy set and
            # other refusals (a WAF's own status code) get the hint.
            _status = re.search(r"\b[45]\d\d\b", err)
            _clean = {"400", "404", "405", "409", "410", "422", "451"}
            if err in BROWSER_RETRY_ERRORS or (_status and _status.group(0) not in _clean):
                tier_ran = err in BROWSER_RETRY_ERRORS and _BROWSER_TIER_READY is True
                if tier_ran:
                    print(f"hint: this page looks bot-gated — the real-browser tier was "
                          f"already tried; force it again with --scroll/--wait: "
                          f"--url-chrome {url}", file=sys.stderr)
                else:
                    print(f"hint: this page looks bot-gated — retry it with a real browser: "
                          f"--url-chrome {url}", file=sys.stderr)
            sys.exit(1)

        # Raw page text: --url mode skips ALL quality filters (F4/F1 cleanup is
        # off — the user asked for the page, not a cleaned digest). The text is
        # exactly what extraction produced; no length cap, no fail-soft filter.
        text = result.content
        path = _report_dir() / (_make_run_id(url) + ".txt")
        try:
            path.write_text(f"=== {url} ===\n\n{text}\n", encoding="utf-8")
        except OSError as e:
            log_usage({
                "query": "", "mode": "url-fetch", "urls_searched": 0,
                "urls_fetched": 1, "content_chars": len(text),
                "ok": False, "error": f"write failed: {e}",
                "ms": int((time.monotonic() - t0) * 1000), "timeout": False,
                **_quality_fields([result]),
            })
            print(f"Failed to write report file {path}: {e}", file=sys.stderr)
            sys.exit(1)
        log_usage({
            "query": "", "mode": "url-fetch", "urls_searched": 0,
            "urls_fetched": 1, "content_chars": len(text),
            "ok": True, "error": None,
            "ms": int((time.monotonic() - t0) * 1000), "timeout": False,
            **_quality_fields([result]),
        })
        # Exactly ONE line on stdout: the absolute path of the saved file.
        print(f"Full web page saved at: {path}")
        sys.exit(0)

    if not args.query:
        parser.error("query is required (or use --url for direct fetch)")

    # Prepare report dir at startup: mkdir + rotation (both fail-soft)
    _ensure_report_dir()

    # NOTE: search mode is static-only by design (benchmarked: browser
    # escalation cost +44% fetch time and rescued ~0-2 pages — the browser
    # value is --url single-page rendering). No browser preflight here.

    queries = [args.query]

    def make_config(query: str) -> ResearchConfig:
        return ResearchConfig(
            query=query,
            scientific=args.sci,
            medical=args.med,
            tech=args.tech,
            since=args.since or "",
        )

    # Hard wall-clock timeout: kill the entire process after WALL_TIMEOUT.
    # Unix belt (SIGALRM) — bounds the WHOLE process including the
    # post-processing phase outside run_research (quality filters, report
    # build), which wait_for inside run_research does not cover. The wait_for
    # bound covers the async core on ALL platforms (incl. Windows, which has
    # no SIGALRM). Both use the same WALL_TIMEOUT value; whichever fires first
    # on Unix produces identical behavior (message + os._exit(1)).
    import signal
    _wall_t0 = time.monotonic()
    def _timeout_handler(signum, frame):
        for q in queries:
            log_usage({
                "query": q, "mode": "search",
                "urls_searched": 0, "urls_fetched": 0, "content_chars": 0,
                "ok": False, "error": "wall-clock timeout",
                "ms": int((time.monotonic() - _wall_t0) * 1000), "timeout": True,
                "short_pages": 0, "domains": [],
            })
        print(f"\nwall-clock timeout ({WALL_TIMEOUT}s) — exiting", file=sys.stderr)
        os._exit(1)  # kills child processes (ProcessPoolExecutor workers)
    if hasattr(signal, 'SIGALRM'):
        signal.signal(signal.SIGALRM, _timeout_handler)
        signal.alarm(WALL_TIMEOUT)

    try:
        # Single query: original behavior
        config = make_config(queries[0])
        t0 = time.monotonic()
        search_status: dict = {}
        results = run_research(config, search_status)
        ok = [r for r in (results or []) if r.success]
        # Telemetry fields are computed from the PRE-filter fetch results
        # (unchanged semantics); only "ok" below reflects post-filter survival.
        fetched_chars = sum(len(r.content) for r in (results or []))
        quality_fields = _quality_fields(results)
        pages: List[Tuple[FetchResult, str, bool]] = []
        failures: List[FetchResult] = []
        if results:
            # F5 first: page-level farm/syndication detection must see the FULL
            # bodies (sentence dedup would gut near-identical copies and destroy
            # the page-level Jaccard signal). Stub drop runs alongside it. Then
            # the existing sentence dedup.
            quality_stats: dict = {"farm_dropped": 0,
                                   "stale_dropped": 0,
                                   "stub_dropped": 0, "dedup_dropped": 0}
            if QUALITY_FILTERS_ENABLED:
                results, stub_st = _drop_stub_pages(results)
                quality_stats["stub_dropped"] += stub_st.get("stub_dropped", 0)
                results, f5_st = _dedup_farm_pages(results)
                quality_stats["farm_dropped"] += f5_st.get("farm_dropped", 0)
            results, dedup_st = _dedup_results(results)
            # Count pages the dedup shrank below the 50-char floor (previously
            # only a debug log; I7: make the drop observable in the digest).
            quality_stats["dedup_dropped"] = dedup_st.pages_dropped
            if QUALITY_FILTERS_ENABLED:
                results, f7_st = _recency_filter(results, config.query)
                quality_stats.update(f7_st)
            results, _f3_st = _dedup_cross_page_sentences(results)
            # Digest + report-file output: the file holds the full filtered text
            # (grep-safe), stdout carries only the compact digest. If the file
            # cannot be written, fall back to the old inline raw output.
            pages = _prepare_report_pages(results)
            failures = [r for r in results if not r.success]
        log_usage({
            "query": config.query, "mode": "search",
            "urls_fetched": len(ok),
            "content_chars": fetched_chars,
            "ok": bool(pages), "error": None,
            "ms": int((time.monotonic() - t0) * 1000), "timeout": False,
            **quality_fields,
        })
        if pages:
            report_path = _report_dir() / f"{_make_run_id(config.query)}.txt"
            digest, report_content = _assemble_report(config.query, pages, failures, report_path, quality_stats, since=config.since)
            if _write_report_file(report_path, report_content):
                print(digest)
                # Redundant path on stderr: harnesses capture both streams, and
                # the path must survive any stdout trimming.
                print(f"FULL REPORT: {report_path}", file=sys.stderr)
            else:
                print(format_batch_raw(results))
        elif ok:
            # Every fetched page was dropped downstream (quality filters
            # F5/F7, stub/sentence dedup) or filtered to empty text. Exit
            # non-zero — matching --url mode — instead of the old silent exit-0.
            print(
                f"No results: all {len(ok)} fetched pages were dropped by quality filters "
                "(farm/recency/stub/dedup) or filtered to empty text",
                file=sys.stderr,
            )
            sys.exit(1)
        elif results:
            # Every fetched page failed (fetch errors, HTTP 403, CAPTCHA...).
            print(f"No results: all {len(results)} fetched pages failed", file=sys.stderr)
            sys.exit(1)
        else:
            attempts = search_status.get("search_attempts", 1)
            err = search_status.get("search_error")
            if attempts > 1 or err:
                print(
                    f"No results: search backend returned 0 URLs after {attempts} attempt(s)"
                    + (f" ({err})" if err else ""),
                    file=sys.stderr,
                )
            else:
                print("No results found", file=sys.stderr)
            sys.exit(1)

    except KeyboardInterrupt:
        print("\nInterrupted", file=sys.stderr)
        sys.exit(130)
    except BrokenPipeError:
        # Output pipe closed (e.g. piped to head) — not a real error
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        sys.exit(0)
    except Exception as e:
        log_usage({
            "query": queries[0] if queries else "", "mode": "search",
            "urls_fetched": 0, "content_chars": 0,
            "ok": False, "error": str(e)[:200],
            "ms": int((time.monotonic() - _wall_t0) * 1000), "timeout": False,
        })
        logger.exception(f"Research failed: {e}")
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
