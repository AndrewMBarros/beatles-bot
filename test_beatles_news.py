import json
import os
import tempfile
import unittest
from unittest import mock

import beatles_news as bot
from beatles_news import DIGEST, NOW, SENT, DROP, RETRY, Item, Source

T0 = 1_800_000_000
NEWS = Source("Noticias", "http://x", "The Beatles", NOW, 1)
SOCIAL = Source("Social", "http://y", "The Beatles", DIGEST, 1)


def news(title, publisher="Site", published=T0):
    return Item("The Beatles", title, f"http://x/{abs(hash(title))}", publisher, published, NOW)


class Sender:
    def __init__(self, statuses=None):
        self.texts = []
        self.statuses = statuses or {}

    def __call__(self, text, preview=True):
        self.texts.append(text)
        for fragment, status in self.statuses.items():
            if fragment in text:
                return status
        return SENT


def run(state, send, items, now=T0, source=NEWS):
    with mock.patch.object(bot.time, "sleep"):
        return bot.run_once(state, [source], send, save=lambda s: None, fetch=lambda s: items, now=now)


class RunOnceTests(unittest.TestCase):
    def seeded(self, *sources):
        state = bot.new_state()
        state["seeded"] = [s.name for s in sources or (NEWS,)]
        state["last_digest"] = T0
        return state

    def test_new_source_is_recorded_silently(self):
        state, send = bot.new_state(), Sender()
        run(state, send, [news("Paul McCartney announces a brand new world tour")])
        self.assertEqual(send.texts, [])
        self.assertIn("Noticias", state["seeded"])
        run(state, send, [news("Paul McCartney announces a brand new world tour")])
        self.assertEqual(send.texts, [])

    def test_failed_send_is_retried_next_run(self):
        state = self.seeded()
        item = news("Ringo Starr releases a new country album today")
        run(state, Sender({"Ringo": RETRY}), [item])
        self.assertEqual(state["seen"], {})
        send = Sender()
        run(state, send, [item])
        self.assertEqual(len(send.texts), 1)

    def test_rejected_message_is_not_retried_forever(self):
        state = self.seeded()
        item = news("Ringo Starr releases a new country album today")
        run(state, Sender({"Ringo": DROP}), [item])
        send = Sender()
        run(state, send, [item])
        self.assertEqual(send.texts, [])

    def test_same_story_from_two_publishers_is_sent_once(self):
        state, send = self.seeded(), Sender()
        run(state, send, [
            news("Paul McCartney announces new documentary about Wings - BBC News", "BBC News"),
            news("Paul McCartney announces new documentary about Wings", "The Guardian"),
            news("Paul McCartney announces a new documentary about Wings", "NME"),
        ])
        self.assertEqual(len(send.texts), 1)

    def test_different_stories_are_both_sent(self):
        state, send = self.seeded(), Sender()
        run(state, send, [
            news("Paul McCartney announces new documentary about Wings"),
            news("Ringo Starr postpones his autumn tour after illness"),
        ])
        self.assertEqual(len(send.texts), 2)

    def test_old_items_are_ignored(self):
        state, send = self.seeded(), Sender()
        run(state, send, [news("John Lennon letter sold at auction in London", published=T0 - 100 * 3600)])
        self.assertEqual(send.texts, [])

    def test_html_in_title_is_escaped(self):
        state, send = self.seeded(), Sender()
        run(state, send, [news("Rock & Roll <3 Beatles forever and ever more")])
        self.assertIn("Rock &amp; Roll &lt;3", send.texts[0])

    def test_failing_source_does_not_stop_the_others(self):
        state, send = self.seeded(NEWS), Sender()
        broken = Source("Quebrada", "http://z", "The Beatles", NOW, 1)

        def fetch(source):
            if source is broken:
                raise OSError("fora do ar")
            return [news("George Harrison archive opens to the public soon")]

        with mock.patch.object(bot.time, "sleep"):
            bot.run_once(state, [broken, NEWS], send, save=lambda s: None, fetch=fetch, now=T0)
        self.assertEqual(len(send.texts), 1)
        self.assertIn("Quebrada", state["failing"])

    def test_long_outage_alerts_only_once(self):
        state, send = self.seeded(NEWS), Sender()

        def fetch(source):
            raise OSError("fora do ar")

        with mock.patch.object(bot.time, "sleep"):
            for hours in (0, 25, 26):
                bot.run_once(state, [NEWS], send, save=lambda s: None, fetch=fetch, now=T0 + hours * 3600)
        self.assertEqual(len(send.texts), 1)
        self.assertIn("Noticias", send.texts[0])

    def test_social_items_wait_for_the_digest(self):
        state, send = self.seeded(SOCIAL), Sender()
        item = Item("Ringo Starr", "Ringo Starr just posted a lovely message today", "http://b/1", "Reddit", T0, DIGEST)
        run(state, send, [item], source=SOCIAL)
        self.assertEqual(send.texts, [])
        self.assertEqual(len(state["pending"]), 1)
        run(state, send, [item], now=T0 + bot.DIGEST_MINUTES * 60, source=SOCIAL)
        self.assertEqual(len(send.texts), 1)
        self.assertIn("Ringo Starr", send.texts[0])
        self.assertEqual(state["pending"], [])

    def test_failed_digest_is_kept(self):
        state, send = self.seeded(SOCIAL), Sender({"Resumo": RETRY})
        state["pending"] = [{"label": "The Beatles", "title": "t", "link": "http://b", "publisher": "p"}]
        run(state, send, [], now=T0 + bot.DIGEST_MINUTES * 60, source=SOCIAL)
        self.assertEqual(len(state["pending"]), 1)

    def test_digest_respects_telegram_limit(self):
        pending = [{"label": "The Beatles", "title": "x" * 200, "link": f"http://b/{i}", "publisher": "p"} for i in range(100)]
        messages = bot.digest_messages(pending)
        self.assertGreater(len(messages), 1)
        self.assertTrue(all(len(m) <= 4096 for m in messages))


