![Python](https://img.shields.io/badge/Python-3.11-blue?logo=python&logoColor=white)
![Telegram](https://img.shields.io/badge/Telegram-Bot_API-26A5E4?logo=telegram&logoColor=white)
![GitHub Actions](https://img.shields.io/badge/GitHub_Actions-2088FF?logo=githubactions&logoColor=white)
![Railway](https://img.shields.io/badge/Railway-0B0D0E?logo=railway&logoColor=white)

# Beatles Bot

[🇺🇸 English version](README.md)

Monitora notícias, redes sociais e fóruns sobre os Beatles e os quatro integrantes e avisa no Telegram.

- **Na hora:** Google News e Bing News (só com o nome no título), The Guardian, sites de música, sites oficiais e YouTube.
- **No resumo (de hora em hora):** Reddit, Steve Hoffman Forums, Mastodon, Bluesky e resultados de busca que só citam o nome no texto.

Quando uma fonte entra pela primeira vez, o que já existe nela é gravado sem enviar mensagens. Se ela ficar 24h fora do ar, o bot avisa no Telegram.

## ✨ Destaques técnicos

- **46 fontes** (feeds RSS/Atom e a API do Bluesky), de portais de notícias a fóruns de fãs.
- **Anti-duplicação:** histórico em `seen.json` e comparação de títulos parecidos, para a mesma notícia publicada por vários sites chegar uma vez só.
- **Envio resiliente:** separa erros passageiros (tenta de novo na próxima execução) de mensagens que o Telegram nunca aceitaria, e limita os envios por execução.
- **Monitoramento das fontes:** alerta no Telegram quando uma fonte fica 24h fora do ar.
- **Testes automatizados** com `unittest` e modo `--dry-run` para conferir sem enviar nada.
- **Dois modos de execução:** loop contínuo no Railway ou agendamento no GitHub Actions.

## Testar no computador

```bash
pip install -r requirements.txt
python -m unittest test_beatles_news
python beatles_news.py --dry-run
```

`--dry-run` mostra o que seria enviado das últimas 6 horas, sem enviar nem gravar nada.

Para enviar de verdade, copie `.env.example` para `.env` e preencha `TELEGRAM_BOT_TOKEN` e `TELEGRAM_CHAT_ID`.

## Onde rodar

Use só um dos dois: com ambos ligados no mesmo chat, cada notícia chega duas vezes.

### Railway (a cada 1 minuto)

1. Crie o projeto a partir do repositório. O `Procfile` já roda `python beatles_news.py --loop`.
2. Em Variables, defina `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` e `SEEN_FILE=/data/seen.json`.
3. Crie um volume montado em `/data`, para o histórico sobreviver aos reinícios.
4. Desative o workflow no GitHub (aba Actions).

### GitHub Actions (a cada 15 minutos ou mais)

Já configurado em `.github/workflows/beatles-news.yml`. Defina os Secrets `TELEGRAM_BOT_TOKEN` e `TELEGRAM_CHAT_ID`.

## 🛠 Tecnologias

Python · requests · feedparser · python-dotenv · Telegram Bot API · GitHub Actions · Railway

## 👤 Autor

**Andrew Matheus** — [LinkedIn](https://www.linkedin.com/in/andrewmbs/) · [GitHub](https://github.com/AndrewMBarros)
