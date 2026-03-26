# Lucid Folder Export - Simple & Automated

Export all diagrams from any Lucid folder with a single command.

## Quick Start

```bash
./export_folder.py <folder_url>
```

## Installation

### 1. Install Dependencies

```bash
# Install Python packages
pip install playwright python-dotenv requests

# Install browser
playwright install chromium
```

### 2. Optional: Create .env File

The script works without any API key using browser-based discovery.

If you want to experiment with API-based features (currently limited), create a `.env` file:

```bash
LUCID_API_KEY=your-api-key-here
```

**Note:** Lucid's REST API has limited endpoints available. The script primarily uses browser automation for reliable document discovery and export.

## Usage

### 1. Get Your Folder URL

Open your Lucid folder in browser and copy the URL:

**Personal Folder:**
Example:

```
https://lucid.app/documents#/documents?folder_id=386721887

```

**Team Folder:**

Example:

```
https://lucid.app/documents#/teams/354992253?folder_id=suggestedTeamDocuments-354992253
```

### 2. Run Export

```bash
./export_folder.py "https://lucid.app/documents#/documents?folder_id=386721887"
```

Or just use the folder ID:
```bash
./export_folder.py 386721887
```

### 3. Log In & Export

1. Browser opens automatically
2. Log in to Lucid (one time)
3. Wait for folder to load
4. Press ENTER
5. Script exports all documents automatically

### 4. Get Your Files

Exported files are saved to:
```
exports/{folder_name}/
  ├── Diagram1.vsdx
  ├── Diagram2.vsdx
  ├── Diagram3.vsdx
  └── Diagram4.vsdx
```

**Note:** All documents from the folder (including subfolders) are exported to a single directory. Subfolder structure is not preserved due to Lucid API limitations.

## Features

 **Single Command** - No setup or configuration needed  
 **Automatic Discovery** - Finds all documents in folder via browser  
 **Personal & Team Folders** - Works with both  
 **Progress Tracking** - Saves checkpoint every 5 documents  
 **Resumable** - Continue after interruption  
 **No API Required** - Pure browser automation  

## Resume After Interruption

If export is interrupted, run the same command again:

```bash
./export_folder.py 386721887
```

The script will:
- Skip already exported files
- Continue from where it stopped
- Retry failed documents

## Troubleshooting

### "No documents found"
- Verify folder ID is correct
- Check you have access to the folder
- Ensure folder contains documents

### "Session expired"
- Log in again when prompted
- Script continues automatically

### Export failures
- Check `export_log.txt` for details
- Run script again to retry failed documents

