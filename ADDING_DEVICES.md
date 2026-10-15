# Adding support for another e-reader

The exporter is split into two parts:

1. **An adapter** reads the device's highlight storage and returns a list of plain
   dictionaries ("records").
2. **The exporter** (everything else in `ereader_to_obsidian.py`) turns records into dated
   folders, notes, and index pages, and remembers what was exported.

To support a new device you only write the adapter. The exporter never changes.

## The record format

Every record is a dict with these keys. Use an empty string when a value is unknown.

| Key | Meaning |
|---|---|
| `id` | **Stable, unique** id of the highlight. Used to decide what is new |
| `book_id` | Device-internal book identifier (only shown in fallback titles) |
| `title` | Book title (empty → `Unmatched book - <book_id>`) |
| `author` | Book author |
| `text` | The highlighted text. Records with empty text must be skipped |
| `annotation` | The reader's own note on the highlight |
| `context` | Chapter / page / location information |
| `date` | Creation date as a string. Records are sorted by this string, so prefer ISO format when you can |

### Rules for `id`

- It must **never change** between runs for the same highlight, otherwise it is exported again.
- It must be **unique across devices** you export to the same archive. If the device has a
  real highlight id, use it. Otherwise use a hash of stable content and prefix it with the
  device name, e.g. `"kindle:" + sha1(title + "|" + text)`.
- Do not use the highlight's position in a list; positions shift when highlights are deleted.

## Steps

1. **Find where the device stores highlights** (see the table below).
2. **Write `find_<device>(path_arg)`**: return a `Path` to the source file. Accept an explicit
   path, then try auto-detection, then raise `FileNotFoundError` with a helpful message.
3. **Write `load_<device>(path)`**: return the list of records.
4. **Register it**:
   ```python
   SOURCES = {
       "kobo": (find_kobo, load_kobo),
       "kindle": (find_kindle, load_kindle),
   }
   ```
5. **Test** with the checklist at the end, then open a pull request.

## Where do highlights live?

| Source | Format | Notes |
|---|---|---|
| Kobo | SQLite: `.kobo/KoboReader.sqlite`, tables `Bookmark` and `content` | Built in |
| Kindle | Text file: `documents/My Clippings.txt` | One block per highlight, blocks separated by a line of `=` characters. Bookmarks have no text. Header lines are localized by language. Worked example below |
| KOReader (runs on Kobo, Kindle, PocketBook, Android and more) | Bundled **Export highlights** plugin: JSON, Markdown, HTML, text, and a Kindle "My Clippings" style format | If your device runs KOReader, exporting to JSON and writing one JSON adapter covers it. Inspect a sample export first to see the exact structure. KOReader also keeps highlights in `.sdr` folders as Lua metadata |
| Apple Books | SQLite databases inside the macOS app container | Paths and schema change between macOS versions. Open the files with a SQLite browser and check before relying on them |
| Boox, PocketBook, others | Usually a built-in "export notes" feature (text, HTML, PDF) | Export a sample, look at its structure, and parse that. Some also store highlights in SQLite |

> The table is a starting point, not a guarantee. Formats change between firmware versions.
> Always inspect a real file from your own device before you write the parser.

### Exploring an unknown SQLite database

```bash
sqlite3 SomeDevice.sqlite ".tables"
sqlite3 SomeDevice.sqlite ".schema Bookmark"
sqlite3 SomeDevice.sqlite "SELECT * FROM Bookmark LIMIT 5;"
```

Or open the file in [DB Browser for SQLite](https://sqlitebrowser.org/). Always work on a
copy of the file. The Kobo adapter shows how: copy to a temp folder, open with
`?mode=ro`, then read.

## Worked example: Kindle `My Clippings.txt`

This example is a sketch. Test it against your own file before depending on it.
Date strings in the file are localized, so they are kept as plain text.

```python
def find_kindle(path_arg):
    if path_arg:
        path = Path(path_arg.strip('"'))
        if path.exists():
            return path
        raise FileNotFoundError(f"Path not found: {path}")

    for letter in string.ascii_uppercase:
        candidate = Path(f"{letter}:/") / "documents" / "My Clippings.txt"
        if candidate.exists():
            print(f"Kindle found: {candidate}")
            return candidate

    fallback = BASE / "My Clippings.txt"
    if fallback.exists():
        return fallback
    raise FileNotFoundError("My Clippings.txt not found. Pass its path as an argument.")


TITLE_RE = re.compile(r"^(?P<title>.*) \((?P<author>[^()]*)\)$")


def load_kindle(path):
    raw = path.read_text(encoding="utf-8-sig")
    records = []
    for block in raw.split("=========="):
        lines = [line.strip() for line in block.strip().splitlines()]
        if len(lines) < 3:
            continue
        header, meta, *body = lines
        text = "\n".join(line for line in body if line).strip()
        if not text:                      # bookmarks have no text
            continue

        match = TITLE_RE.match(header)
        title, author = (match["title"], match["author"]) if match else (header, "")
        key = hashlib.sha1(f"{title}|{text}".encode("utf-8")).hexdigest()

        records.append({
            "id": "kindle:" + key,
            "book_id": title,
            "title": title,
            "author": author,
            "text": text,
            "annotation": "",
            "context": meta.split("|")[0].lstrip("- ").strip(),
            "date": meta.split("|")[-1].strip(),
        })
    return records
```

Things to know about this format:

- A Kindle "note" appears as a separate entry. This example exports it like a highlight.
- Editing a highlight on the device can leave duplicate entries in the file. Hashing
  `title + text` prevents most duplicates from being exported twice.
- Because dates are localized text, sorting is by file order within a run. Convert dates to
  ISO format in the adapter if you need exact ordering.

## Testing checklist

Use fake data first; never experiment on your only copy of a device database.

1. Build a tiny sample source (a few highlights, one bookmark without text, one book with
   special characters like quotes in the title).
2. Run once → check the folder, the notes, the frontmatter.
3. Add one highlight → run again → **only the new one** must appear, with the numbering
   continuing.
4. Run a third time with nothing new → no folder should be created.
5. Test `--baseline` with an empty output folder, then run normally: nothing should export.
6. Test with a real file from your device. Confirm the count of exported highlights matches
   what you see on the device.

## Pull requests

Please include: the adapter, its registration in `SOURCES`, the device and firmware you
tested on, and a short section in the README table. Never include real highlights or
device databases in a PR.
