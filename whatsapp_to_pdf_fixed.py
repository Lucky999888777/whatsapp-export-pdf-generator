#!/usr/bin/env python3
"""
whatsapp_to_pdf.py
==================

Convert every compatible WhatsApp chat export (.txt) inside a folder into a
PDF that looks like a real chat history: rounded speech bubbles, alternating
sides per sender, date dividers, a WhatsApp-style header, timestamps, and
placeholders for media/deleted/edited messages.

Run it with no arguments and it will ask you to paste in the folder path.
It then scans every file directly inside that folder, converts each file
that looks like a genuine WhatsApp .txt export, and leaves everything else
(other file types, unrelated .txt files) untouched. Each PDF is written
next to its source .txt file, in the same folder, with the same name (just
a .pdf extension instead of .txt).

USAGE
-----
    python whatsapp_to_pdf.py

    Paste the folder path containing your WhatsApp .txt exports: /path/to/chats

    # or pass the folder directly, skipping the prompt
    python whatsapp_to_pdf.py /path/to/chats

    # also look inside its subfolders
    python whatsapp_to_pdf.py /path/to/chats --recursive

    # explicit renaming / "me" / forced title (title normally auto-detected
    # per file from its filename, e.g. "WhatsApp Chat with X.txt" -> "X")
    python whatsapp_to_pdf.py /path/to/chats \
        --rename "Old Name=New Name" \
        --me "New Name"

OPTIONS
-------
    --rename "OLD=NEW"   Rename a sender as they appear in the .txt file to
                          whatever you want them to show as in the PDF.
                          Can be passed more than once. Applied to every
                          file that gets converted.
    --me NAME             The (renamed) sender whose bubbles should be shown
                          on the right in green, like "you" in WhatsApp.
    --title TEXT          Force this exact chat title in the header bar for
                          every converted file, instead of auto-detecting
                          one per file from its filename.
    --recursive           Also scan subfolders, not just the top level.

Requires: reportlab (pip install reportlab), and optionally the `emoji`
package (pip install emoji) for nicer fallback rendering of emoji that the
built-in font can't draw.
"""

import argparse
import os
import re
import sys
from datetime import datetime

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.pdfgen import canvas
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase import pdfmetrics

try:
    import emoji as emoji_lib
except ImportError:  # optional dependency
    emoji_lib = None

try:
    from fontTools.ttLib import TTFont as _FTFont
except ImportError:
    _FTFont = None


# ---------------------------------------------------------------------------
# Defaults (edit these, or override on the command line)
# ---------------------------------------------------------------------------
DEFAULT_RENAME = {}
DEFAULT_ME = None

# ---------------------------------------------------------------------------
# Fonts - DejaVu Sans ships on most Linux systems and covers a wide range
# of characters (Latin, Greek, and quite a few symbols/basic emoji).
# ---------------------------------------------------------------------------
# Font locations for Linux, Windows, and a local "fonts" folder.
# The original version only searched Linux paths, which caused the
# "Could not find DejaVu Sans" error on Windows.
FONT_DIRS = [
    "/usr/share/fonts/truetype/dejavu/",
    "/usr/share/fonts/dejavu/",
    os.path.join(os.environ.get("WINDIR", r"C:\\Windows"), "Fonts"),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts"),
]

FONT_REGULAR = "ChatFontRegular"
FONT_BOLD = "ChatFontBold"
FONT_ITALIC = "ChatFontItalic"

_SUPPORTED_CHARS = set()


def _find_font_file(names):
    """Find the first existing font file from a list of possible names."""
    if isinstance(names, str):
        names = [names]

    for d in FONT_DIRS:
        for name in names:
            p = os.path.join(d, name)
            if os.path.isfile(p):
                return p
    return None


