# news-bot-dc

A Discord bot that watches websites and posts to your channels in two ways:

- **Live**: anything new from a source is posted as it appears.
- **Daily digest**: once a day a Claude model reads everything collected in the last 24 hours and picks at most one article per category (by default "tech and research" and "good news"), or none when nothing is good enough. Slovenian picks get an English article on the same story linked under them, and the digest ends with a line from The Office that fits the day's picks.

Feeds, plain HTML pages and JSON endpoints all work. Every setting lives in three local files made from templates: `.env`, `sources.yml` and `digest.yml`.

## Setup

```bash
git clone https://github.com/Maksim3l/news-bot-dc && cd news-bot-dc
make setup
```

`make setup` creates a venv and copies the three templates to your own local files. Existing files are never overwritten, and none of the three is tracked by git:

| Template | Your copy | Holds |
| --- | --- | --- |
| `.env.example` | `.env` | Tokens, IDs, paths and runtime knobs |
| `sources.example.yml` | `sources.yml` | Every site the bot reads |
| `digest.example.yml` | `digest.yml` | Daily digest schedule, model and categories |

1. Open https://discord.com/developers/applications, create an application, open **Bot**, press **Reset Token**, copy it into `DISCORD_TOKEN` in `.env`.
2. In Discord turn on **User Settings > Advanced > Developer Mode**, right-click the channel you want, **Copy Channel ID**, paste it into `DISCORD_CHANNEL_ID`.
3. Get an API key at https://console.anthropic.com and paste it into `ANTHROPIC_API_KEY`. Only the digest needs it.
4. Optional: copy **General Information > Application ID** into `DISCORD_APPLICATION_ID`; the bot then logs a ready invite link on startup.
5. Invite the bot: **OAuth2 > URL Generator**, scopes `bot` and `applications.commands`, permissions `Send Messages` and `Embed Links`. Open the generated URL.
6. Start it:

```bash
python -m newsbot
```

No privileged intents have to be enabled. The bot never reads message content.

Optional: put your server ID in `DISCORD_GUILD_ID` and slash commands show up immediately instead of taking up to an hour to spread globally.

Before starting the bot you can verify every site without a token at all:

```bash
python -m newsbot check
```

That prints one line per source, with the newest headline it found or the reason it failed, then the digest schedule.

To see what the digest would pick right now, without posting anything:

```bash
python -m newsbot digest
```

It fetches every source once, fills the pool and prints the picks, English links and quote.

## Adding a site

### From Discord

```
/watch url: https://www.phoronix.com
```

The bot finds the feed behind the page, writes it into `sources.yml` and starts polling. Add `name:` and `channel:` if you want to override them.

### By editing sources.yml

```yaml
sources:
  - name: Slo-Tech
    url: https://slo-tech.com/rss
```

Then run `/reload` in Discord, or restart. Two lines is a complete entry, everything else is optional:

| Key | Default | Meaning |
| --- | --- | --- |
| `name` | required | Shown in the embed footer, must be unique |
| `url` | required | Feed or page URL |
| `type` | `rss` | `rss`, `html` or `json` |
| `channel` | `DISCORD_CHANNEL_ID` | Channel ID, or a name from the `channels` block |
| `interval` | `POLL_INTERVAL` | Seconds between polls |
| `max_items` | `MAX_ITEMS_PER_POLL` | Most posts per poll, the rest follow next tick |
| `color` | derived from the name | Embed colour, for example `"#2b7fff"` |
| `enabled` | `true` | `false` pauses the source without deleting it |
| `filters` | none | `include` and `exclude` lists of regexes |
| `selectors` | none | Required for `html` and `json` |
| `headers` | none | Extra HTTP headers, for example an API key |
| `language` | `en` | `sl` for Slovenian sources; non-English digest picks get an English link |
| `post` | `true` | `false` stops live posting; the source then only feeds the digest |

Name your channels once and every source reads cleanly:

```yaml
channels:
  slovenija: 123456789012345678
  tech: 987654321098765432

sources:
  - name: Slo-Tech
    url: https://slo-tech.com/rss
    channel: tech
```

