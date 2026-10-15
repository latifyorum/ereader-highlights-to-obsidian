#!/usr/bin/env python3
"""
ereader_to_obsidian.py

Export e-reader highlights to Markdown notes for Obsidian, incrementally:
every run creates a new dated folder that contains ONLY the highlights
that were not exported before.

Built-in device support: Kobo (reads KoboReader.sqlite directly).
Other devices: add an adapter (see docs/ADDING_DEVICES.md).
"""

import argparse
import hashlib
import json
import re
import shutil
import sqlite3
import string
import sys
import tempfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent

DEFAULT_OUTPUT = BASE / "Highlight Exports"
DEFAULT_RENAMES = BASE / "renames.json"
STATE_NAME = ".ereader_state.json"

# True: the book's index note embeds all highlights of that export on one page.
# False: the index note only contains a list of links.
EMBED_IN_INDEX = True


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def clean(value):
    return (str(value) if value is not None else "").strip()


def safe_filename(name):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    return (name or "Untitled book")[:150]


def markdown_quote(text):
    lines = text.splitlines() or [text]
    return "\n".join("> " + line for line in lines)


def yaml_str(value):
    """Quoted, escaped string that is safe inside YAML frontmatter."""
    return '"' + clean(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


# --------------------------------------------------------------------------
# Device adapters
#
# An adapter is a pair of functions:
#   find(path_arg)  -> Path to the source file (or raise FileNotFoundError)
#   load(path)      -> list of record dicts
#
# Every record must contain these keys (empty string if unknown):
#   id          stable, unique id of the highlight (used for incremental export)
#   book_id     device-internal book identifier (only used in fallback titles)
#   title       book title
#   author      book author
#   text        highlighted text (must not be empty)
#   annotation  the reader's own note on the highlight
#   context     chapter / location information
#   date        creation date (any string; sorted as text)
# --------------------------------------------------------------------------

def find_kobo(path_arg):
    """Order: explicit path -> connected Kobo -> file next to the script."""
    if path_arg:
        path = Path(path_arg.strip('"'))
        if path.exists():
            return path
        raise FileNotFoundError(f"Path not found: {path}")

    for letter in string.ascii_uppercase:  # Windows drive letters
        candidate = Path(f"{letter}:/") / ".kobo" / "KoboReader.sqlite"
        if candidate.exists():
            print(f"Kobo device found: {candidate}")
            return candidate

    for root in ("/Volumes", "/media", "/run/media"):  # macOS / Linux
        root_path = Path(root)
        if root_path.exists():
            for candidate in root_path.glob("**/.kobo/KoboReader.sqlite"):
                print(f"Kobo device found: {candidate}")
                return candidate

    fallback = BASE / "KoboReader.sqlite"
    if fallback.exists():
        print(f"No device found, using local file: {fallback}")
        return fallback

    raise FileNotFoundError(
        "KoboReader.sqlite not found. Connect your Kobo, put the file next to "
        "this script, or pass its path as an argument."
    )


def load_kobo(db_path):
    # Work on a temporary copy, read-only, so the device file is never touched.
    with tempfile.TemporaryDirectory() as tmp:
        tmp_db = Path(tmp) / "KoboReader.sqlite"
        shutil.copy2(db_path, tmp_db)

        conn = sqlite3.connect(f"file:{tmp_db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            columns = {r["name"] for r in conn.execute("PRAGMA table_info(Bookmark)")}
            if "Text" not in columns:
                raise ValueError("The Bookmark table has no Text column.")

            def col(name):
                return f"b.{name} AS {name}" if name in columns else f"NULL AS {name}"

            hidden_filter = (
                "AND (b.Hidden IS NULL OR LOWER(CAST(b.Hidden AS TEXT)) NOT IN ('true', '1'))"
                if "Hidden" in columns
                else ""
            )

            query = f"""
                SELECT
                    {col('BookmarkID')},
                    b.VolumeID AS VolumeID,
                    b.Text AS Text,
                    {col('Annotation')},
                    {col('ContextString')},
                    {col('DateCreated')},
                    c.Title AS Title,
                    c.BookTitle AS BookTitle,
                    c.Attribution AS Attribution
                FROM Bookmark b
                LEFT JOIN content c
                       ON c.ContentID = b.VolumeID
                      AND c.ContentType = 6
                WHERE b.Text IS NOT NULL AND TRIM(b.Text) != ''
                {hidden_filter}
                ORDER BY b.DateCreated
            """

            records = []
            for row in conn.execute(query):
                r = dict(row)
                highlight_id = clean(r["BookmarkID"])
                if not highlight_id:
                    raw = "|".join(clean(r.get(k)) for k in ("VolumeID", "DateCreated", "Text"))
                    highlight_id = "h-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()
                records.append({
                    "id": highlight_id,
                    "book_id": clean(r["VolumeID"]),
                    "title": clean(r["Title"]) or clean(r["BookTitle"]),
                    "author": clean(r["Attribution"]),
                    "text": clean(r["Text"]),
                    "annotation": clean(r["Annotation"]),
                    "context": clean(r["ContextString"]),
                    "date": clean(r["DateCreated"]),
                })
            return records
        finally:
            conn.close()


# Register new devices here: name -> (find, load)
SOURCES = {
    "kobo": (find_kobo, load_kobo),
}


# --------------------------------------------------------------------------
# Renames (optional): fix book titles / authors without touching the device
# --------------------------------------------------------------------------

def load_renames(path):
    """renames.json -> {title as exported (lowercase): (new title, new author)}"""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as error:
        raise ValueError(f"{path.name} is not valid JSON: {error}")

    renames = {}
    for old, new in data.items():
        if isinstance(new, str):
            new_title, new_author = new, ""
        else:
            new_title = clean(new.get("title"))
            new_author = clean(new.get("author"))
        renames[clean(old).casefold()] = (new_title, new_author)
    return renames


# --------------------------------------------------------------------------
# State: which highlights were already exported
# --------------------------------------------------------------------------

def load_state(state_file):
    legacy = state_file.with_name(".kobo_state.json")
    for candidate in (state_file, legacy):
        if candidate.exists():
            data = json.loads(candidate.read_text(encoding="utf-8"))
            return set(data.get("exported_ids", [])), dict(data.get("counters", {}))
    return set(), {}


def save_state(state_file, exported, counters):
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(
        json.dumps(
            {"exported_ids": sorted(exported), "counters": counters},
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def run(args):
    output_dir = Path(args.output)
    state_file = output_dir / STATE_NAME

    find, load = SOURCES[args.device]
    source_path = find(args.source)
    records = load(source_path)

    exported, counters = load_state(state_file)

    # --baseline: mark everything currently on the device as already exported.
    if args.baseline:
        exported |= {r["id"] for r in records}
        save_state(state_file, exported, counters)
        print(f"Baseline saved: {len(records)} existing highlights marked as exported.")
        print("Future runs will only export highlights added after this point.")
        return

    new_records = [r for r in records if r["id"] not in exported]

    if not new_records:
        print("No new highlights. No folder was created.")
        print(f"({len(records)} highlights on the device, all exported before.)")
        return

    # New dated export folder (a second run on the same day gets "(2)", "(3)"...)
    today = datetime.now().strftime("%Y-%m-%d")
    export_name = today
    suffix = 2
    while (output_dir / export_name).exists():
        export_name = f"{today} ({suffix})"
        suffix += 1
    export_dir = output_dir / export_name
    export_dir.mkdir(parents=True)

    renames = load_renames(Path(args.renames))
    grouped = defaultdict(list)
    meta = {}
    unmatched = 0

    for record in new_records:
        title = record["title"]
        if not title:
            title = f"Unmatched book - {record['book_id'] or 'Unknown ID'}"
            unmatched += 1
        author = record["author"]

        fix = renames.get(title.casefold())
        if fix:
            title = fix[0] or title
            author = fix[1] or author

        grouped[title].append(record)
        meta.setdefault(title, author)

    quotes_created = 0

    for title, entries in sorted(grouped.items()):
        base_name = safe_filename(title)[:100].strip(" .") or "Untitled book"
        book_dir = export_dir / base_name
        book_dir.mkdir(exist_ok=True)

        # The index note name contains the export date, so it never collides
        # with the index notes of other exports.
        index_name = f"{base_name} ({export_name})"

        entries.sort(key=lambda r: r["date"])
        counter = counters.get(base_name, 0)

        index = [f"# {title}", ""]
        if meta[title]:
            index.extend([f"Author: {meta[title]}", ""])
        index.extend([
            f"Export: {export_name}",
            f"Highlights in this export: {len(entries)}",
            "", "---", "",
        ])

        for entry in entries:
            counter += 1
            # Numbering continues across exports, so note names stay unique.
            note_name = f"{base_name} - {counter:03d}"

            note = [
                "---",
                f'book: "[[{index_name}]]"',
                f"book_title: {yaml_str(title)}",
            ]
            if meta[title]:
                note.append(f"author: {yaml_str(meta[title])}")
            if entry["date"]:
                note.append(f"date: {yaml_str(entry['date'])}")
            note.append(f"export: {yaml_str(export_name)}")
            note.extend(["---", "", markdown_quote(entry["text"]), ""])

            if entry["annotation"]:
                note.extend([f"**My note:** {entry['annotation']}", ""])
            if entry["context"]:
                note.extend([f"**Context:** {entry['context']}", ""])

            (book_dir / f"{note_name}.md").write_text("\n".join(note), encoding="utf-8")
            quotes_created += 1

            if EMBED_IN_INDEX:
                index.extend([f"![[{note_name}]]", "", "---", ""])
            else:
                index.append(f"- [[{note_name}]]")

        (book_dir / f"{index_name}.md").write_text("\n".join(index), encoding="utf-8")
        counters[base_name] = counter

    # Save state last, only after everything was written successfully.
    exported |= {r["id"] for r in new_records}
    save_state(state_file, exported, counters)

    print("\nDone.")
    print(f"Highlights on the device: {len(records)}")
    print(f"New in this export: {quotes_created}")
    print(f"Books affected: {len(grouped)}")
    print(f"Highlights without a matching book: {unmatched}")
    print(f"\nExport folder:\n{export_dir}")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Incrementally export e-reader highlights to Obsidian Markdown notes."
    )
    parser.add_argument("source", nargs="?", help="Path to the highlight source file "
                        "(default: auto-detect the connected device)")
    parser.add_argument("--device", choices=sorted(SOURCES), default="kobo",
                        help="Device adapter to use (default: kobo)")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT),
                        help="Archive folder, e.g. a folder inside your Obsidian vault")
    parser.add_argument("--renames", default=str(DEFAULT_RENAMES),
                        help="Optional JSON file that fixes book titles/authors")
    parser.add_argument("--baseline", action="store_true",
                        help="Mark all current highlights as exported without writing notes")
    parser.add_argument("--no-pause", action="store_true",
                        help="Do not wait for Enter at the end (for scripts and schedulers)")
    return parser.parse_args()


if __name__ == "__main__":
    arguments = parse_args()
    try:
        run(arguments)
    except Exception as error:
        print(f"\nERROR: {error}")
        if arguments.no_pause:
            sys.exit(1)

    if not arguments.no_pause:
        input("\nPress Enter to close...")