def register_fonts():
    """Register fonts used by the PDF.

    Prefer DejaVu Sans because it supports many Unicode characters.
    On Windows, if DejaVu Sans is not installed, fall back to Arial,
    which is normally available. This prevents the program from stopping
    just because the Linux-only DejaVu paths are absent.
    """
    global _SUPPORTED_CHARS

    # Prefer DejaVu Sans.
    reg_path = _find_font_file([
        "DejaVuSans.ttf",
        "DejaVuSans.TTF",
    ])
    bold_path = _find_font_file([
        "DejaVuSans-Bold.ttf",
        "DejaVuSans-Bold.TTF",
    ])
    ital_path = _find_font_file([
        "DejaVuSans-Oblique.ttf",
        "DejaVuSans-Oblique.TTF",
    ])

    # Windows fallback: Arial is normally present in C:\Windows\Fonts.
    if not (reg_path and bold_path and ital_path):
        reg_path = _find_font_file(["arial.ttf", "Arial.ttf"])
        bold_path = _find_font_file(["arialbd.ttf", "Arial Bold.ttf"])
        ital_path = _find_font_file(["ariali.ttf", "Arial Italic.ttf"])

    if not (reg_path and bold_path and ital_path):
        sys.exit(
            "Could not find a usable TrueType font.\n"
            "For best Unicode/emoji support, install DejaVu Sans and "
            "place the .ttf files in a 'fonts' folder next to this script."
        )

    pdfmetrics.registerFont(TTFont(FONT_REGULAR, reg_path))
    pdfmetrics.registerFont(TTFont(FONT_BOLD, bold_path))
    pdfmetrics.registerFont(TTFont(FONT_ITALIC, ital_path))

    if _FTFont is not None:
        try:
            ft = _FTFont(reg_path)
            _SUPPORTED_CHARS = set(ft.getBestCmap().keys())
        except Exception:
            _SUPPORTED_CHARS = set()


# Zero-width / formatting codepoints that should just be dropped rather
# than shown as a fallback label.
_INVISIBLE_CODEPOINTS = {0x200D, 0xFE0F, 0x200B, 0x200C, 0x200E, 0x200F}


def sanitize_text(text):
    """Replace characters the PDF font cannot draw with a short bracketed
    description (derived from the emoji's name) instead of showing a
    broken-glyph box, and drop invisible joiner/formatting codepoints."""
    if not text:
        return text
    out = []
    for ch in text:
        cp = ord(ch)
        if cp in _INVISIBLE_CODEPOINTS:
            continue
        if 0x1F3FB <= cp <= 0x1F3FF:  # emoji skin-tone modifiers
            continue
        if ch in ("\n", "\t", "\r") or not _SUPPORTED_CHARS or cp in _SUPPORTED_CHARS:
            out.append(ch)
            continue
        if emoji_lib is not None and emoji_lib.is_emoji(ch):
            name = emoji_lib.demojize(ch).strip(":").replace("_", " ")
            out.append(f"[{name}]")
        else:
            out.append("?")
    return "".join(out)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------
MSG_START_RE = re.compile(r"^(\d{1,2}/\d{1,2}/\d{2,4}), (\d{1,2}:\d{2}) - (.*)$")
SENDER_RE = re.compile(r"^([^:]{1,60}?): (.*)$")

EDITED_SUFFIX = " <This message was edited>"
DELETED_TEXTS = {"This message was deleted", "You deleted this message"}
MEDIA_TEXT = "<Media omitted>"


def parse_chat(path, rename_map):
    with open(path, encoding="utf-8-sig") as f:
        raw_lines = f.read().split("\n")

    messages = []
    for line in raw_lines:
        m = MSG_START_RE.match(line)
        if m:
            date_str, time_str, rest = m.groups()
            try:
                date_obj = datetime.strptime(date_str, "%d/%m/%y").date()
            except ValueError:
                date_obj = datetime.strptime(date_str, "%d/%m/%Y").date()

            sm = SENDER_RE.match(rest)
            if sm:
                sender, text = sm.groups()
                sender = rename_map.get(sender, sender)
                system = False
            else:
                sender, text, system = None, rest, True

            messages.append(
                {
                    "date": date_obj,
                    "time": time_str,
                    "sender": sender,
                    "text": text,
                    "system": system,
                    "media": False,
                    "deleted": False,
                    "edited": False,
                }
            )
        else:
            # continuation of a multi-line message
            if messages:
                messages[-1]["text"] += "\n" + line

    for msg in messages:
        t = msg["text"]
        if t.endswith(EDITED_SUFFIX):
            msg["edited"] = True
            t = t[: -len(EDITED_SUFFIX)]
        if t.strip() == MEDIA_TEXT:
            msg["media"] = True
            t = ""
        elif t.strip() in DELETED_TEXTS:
            msg["deleted"] = True
        msg["text"] = sanitize_text(t)

    return messages