### Sites without a feed

Point `item` at each card on the page and read the rest from inside it. A selector ending in `@attr` reads that attribute, a bare selector reads text, and `@attr` alone reads the item element itself. Relative links are resolved against `url`.

```yaml
  - name: Example Blog
    url: https://example.com/news
    type: html
    selectors:
      item: "article.post"
      title: "h2 a"
      link: "h2 a@href"
      summary: "p.excerpt"
      image: "img@src"
      published: "time@datetime"
```

Check the selectors before trusting them:

```bash
python -m newsbot check "Example Blog"
```

### JSON endpoints

Selectors are dotted paths, and `item` points at the list.

```yaml
  - name: Example API
    url: https://example.com/api/posts
    type: json
    selectors:
      item: "data.posts"
      title: "headline"
      link: "permalink"
      published: "published_at"
```

### Filtering

Both lists are case-insensitive regexes matched against title and summary. `exclude` is applied first, then `include` acts as an allowlist.

```yaml
  - name: Slo-Tech
    url: https://slo-tech.com/rss
    filters:
      include:
        - "linux|nvidia|amd"
      exclude:
        - "oglasno sporocilo"
```

### Sites without a usable feed

A Google News search feed works for any site or keyword. The template reads GZS and all Dewesoft mentions this way:

```yaml
  - name: GZS
    url: https://news.google.com/rss/search?q=site:gzs.si&hl=sl&gl=SI&ceid=SI:sl
    language: sl
    post: false
```

## Daily digest

Every fetched item goes into a pool, whether or not it is posted live. At `post_at` the bot sends the last `window_hours` of the pool to the model in one request and asks for at most one article per category. Then:

1. A category with `prefer` patterns is filled by a matching article whenever one exists, whatever the model chose. The template makes anything mentioning Dewesoft win the tech slot.
2. For each non-English pick, an English article from the pool is used if the model found one covering the same story. Otherwise the bot searches Google News in English and the model checks which result is the same story, or none.
3. The model gives theme words and Office lines it remembers. Remembered lines are checked against the full transcript in `assets/office_quotes.csv`; unverified ones are dropped. The verified lines plus the best keyword matches go back to the model, which chooses one. The posted quote is always the transcript's text, never the model's.

Picked articles are never picked again, and each day posts at most once even across restarts.

A day uses three to five requests, about 50k input tokens in total with a 400-article pool. On Haiku 5.5 that is well under a cent a day.

Categories are plain data in `digest.yml`:

```yaml
categories:
  - key: tech
    title: Tech and research
    brief: >
      A notable technology improvement, research result, or new EU rule...
    prefer:
      - "dewe ?soft"
    prefer_note: Anything new about Dewesoft takes this slot.
```

Add, remove or reword categories there and run `/reload`. The rest of the file (time, time zone, model, effort, quality bar, window sizes) is commented in the template.

## Commands

| Command | Who | What it does |
| --- | --- | --- |
| `/sources` | everyone | Lists every configured site |
| `/status` | everyone | Last poll, posts sent and last error per site |
| `/preview name` | everyone | Shows the newest items without posting them |
| `/watch url` | Manage Server | Finds a site's feed and starts watching it |
| `/unwatch name` | Manage Server | Stops watching, optionally forgets its history |
| `/pause name` | Manage Server | Pauses a site, keeps the entry |
| `/resume name` | Manage Server | Resumes a paused site |
| `/pollnow [name]` | Manage Server | Polls one site or all of them immediately |
| `/reload` | Manage Server | Re-reads `sources.yml` and `digest.yml` from disk |
| `/digest [post]` | Manage Server | Builds today's digest now; shows it only to you unless `post:true` |

## Settings

Everything in `.env` except the first two values has a working default.

