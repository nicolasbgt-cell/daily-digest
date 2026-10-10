# daily-digest

A personal tech, AI and dev news digest that writes itself every day, and a public archive of what it covered.

> **Note on language:** the bot writes its articles in French by choice, because its reader is French. Topic titles in the archive are published in English for international readers; the earliest archive entries, written before this change, have French titles.

A Telegram bot running on a small cloud server collects articles, groups them by topic, lets me choose what I want to read, and writes the chosen topics as a French-language PDF "newspaper". This repository is the **public showcase and archive**: sample output, plus a daily log of the topics collected, with links to their original sources.

> The bot's source code lives in a separate private repository. This repository contains only the archive, the samples and this description.

---

## Contents

- [`archive.md`](archive.md): one section per day, with the date, the topic titles and links to the original sources.
- [`samples/`](samples/): two example editions (PDF), in English translation, showing what the bot produces.

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
