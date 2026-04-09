#!/usr/bin/env python3
"""
Simplified Lucid Folder Export
Discovers and exports all documents from a specific Lucid folder.

Usage:
  ./export_folder.py <folder_id>
  
Example:
  ./export_folder.py abc123-def456
  
Get folder_id from Lucid URL: https://lucid.app/folder/{folder_id}
"""

import json
import os
import re
import sys
import time
import requests
from datetime import datetime
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
from urllib.parse import parse_qs, urlparse

load_dotenv()

# Configuration
OUTPUT_BASE = "./exports"
CHECKPOINT_FILE = "./.export_checkpoint.json"
LOG_FILE = "./export_log.txt"
API_BASE_URL = "https://api.lucid.co"
API_VERSION = "1"
RAW_DOCUMENT_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{8,}")
DOCUMENT_ID_PATTERNS = (
    re.compile(r"/lucidchart/([A-Za-z0-9_-]{8,})/edit"),
    re.compile(r"/documents/thumb/([A-Za-z0-9_-]{8,})/"),
)
DOCUMENT_ID_KEYS = ("documentId", "docId", "document_id", "doc_id", "url", "href")
DOCUMENT_TITLE_KEYS = ("title", "name", "documentName", "docName")
FOLDER_ID_KEYS = ("folderId", "folder_id")
PARENT_KEYS = ("parentId", "parent_id")
NESTED_FOLDER_KEYS = ("parent", "folder", "container")
DOCUMENT_TYPE_KEYS = ("product", "documentType", "type", "kind")
DOCUMENT_URL_KEYS = ("editUrl", "edit_url", "editorUrl", "editor_url", "url", "href")
NETWORK_URL_KEYWORDS = (
    "document",
    "folder",
    "graphql",
    "search",
    "browse",
    "list",
    "recent",
)