| Variable | Default | Meaning |
| --- | --- | --- |
| `DISCORD_TOKEN` | required | Bot token |
| `DISCORD_CHANNEL_ID` | required unless every source names a channel | Default channel |
| `ANTHROPIC_API_KEY` | required for the digest | Claude API key |
| `DISCORD_GUILD_ID` | empty | Set it for instant slash command registration |
| `DISCORD_APPLICATION_ID` | empty | Bot application ID; enables the invite link in the log |
| `SOURCES_FILE` | `sources.yml` | Watch list location |
| `DIGEST_FILE` | `digest.yml` | Digest settings; missing file turns the digest off |
| `DATABASE_PATH` | `data/seen.sqlite3` | Dedupe database |
| `POLL_INTERVAL` | `600` | Default seconds between polls |
| `MAX_ITEMS_PER_POLL` | `5` | Flood guard per source per poll |
| `ANNOUNCE_BACKLOG` | `false` | `true` posts everything a site already has on the first poll |
| `HTTP_TIMEOUT` | `20` | Seconds before a slow site is given up on |
| `RETENTION_DAYS` | `90` | How long a posted item is remembered |
| `USER_AGENT` | a browser-like string | Some sites block the default python agent |
| `LOG_LEVEL` | `INFO` | `DEBUG` prints every poll |

## Behaviour worth knowing

The first poll of a new site records what already exists and posts nothing, so adding twenty sites does not dump a thousand messages into the channel. Set `ANNOUNCE_BACKLOG=true` if you want the opposite.

Posted items are remembered in SQLite by source and URL, so restarts never repeat a story. Delete `data/seen.sqlite3` to make the bot forget everything, or use `/unwatch name forget:true` for one site.

When a site publishes more than `max_items` between two polls, the oldest are posted first and the rest arrive on the next tick. Nothing is dropped.

Each source polls on its own timer in its own task. One broken selector or one site returning 503 does not stop the others; failures back off exponentially up to an hour and show up in `/status`.

## Running in the background

```bash
make docker
```

That copies any missing templates first, then runs `docker compose up -d --build`. The container reads `.env` and mounts `sources.yml`, `digest.yml` and `data/` from the host, so every setting stays local: edit the files, then `/reload` (YAML) or `docker compose restart` (`.env`). The databases in `data/` survive rebuilds.

Create `sources.yml` and `digest.yml` before the first `docker compose up`. If they are missing, docker creates empty directories with those names and the bot stops with a message saying so.

For systemd instead:

```ini
[Unit]
Description=news-bot-dc
After=network-online.target

[Service]
WorkingDirectory=/opt/news-bot-dc
ExecStart=/opt/news-bot-dc/.venv/bin/python -m newsbot
Restart=always

[Install]
WantedBy=multi-user.target
```

## Layout

```
newsbot/
  config.py       .env and sources.yml into typed settings
  models.py       the Item every fetcher produces
  fetchers/       rss.py, html.py, json_api.py, registered by type name
  filters.py      include and exclude matching
  store.py        SQLite dedupe
  http.py         one shared session
  poller.py       one loop per source: fetch, filter, dedupe, publish
  discovery.py    finds the feed behind a page
  subscribe.py    turns a pasted URL into a source entry
  sourcefile.py   edits sources.yml without losing comments
  render.py       Item to Discord embed
  digest/
    config.py     digest.yml into typed settings
    pool.py       every fetched item, stored for the daily pick
    llm.py        the only module calling the Claude API
    select.py     one article per category, preferred patterns enforced
    english.py    English coverage for non-English picks
    quotes.py     Office transcript search and quote verification
    builder.py    runs the steps in order
    render.py     digest to Discord embeds or plain text
    scheduler.py  fires once a day at post_at
  bot.py          the Discord client
  commands.py     slash commands
  __main__.py     run, check and digest entry points
assets/
  office_quotes.csv   29k lines from the schrute transcript (MIT), rebuilt by tools/build_office_quotes.py
```

Adding a new kind of site means one module in `fetchers/` with a `@register("name")` decorator. Nothing else changes.

## Tests

```bash
pip install pytest pytest-asyncio
pytest -q
```

The suite runs offline; the network is stubbed.