class TelegramTests(unittest.TestCase):
    def post(self, status, body):
        response = mock.Mock(ok=status == 200, status_code=status, text=body)
        return mock.patch.object(bot.requests, "post", return_value=response)

    def test_wrong_chat_stops_with_error(self):
        with self.post(400, "Bad Request: chat not found"), self.assertRaises(bot.TelegramConfigError):
            bot.telegram_sender("t", "c")("oi")

    def test_revoked_token_stops_with_error(self):
        with self.post(401, "Unauthorized"), self.assertRaises(bot.TelegramConfigError):
            bot.telegram_sender("t", "c")("oi")

    def test_unparsable_message_is_dropped(self):
        with self.post(400, "Bad Request: can't parse entities"):
            self.assertEqual(bot.telegram_sender("t", "c")("oi"), DROP)

    def test_server_error_is_retried(self):
        with self.post(502, "Bad Gateway"):
            self.assertEqual(bot.telegram_sender("t", "c")("oi"), RETRY)


class ParsingTests(unittest.TestCase):
    def test_airport_is_not_john_lennon(self):
        self.assertFalse(bot.mentions_any("Liverpool John Lennon Airport arrivals"))
        self.assertTrue(bot.mentions_any("John Lennon letter sold"))

    def test_terms_require_full_names(self):
        self.assertFalse(bot.mentions_any("Stella McCartney launches new collection"))
        self.assertFalse(bot.mentions_any("Ringo the dog wins a prize"))
        self.assertTrue(bot.mentions_any("A new book about Paul  McCartney"))
        self.assertTrue(bot.mentions_any("BEATLES reissue"))

    def test_atom_feed_is_read(self):
        atom = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
        <entry><title>The Beatles on r/beatles</title><link href="http://r/1"/>
        <updated>2026-11-01T10:00:00Z</updated></entry></feed>"""
        items = bot.parse_feed(SOCIAL, atom)
        self.assertEqual(items[0].link, "http://r/1")
        self.assertIsNotNone(items[0].published)

    def test_filtered_feed_keeps_only_matches(self):
        rss = b"""<rss version="2.0"><channel>
        <item><title>Taylor Swift tour</title><link>http://a</link></item>
        <item><title>Tribute</title><description>A tribute to Ringo Starr</description><link>http://b</link></item>
        </channel></rss>"""
        source = Source("Geral", "u", "The Beatles", NOW, 1, True)
        self.assertEqual([i.link for i in bot.parse_feed(source, rss)], ["http://b"])

    def test_search_result_without_name_in_title_goes_to_digest(self):
        rss = b"""<rss version="2.0"><channel>
        <item><title>Metallica tour news - Site</title><link>http://a</link></item>
        <item><title>Ringo Starr tour news - Site</title><link>http://b</link></item>
        </channel></rss>"""
        source = Source("Busca", "u", "Ringo Starr", NOW, 1, False, "search")
        self.assertEqual([i.tier for i in bot.parse_feed(source, rss)], [DIGEST, NOW])

    def test_html_page_is_an_error(self):
        with self.assertRaises(ValueError):
            bot.parse_feed(NEWS, b"")

    def test_bing_redirect_is_unwrapped(self):
        link = "http://www.bing.com/news/apiclick.aspx?ref=FexRss&url=https%3a%2f%2fsite.com%2fa&c=1"
        self.assertEqual(bot.real_link(link), "https://site.com/a")

    def test_bluesky_needs_a_link(self):
        post = {
            "uri": "at://did/app.bsky.feed.post/abc",
            "indexedAt": "2026-11-01T10:00:00.000Z",
            "author": {"handle": "fan.bsky.social"},
            "record": {"text": "Ringo Starr is on tour"},
        }
        with_link = {**post, "embed": {"$type": "app.bsky.embed.external#view"}}
        items = bot.parse_bluesky(SOCIAL, {"posts": [post, with_link]})
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].link, "https://bsky.app/profile/fan.bsky.social/post/abc")


class StateTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        patcher = mock.patch.object(bot, "SEEN_FILE", os.path.join(self.dir.name, "seen.json"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_old_format_is_migrated_and_sends_nothing(self):
        with open(bot.SEEN_FILE, "w", encoding="utf-8") as f:
            json.dump(["paul mccartney announces world tour bbc news"], f)
        state = bot.load_state()
        self.assertEqual(state["seeded"], [])
        send = Sender()
        run(state, send, [news("A totally unrelated new story about George Harrison")])
        self.assertEqual(send.texts, [])

    def test_old_key_with_publisher_still_matches(self):
        state = bot.new_state()
        state["seeded"] = [NEWS.name]
        state["seen"]["paul mccartney announces world tour bbc news"] = T0
        send = Sender()
        run(state, send, [news("Paul McCartney announces world tour - BBC News", "BBC News")])
        self.assertEqual(send.texts, [])

    def test_trim_removes_the_oldest(self):
        state = bot.new_state()
        with mock.patch.object(bot, "MAX_SEEN", 2):
            for key in ("a", "b", "c"):
                state["seen"][key] = T0
            bot.save_state(state)
        self.assertEqual(list(bot.load_state()["seen"]), ["b", "c"])
        self.assertFalse(os.path.exists(bot.SEEN_FILE + ".tmp"))


if __name__ == "__main__":
    unittest.main()
