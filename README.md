# daily-digest

A personal tech, AI and dev news digest that writes itself every day, and a public archive of what it covered.

> **Note on language:** the bot writes its articles in French by choice, because its reader is French. Topic titles in the archive are published in English for international readers; the earliest archive entries, written before this change, have French titles.

A Telegram bot running on a small cloud server collects articles, groups them by topic, lets me choose what I want to read, and writes the chosen topics as a French-language PDF "newspaper". This repository is the **public showcase and archive**: sample output, plus a daily log of the topics collected, with links to their original sources.

> The Python files at the root are a **demo of the code**, published so the architecture can be read. The production deployment (server configuration, secrets, scheduling, archive publishing) is not part of this repository.

---

## Contents

- [`archive.md`](archive.md): one section per day, with the date, the topic titles and links to the original sources.
- [`samples/`](samples/): two example editions (PDF), in English translation, showing what the bot produces.
- [`collecte.py`](collecte.py), [`regrouper.py`](regrouper.py), [`bot.py`](bot.py), [`redaction.py`](redaction.py), [`budget.py`](budget.py): the demo source code (see [Architecture](#architecture)).
- [`requirements.txt`](requirements.txt): pinned dependencies.

## See it working

| Sample | What it shows |
|---|---|
| [`samples/edition-2026-10-08-evening-EN.pdf`](samples/edition-2026-10-08-evening-EN.pdf) | Thursday 8 October 2026: five topics, with cover page, table of contents, one article per topic and source list with dates. |
| [`samples/edition-2026-10-09-evening-EN.pdf`](samples/edition-2026-10-09-evening-EN.pdf) | Friday 9 October 2026: five topics, including a reading-status line for each source and "to check in the sources" flags. |

The bot writes its editions in French. These two samples are English translations of real editions, with the same layout, so that international readers can follow them.

## How it works

1. **Collect**: several times a day (8am, 11am, 2pm, 5pm, Paris time), a script gathers articles from RSS feeds and other sources, and drops anything unreadable or paywalled before it reaches the list.
2. **Group**: at 5:45pm, a language model first filters out everything that is not tech, AI or software, then merges articles about the **same event** into one topic.
3. **Choose**: at 5:55pm, the Telegram bot sends me the list of topics. I tick up to five.
4. **Write**: for each chosen topic, the program reads the source pages and the model writes an original article in French. The result is laid out as an A5 PDF and sent back to me, with its estimated cost.
5. **Archive**: the day's topics and their source links are added to [`archive.md`](archive.md).

## Architecture

The program is a pipeline of small scripts that communicate **only through JSON files**. Each step can run, fail and be re-run on its own.

```
 RSS feeds ──► collecte.py ──► articles.json
                                    │
                                    ▼
                             regrouper.py ──► sujets.json  (+ ignores.txt, rejets.log)
                              │  2 LLM calls + readability filter
                              ▼
                           bot.py  (Telegram, long-running)
                              │  /liste → I tick up to 5 topics → "Validate"
                              ▼
                          redaction.py ──► editions/edition-YYYY-MM-DD-{matin|soir}.pdf
                              │                       │
                              └──► budget.py ◄────────┘   (usage.log, monthly total)
```

| File | Role | Reads | Writes |
|---|---|---|---|
| [`collecte.py`](collecte.py) | Reads the RSS feeds (Hacker News, Hugging Face, TechCrunch, Le Monde, franceinfo, Libération, Le Figaro), keeps `http(s)` links only, skips links already seen, purges articles older than 24 h and links older than 7 days. Control characters from the web are stripped before display. | feeds | `articles.json`, `liens_vus.json` |
| [`regrouper.py`](regrouper.py) | **Call 1:** filters out everything that is not tech, AI or software. **Call 2:** merges articles about the same event into one topic, with a French and an English title. Model answers are validated against a JSON schema; links are re-attached from `articles.json`, never taken from the model. Then a readability filter downloads each candidate page (parallel, cached) and drops paywalled or unreadable ones. Topics are ranked by number of distinct sources. | `articles.json` | `sujets.json`, `ignores.txt`, `rejets.log` |
| [`bot.py`](bot.py) | Telegram bot (`python-telegram-bot`). Sends the topic list automatically at 5:55pm (`/liste` on demand), shows tick-box buttons (max 5), then calls `redaction.py` on "Validate" and sends the PDF back with its cost. `/cout` gives the month's spending. Answers one user ID only; secrets are masked in logs. | `sujets.json` | Telegram messages |
| [`redaction.py`](redaction.py) | For each chosen topic: fetches the sources safely (no private addresses, limited redirects, size and time caps), asks the model for a short article, checks it, and lays out an A5 PDF with ReportLab: cover, table of contents, articles, sources with dates. Contains the anti-invention checks (number verification, "not enough facts" abstention, partial-read labels). | `articles.json`, source pages | `editions/*.pdf` (last 14 kept) |
| [`budget.py`](budget.py) | Computes the cost of each API call from token counts and a price table (unknown model → highest price, for safety), appends it to `usage.log` and totals the month. Used by the bot to enforce the spending cap. | `usage.log` | `usage.log` |

### Design choices

- **Files as interfaces.** No database, no queue: JSON files between steps. Easy to inspect, easy to replay one stage.
- **Cheap model, strict inputs.** Claude Haiku 5.5 does the filtering, grouping and writing; the code does everything that can be checked deterministically (links, dates, numbers, paywalls, budget).
- **Structured outputs.** Model answers are constrained by a JSON schema and re-validated in code before use; an invalid answer drops the topic rather than the edition.
- **Fail small.** Timeouts at every level (network, page, edition), atomic PDF write (`.tmp` then rename), and a topic that fails is reported with its reason while the rest continue.
- **Scheduling lives outside the code.** `cron` runs `collecte.py` and `regrouper.py`; `systemd` keeps `bot.py` running.

## The rule: it never invents

The writing step is built around one rule: **the program must not invent anything.** In practice:

- **No text, no article.** If only a headline or an extract could be read, the topic is not sent to the model at all. I get the title and the link back instead.
- **The model can decline.** It has an explicit way to answer "not enough facts to write this", and its answer is respected.
- **One failure does not sink the edition.** A topic that cannot be written is skipped with its reason; the others continue.
- **Numbers are checked.** Every figure in a written article (digits or spelled out) is compared with the source texts; anything not found there is flagged in the PDF as "to check in the sources".
- **Partial reads are labelled.** Sources that could only be read in part are marked as such.
- **Claims are attributed.** Results announced by a company about its own work are introduced with "according to [company]", never presented as established.
- **Links come from files, never from the model.** The model never produces a URL.
- **Paywalls are respected.** No attempt is made to get around a subscription or a block.

These checks reduce errors; they do not remove them. A subtle mistranslation or an over-confident summary can still slip through, so the linked sources remain the reference.

## Safety and cost

- **Single user.** The bot answers one Telegram account only and silently ignores everyone else.
- **Secrets never appear in logs.** Tokens and API keys are masked, including in error traces.
- **Careful page fetching.** Only `http(s)` links, no local or private addresses (even after redirects), strict size and time limits, no execution of anything found in a page.
- **Untrusted text stays data.** Headlines and page text are never treated as instructions for the model, and clickable links in the PDF are limited to `http(s)`.
- **Server hardening.** Key-only SSH, firewall, automatic security updates, a dedicated unprivileged user, a read-only deploy key, and a hardened `systemd` service.
- **Spending cap.** Every API call is logged; above a monthly ceiling the bot refuses to write and returns titles and links.
- **Cost.** A five-topic edition costs about **US$0.01** in API calls. The whole project runs for roughly **€5 a month** of hosting plus under a dollar of API usage per month.
- **More than 100 automated tests**, none of which need the network or the API.

## Stack

- Python
- `cron` for scheduling
- `systemd` to keep the bot running
- Telegram Bot API
- Anthropic API (Claude Haiku 5.5)
- ReportLab for the PDF layout
- Debian VPS (OVHcloud)

## Limits

- The model can still make mistakes: approximate titles, lost nuance, a wrong turn of phrase. Anything important should be checked at the source.
- Paywalled outlets are only read through their public extract, so they rarely produce a full article.
- Built for one reader, one language and one set of sources.

## Sources and copyright

Articles belong to their publishers. The archive publishes **only topic titles and links to the original sources**. The two sample PDFs are demonstration editions (translated from French): the text is written in my own words from the linked sources, which are credited in each article. If you are a rights holder and would like something removed, please open an issue and I will take it down.

## Status

Personal project, running daily.

---

*Built by Nicolas, student at 42 Paris, as a hands-on project in automation, language models and secure deployment. Written with the help of an AI coding assistant, then reviewed, tested and hardened step by step.*
