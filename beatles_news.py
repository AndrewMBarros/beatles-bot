import argparse
import calendar
import html
import json
import os
import re
import time
from collections import namedtuple
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, quote_plus, urlparse

import feedparser
import requests

try:
    from dotenv import load_dotenv
    load_dotenv()  # se existir um .env local, carrega; no GitHub Actions isso não faz nada (usa os Secrets)
except ImportError:
    pass

SEEN_FILE = os.environ.get("SEEN_FILE") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "seen.json")
MAX_SEEN = 12000  # evita o arquivo crescer para sempre
MAX_AGE_HOURS = 72  # ignora itens antigos que reaparecem nos feeds
DRY_RUN_HOURS = 6
MAX_PER_RUN = 40  # o que passar disso fica para a próxima execução
DIGEST_MINUTES = int(os.environ.get("DIGEST_MINUTES", "60"))
LOOP_SECONDS = int(os.environ.get("LOOP_SECONDS", "60"))
FAIL_ALERT_HOURS = 24  # avisa no Telegram se uma fonte ficar fora do ar por esse tempo
SIMILAR_TITLE = 0.7  # quanto dois títulos precisam coincidir para serem a mesma notícia
MIN_WORDS_SIMILAR = 6
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36"

NOW = "now"  # cada item vira uma mensagem na hora
DIGEST = "digest"  # redes sociais e fóruns: vários itens num resumo periódico

# Em ordem de prioridade: quem aparece primeiro dá o rótulo da mensagem
TERMS = [
    ("Paul McCartney", re.compile(r"\bpaul\s+mccartney\b", re.I)),
    ("John Lennon", re.compile(r"\bjohn\s+lennon\b(?!\s+(international\s+)?airport)", re.I)),
    ("George Harrison", re.compile(r"\bgeorge\s+harrison\b", re.I)),
    ("Ringo Starr", re.compile(r"\bringo\s+starr\b", re.I)),
    ("The Beatles", re.compile(r"\b(beatles?|beatlemania|fab\s+four)\b", re.I)),
]

Source = namedtuple("Source", "name url label tier every filtered kind", defaults=(False, "feed"))
Item = namedtuple("Item", "label title link publisher published tier")


def google_news(query, hl="en-GB", gl="GB", ceid="GB:en"):
    return f"https://news.google.com/rss/search?q={quote_plus(query)}%20when:1d&hl={hl}&gl={gl}&ceid={ceid}"