def log(message: str):
    """Log message to console and file."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_message = f"[{timestamp}] {message}"
    print(log_message)
    with open(LOG_FILE, 'a') as f:
        f.write(log_message + "\n")

def sanitize_filename(name: str) -> str:
    """Remove invalid characters from filenames."""
    invalid_chars = '<>:"/\\|?*'
    for char in invalid_chars:
        name = name.replace(char, '_')
    return name.strip()

def load_checkpoint(folder_id: str):
    """Load checkpoint for specific folder."""
    if os.path.exists(CHECKPOINT_FILE):
        with open(CHECKPOINT_FILE, 'r') as f:
            data = json.load(f)
            if data.get("folder_id") == folder_id:
                return data
    return {
        "folder_id": folder_id,
        "folder_name": "",
        "completed": [],
        "failed": []
    }

def save_checkpoint(checkpoint):
    """Save checkpoint to file."""
    checkpoint["last_updated"] = datetime.now().isoformat()
    with open(CHECKPOINT_FILE, 'w') as f:
        json.dump(checkpoint, f, indent=2)

def extract_document_id(value: str):
    """Extract a Lucid document ID from a URL or attribute value."""
    if not value:
        return None
    
    value = str(value).strip()
    if RAW_DOCUMENT_ID_PATTERN.fullmatch(value):
        return value
    
    for pattern in DOCUMENT_ID_PATTERNS:
        match = pattern.search(value)
        if match:
            return match.group(1)
    
    return None

def extract_document_id_from_item(item):
    """Extract a document ID from a payload item using known key variants."""
    if not isinstance(item, dict):
        return None
    
    for key in ("id", *DOCUMENT_ID_KEYS):
        doc_id = extract_document_id(item.get(key))
        if doc_id:
            return doc_id
    
    return None

def page_matches_folder(page_url: str, folder_id: str) -> bool:
    """Check whether the current page URL already points at the requested folder."""
    if not page_url or not folder_id:
        return False
    
    if f"folder_id={folder_id}" in page_url:
        return True
    
    parsed = urlparse(page_url)
    if folder_id in parse_qs(parsed.query).get("folder_id", []):
        return True
    
    if parsed.fragment and "?" in parsed.fragment:
        fragment_query = parsed.fragment.split("?", 1)[1]
        if folder_id in parse_qs(fragment_query).get("folder_id", []):
            return True
    
    return False

def normalize_document_name(raw_name: str, doc_id: str) -> str:
    """Convert discovered text into a usable document filename."""
    if raw_name:
        for line in (part.strip() for part in raw_name.splitlines()):
            if not line:
                continue
            if len(line) > 200:
                continue
            if re.search(r"(share|menu|last modified|owned by|open details)", line, re.IGNORECASE):
                continue
            return sanitize_filename(line)
    
    return f"Document_{doc_id[:8]}"

def normalize_product(value) -> str:
    """Normalize product/type hints to a stable lowercase label."""
    if isinstance(value, str) and value.strip():
        return value.strip().lower()
    return "unknown"

def looks_like_folder_page(page_url: str) -> bool:
    """Require a documents/teams page instead of matching login redirects."""
    if not page_url:
        return False
    lowered = page_url.lower()
    return "lucid.app/documents" in lowered and "login" not in lowered

def extract_text_field(data, keys):
    """Return the first non-empty string found for a list of candidate keys."""
    if not isinstance(data, dict):
        return None
    
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    
    return None

def extract_nested_id(value):
    """Extract IDs from strings or nested dicts."""
    if isinstance(value, dict):
        return extract_text_field(value, ("id", "folderId", "folder_id"))
    if isinstance(value, str):
        return value.strip() or None
    return None

def extract_folder_id_from_item(item):
    """Extract a folder/parent identifier from a network payload item."""
    if not isinstance(item, dict):
        return None
    
    direct = extract_text_field(item, FOLDER_ID_KEYS)
    if direct:
        return direct
    
    for parent_key in NESTED_FOLDER_KEYS:
        nested = extract_nested_id(item.get(parent_key))
        if nested:
            return nested
    
    return extract_text_field(item, PARENT_KEYS)

def extract_document_link(item, doc_id: str):
    """Extract a plausible browser URL for a discovered document."""
    if not isinstance(item, dict):
        return None
    
    def normalize_url(value):
        if not isinstance(value, str):
            return None
        
        value = value.strip()
        if not value or "lucid" not in value.lower():
            return None
        if doc_id and doc_id not in value:
            return None
        if any(part in value for part in ("/shareSettings", "/extended", "/shapeLibraries/", "/byDocIds", "/folderEntries")):
            return None
        if value.startswith("http://") or value.startswith("https://"):
            return value
        return None
    
    direct = extract_text_field(item, DOCUMENT_URL_KEYS)
    normalized = normalize_url(direct)
    if normalized:
        return normalized
    
    for nested_key in ("links", "actions", "navigation", "_links"):
        nested = item.get(nested_key)
        if isinstance(nested, dict):
            for value in nested.values():
                normalized = normalize_url(value)
                if normalized:
                    return normalized
    
    return None

def source_url_looks_like_folder_listing(source_url: str) -> bool:
    """Return True when a network response URL is clearly returning folders, not documents."""
    if not source_url:
        return False
    lowered = source_url.lower()
    return "/folders" in lowered and "/documents/" not in lowered and "/documents?" not in lowered

def item_looks_like_document(item, source_url: str = None):
    """Heuristic check whether a JSON object represents a Lucid document."""
    if not isinstance(item, dict):
        return False
    
    if source_url_looks_like_folder_listing(source_url):
        return False
    
    doc_id = extract_document_id_from_item(item)
    if not doc_id:
        return False
    
    doc_type = extract_text_field(item, DOCUMENT_TYPE_KEYS)
    if doc_type and "folder" in doc_type.lower():
        return False
    
    title = extract_text_field(item, DOCUMENT_TITLE_KEYS)
    folder_ref = extract_folder_id_from_item(item)
    edit_url = extract_document_link(item, doc_id)
    return bool(title and (edit_url or folder_ref or doc_type))

def document_from_item(item, folder_id: str, source_url: str = None):
    """Convert a network payload object into the local document shape."""
    if not item_looks_like_document(item, source_url):
        return None
    
    doc_id = extract_document_id_from_item(item)
    if not doc_id:
        return None
    
    item_folder_id = extract_folder_id_from_item(item)
    if item_folder_id and str(item_folder_id) != str(folder_id):
        return None
    
    doc_type = extract_text_field(item, DOCUMENT_TYPE_KEYS)
    if doc_type and "chart" not in doc_type.lower() and "document" not in doc_type.lower():
        return None
    
    raw_name = extract_text_field(item, DOCUMENT_TITLE_KEYS)
    product = normalize_product(doc_type)
    edit_url = extract_document_link(item, doc_id)
    return {
        "id": doc_id,
        "name": normalize_document_name(raw_name, doc_id),
        "product": product,
        "edit_url": edit_url,
    }

def extract_documents_from_json_payload(payload, folder_id: str, source_url: str = None):
    """Recursively scan a JSON payload for document entries in the target folder."""
    documents = []
    seen = set()
    
    def walk(node):
        if isinstance(node, dict):
            doc = document_from_item(node, folder_id, source_url)
            if doc and doc["id"] not in seen:
                seen.add(doc["id"])
                documents.append(doc)
            
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)
    
    walk(payload)
    return documents

def attach_network_document_collector(page, folder_id: str):
    """Capture document metadata from Lucid network responses during folder loading."""
    collected = {}
    
    def handle_response(response):
        try:
            if response.request.resource_type not in ("xhr", "fetch"):
                return
            
            url = response.url.lower()
            if not any(keyword in url for keyword in NETWORK_URL_KEYWORDS):
                return
            
            headers = response.headers
            content_type = headers.get("content-type", headers.get("Content-Type", "")).lower()
            if "json" not in content_type:
                return
            
            if source_url_looks_like_folder_listing(response.url):
                return
            
            payload = response.json()
            docs = extract_documents_from_json_payload(payload, folder_id, response.url)
            if not docs:
                return
            
            for doc in docs:
                collected.setdefault(doc["id"], doc)
            
            log(f"  📡 Network discovery: {len(docs)} docs from {response.url[:120]}")
        except Exception:
            return
    
    page.on("response", handle_response)
    return collected

def log_page_diagnostics(page):
    """Log lightweight diagnostics about the current folder page."""
    try:
        title = page.title()
    except Exception:
        title = ""
    
    log(f"  Page title: {title or '(empty)'}")
    
    try:
        frame_urls = [frame.url for frame in page.frames if frame.url and frame.url != page.url]
        if frame_urls:
            for frame_url in frame_urls[:5]:
                log(f"  Frame URL: {frame_url}")
    except Exception:
        pass

    try:
        stats = page.evaluate("""
        () => ({
          anchors: document.querySelectorAll('a[href]').length,
          buttons: document.querySelectorAll('button').length,
          links: document.querySelectorAll('[role="link"]').length,
          iframes: document.querySelectorAll('iframe').length,
          testIds: document.querySelectorAll('[data-test-id]').length,
          bodyText: (document.body && document.body.innerText ? document.body.innerText.slice(0, 400) : '')
        })
        """)
        log(
            "  DOM stats: "
            f"anchors={stats.get('anchors', 0)}, "
            f"buttons={stats.get('buttons', 0)}, "
            f"role_links={stats.get('links', 0)}, "
            f"iframes={stats.get('iframes', 0)}, "
            f"test_ids={stats.get('testIds', 0)}"
        )
        body_text = (stats.get("bodyText") or "").replace("\n", " ").strip()
        if body_text:
            log(f"  Body text sample: {body_text[:200]}")
    except Exception:
        pass

def collect_document_candidates(page):
    """Scrape document cards/links from the currently loaded folder view."""
    return page.evaluate("""
    () => {
      const selectors = [
        'a[href]',
        'button[data-test-id]',
        '[role="link"]',
        '[data-test-id]',
        '[data-document-id]',
        '[data-doc-id]'
      ];

      const elements = [];
      const seenElements = new Set();
      for (const selector of selectors) {
        for (const element of document.querySelectorAll(selector)) {
          if (!seenElements.has(element)) {
            seenElements.add(element);
            elements.push(element);
          }
        }
      }

      const getDocId = (value) => {
        if (!value) return null;
        const patterns = [
          /\\/lucidchart\\/([A-Za-z0-9_-]{8,})\\/edit/i,
          /\\/documents\\/thumb\\/([A-Za-z0-9_-]{8,})\\//i
        ];
        for (const pattern of patterns) {
          const match = String(value).match(pattern);
          if (match) return match[1];
        }
        return null;
      };

      const cleanText = (value) => {
        if (!value) return null;
        const lines = String(value)
          .split('\\n')
          .map((line) => line.trim())
          .filter(Boolean);
        return lines.find((line) => line.length < 200) || null;
      };

      const pickName = (element) => {
        const candidates = [];
        const pushValue = (value) => {
          const cleaned = cleanText(value);
          if (cleaned) candidates.push(cleaned);
        };

        pushValue(element.getAttribute('aria-label'));
        pushValue(element.getAttribute('title'));
        pushValue(element.innerText);
        pushValue(element.textContent);

        const container = element.closest('[role="row"], [role="gridcell"], li, article, a, button, div');
        if (container) {
          pushValue(container.getAttribute('aria-label'));
          pushValue(container.getAttribute('title'));
          pushValue(container.innerText);
        }

        const parent = element.parentElement;
        if (parent) {
          pushValue(parent.getAttribute('aria-label'));
          pushValue(parent.getAttribute('title'));
          pushValue(parent.innerText);
        }

        return candidates[0] || null;
      };

      const results = [];
      const seenDocIds = new Set();

      for (const element of elements) {
        const values = [
          element.href,
          element.getAttribute('href'),
          element.getAttribute('data-href'),
          element.getAttribute('data-test-id'),
          element.getAttribute('data-document-id'),
          element.getAttribute('data-doc-id'),
          element.getAttribute('data-id')
        ];

        let docId = null;
        for (const value of values) {
          docId = getDocId(value);
          if (docId) break;
        }

        if (!docId || seenDocIds.has(docId)) continue;
        seenDocIds.add(docId);

        results.push({
          id: docId,
          name: pickName(element),
          url: element.href || element.getAttribute('href') || element.getAttribute('data-href')
        });
      }

      return results;
    }
    """)

def discover_documents_from_folder(page, folder_id: str, folder_url: str = None):
    """
    Navigate to folder and discover all documents.
    Returns list of {id, name} dictionaries.
    """
    log(f"📂 Navigating to folder: {folder_id}")
    network_documents = attach_network_document_collector(page, folder_id)
    
    # Use provided URL or construct default
    if not folder_url:
        folder_url = f"https://lucid.app/documents#/documents?folder_id={folder_id}"
    
    if page_matches_folder(page.url, folder_id) and looks_like_folder_page(page.url):
        log(f"  Using currently loaded page: {page.url}")
    else:
        log(f"  Opening folder URL: {folder_url}")
        page.goto(folder_url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_load_state("networkidle", timeout=30000)
    
    # Wait for page to load
    page.wait_for_timeout(3000)
    
    # Check if we're on a valid folder page
    if "login" in page.url.lower():
        log("⚠️  Redirected to login page")
        return None
    
    log("🔍 Discovering documents in folder...")
    log(f"  Current page URL: {page.url}")
    log_page_diagnostics(page)
    
    documents = []
    seen_ids = set()
    previous_height = -1
    
    for pass_num in range(1, 6):
        page.wait_for_timeout(2000)
        
        if network_documents:
            new_docs = 0
            for doc in network_documents.values():
                if doc["id"] in seen_ids:
                    continue
                seen_ids.add(doc["id"])
                documents.append(doc)
                new_docs += 1
            
            if new_docs:
                log(f"  Pass {pass_num}: added {new_docs} docs from network responses")
        
        candidates = collect_document_candidates(page)
        log(f"  Pass {pass_num}: found {len(candidates)} document candidates")
        
        for candidate in candidates:
            doc_id = extract_document_id(candidate.get("id"))
            if not doc_id or doc_id in seen_ids:
                continue
            
            seen_ids.add(doc_id)
            doc_name = normalize_document_name(candidate.get("name"), doc_id)
            
            log(f"  ✓ Found: {doc_name} (ID: {doc_id[:8]}...)")
            documents.append({
                "id": doc_id,
                "name": doc_name,
                "product": "unknown",
                "edit_url": candidate.get("url")
            })
        
        current_height = page.evaluate("document.body.scrollHeight")
        if current_height == previous_height and documents:
            break
        
        previous_height = current_height
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    
    log(f"✅ Discovered {len(documents)} documents")
    
    if documents:
        log("\nFirst 5 documents:")
        for doc in documents[:5]:
            log(f"  - {doc['name']}")
        if len(documents) > 5:
            log(f"  ... and {len(documents) - 5} more")
    
    return documents

def get_page_text_sample(page) -> str:
    """Extract a short body text snippet for diagnostics."""
    try:
        sample = page.evaluate(
            "() => document.body && document.body.innerText ? document.body.innerText.slice(0, 300) : ''"
        )
        return sample.replace("\n", " ").strip()
    except Exception:
        return ""

def page_looks_not_found(page) -> bool:
    """Check whether the current page looks like a not-found or unavailable page."""
    title = ""
    try:
        title = page.title().lower()
    except Exception:
        title = ""
    
    url = (page.url or "").lower()
    body = get_page_text_sample(page).lower()
    indicators = (
        "page not found",
        "not found",
        "doesn't exist",
        "isn't available",
        "no longer exists",
    )
    haystack = " ".join((title, url, body))
    return any(indicator in haystack for indicator in indicators)

def log_export_page_context(page, prefix: str = "  "):
    """Log the current export page state for failure diagnosis."""
    try:
        log(f"{prefix}Current export URL: {page.url}")
    except Exception:
        pass
    
    try:
        log(f"{prefix}Current export title: {page.title() or '(empty)'}")
    except Exception:
        pass
    
    body_sample = get_page_text_sample(page)
    if body_sample:
        log(f"{prefix}Current export body: {body_sample[:200]}")

def document_output_path(output_dir: str, doc) -> str:
    """Return the export path for a document, including nested folder paths when present."""
    folder_path = doc.get("folder_path", "")
    if folder_path:
        return os.path.join(output_dir, folder_path, f"{doc['name']}.vsdx")
    return os.path.join(output_dir, f"{doc['name']}.vsdx")

def document_display_name(doc) -> str:
    """Return a readable display path for logging."""
    folder_path = doc.get("folder_path", "")
    return f"{folder_path}/{doc['name']}" if folder_path else doc["name"]

def build_export_urls(doc) -> list:
    """Build candidate browser URLs for exporting a document."""
    doc_id = doc["id"]
    product = normalize_product(doc.get("product"))
    urls = []
    
    for value in (doc.get("edit_url"), doc.get("url")):
        if isinstance(value, str) and value.strip():
            candidate = value.strip()
            if candidate not in urls:
                urls.append(candidate)
    
    if product in ("lucidchart", "chart", "document", "unknown"):
        fallback = f"https://lucid.app/lucidchart/{doc_id}/edit"
        if fallback not in urls:
            urls.append(fallback)
    
    return urls

def export_document(page, doc, output_dir: str):
    """
    Export a single document to VSDX format.
    Returns True if successful, False otherwise.
    """
    doc_id = doc["id"]
    doc_name = doc["name"]
    output_path = document_output_path(output_dir, doc)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    
    try:
        product = normalize_product(doc.get("product"))
        candidate_urls = build_export_urls(doc)
        
        if not candidate_urls:
            log(f"  ⚠️  No export URL available for {doc_name} (product: {product})")
            return False
        
        last_timeout_stage = "unknown"
        for edit_url in candidate_urls:
            try:
                log(f"  → Opening export URL: {edit_url}")
                last_timeout_stage = "navigation"
                page.goto(edit_url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_load_state("networkidle", timeout=15000)
                page.wait_for_timeout(2000)
                
                if "login" in page.url.lower():
                    log(f"  ⚠️  Session expired, need to re-login")
                    return False
                
                if page_looks_not_found(page):
                    log(f"  ⚠️  Export page unavailable for {doc_name}")
                    log_export_page_context(page)
                    continue
                
                last_timeout_stage = "editor-load"
                page.wait_for_selector("canvas, svg", timeout=10000)
                page.wait_for_timeout(3000)
                
                last_timeout_stage = "menu-trigger"
                hamburger = page.locator('[data-test-id="header-hamburger-menu"] [data-test-id="menu-trigger-button"]')
                hamburger.wait_for(state="visible", timeout=12000)
                hamburger.click(timeout=10000)
                page.wait_for_timeout(2000)
                
                try:
                    download_menu = page.locator("text=/Download|Export/i").first
                    if download_menu.is_visible(timeout=2000):
                        log(f"  → Found Download/Export menu")
                        download_menu.click(timeout=5000)
                        page.wait_for_timeout(1500)
                except Exception:
                    log(f"  → Looking for Visio option directly...")
                
                last_timeout_stage = "visio-option"
                visio_option = page.locator("text=/Visio.*VSDX|VSDX.*Visio|Visio \\(VSDX\\)/i").first
                visio_option.wait_for(state="visible", timeout=10000)
                
                log(f"  → Clicking Visio export...")
                
                last_timeout_stage = "download"
                with page.expect_download(timeout=60000) as download_info:
                    visio_option.click(timeout=5000)
                    page.wait_for_timeout(2000)
                
                download = download_info.value
                download.save_as(output_path)
                
                if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                    file_size = os.path.getsize(output_path)
                    log(f"  ✅ Exported: {doc_name} ({file_size:,} bytes)")
                    return True
                
                log(f"  ❌ File not saved: {doc_name}")
                return False
            except PlaywrightTimeout:
                log(f"  ⚠️  Timeout during {last_timeout_stage} for {doc_name}")
                log_export_page_context(page)
                if page_looks_not_found(page):
                    log(f"  ⚠️  Export page appears unavailable for {doc_name}")
                continue
        
        log(f"  ⏱️  Timeout: {doc_name}")
        return False
             
    except PlaywrightTimeout:
        log(f"  ⏱️  Timeout: {doc_name}")
        return False
    except Exception as e:
        log(f"  ❌ Error: {doc_name} - {str(e)[:100]}")
        return False

def get_api_headers(api_key: str) -> dict:
    """Build Lucid API headers from the configured API key."""
    return {
        "Authorization": f"Bearer {api_key}",
        "Lucid-Api-Version": API_VERSION,
    }

def fetch_paginated_items(endpoint: str, headers: dict, error_prefix: str, *, log_response: bool = False):
    """Fetch paginated Lucid API items until no next page is present."""
    items = []
    page = 1
    
    while True:
        url = f"{API_BASE_URL}/{endpoint}"
        response = requests.get(
            url,
            headers=headers,
            params={"page": page, "limit": 100},
            timeout=30
        )
        
        if response.status_code != 200:
            log(f"⚠️  {error_prefix}: {response.status_code}")
            if log_response:
                log(f"   URL: {url}")
                log(f"   Response: {response.text[:200]}")
            return None
        
        data = response.json()
        batch = data.get("items", [])
        if not batch:
            break
        
        items.extend(batch)
        if not data.get("nextPageToken"):
            break
        
        page += 1
    
    return items

def extract_folder_id_and_url(input_str: str) -> tuple:
    """
    Extract folder ID and full URL from input.
    Returns (folder_id, full_url) tuple.
    """
    input_str = input_str.strip()
    
    # Check if it's a full URL
    if 'lucid.app' in input_str:
        # Team folder: https://lucid.app/documents#/teams/354992253?folder_id=suggestedTeamDocuments-354992253
        if '/teams/' in input_str and 'folder_id=' in input_str:
            folder_id = input_str.split('folder_id=')[1].split('&')[0].split('#')[0]
            return (folder_id, input_str)
        
        # Personal folder: https://lucid.app/documents#/documents?folder_id=386721887
        elif 'folder_id=' in input_str:
            folder_id = input_str.split('folder_id=')[1].split('&')[0].split('#')[0]
            return (folder_id, input_str)
        
        # Old format: https://lucid.app/folder/abc123
        elif '/folder/' in input_str:
            folder_id = input_str.split('/folder/')[1].split('/')[0].split('?')[0]
            folder_url = f"https://lucid.app/documents#/documents?folder_id={folder_id}"
            return (folder_id, folder_url)
    
    # Just an ID - assume personal folder
    folder_url = f"https://lucid.app/documents#/documents?folder_id={input_str}"
    return (input_str, folder_url)

def get_folders_hierarchy_api():
    """
    Get all folders and build hierarchy using Lucid API.
    Returns dict of {folder_id: {name, parent_id, path}} or None if fails.
    """
    api_key = os.getenv("LUCID_API_KEY")
    if not api_key:
        return None
    
    headers = get_api_headers(api_key)
    
    log(f"📡 Fetching folder hierarchy via API...")
    
    folders = {}
    
    try:
        items = fetch_paginated_items("folders", headers, "Folders API request failed", log_response=True)
        if items is None:
            return None
        
        for folder in items:
            folder_id = folder.get("id")
            folder_name = folder.get("title", "Untitled")
            parent_id = folder.get("parent", {}).get("id")
            
            folders[folder_id] = {
                "name": sanitize_filename(folder_name),
                "parent_id": parent_id,
                "path": None
            }
        
        log(f"✅ Found {len(folders)} folders via API")
        return folders
        
    except Exception as e:
        log(f"⚠️  Folders API error: {str(e)}")
        return None

def build_folder_paths(folders, root_folder_id):
    """Build full paths for folders based on parent relationships."""
    if not folders:
        return {}
    
    log("🗂️  Building folder hierarchy...")
    
    # Set root folder path
    if root_folder_id in folders:
        folders[root_folder_id]["path"] = ""
    
    # Multiple passes to resolve nested folders
    max_iterations = 10
    for iteration in range(max_iterations):
        unresolved = False
        for folder_id, folder_info in folders.items():
            if folder_info["path"] is None:
                parent_id = folder_info["parent_id"]
                
                # If parent is root folder
                if parent_id == root_folder_id:
                    folders[folder_id]["path"] = folder_info["name"]
                # If parent exists and has path
                elif parent_id in folders and folders[parent_id]["path"] is not None:
                    parent_path = folders[parent_id]["path"]
                    if parent_path:
                        folders[folder_id]["path"] = f"{parent_path}/{folder_info['name']}"
                    else:
                        folders[folder_id]["path"] = folder_info["name"]
                else:
                    unresolved = True
        
        if not unresolved:
            break
    
    # Count resolved folders
    resolved = sum(1 for f in folders.values() if f["path"] is not None)
    log(f"✅ Resolved {resolved}/{len(folders)} folder paths")
    
    return folders

def get_documents_from_folder_api(folder_id: str, folders_hierarchy=None):
    """
    Get documents from a specific folder using Lucid API.
    Returns list of {id, name, folder_path} dictionaries.
    """
    api_key = os.getenv("LUCID_API_KEY")
    if not api_key:
        log("⚠️  LUCID_API_KEY not found in .env file")
        log("Will use browser-based discovery instead")
        return None
    
    headers = get_api_headers(api_key)
    
    log(f"📡 Fetching documents from folder via API...")
    
    documents = []
    
    try:
        items = fetch_paginated_items("documents", headers, "API request failed")
        if items is None:
            return None
        
        for doc in items:
            doc_folder_id = doc.get("parent", {}).get("id")
            
            # Check if document is in target folder or subfolders
            is_in_folder = False
            folder_path = ""
            
            if str(doc_folder_id) == str(folder_id):
                is_in_folder = True
            elif folders_hierarchy and doc_folder_id in folders_hierarchy:
                current_id = doc_folder_id
                path_parts = []
                
                for _ in range(20):  # Max depth
                    if current_id not in folders_hierarchy:
                        break
                    
                    folder_info = folders_hierarchy[current_id]
                    
                    if str(current_id) == str(folder_id):
                        is_in_folder = True
                        folder_path = "/".join(reversed(path_parts))
                        break
                    
                    path_parts.append(folder_info["name"])
                    parent_id = folder_info["parent_id"]
                    
                    if str(parent_id) == str(folder_id):
                        is_in_folder = True
                        folder_path = "/".join(reversed(path_parts))
                        break
                    
                    current_id = parent_id
            
            if is_in_folder:
                doc_id = doc.get("id")
                doc_title = doc.get("title", "Untitled")
                product = normalize_product(doc.get("product", "lucidchart"))
                
                if product == "lucidchart":
                    documents.append({
                        "id": doc_id,
                        "name": sanitize_filename(doc_title),
                        "folder_path": folder_path,
                        "product": product,
                        "edit_url": doc.get("editUrl") or doc.get("editorUrl") or doc.get("url")
                    })
        
        log(f"✅ Found {len(documents)} documents via API")
        return documents
        
    except Exception as e:
        log(f"⚠️  API error: {str(e)}")
        return None

def update_checkpoint_state(checkpoint, completed, failed_docs):
    """Persist the current export progress to the checkpoint file."""
    checkpoint["completed"] = list(completed)
    checkpoint["failed"] = list(failed_docs.values())
    save_checkpoint(checkpoint)

def get_folder_name(page, folder_id: str) -> str:
    """Derive a filesystem-safe folder name from the current page title."""
    try:
        folder_name = page.title().split('|')[0].strip()
        if folder_name:
            return sanitize_filename(folder_name)
    except Exception:
        pass
    
    return f"folder_{folder_id[:8]}"

def main():
    # Check arguments
    if len(sys.argv) < 2:
        print("Usage: ./export_folder.py <folder_id_or_url>")
        print("\nExamples:")
        print("  ./export_folder.py 386721887")
        print("  ./export_folder.py https://lucid.app/documents#/documents?folder_id=386721887")
        print("  ./export_folder.py https://lucid.app/documents#/teams/354992253?folder_id=suggestedTeamDocuments-354992253")
        print("\nGet folder_id from Lucid URL")
        sys.exit(1)
    
    folder_id, folder_url = extract_folder_id_and_url(sys.argv[1])
    
    log("="*60)
    log("Lucid Folder Export - Simplified Workflow")
    log("="*60)
    log(f"Folder ID: {folder_id}")
    
    # Load checkpoint
    checkpoint = load_checkpoint(folder_id)
    completed = set(checkpoint["completed"])
    failed_docs = {f["id"]: f for f in checkpoint.get("failed", [])}
    
    log(f"Previously completed: {len(completed)}")
    
    log("\n" + "="*60)
    log("BROWSER WILL OPEN")
    log("="*60)
    log("1. Browser will open to your Lucid folder")
    log("2. Log in to Lucid if needed")
    log("3. Wait for folder to load")
    log("4. Press ENTER to start discovery and export")
    log("\nPress ENTER to continue...")
    input()
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()
        
        # Navigate to folder using provided URL
        page.goto(folder_url)
        
        log("\n⏸️  Please log in to Lucid if needed")
        log("Make sure the folder page is fully loaded")
        log("Press ENTER when ready to discover documents...")
        input()
        
        # Try API-based discovery first (with folder hierarchy)
        api_key = os.getenv("LUCID_API_KEY")
        if api_key:
            log("\n📡 Attempting API-based discovery...")
            folders_hierarchy = get_folders_hierarchy_api()
            documents = get_documents_from_folder_api(folder_id, folders_hierarchy)
        else:
            log("\n⚠️  No LUCID_API_KEY found - using browser-based discovery")
            documents = None
        
        # Fall back to browser-based discovery if API fails
        if not documents:
            if api_key:
                log("\n🌐 API unavailable, using browser-based discovery...")
            documents = discover_documents_from_folder(page, folder_id, folder_url)
            # Add empty folder_path for browser-discovered documents
            if documents:
                for doc in documents:
                    doc.setdefault("folder_path", "")
        
        if not documents:
            log("\n❌ No documents found in folder!")
            log("Please check:")
            log("  - Folder ID is correct")
            log("  - You have access to the folder")
            log("  - Folder contains documents")
            browser.close()
            sys.exit(1)
        
        # Get folder name from page title
        folder_name = get_folder_name(page, folder_id)
        checkpoint["folder_name"] = folder_name
        
        # Create output directory
        output_dir = os.path.join(OUTPUT_BASE, folder_name)
        os.makedirs(output_dir, exist_ok=True)
        log(f"\n📁 Output directory: {output_dir}")
        
        # Filter documents to process
        to_process = [doc for doc in documents if doc["id"] not in completed]
        
        # Check for existing files (considering subfolder structure)
        for doc in to_process[:]:
            output_path = document_output_path(output_dir, doc)
            if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                completed.add(doc["id"])
                to_process.remove(doc)
        
        log(f"\nAlready exported: {len(completed)}")
        log(f"To export: {len(to_process)}")
        
        if not to_process:
            log("\n✅ All documents already exported!")
            browser.close()
            return
        
        log("\n✅ Starting export...\n")
        
        success_count = 0
        fail_count = 0
        
        for i, doc in enumerate(to_process, 1):
            log(f"[{i}/{len(to_process)}] Exporting: {document_display_name(doc)}")
            
            if export_document(page, doc, output_dir):
                completed.add(doc["id"])
                success_count += 1
                if doc["id"] in failed_docs:
                    del failed_docs[doc["id"]]
            else:
                fail_count += 1
                failed_docs[doc["id"]] = {
                    "id": doc["id"],
                    "name": doc["name"],
                    "folder_path": doc.get("folder_path", ""),
                    "attempts": failed_docs.get(doc["id"], {}).get("attempts", 0) + 1
                }
            
            # Save checkpoint every 5 documents
            if i % 5 == 0:
                update_checkpoint_state(checkpoint, completed, failed_docs)
                log(f"  💾 Checkpoint saved ({success_count} successful, {fail_count} failed)")
            
            # Small delay between documents
            time.sleep(2)
        
        browser.close()
    
    # Final save
    update_checkpoint_state(checkpoint, completed, failed_docs)
    
    # Summary
    log("\n" + "="*60)
    log("EXPORT COMPLETE!")
    log("="*60)
    log(f"Total documents: {len(documents)}")
    log(f"Successfully exported: {success_count}")
    log(f"Failed: {fail_count}")
    log(f"Already completed: {len(completed) - success_count}")
    
    if failed_docs:
        log("\n⚠️  Failed documents:")
        for doc in list(failed_docs.values())[:10]:
            log(f"  - {doc['name']} (attempts: {doc['attempts']})")
        if len(failed_docs) > 10:
            log(f"  ... and {len(failed_docs) - 10} more")
    
    log(f"\n📁 Exported files: {output_dir}/")
    log(f"📄 Export log: {LOG_FILE}")
    log(f"💾 Checkpoint: {CHECKPOINT_FILE}")

if __name__ == "__main__":
    main()