def is_compatible_export(messages):
    """A file is treated as a real WhatsApp export only if it contains at
    least one actual chat message (date/time + sender + text) - not just
    stray lines that happen to match the date pattern."""
    return any(not m["system"] for m in messages)


# ---------------------------------------------------------------------------
# Layout constants
# ---------------------------------------------------------------------------
PAGE_W, PAGE_H = A4
MARGIN_X = 26
HEADER_H = 58
FOOTER_H = 22
CONTENT_TOP = PAGE_H - HEADER_H - 10
CONTENT_BOTTOM = FOOTER_H + 6
CHAT_WIDTH = PAGE_W - 2 * MARGIN_X
BUBBLE_MAX_WIDTH = CHAT_WIDTH * 0.72
PAD_X = 10
PAD_Y = 7
LINE_HEIGHT = 12.5
FONT_SIZE = 10
TIME_FONT_SIZE = 7.3
META_ROW_H = 11
BUBBLE_RADIUS = 8
TAIL_SIZE = 7

BG_COLOR = colors.HexColor("#E4DDD6")
HEADER_COLOR = colors.HexColor("#075E54")
HEADER_SUB_COLOR = colors.HexColor("#B7DCD6")
BUBBLE_OUT = colors.HexColor("#D9FDD3")   # "me" / outgoing sender
BUBBLE_IN = colors.white                  # friend
BUBBLE_SHADOW = colors.Color(0, 0, 0, alpha=0.06)
TEXT_COLOR = colors.HexColor("#111B21")
MUTED_TEXT_COLOR = colors.HexColor("#667781")
DATE_PILL_COLOR = colors.HexColor("#FFFFFF")
SYSTEM_PILL_COLOR = colors.HexColor("#FEF6E0")
SYSTEM_TEXT_COLOR = colors.HexColor("#8A6D0B")
MEDIA_ICON_COLOR = colors.HexColor("#9AA6AA")
DELETE_COLOR = colors.HexColor("#8696A0")


def wrap_text(text, font, size, max_width):
    """Word-wrap text (which may already contain explicit newlines) to fit
    within max_width, splitting oversized single words/URLs by character."""
    lines = []
    for paragraph in text.split("\n"):
        if paragraph == "":
            lines.append("")
            continue
        words = paragraph.split(" ")
        current = ""
        for word in words:
            trial = word if not current else current + " " + word
            if pdfmetrics.stringWidth(trial, font, size) <= max_width:
                current = trial
                continue
            if current:
                lines.append(current)
                current = ""
            # word alone may still be too wide (long URL/no-space string)
            while pdfmetrics.stringWidth(word, font, size) > max_width and len(word) > 1:
                split_at = 1
                while (
                    split_at < len(word)
                    and pdfmetrics.stringWidth(word[: split_at + 1], font, size) <= max_width
                ):
                    split_at += 1
                lines.append(word[:split_at])
                word = word[split_at:]
            current = word
        if current or paragraph == "":
            lines.append(current)
    return lines if lines else [""]


