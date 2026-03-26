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
import sys
import time
import requests
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

load_dotenv()

# Configuration
OUTPUT_BASE = "./exports"
CHECKPOINT_FILE = "./.export_checkpoint.json"
LOG_FILE = "./export_log.txt"
API_BASE_URL = "https://api.lucid.co"
API_VERSION = "1"

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

def discover_documents_from_folder(page, folder_id: str, folder_url: str = None):
    """
    Navigate to folder and discover all documents.
    Returns list of {id, name} dictionaries.
    """
    log(f"📂 Navigating to folder: {folder_id}")
    
    # Use provided URL or construct default
    if not folder_url:
        folder_url = f"https://lucid.app/documents#/documents?folder_id={folder_id}"
    
    page.goto(folder_url, wait_until="networkidle", timeout=30000)
    
    # Wait for page to load
    page.wait_for_timeout(5000)
    
    # Check if we're on a valid folder page
    if "login" in page.url.lower():
        log("⚠️  Redirected to login page")
        return None
    
    log("🔍 Discovering documents in folder...")
    
    # Wait longer for documents to load
    page.wait_for_timeout(5000)
    
    documents = []
    seen_ids = set()
    
    # Lucid uses thumbnail images with data-test-id containing document IDs
    # Pattern: [data-test-id="https://thumbs.lucid.app/documents/thumb/{doc_id}/..."]
    log("  Looking for document thumbnails...")
    
    # Find all elements with data-test-id containing thumbnail URLs
    all_elements = page.locator('[data-test-id*="thumbs.lucid.app/documents/thumb/"]').all()
    
    log(f"  Found {len(all_elements)} document thumbnails")
    
    for element in all_elements:
        try:
            test_id = element.get_attribute('data-test-id')
            if not test_id or 'thumbs.lucid.app/documents/thumb/' not in test_id:
                continue
            
            # Extract document ID from thumbnail URL
            # Format: https://thumbs.lucid.app/documents/thumb/{doc_id}/...
            parts = test_id.split('/documents/thumb/')
            if len(parts) < 2:
                continue
            
            doc_id = parts[1].split('/')[0]
            
            if not doc_id or doc_id in seen_ids or len(doc_id) < 10:
                continue
            
            seen_ids.add(doc_id)
            
            # Try to get document name from nearby text or aria-label
            doc_name = None
            
            # Try parent element's aria-label or title
            try:
                parent = element.locator('xpath=..').first
                doc_name = parent.get_attribute('aria-label') or parent.get_attribute('title')
            except:
                pass
            
            # Try to find text near the thumbnail
            if not doc_name:
                try:
                    # Look for text in parent container
                    parent = element.locator('xpath=../..').first
                    text = parent.inner_text().strip()
                    if text and len(text) < 200:
                        doc_name = text.split('\n')[0]  # Take first line
                except:
                    pass
            
            if not doc_name or len(doc_name) < 1:
                doc_name = f"Document_{doc_id[:8]}"
            
            log(f"  ✓ Found: {doc_name} (ID: {doc_id[:8]}...)")
            
            documents.append({
                "id": doc_id,
                "name": sanitize_filename(doc_name)
            })
            
        except Exception as e:
            log(f"  ⚠️  Error processing element: {str(e)[:50]}")
            continue
    
    log(f"✅ Discovered {len(documents)} documents")
    
    if documents:
        log("\nFirst 5 documents:")
        for doc in documents[:5]:
            log(f"  - {doc['name']}")
        if len(documents) > 5:
            log(f"  ... and {len(documents) - 5} more")
    
    return documents

