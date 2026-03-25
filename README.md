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

### 2. Get Lucid API Key

1. Go to https://lucid.app/users/settings#/api
2. Generate an API key
3. Copy the key

### 3. Create .env File

Create a `.env` file in the project root:

```bash
LUCID_API_KEY=your-api-key-here
```

Replace `your-api-key-here` with your actual API key from step 2.

**Note:** The API key is used for faster document discovery. If not provided, the script falls back to browser-based discovery (slower but still works).

## Usage

### 1. Get Your Folder URL

Open your Lucid folder in browser and copy the URL:

**Personal Folder:**
```
https://lucid.app/documents#/documents?folder_id=386721887
```

**Team Folder:**
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
  └── ...
```

## Features

✅ **Single Command** - No setup or configuration needed  
✅ **Automatic Discovery** - Finds all documents in folder  
✅ **Personal & Team Folders** - Works with both  
✅ **Progress Tracking** - Saves checkpoint every 5 documents  
✅ **Resumable** - Continue after interruption  
✅ **No API Key** - Uses browser authentication  

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

## Sharing with Team

Share these files:
- `export_folder.py` - The export script
- `README.md` - This documentation
- `.env.example` - Environment template (optional)

Team members need:
1. Python 3.8+
2. Playwright: `pip install playwright python-dotenv`
3. Browser: `playwright install chromium`
4. Access to the Lucid folder

## Files

- `export_folder.py` - Main export script
- `README.md` - Documentation
- `TEAM_USAGE.md` - Quick guide for team members
- `README_SIMPLIFIED.md` - Alternative quick start
- `.env` - Your API key (optional, for API-based discovery)
- `.gitignore` - Protects sensitive files
- `exports/` - Output directory (created automatically)
- `venv/` - Python virtual environment

## How It Works

1. **Discovery**: Script navigates to folder and finds all document thumbnails
2. **Extraction**: Extracts document IDs from thumbnail `data-test-id` attributes
3. **Export**: For each document:
   - Opens document in edit mode
   - Clicks hamburger menu → Download → Visio (VSDX)
   - Saves file with original name
4. **Progress**: Saves checkpoint every 5 documents

## Requirements

- Python 3.8 or higher
- Playwright library
- Chromium browser (installed via Playwright)
- Access to Lucid folder

## License

Utility script for personal/team use. Ensure compliance with Lucid terms of service.