def build_blocks(messages, me_name):
    """Turn parsed messages into drawable blocks with pre-computed sizes."""
    blocks = []
    last_date = None

    for msg in messages:
        if msg["date"] != last_date:
            label = msg["date"].strftime("%d %B %Y")
            blocks.append({"kind": "date", "label": label})
            last_date = msg["date"]

        if msg["system"]:
            lines = wrap_text(msg["text"], FONT_ITALIC, 8.5, CHAT_WIDTH * 0.75 - 2 * PAD_X)
            blocks.append({"kind": "system", "lines": lines})
            continue

        align = "right" if msg["sender"] == me_name else "left"

        if msg["media"]:
            blocks.append(
                {
                    "kind": "media",
                    "align": align,
                    "time": msg["time"],
                    "edited": msg["edited"],
                    "height": 76,
                    "width": 150,
                }
            )
            continue

        if msg["deleted"]:
            label = "This message was deleted"
            width = pdfmetrics.stringWidth(label, FONT_ITALIC, FONT_SIZE) + 2 * PAD_X + 16
            blocks.append(
                {
                    "kind": "deleted",
                    "align": align,
                    "time": msg["time"],
                    "width": width,
                    "height": 2 * PAD_Y + LINE_HEIGHT + META_ROW_H,
                }
            )
            continue

        is_blank = msg["text"].strip() == ""
        text = msg["text"] if not is_blank else "(blank message)"
        font_for_wrap = FONT_ITALIC if is_blank else FONT_REGULAR
        avail = BUBBLE_MAX_WIDTH - 2 * PAD_X
        lines = wrap_text(text, font_for_wrap, FONT_SIZE, avail)
        max_line_w = max(pdfmetrics.stringWidth(l, font_for_wrap, FONT_SIZE) for l in lines)
        time_w = pdfmetrics.stringWidth(
            ("Edited  " if msg["edited"] else "") + msg["time"], FONT_ITALIC, TIME_FONT_SIZE
        )
        width = max(max_line_w, time_w) + 2 * PAD_X
        width = max(width, 70)
        height = 2 * PAD_Y + len(lines) * LINE_HEIGHT + META_ROW_H
        blocks.append(
            {
                "kind": "msg",
                "align": align,
                "lines": lines,
                "time": msg["time"],
                "edited": msg["edited"],
                "blank": is_blank,
                "width": width,
                "height": height,
            }
        )

    return blocks


# ---------------------------------------------------------------------------
# Drawing helpers
# ---------------------------------------------------------------------------
def draw_background(c):
    c.setFillColor(BG_COLOR)
    c.rect(0, 0, PAGE_W, PAGE_H, stroke=0, fill=1)


def draw_header(c, title, subtitle):
    c.setFillColor(HEADER_COLOR)
    c.rect(0, PAGE_H - HEADER_H, PAGE_W, HEADER_H, stroke=0, fill=1)

    # avatar circle with the contact's initial
    cx, cy, r = MARGIN_X + 18, PAGE_H - HEADER_H / 2, 18
    c.setFillColor(colors.HexColor("#0B4A42"))
    c.circle(cx, cy, r, stroke=0, fill=1)
    c.setFillColor(colors.white)
    c.setFont(FONT_BOLD, 16)
    initial = (title[:1] or "?").upper()
    c.drawCentredString(cx, cy - 5.5, initial)

    c.setFillColor(colors.white)
    c.setFont(FONT_BOLD, 13)
    c.drawString(cx + r + 12, cy - 1, title)
    c.setFillColor(HEADER_SUB_COLOR)
    c.setFont(FONT_REGULAR, 8.5)
    c.drawString(cx + r + 12, cy - 13, subtitle)


def draw_footer(c, page_num):
    c.setFillColor(MUTED_TEXT_COLOR)
    c.setFont(FONT_REGULAR, 8)
    c.drawCentredString(PAGE_W / 2, 9, str(page_num))


def draw_date_pill(c, top_y, label):
    c.setFont(FONT_BOLD, 8.5)
    w = pdfmetrics.stringWidth(label, FONT_BOLD, 8.5) + 22
    h = 18
    x = (PAGE_W - w) / 2
    y = top_y - h
    c.setFillColor(BUBBLE_SHADOW)
    c.roundRect(x + 0.6, y - 0.6, w, h, h / 2, stroke=0, fill=1)
    c.setFillColor(DATE_PILL_COLOR)
    c.roundRect(x, y, w, h, h / 2, stroke=0, fill=1)
    c.setFillColor(MUTED_TEXT_COLOR)
    c.drawCentredString(PAGE_W / 2, y + h / 2 - 3, label)
    return h