def export_document(page, doc, output_dir: str):
    """
    Export a single document to VSDX format.
    Returns True if successful, False otherwise.
    """
    doc_id = doc["id"]
    doc_name = doc["name"]
    folder_path = doc.get("folder_path", "")
    
    # Create full output path with subfolder structure
    if folder_path:
        full_output_dir = os.path.join(output_dir, folder_path)
        os.makedirs(full_output_dir, exist_ok=True)
        output_path = os.path.join(full_output_dir, f"{doc_name}.vsdx")
    else:
        output_path = os.path.join(output_dir, f"{doc_name}.vsdx")
    
    try:
        # Navigate to document edit page
        edit_url = f"https://lucid.app/lucidchart/{doc_id}/edit"
        page.goto(edit_url, wait_until="networkidle", timeout=45000)
        
        # Wait for document to load
        page.wait_for_selector("canvas, svg", timeout=15000)
        page.wait_for_timeout(5000)
        
        # Check if redirected to login
        if "login" in page.url.lower():
            log(f"  ⚠️  Session expired, need to re-login")
            return False
        
        # Click hamburger menu
        hamburger = page.locator('[data-test-id="header-hamburger-menu"] [data-test-id="menu-trigger-button"]')
        hamburger.wait_for(state="visible", timeout=15000)
        hamburger.click(timeout=10000)
        page.wait_for_timeout(2000)
        
        # Look for Download/Export submenu
        try:
            download_menu = page.locator("text=/Download|Export/i").first
            if download_menu.is_visible(timeout=2000):
                log(f"  → Found Download/Export menu")
                download_menu.click(timeout=5000)
                page.wait_for_timeout(1500)
        except:
            log(f"  → Looking for Visio option directly...")
        
        # Click Visio option
        visio_option = page.locator("text=/Visio.*VSDX|VSDX.*Visio|Visio \\(VSDX\\)/i").first
        visio_option.wait_for(state="visible", timeout=10000)
        
        log(f"  → Clicking Visio export...")
        
        # Click and wait for download
        with page.expect_download(timeout=60000) as download_info:
            visio_option.click(timeout=5000)
            page.wait_for_timeout(2000)
        
        download = download_info.value
        
        # Save file
        download.save_as(output_path)
        
        # Verify file
        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            file_size = os.path.getsize(output_path)
            log(f"  ✅ Exported: {doc_name} ({file_size:,} bytes)")
            return True
        else:
            log(f"  ❌ File not saved: {doc_name}")
            return False
            
    except PlaywrightTimeout as e:
        log(f"  ⏱️  Timeout: {doc_name}")
        return False
    except Exception as e:
        log(f"  ❌ Error: {doc_name} - {str(e)[:100]}")
        return False

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
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Lucid-Api-Version": API_VERSION,
    }
    
    log(f"📡 Fetching folder hierarchy via API...")
    
    folders = {}
    page = 1
    
    try:
        while True:
            url = f"{API_BASE_URL}/folders"
            response = requests.get(
                url,
                headers=headers,
                params={"page": page, "limit": 100},
                timeout=30
            )
            
            if response.status_code != 200:
                log(f"⚠️  Folders API request failed: {response.status_code}")
                log(f"   URL: {url}")
                log(f"   Response: {response.text[:200]}")
                return None
            
            data = response.json()
            items = data.get("items", [])
            
            if not items:
                break
            
            for folder in items:
                folder_id = folder.get("id")
                folder_name = folder.get("title", "Untitled")
                parent_id = folder.get("parent", {}).get("id")
                
                folders[folder_id] = {
                    "name": sanitize_filename(folder_name),
                    "parent_id": parent_id,
                    "path": None
                }
            
            page += 1
            
            if not data.get("nextPageToken"):
                break
        
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
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Lucid-Api-Version": API_VERSION,
    }
    
    log(f"📡 Fetching documents from folder via API...")
    
    documents = []
    page = 1
    
    try:
        while True:
            # Get all documents
            response = requests.get(
                f"{API_BASE_URL}/documents",
                headers=headers,
                params={"page": page, "limit": 100},
                timeout=30
            )
            
            if response.status_code != 200:
                log(f"⚠️  API request failed: {response.status_code}")
                return None
            
            data = response.json()
            items = data.get("items", [])
            
            if not items:
                break
            
            # Process documents
            for doc in items:
                doc_folder_id = doc.get("parent", {}).get("id")
                
                # Check if document is in target folder or subfolders
                is_in_folder = False
                folder_path = ""
                
                if str(doc_folder_id) == str(folder_id):
                    is_in_folder = True
                    folder_path = ""
                elif folders_hierarchy and doc_folder_id in folders_hierarchy:
                    # Check if this folder is a subfolder of target
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
                    product = doc.get("product", "lucidchart")
                    
                    if product == "lucidchart":
                        documents.append({
                            "id": doc_id,
                            "name": sanitize_filename(doc_title),
                            "folder_path": folder_path
                        })
            
            page += 1
            
            if not data.get("nextPageToken"):
                break
        
        log(f"✅ Found {len(documents)} documents via API")
        return documents
        
    except Exception as e:
        log(f"⚠️  API error: {str(e)}")
        return None

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
            for doc in documents:
                if "folder_path" not in doc:
                    doc["folder_path"] = ""
        
        if not documents:
            log("\n❌ No documents found in folder!")
            log("Please check:")
            log("  - Folder ID is correct")
            log("  - You have access to the folder")
            log("  - Folder contains documents")
            browser.close()
            sys.exit(1)
        
        # Get folder name from page title
        try:
            folder_name = page.title().split('|')[0].strip()
            if not folder_name:
                folder_name = f"folder_{folder_id[:8]}"
        except:
            folder_name = f"folder_{folder_id[:8]}"
        
        folder_name = sanitize_filename(folder_name)
        checkpoint["folder_name"] = folder_name
        
        # Create output directory
        output_dir = os.path.join(OUTPUT_BASE, folder_name)
        os.makedirs(output_dir, exist_ok=True)
        log(f"\n📁 Output directory: {output_dir}")
        
        # Filter documents to process
        to_process = [doc for doc in documents if doc["id"] not in completed]
        
        # Check for existing files (considering subfolder structure)
        for doc in to_process[:]:
            folder_path = doc.get("folder_path", "")
            if folder_path:
                output_path = os.path.join(output_dir, folder_path, f"{doc['name']}.vsdx")
            else:
                output_path = os.path.join(output_dir, f"{doc['name']}.vsdx")
            
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
            folder_path = doc.get("folder_path", "")
            display_path = f"{folder_path}/{doc['name']}" if folder_path else doc['name']
            log(f"[{i}/{len(to_process)}] Exporting: {display_path}")
            
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
                    "folder_path": folder_path,
                    "attempts": failed_docs.get(doc["id"], {}).get("attempts", 0) + 1
                }
            
            # Save checkpoint every 5 documents
            if i % 5 == 0:
                checkpoint["completed"] = list(completed)
                checkpoint["failed"] = list(failed_docs.values())
                save_checkpoint(checkpoint)
                log(f"  💾 Checkpoint saved ({success_count} successful, {fail_count} failed)")
            
            # Small delay between documents
            time.sleep(2)
        
        browser.close()
    
    # Final save
    checkpoint["completed"] = list(completed)
    checkpoint["failed"] = list(failed_docs.values())
    save_checkpoint(checkpoint)
    
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
