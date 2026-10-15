# ereader-highlights-to-obsidian

Turn your e-reader highlights into Markdown notes for [Obsidian](https://obsidian.md) —
**incrementally**. Every run creates a new dated folder that contains only the highlights
you added since the last export. Nothing is overwritten, nothing is exported twice.

- **Zero dependencies** — a single Python file (Python 3.8+, standard library only).
- **Reads the device database directly** — no CSV exports, no DB browser.
- **Incremental** — a small state file remembers what was already exported.
- **Obsidian-friendly** — one note per highlight, a book index note, and queryable properties.
- **Device adapters** — Kobo works out of the box; other devices can be added with ~30 lines
  ([guide](docs/ADDING_DEVICES.md)).

## Supported devices

| Device | Status |
|---|---|
| Kobo | Built in (reads `KoboReader.sqlite`) |
| Kindle (`My Clippings.txt`) | Worked example in the [guide](docs/ADDING_DEVICES.md) |
| KOReader (any device) | Via its built-in exporter, see the [guide](docs/ADDING_DEVICES.md) |
| Others | Write an adapter — see the [guide](docs/ADDING_DEVICES.md) |

## Quick start

```bash
git clone https://github.com/latifyorum/ereader-highlights-to-obsidian.git
cd ereader-highlights-to-obsidian

# Connect your Kobo, then:
python ereader_to_obsidian.py --output "/path/to/your/vault/Highlight Exports"
```

The script looks for `.kobo/KoboReader.sqlite` on connected drives. You can also pass the
file explicitly, or place it next to the script:

```bash
python ereader_to_obsidian.py /path/to/KoboReader.sqlite
```

### Already exported your highlights some other way?

Run this once first. It marks everything currently on the device as exported and writes
no notes, so later runs contain only new highlights:

```bash
python ereader_to_obsidian.py --baseline --output "/path/to/vault/Highlight Exports"
```

## Output layout

```
Highlight Exports/
├── .ereader_state.json              ← remembers what was exported (do not delete)
├── 2026-10-14/
│   └── Crime and Punishment/
│       ├── Crime and Punishment (2026-10-14).md   ← index note (embeds the highlights)
│       ├── Crime and Punishment - 001.md
│       └── Crime and Punishment - 002.md
└── 2026-11-14/
    └── Crime and Punishment/
        ├── Crime and Punishment (2026-11-14).md
        └── Crime and Punishment - 003.md          ← numbering continues
```

Each highlight note looks like this:

```markdown
---
book: "[[Crime and Punishment (2026-10-14)]]"
book_title: "Crime and Punishment"
author: "Fyodor Dostoevsky"
date: "2026-10-01T10:11:12.000"
export: "2026-10-14"
---

> The highlighted text

**My note:** your annotation, if you wrote one
**Context:** chapter information, if the device provides it
```

Because `book_title` is stable across exports, you can collect all highlights of a book
with the [Dataview](https://github.com/blacksmithgu/obsidian-dataview) plugin:

````
```dataview
LIST FROM "Highlight Exports"
WHERE book_title = "Crime and Punishment"
SORT date ASC
```
````

## Options

| Option | Description |
|---|---|
| `source` | Path to the source file. Default: auto-detect the connected device |
| `--device` | Device adapter. Default: `kobo` |
| `--output` | Archive folder (put it inside your vault to see notes immediately) |
| `--renames` | JSON file that fixes titles/authors (default: `renames.json` next to the script) |
| `--baseline` | Mark everything as exported without writing notes |
| `--no-pause` | Don't wait for Enter at the end (for schedulers/scripts) |

Set `EMBED_IN_INDEX = False` at the top of the script if you prefer a plain link list in
the index note instead of embedded highlights.

## Fixing book titles and authors

Metadata edited in Calibre or elsewhere may not reach the device. Copy
`renames.example.json` to `renames.json` and map the title **as stored on the device**
to the one you want:

```json
{
  "Old title": "Corrected Title",
  "Crime and Punishment - Fyodor Dostoevsky": {
    "title": "Crime and Punishment",
    "author": "Fyodor Dostoevsky"
  }
}
```

Matching is case-insensitive. Renames apply to new exports only; old notes are not touched.
Highlights whose book cannot be matched are saved as `Unmatched book - <id>` and can be
renamed the same way.

## Good to know

- **Do not delete `.ereader_state.json`.** Without it, every highlight counts as new again.
- **Highlights are exported once.** If you add or change a note on an already exported
  highlight later, the new version is not picked up.
- **Notes are generated, not edited.** Keep your own thinking in separate notes and link to
  the generated ones.
- The device file is never modified. The script works on a temporary read-only copy.
- Highlights deleted on the device (Kobo marks them as hidden) are skipped; notes that were
  already exported stay in your vault.
- The Kobo adapter relies on the standard `KoboReader.sqlite` schema (`Bookmark` and
  `content` tables). Firmware versions can differ; open an issue if a column is missing.

## Contributing

Adapters for more devices are the most useful contribution. See
[docs/ADDING_DEVICES.md](docs/ADDING_DEVICES.md) for the record format and a worked example.

## License

[MIT](LICENSE). This project is not affiliated with or endorsed by Rakuten Kobo, Amazon,
Obsidian, or any other company mentioned here.
