# Editor's Desk — Reporter Guide

This folder is the newspaper's editor's desk. Every `.md` file here is an
article that will appear on the Rocky Mountain News (Sports) front page,
news index, or sidebar, depending on its `placement`.

There is **no admin UI**. To publish: commit a `.md` file to this folder
on the working branch, open a PR, merge. Fly redeploys, the article goes
live within a minute. To unpublish: delete the file.

This guide is the template a reporter (human or LLM) should follow.

---

## File location and naming

- Put the article in `desk/<slug>.md`.
- `<slug>` is the URL — keep it short, lowercase, hyphens only, no dates
  needed in the name. Example: `desk/grand-valley-state-still-undefeated.md`.
- Images go in `static/desk/<filename>` and you reference them by
  filename in the frontmatter `image:` field.
- Files starting with `_` are ignored (use that for drafts).

## File shape

```markdown
---
headline: Grand Valley State stays unbeaten after one-point thriller
dek: A blocked drop kick with under a minute left preserved a perfect run.
byline: Jane Reporter
sport: Viperball
placement: lead
published_at: 2026-06-13
image: gvsu-blocked-kick.jpg
image_alt: A Coast Guard defender raising both arms after a blocked kick
kicker: Viperball · Editor's Desk
---

Opening paragraph — this becomes the lede everywhere a summary is shown
(home page, news index, sidebar). Keep it tight: one sentence is fine,
two is the ceiling.

## Optional section heading

Subsequent paragraphs are the body. Blank lines separate paragraphs.
`## ` at the start of a line becomes a subhead. No other markdown
formatting is rendered — write in plain prose.
```

## Field reference

The frontmatter is the block between the two `---` lines at the very top.
Everything is a single line of `key: value`. Fields:

| field          | required | default          | purpose                                                       |
| -------------- | -------- | ---------------- | ------------------------------------------------------------- |
| `headline`     | yes      | —                | The article title. Title-cased, no terminal period.           |
| `dek`          | no       | the first ¶      | Subhead under the headline; the lede shown on index pages.    |
| `byline`       | no       | `Staff`          | Who filed it. "Jane Reporter", "By the AI Desk", etc.         |
| `sport`        | no       | `Desk`           | One of `Baseball`, `Viperball`, `Tennis`, or any tag.         |
| `kicker`       | no       | `<sport> · Editor's Desk` | The small line above the headline.                            |
| `placement`    | no       | `rail`           | Where it lands: `lead`, `featured`, `rail`, `wire`.           |
| `published_at` | no       | empty            | `YYYY-MM-DD`. Used for sorting and the byline timestamp.      |
| `image`        | no       | pixel-art SVG    | Filename inside `static/desk/`. Skip if you want auto-art.    |
| `image_alt`    | no       | empty            | Alt text. Required if you set `image`.                        |

## Placement — where the article shows up

- `lead` — top of the front page. Beats both the baseball gazette and the
  mechanical wire lead. **Only run one `lead` article at a time** — newer
  `published_at` wins if there are multiple, but it's cleaner to demote
  the old one to `featured` or `rail`.
- `featured` — one of three cards in the brief grid under the lead.
- `rail` — the "Top Headlines" sidebar. Good for opinion, commentary, or
  evergreen pieces.
- `wire` — same as `rail` for now; reserved for future wire-style stories.

## Image rules

- 1200×630 is the right ratio (matches the lead and card aspect).
- Put the file in `static/desk/`. Commit it.
- Reference it as just the filename in frontmatter — the loader prepends
  `/static/desk/`.
- Skip `image:` entirely and the article gets a deterministic pixel-art
  stand-in from the slug — fine for opinion/rail pieces.

## Body conventions

- One blank line between paragraphs.
- `## Heading` to break long pieces into sections.
- No other markdown (no bold, italic, links, lists) — they render as
  literal text. Keep the prose clean.
- Aim for a strong opening sentence: it doubles as the lede on every
  index page.

## Worked example for an LLM reporter

If you're an agent being asked to file a story, the prompt you'll be
given will name the topic, the angle, the desired placement, and any
image to use. Your job is to produce a single `.md` file matching this
template exactly. Hand back:

1. The proposed filename (`desk/<slug>.md`).
2. The full file contents — frontmatter block, blank line, body.
3. If an image is referenced, confirm its filename and remind the editor
   it needs to be committed under `static/desk/`.

Do not invent scores, standings, or game results — the wire and the sim
adapters already publish those. The desk is for *human-angle* stories:
features, commentary, color, analysis, op-ed. If the story needs a
factual hook, cite it from the wire/scores pages already on the site.