def build_sources():
    all_names = '"Beatles" OR "Paul McCartney" OR "John Lennon" OR "George Harrison" OR "Ringo Starr"'
    sources = [
        Source("Google News (PT)", google_news(all_names, "pt-BR", "BR", "BR:pt-419"), "The Beatles", NOW, 3, False, "search"),
        Source("Google News (ES)", google_news(all_names, "es-419", "MX", "MX:es-419"), "The Beatles", NOW, 3, False, "search"),
    ]

    for label, query in [
        ("The Beatles", "Beatles"),
        ("Paul McCartney", '"Paul McCartney"'),
        ("John Lennon", '"John Lennon"'),
        ("George Harrison", '"George Harrison"'),
        ("Ringo Starr", '"Ringo Starr"'),
    ]:
        sources.append(Source(f"Google News: {label}", google_news(query), label, NOW, 3, False, "search"))
        sources.append(Source(f"Bing News: {label}", f"https://www.bing.com/news/search?q={quote_plus(query)}&format=rss", label, NOW, 5, False, "search"))

    for label, tag in [
        ("The Beatles", "thebeatles"),
        ("Paul McCartney", "paulmccartney"),
        ("John Lennon", "johnlennon"),
        ("George Harrison", "georgeharrison"),
    ]:
        sources.append(Source(f"The Guardian: {label}", f"https://www.theguardian.com/music/{tag}/rss", label, NOW, 10))

    # Sites gerais de música: só interessa o que citar um dos nomes
    for name, url in [
        ("Rolling Stone", "https://www.rollingstone.com/music/feed/"),
        ("NME", "https://www.nme.com/news/music/feed"),
        ("Ultimate Classic Rock", "https://ultimateclassicrock.com/feed/"),
        ("Far Out", "https://faroutmagazine.co.uk/feed/"),
        ("Billboard", "https://www.billboard.com/feed/"),
        ("Variety", "https://variety.com/v/music/feed/"),
        ("Consequence", "https://consequence.net/feed/"),
        ("American Songwriter", "https://americansongwriter.com/feed/"),
        ("Best Classic Bands", "https://bestclassicbands.com/feed/"),
        ("Stereogum", "https://www.stereogum.com/feed/"),
        ("Pitchfork", "https://pitchfork.com/feed/feed-news/rss"),
    ]:
        sources.append(Source(name, url, "The Beatles", NOW, 10, True))

    # Sites oficiais, de fãs e canais do YouTube
    for name, url, label in [
        ("thebeatles.com", "https://www.thebeatles.com/rss.xml", "The Beatles"),
        ("georgeharrison.com", "https://www.georgeharrison.com/feed/", "George Harrison"),
        ("ringostarr.com", "https://www.ringostarr.com/feed/", "Ringo Starr"),
        ("Meet the Beatles for Real", "http://www.meetthebeatlesforreal.com/feeds/posts/default", "The Beatles"),
        ("YouTube: The Beatles", "https://www.youtube.com/feeds/videos.xml?user=TheBeatles", "The Beatles"),
        ("YouTube: Paul McCartney", "https://www.youtube.com/feeds/videos.xml?user=PAULMCCARTNEY", "Paul McCartney"),
        ("YouTube: John Lennon", "https://www.youtube.com/feeds/videos.xml?user=johnlennon", "John Lennon"),
        ("YouTube: Ringo Starr", "https://www.youtube.com/feeds/videos.xml?user=RingoStarrVEVO", "Ringo Starr"),
    ]:
        sources.append(Source(name, url, label, NOW, 10))

    # Redes sociais e fóruns
    sources.append(Source(
        "Reddit",
        "https://www.reddit.com/r/beatles+PaulMcCartney+johnlennon+georgeharrison+ringostarr/new/.rss",
        "The Beatles", DIGEST, 5,
    ))
    sources.append(Source("Steve Hoffman Forums", "https://forums.stevehoffman.tv/forums/-/index.rss", "The Beatles", DIGEST, 10, True))
    for label, tag in [
        ("The Beatles", "beatles"),
        ("Paul McCartney", "PaulMcCartney"),
        ("John Lennon", "JohnLennon"),
        ("George Harrison", "GeorgeHarrison"),
        ("Ringo Starr", "RingoStarr"),
    ]:
        sources.append(Source(f"Mastodon: {label}", f"https://mastodon.social/tags/{tag}.rss", label, DIGEST, 5, True))
    # "Beatles" sozinho no Bluesky passa de 20 posts por hora: só os nomes completos
    for label in ["Paul McCartney", "John Lennon", "George Harrison", "Ringo Starr"]:
        sources.append(Source(
            f"Bluesky: {label}",
            "https://api.bsky.app/xrpc/app.bsky.feed.searchPosts?" + f'q="{quote_plus(label)}"&sort=latest&limit=100',
            label, DIGEST, 5, True, "bluesky",
        ))
    return sources


def normalize(title: str) -> str:
    """Deixa o título só com letras/números minúsculos, pra comparar
    mesmo quando o Google muda pontuação/capitalização entre execuções."""
    title = title.lower()
    title = re.sub(r"[^a-z0-9à-úãõâêîôûç ]", "", title)
    return re.sub(r"\s+", " ", title).strip()


def strip_publisher(title: str, publisher: str) -> str:
    """O Google termina os títulos com " - Nome do Site", o que impediria
    reconhecer a mesma notícia vinda de outra fonte."""
    suffix = f" - {publisher}"
    if publisher and title.endswith(suffix):
        return title[:-len(suffix)]
    return title


