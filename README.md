# WhatsApp Chat Export to PDF

`whatsapp_to_pdf_fixed.py` converts compatible WhatsApp text chat exports (`.txt`) into styled PDF chat histories. It scans a folder, checks that each text file resembles a WhatsApp export, and saves a PDF beside each compatible source file. Other files are left unchanged.

The PDFs include date dividers, sender-aligned message bubbles, timestamps, system messages, and placeholders for omitted media, deleted messages, and edited messages. Media itself is not embedded in the PDF.

## Requirements

- Python 3.9 or newer
- `reportlab` to create PDFs
- `emoji` for improved descriptions of unsupported emoji characters (optional)
- `fontTools` for checking font character coverage (optional)
- A usable TrueType font: DejaVu Sans is preferred; Arial is used as a fallback on Windows. For best Unicode coverage, put DejaVu Sans regular, bold, and oblique `.ttf` files in a `fonts` folder beside the script if they are not installed on the system.

Install the Python packages from PowerShell:

```powershell
py -m pip install --upgrade reportlab emoji fonttools
```

Only `reportlab` is required to create PDFs. The other two packages are optional enhancements.

## Run

In PowerShell, change to the folder containing the script:

```powershell
cd "C:\path\to\whatsapp_to_pdf"
```

Run it and paste the folder containing the exported chat text files when prompted:

```powershell
py whatsapp_to_pdf_fixed.py
```

Or provide the folder directly:

```powershell
py whatsapp_to_pdf_fixed.py "C:\path\to\chat-exports"
```

By default, the script scans only that folder's top level. Add `--recursive` to include subfolders:

```powershell
py whatsapp_to_pdf_fixed.py "C:\path\to\chat-exports" --recursive
```

## Options

- `--rename "OLD=NEW"` changes a sender's displayed name. Repeat the option to rename multiple senders.
- `--me NAME` specifies which sender's messages appear on the right in green.
- `--title TEXT` forces the same title in the PDF header for every converted file; otherwise each title is derived from its filename.
- `--recursive` searches subfolders too.

Example:

```powershell
py whatsapp_to_pdf_fixed.py "C:\path\to\chat-exports" --rename "Old Name=New Name" --me "New Name" --recursive
```

By default, no sender names are renamed and no sender is designated as “me,” so messages are aligned on the left. Use `--rename` and `--me` for a specific export, or edit `DEFAULT_RENAME` and `DEFAULT_ME` near the top of the script to set your own defaults.

## Input and output behavior

- The input must be a WhatsApp chat exported as a text (`.txt`) file. This tool does not import WhatsApp databases or decrypt backups.
- Each compatible export produces a PDF with the same filename in the same folder. For example, `Chat.txt` becomes `Chat.pdf`.
- Existing PDFs with the same name may be replaced when converted again.
- Non-WhatsApp text files are skipped. The script reports converted and skipped files in the terminal.
- Attachments and media are not included; omitted media is shown as a placeholder.

## Privacy

WhatsApp chat exports can contain private or sensitive information. Keep the `.txt` exports and generated PDFs out of public repositories. The accompanying `.gitignore` excludes `.txt` and `.pdf` files by default.