def draw_system_pill(c, top_y, lines):
    h = 2 * 6 + len(lines) * 11
    w = max(pdfmetrics.stringWidth(l, FONT_ITALIC, 8.5) for l in lines) + 24
    x = (PAGE_W - w) / 2
    y = top_y - h
    c.setFillColor(SYSTEM_PILL_COLOR)
    c.roundRect(x, y, w, h, 9, stroke=0, fill=1)
    c.setFillColor(SYSTEM_TEXT_COLOR)
    c.setFont(FONT_ITALIC, 8.5)
    ty = y + h - 6 - 8
    for line in lines:
        c.drawCentredString(PAGE_W / 2, ty, line)
        ty -= 11
    return h


def _bubble_geometry(block):
    width = block["width"]
    align = block["align"]
    x = MARGIN_X if align == "left" else PAGE_W - MARGIN_X - width
    return x, width


def draw_tail(c, x, y_bottom, width, align, fill_color):
    """Small triangular tail at the bottom corner of the bubble."""
    c.setFillColor(fill_color)
    p = c.beginPath()
    if align == "left":
        p.moveTo(x, y_bottom + 9)
        p.lineTo(x - TAIL_SIZE, y_bottom + 2)
        p.lineTo(x, y_bottom)
    else:
        p.moveTo(x + width, y_bottom + 9)
        p.lineTo(x + width + TAIL_SIZE, y_bottom + 2)
        p.lineTo(x + width, y_bottom)
    p.close()
    c.drawPath(p, stroke=0, fill=1)


def draw_message_bubble(c, block, top_y):
    height = block["height"]
    x, width = _bubble_geometry(block)
    y_bottom = top_y - height
    fill = BUBBLE_OUT if block["align"] == "right" else BUBBLE_IN

    c.setFillColor(BUBBLE_SHADOW)
    c.roundRect(x + 1, y_bottom - 1, width, height, BUBBLE_RADIUS, stroke=0, fill=1)
    c.setFillColor(fill)
    c.roundRect(x, y_bottom, width, height, BUBBLE_RADIUS, stroke=0, fill=1)
    draw_tail(c, x, y_bottom, width, block["align"], fill)

    is_blank = block.get("blank", False)
    c.setFillColor(MUTED_TEXT_COLOR if is_blank else TEXT_COLOR)
    c.setFont(FONT_ITALIC if is_blank else FONT_REGULAR, FONT_SIZE)
    ty = top_y - PAD_Y - FONT_SIZE + 1
    for line in block["lines"]:
        c.drawString(x + PAD_X, ty, line)
        ty -= LINE_HEIGHT

    meta = (("Edited  ") if block["edited"] else "") + block["time"]
    c.setFillColor(MUTED_TEXT_COLOR)
    c.setFont(FONT_ITALIC, TIME_FONT_SIZE)
    c.drawRightString(x + width - PAD_X, y_bottom + 5, meta)


def draw_media_bubble(c, block, top_y):
    height, x_width = block["height"], block["width"]
    x, width = _bubble_geometry(block)
    y_bottom = top_y - height
    fill = BUBBLE_OUT if block["align"] == "right" else BUBBLE_IN

    c.setFillColor(BUBBLE_SHADOW)
    c.roundRect(x + 1, y_bottom - 1, width, height, BUBBLE_RADIUS, stroke=0, fill=1)
    c.setFillColor(fill)
    c.roundRect(x, y_bottom, width, height, BUBBLE_RADIUS, stroke=0, fill=1)
    draw_tail(c, x, y_bottom, width, block["align"], fill)

    # simple vector "photo" placeholder icon (frame + mountain + sun)
    icon_x, icon_w, icon_h = x + PAD_X, width - 2 * PAD_X, 26
    icon_y = y_bottom + height - PAD_Y - icon_h
    c.setStrokeColor(MEDIA_ICON_COLOR)
    c.setFillColor(colors.HexColor("#F0F2F1"))
    c.roundRect(icon_x, icon_y, icon_w, icon_h, 4, stroke=1, fill=1)
    c.setFillColor(MEDIA_ICON_COLOR)
    c.circle(icon_x + 9, icon_y + icon_h - 8, 3, stroke=0, fill=1)
    p = c.beginPath()
    p.moveTo(icon_x + 3, icon_y + 5)
    p.lineTo(icon_x + icon_w * 0.4, icon_y + icon_h - 6)
    p.lineTo(icon_x + icon_w * 0.6, icon_y + 10)
    p.lineTo(icon_x + icon_w - 3, icon_y + icon_h - 4)
    p.lineTo(icon_x + icon_w - 3, icon_y + 5)
    p.close()
    c.drawPath(p, stroke=0, fill=1)

    c.setFillColor(MUTED_TEXT_COLOR)
    c.setFont(FONT_ITALIC, 8)
    c.drawCentredString(x + width / 2, icon_y - 12, "Media not included")

    meta = (("Edited  ") if block["edited"] else "") + block["time"]
    c.setFillColor(MUTED_TEXT_COLOR)
    c.setFont(FONT_ITALIC, TIME_FONT_SIZE)
    c.drawRightString(x + width - PAD_X, y_bottom + 5, meta)