def clean_text(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def shorten(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def detect_label(text: str, default: str) -> str:
    for label, pattern in TERMS:
        if pattern.search(text):
            return label
    return default


def mentions_any(text: str) -> bool:
    return any(pattern.search(text) for _, pattern in TERMS)


def real_link(link: str) -> str:
    """Os links do Bing passam por um redirecionamento: pega o endereço de destino."""
    parsed = urlparse(link)
    if "bing.com" in parsed.netloc and "apiclick" in parsed.path:
        return parse_qs(parsed.query).get("url", [link])[0]
    return link


def parse_feed(source: Source, content: bytes):
    feed = feedparser.parse(content)
    if not feed.get("version") and not feed.entries:
        raise ValueError("a resposta não é um feed")

    items = []
    for entry in feed.entries:
        summary = clean_text(entry.get("summary", ""))
        title = clean_text(entry.get("title", "")) or summary
        link = real_link((entry.get("link") or "").strip())
        if not title or not link:
            continue
        if source.filtered and not mentions_any(f"{title} {summary}"):
            continue

        # Buscadores trazem matérias que só citam o nome no meio do texto: vão para o resumo
        tier = source.tier
        if source.kind == "search" and not mentions_any(title):
            tier = DIGEST

        publisher = (entry.get("source") or {}).get("title") or entry.get("news_source") or source.name
        date = entry.get("published_parsed") or entry.get("updated_parsed")
        items.append(Item(
            label=detect_label(title, source.label),
            title=shorten(title, 500),
            link=link,
            publisher=publisher,
            published=calendar.timegm(date) if date else None,
            tier=tier,
        ))
    return items


def parse_bluesky(source: Source, data: dict):
    items = []
    for post in data.get("posts", []):
        record = post.get("record") or {}
        text = clean_text(record.get("text", ""))
        # Só posts que apontam para um link: sem isso o volume vira ruído de conversa
        if "external" not in str((post.get("embed") or {}).get("$type", "")):
            continue
        if not text or not mentions_any(text):
            continue

        handle = (post.get("author") or {}).get("handle", "")
        rkey = post.get("uri", "").rsplit("/", 1)[-1]
        created = post.get("indexedAt", "")
        date = time.strptime(created[:19], "%Y-%m-%dT%H:%M:%S") if created else None
        items.append(Item(
            label=detect_label(text, source.label),
            title=shorten(text, 500),
            link=f"https://bsky.app/profile/{handle}/post/{rkey}",
            publisher=f"Bluesky @{handle}",
            published=calendar.timegm(date) if date else None,
            tier=source.tier,
        ))
    return items


def fetch_source(source: Source):
    resp = requests.get(source.url, timeout=20, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    if source.kind == "bluesky":
        return parse_bluesky(source, resp.json())
    return parse_feed(source, resp.content)


def new_state():
    return {"version": 2, "seen": {}, "seeded": [], "pending": [], "last_digest": 0, "failing": {}}


def load_state():
    if not os.path.exists(SEEN_FILE):
        return new_state()
    with open(SEEN_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        # Formato antigo: lista de títulos. Mantém o histórico e deixa
        # "seeded" vazio, então nada é enviado na primeira passada de cada fonte.
        state = new_state()
        state["seen"] = {key: int(time.time()) for key in data}
        return state
    return {**new_state(), **data}


def save_state(state):
    keys = list(state["seen"])
    for key in keys[:-MAX_SEEN]:  # dicionário mantém a ordem: remove os mais antigos
        del state["seen"][key]
    temp_file = f"{SEEN_FILE}.tmp"
    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=0)
    os.replace(temp_file, SEEN_FILE)  # um reinício no meio da gravação não corrompe o histórico


def item_key(item: Item) -> str:
    key = normalize(strip_publisher(item.title, item.publisher))
    if item.tier == DIGEST and len(key.split()) < 5:
        return item.link  # posts curtos demais não identificam nada pelo texto
    return key


class SeenIndex:
    """Reconhece títulos iguais e também a mesma notícia escrita de jeito parecido."""

    def __init__(self, seen: dict):
        self.seen = seen
        self.recent = [set(key.split()) for key in list(seen)[-3000:] if not key.startswith("http")]

    def contains(self, item: Item) -> bool:
        key = item_key(item)
        if not key or key in self.seen or normalize(item.title) in self.seen:
            return True
        words = set(key.split())
        if len(words) < MIN_WORDS_SIMILAR:
            return False
        for other in self.recent:
            if len(other) >= MIN_WORDS_SIMILAR and len(words & other) / len(words | other) >= SIMILAR_TITLE:
                return True
        return False

    def add(self, item: Item, now: float):
        key = item_key(item)
        self.seen[key] = int(now)
        if not key.startswith("http"):
            self.recent.append(set(key.split()))


def message_for(item: Item) -> str:
    return (
        f"🎵 <b>{html.escape(item.label)}</b>\n"
        f"{html.escape(strip_publisher(item.title, item.publisher), quote=False)}\n"
        f"<i>{html.escape(item.publisher, quote=False)}</i>\n"
        f'<a href="{html.escape(item.link)}">Abrir</a>'
    )


def digest_messages(pending: list):
    """Agrupa por integrante e divide respeitando o limite de 4096 caracteres do Telegram."""
    header = f"🗞 <b>Resumo de redes sociais e fóruns</b> ({len(pending)})"
    messages, current = [], header
    for label in sorted({entry["label"] for entry in pending}):
        lines = [f"\n\n<b>{html.escape(label)}</b>"]
        for entry in pending:
            if entry["label"] == label:
                title = html.escape(shorten(entry["title"], 160), quote=False)
                publisher = html.escape(entry["publisher"], quote=False)
                lines.append(f'\n• <a href="{html.escape(entry["link"])}">{title}</a> <i>{publisher}</i>')
        for line in lines:
            if len(current) + len(line) > 3900:
                messages.append(current)
                current = line.lstrip("\n")
            else:
                current += line
    messages.append(current)
    return messages


SENT = "sent"
DROP = "drop"  # o Telegram nunca vai aceitar essa mensagem
RETRY = "retry"  # problema passageiro: tenta de novo na próxima execução


class TelegramConfigError(Exception):
    """Token ou chat errado: nada vai chegar até alguém corrigir."""


def telegram_sender(token: str, chat_id: str):
    url = f"https://api.telegram.org/bot{token}/sendMessage"

    def send(text: str, preview: bool = True) -> str:
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": not preview,
        }
        for _ in range(2):
            try:
                resp = requests.post(url, data=payload, timeout=20)
            except requests.RequestException as exc:
                print(f"Falha de rede ao enviar pro Telegram: {type(exc).__name__}")
                return RETRY
            if resp.ok:
                return SENT
            if resp.status_code == 429:
                wait = resp.json().get("parameters", {}).get("retry_after", 30)
                print(f"Telegram pediu para esperar {wait}s.")
                time.sleep(min(wait, 60))
                continue
            print(f"Falha ao enviar pro Telegram: {resp.status_code} {resp.text}")
            if resp.status_code in (401, 403) or "chat not found" in resp.text:
                # Interrompe com erro: no GitHub Actions a execução fica vermelha e o GitHub avisa
                raise TelegramConfigError(f"Telegram recusou o envio ({resp.status_code}): confira o token e o chat")
            if resp.status_code == 400 and ("parse entities" in resp.text or "too long" in resp.text):
                return DROP
            return RETRY
        return RETRY

    return send


def dry_run_sender(text: str, preview: bool = True) -> str:
    print(f"[teste, não enviado]\n{text}\n")
    return SENT


def fetch_due(sources, last_fetch, fetch, now):
    due = [s for s in sources if now - last_fetch.get(s.name, 0) >= s.every * 60 - 5]

    def worker(source):
        try:
            return source, fetch(source), None
        except Exception as exc:
            return source, [], exc

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(worker, due))
    for source in due:
        last_fetch[source.name] = now
    return results


def alert_failures(state, results, send, now):
    failing = state["failing"]
    for source, _, error in results:
        if error is None:
            failing.pop(source.name, None)
        else:
            print(f"Erro buscando {source.name}: {type(error).__name__}: {error}")
            failing.setdefault(source.name, {"since": int(now), "alerted": False})

    late = [name for name, info in failing.items()
            if not info["alerted"] and now - info["since"] >= FAIL_ALERT_HOURS * 3600]
    if late:
        text = f"⚠️ <b>Fontes fora do ar há mais de {FAIL_ALERT_HOURS}h</b>\n" + "\n".join(html.escape(n) for n in late)
        if send(text, False) == SENT:
            for name in late:
                failing[name]["alerted"] = True


def flush_digest(state, send, now):
    if not state["pending"]:
        state["last_digest"] = now
        return 0
    if now - state["last_digest"] < DIGEST_MINUTES * 60:
        return 0
    messages = digest_messages(state["pending"])
    for text in messages:
        if send(text, False) == RETRY:
            return 0
    count = len(state["pending"])
    state["pending"] = []
    state["last_digest"] = now
    return count


def run_once(state, sources, send, save=save_state, fetch=fetch_source, last_fetch=None, now=None):
    now = time.time() if now is None else now
    last_fetch = {} if last_fetch is None else last_fetch
    results = fetch_due(sources, last_fetch, fetch, now)
    alert_failures(state, results, send, now)

    index = SeenIndex(state["seen"])
    queue = []
    for source, items, error in results:
        if error is not None:
            continue
        first_pass = source.name not in state["seeded"]
        for item in items:
            if item.published and now - item.published > MAX_AGE_HOURS * 3600:
                continue
            if first_pass:
                # Fonte nova: grava o que já existe, sem disparar tudo de uma vez
                index.add(item, now)
            else:
                queue.append(item)
        if first_pass:
            state["seeded"].append(source.name)
            print(f"Primeira passada de {source.name}: histórico gravado sem enviar mensagens.")

    queue.sort(key=lambda item: item.published or now)
    sent = queued = 0
    for item in queue:
        if index.contains(item):
            continue
        if item.tier == DIGEST:
            title = strip_publisher(item.title, item.publisher)
            state["pending"].append({"label": item.label, "title": title, "link": item.link, "publisher": item.publisher})
            index.add(item, now)
            queued += 1
            continue
        if sent >= MAX_PER_RUN:
            continue
        status = send(message_for(item))
        if status == RETRY:
            print("Envio interrompido: as notícias restantes ficam para a próxima execução.")
            break
        index.add(item, now)  # só depois da resposta do Telegram
        if status == SENT:
            sent += 1
        save(state)
        time.sleep(1)  # evita rate limit do Telegram

    summarized = flush_digest(state, send, now)
    save(state)
    print(f"Concluído. Mensagens enviadas: {sent}. Na fila do resumo: {queued}. "
          f"Resumidos agora: {summarized}. Total memorizado: {len(state['seen'])}.")
    return sent


def main():
    parser = argparse.ArgumentParser(description="Notícias dos Beatles e dos integrantes no Telegram")
    parser.add_argument("--loop", action="store_true", help="fica rodando continuamente (Railway)")
    parser.add_argument("--dry-run", action="store_true", help="mostra o que enviaria nas últimas horas, sem enviar nem gravar nada")
    args = parser.parse_args()

    if args.dry_run:
        send, save = dry_run_sender, lambda state: None
    else:
        token = os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ["BOT_TOKEN"]
        chat_id = os.environ.get("TELEGRAM_CHAT_ID") or os.environ["CHAT_ID"]
        send, save = telegram_sender(token, chat_id), save_state

    sources = build_sources()
    state = load_state()
    last_fetch = {}

    if args.dry_run:
        # Sem isso a primeira passada de cada fonte não mostraria nada
        global MAX_AGE_HOURS
        MAX_AGE_HOURS = DRY_RUN_HOURS
        state["seeded"] = [source.name for source in sources]
        state["last_digest"] = 0

    if not args.loop:
        run_once(state, sources, send, save=save, last_fetch=last_fetch)
        return

    while True:
        try:
            run_once(state, sources, send, save=save, last_fetch=last_fetch)
        except Exception as exc:
            print(f"Erro na execução: {type(exc).__name__}: {exc}")
        time.sleep(LOOP_SECONDS)


if __name__ == "__main__":
    main()
