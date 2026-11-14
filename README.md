![Python](https://img.shields.io/badge/Python-3.11-blue?logo=python&logoColor=white)
![Telegram](https://img.shields.io/badge/Telegram-Bot_API-26A5E4?logo=telegram&logoColor=white)
![GitHub Actions](https://img.shields.io/badge/GitHub_Actions-2088FF?logo=githubactions&logoColor=white)
![Railway](https://img.shields.io/badge/Railway-0B0D0E?logo=railway&logoColor=white)

# Beatles Bot

[🇧🇷 Versão em Português](README.pt-BR.md)

Monitors news, social networks and forums about the Beatles and its four members, and sends alerts to Telegram.

- **Instantly:** Google News and Bing News (name in the title only), The Guardian, music sites, official sites and YouTube.
- **In the hourly digest:** Reddit, Steve Hoffman Forums, Mastodon, Bluesky and search results that only mention the name in the text.

When a source is added for the first time, its existing items are recorded without sending messages. If a source stays down for 24 hours, the bot sends an alert to Telegram.

## ✨ Technical highlights

- **46 sources** (RSS/Atom feeds and the Bluesky API), from news portals to fan forums.
- **Deduplication:** history in `seen.json` plus similar-title matching, so the same story published by several sites arrives only once.
- **Resilient delivery:** separates transient errors (retried on the next run) from messages Telegram would never accept, and caps the number of sends per run.
- **Source health monitoring:** Telegram alert when a source has been down for 24 hours.
- **Automated tests** with `unittest`, plus a `--dry-run` mode to check output without sending anything.
- **Two run modes:** continuous loop on Railway or scheduled runs on GitHub Actions.

## Run locally

```bash
pip install -r requirements.txt
python -m unittest test_beatles_news
python beatles_news.py --dry-run
```

`--dry-run` shows what would have been sent in the last 6 hours, without sending or saving anything.

To send for real, copy `.env.example` to `.env` and fill in `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.

## Where to run

Pick only one: with both enabled on the same chat, every story arrives twice.

### Railway (every minute)

1. Create the project from the repository. The `Procfile` already runs `python beatles_news.py --loop`.
2. Under Variables, set `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` and `SEEN_FILE=/data/seen.json`.
3. Create a volume mounted at `/data`, so the history survives restarts.
4. Disable the workflow on GitHub (Actions tab).

### GitHub Actions (every 15 minutes or more)

Already configured in `.github/workflows/beatles-news.yml`. Set the `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` secrets.

## 🛠 Tech stack

Python · requests · feedparser · python-dotenv · Telegram Bot API · GitHub Actions · Railway

## 👤 Author

**Andrew Matheus** — [LinkedIn](https://www.linkedin.com/in/andrewmbs/) · [GitHub](https://github.com/AndrewMBarros)