def draw_deleted_bubble(c, block, top_y):
    height = block["height"]
    x, width = _bubble_geometry(block)
    y_bottom = top_y - height
    fill = colors.HexColor("#ECE9E3")

    c.setFillColor(fill)
    c.roundRect(x, y_bottom, width, height, BUBBLE_RADIUS, stroke=0, fill=1)
    draw_tail(c, x, y_bottom, width, block["align"], fill)

    # small "no entry" icon
    icon_cx, icon_cy, icon_r = x + PAD_X + 6, y_bottom + height - PAD_Y - 6, 6
    c.setStrokeColor(DELETE_COLOR)
    c.setLineWidth(1.1)
    c.circle(icon_cx, icon_cy, icon_r, stroke=1, fill=0)
    c.line(icon_cx - 4, icon_cy - 4, icon_cx + 4, icon_cy + 4)

    c.setFillColor(DELETE_COLOR)
    c.setFont(FONT_ITALIC, FONT_SIZE - 0.5)
    c.drawString(x + PAD_X + 16, y_bottom + height - PAD_Y - FONT_SIZE + 2, "This message was deleted")

    c.setFillColor(MUTED_TEXT_COLOR)
    c.setFont(FONT_ITALIC, TIME_FONT_SIZE)
    c.drawRightString(x + width - PAD_X, y_bottom + 5, block["time"])


# ---------------------------------------------------------------------------
# Main render loop
# ---------------------------------------------------------------------------
def render_pdf(blocks, output_path, title):
    c = canvas.Canvas(output_path, pagesize=A4)
    page_num = 1
    y = None

    def start_page():
        nonlocal y, page_num
        draw_background(c)
        draw_header(c, title, "Exported chat history")
        draw_footer(c, page_num)
        y = CONTENT_TOP

    start_page()

    for block in blocks:
        if block["kind"] == "date":
            h = 18 + 14
        elif block["kind"] == "system":
            h = (2 * 6 + len(block["lines"]) * 11) + 14
        else:
            h = block["height"] + 8

        if y - h < CONTENT_BOTTOM:
            c.showPage()
            page_num += 1
            start_page()

        if block["kind"] == "date":
            used = draw_date_pill(c, y, block["label"])
            y -= used + 14
        elif block["kind"] == "system":
            used = draw_system_pill(c, y, block["lines"])
            y -= used + 14
        elif block["kind"] == "media":
            draw_media_bubble(c, block, y)
            y -= block["height"] + 8
        elif block["kind"] == "deleted":
            draw_deleted_bubble(c, block, y)
            y -= block["height"] + 8
        else:
            draw_message_bubble(c, block, y)
            y -= block["height"] + 8

    c.save()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def derive_title(input_path):
    base = os.path.splitext(os.path.basename(input_path))[0]
    m = re.match(r"WhatsApp[ _]Chat[ _]with[ _](.+)", base, re.IGNORECASE)
    if m:
        return m.group(1).replace("_", " ")
    return base.replace("_", " ")


def find_txt_files(folder, recursive):
    """Every .txt file directly inside `folder` (and its subfolders, if
    `recursive`), sorted for stable, predictable ordering."""
    found = []
    if recursive:
        for root, _dirs, files in os.walk(folder):
            for name in files:
                if name.lower().endswith(".txt"):
                    found.append(os.path.join(root, name))
    else:
        for name in os.listdir(folder):
            full = os.path.join(folder, name)
            if os.path.isfile(full) and name.lower().endswith(".txt"):
                found.append(full)
    return sorted(found)


def parse_args():
    ap = argparse.ArgumentParser(
        description="Convert every compatible WhatsApp .txt export in a folder into a styled PDF."
    )
    ap.add_argument(
        "folder",
        nargs="?",
        default=None,
        help="Folder to scan for WhatsApp .txt exports. If omitted, you'll be prompted for it.",
    )
    ap.add_argument(
        "--rename",
        action="append",
        default=[],
        metavar="OLD=NEW",
        help="Rename a sender, e.g. --rename \"Old Name=New Name\" (repeatable)",
    )
    ap.add_argument("--me", default=None, help="Renamed sender to show on the right in green")
    ap.add_argument(
        "--title", default=None, help="Force this chat title for every file (default: auto-detect per file)"
    )
    ap.add_argument(
        "--recursive", action="store_true", help="Also scan subfolders of the given folder"
    )
    return ap.parse_args()


def _clean_pasted_path(raw):
    """Terminals/file managers often paste a path wrapped in quotes, with a
    trailing slash, or with a trailing newline - tidy that up."""
    path = raw.strip().strip("\r\n")
    if len(path) >= 2 and path[0] == path[-1] and path[0] in ("'", '"'):
        path = path[1:-1]
    return os.path.expanduser(path.strip())


def prompt_for_folder():
    while True:
        raw = input("Paste the folder path containing your WhatsApp .txt exports: ")
        folder = _clean_pasted_path(raw)
        if not folder:
            print("Please enter a folder path.")
            continue
        if not os.path.isdir(folder):
            print(f"That's not a folder I can find: {folder}\n")
            continue
        return folder


def main():
    args = parse_args()

    rename_map = dict(DEFAULT_RENAME)
    for pair in args.rename:
        if "=" not in pair:
            sys.exit(f"--rename expects OLD=NEW, got: {pair!r}")
        old, new = pair.split("=", 1)
        rename_map[old.strip()] = new.strip()

    me_name = args.me or DEFAULT_ME

    if args.folder:
        folder = _clean_pasted_path(args.folder)
        if not os.path.isdir(folder):
            sys.exit(f"Folder not found: {folder}")
    else:
        folder = prompt_for_folder()

    candidates = find_txt_files(folder, args.recursive)
    if not candidates:
        sys.exit(f"No .txt files found in: {folder}")

    register_fonts()

    converted, skipped = [], []

    for txt_path in candidates:
        name = os.path.relpath(txt_path, folder)
        try:
            messages = parse_chat(txt_path, rename_map)
        except UnicodeDecodeError:
            skipped.append((name, "not a readable text file"))
            continue
        except Exception as exc:  # noqa: BLE001 - report and keep going
            skipped.append((name, f"could not be read ({exc})"))
            continue

        if not messages or not is_compatible_export(messages):
            skipped.append((name, "doesn't look like a WhatsApp chat export"))
            continue

        title = args.title or derive_title(txt_path)
        output_path = os.path.splitext(txt_path)[0] + ".pdf"

        try:
            blocks = build_blocks(messages, me_name)
            render_pdf(blocks, output_path, title)
        except Exception as exc:  # noqa: BLE001 - report and keep going
            skipped.append((name, f"failed while rendering PDF ({exc})"))
            continue

        converted.append((name, os.path.basename(output_path), len(messages)))

    print(f"Converted {len(converted)} file(s):")
    for src, out, count in converted:
        print(f"  - {src}  ->  {out}  ({count} messages)")

    if skipped:
        print(f"\nSkipped {len(skipped)} file(s):")
        for name, reason in skipped:
            print(f"  - {name}: {reason}")


if __name__ == "__main__":
    main()

