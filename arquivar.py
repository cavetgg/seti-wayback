#!/usr/bin/env python3
"""
Arquiva o site completo da SETI/PR no Wayback Machine (Internet Archive).

Fluxo:
  1. Lê o sitemap.xml do site (índice + todas as páginas filhas).
  2. Monta a fila do dia:
     - home (todo dia, com os links dela, como PDFs);
     - notícias novas ou atualizadas desde a última execução (com links);
       na primeira execução, só as FIRST_NEWS mais recentes;
     - páginas principais (tudo que não é notícia): as alteradas todo dia,
       as demais em rodízio, MAIN_PER_RUN por execução (as salvas há mais tempo).
  3. Envia cada URL para o Save Page Now 2 (SPN2), respeitando o limite.
  4. Grava em status/ o registro da execução e a lista de notícias já vistas.

Só usa a biblioteca padrão do Python.
"""
import datetime as dt
import json
import os
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

# ---------------------------------------------------------------- config
SITEMAP_URL = os.environ.get("SITEMAP_URL", "https://www.seti.pr.gov.br/sitemap.xml")
HOME_URL = os.environ.get("HOME_URL", "https://www.seti.pr.gov.br/")
SPN_ENDPOINT = os.environ.get("SPN_ENDPOINT", "https://web.archive.org/save")
IA_ACCESS = os.environ.get("IA_ACCESS", "").strip()
IA_SECRET = os.environ.get("IA_SECRET", "").strip()

# Home, notícias novas e páginas alteradas: não reenvia se salva há menos de X horas
SKIP_IF_ARCHIVED_HOURS = float(os.environ.get("SKIP_IF_ARCHIVED_HOURS", "4"))
# Páginas principais sem mudança: quantas entram no rodízio a cada execução
MAIN_PER_RUN = int(os.environ.get("MAIN_PER_RUN", "25"))
# Na primeira execução, quantas notícias recentes salvar
FIRST_NEWS = int(os.environ.get("FIRST_NEWS", "50"))
NEWS_MARK = os.environ.get("NEWS_MARK", "/Noticia/")
# Intervalo entre envios. O SPN aceita ~15 por minuto; 5 s fica abaixo disso.
INTERVAL = float(os.environ.get("INTERVAL_SECONDS", "5"))
# Tempo máximo da execução (o GitHub mata o job em 6 h)
MAX_MINUTES = float(os.environ.get("MAX_MINUTES", "320"))
EXTRA_FILE = os.environ.get("EXTRA_FILE", "urls_extras.txt")
STATUS_DIR = os.environ.get("STATUS_DIR", "status")

UA = "SETI-PR-Wayback-Archiver/1.0 (+GitHub Actions)"
START = time.monotonic()


def log(msg):
    print(f"[{dt.datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def elapsed_min():
    return (time.monotonic() - START) / 60


# ---------------------------------------------------------------- sitemap
def fetch(url, tries=4):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001
            last = e
            log(f"  falha ao baixar {url} ({e}); tentativa {i + 1}/{tries}")
            time.sleep(10 * (i + 1))
    raise RuntimeError(f"não consegui baixar {url}: {last}")


def local(tag):
    return tag.rsplit("}", 1)[-1]


def parse_lastmod(s):
    if not s:
        return None
    try:
        d = dt.datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def read_sitemap(url, seen=None):
    """Devolve dict {url: lastmod|None}, seguindo índices recursivamente."""
    seen = seen if seen is not None else set()
    if url in seen:
        return {}
    seen.add(url)
    root = ET.fromstring(fetch(url))
    out = {}
    if local(root.tag) == "sitemapindex":
        for sm in root:
            loc = next((c.text for c in sm if local(c.tag) == "loc"), None)
            if loc:
                log(f"sitemap filho: {loc.strip()}")
                out.update(read_sitemap(loc.strip(), seen))
    else:
        for u in root:
            if local(u.tag) != "url":
                continue
            loc = lastmod = None
            for c in u:
                if local(c.tag) == "loc":
                    loc = (c.text or "").strip()
                elif local(c.tag) == "lastmod":
                    lastmod = parse_lastmod(c.text)
            if loc:
                out[loc] = lastmod
    return out


def read_extras():
    if not os.path.exists(EXTRA_FILE):
        return []
    with open(EXTRA_FILE, encoding="utf-8") as f:
        return [l.strip() for l in f if l.strip() and not l.lstrip().startswith("#")]


# ---------------------------------------------------------------- SPN2
class StopRun(Exception):
    pass


def spn_save(url, outlinks, skip_hours):
    data = {
        "url": url,
        "capture_all": "1",
        "if_not_archived_within": f"{int(skip_hours * 3600)}",
        "skip_first_archive": "1",
    }
    if outlinks:
        data["capture_outlinks"] = "1"
    body = urllib.parse.urlencode(data).encode()
    headers = {
        "Accept": "application/json",
        "Authorization": f"LOW {IA_ACCESS}:{IA_SECRET}",
        "User-Agent": UA,
        "Content-Type": "application/x-www-form-urlencoded",
    }
    for attempt in range(5):
        req = urllib.request.Request(SPN_ENDPOINT, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                txt = r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = 30 * (attempt + 1)
                log(f"  429 (muitas requisições); esperando {wait}s")
                time.sleep(wait)
                continue
            if e.code in (401, 403):
                raise StopRun(f"credenciais recusadas (HTTP {e.code}). Confira IA_ACCESS/IA_SECRET.")
            if e.code >= 500:
                time.sleep(30 * (attempt + 1))
                continue
            return "erro", f"HTTP {e.code}"
        except Exception as e:  # noqa: BLE001
            time.sleep(20 * (attempt + 1))
            continue

        try:
            j = json.loads(txt)
        except json.JSONDecodeError:
            return "erro", f"resposta não-JSON: {txt[:120]!r}"

        ext = j.get("status_ext", "") or ""
        msg = j.get("message", "") or ""
        if ext in ("error:user-session-limit", "error:too-many-requests"):
            # muitas capturas em andamento ao mesmo tempo: espera e tenta de novo
            wait = 45 * (attempt + 1)
            log(f"  {ext}; esperando {wait}s")
            time.sleep(wait)
            continue
        if ext == "error:too-many-daily-captures":
            # limite por URL: a página já foi capturada muitas vezes hoje
            return "pulado", "limite diário desta URL"
        if ext in ("error:user-daily-limit", "error:daily-limit"):
            raise StopRun(f"limite diário da conta no Internet Archive ({msg[:100]})")
        if j.get("job_id"):
            return "enviado", j["job_id"]
        if "same snapshot" in msg.lower() or "already" in msg.lower():
            return "pulado", msg[:120]
        return "erro", (ext or msg or txt)[:160]
    return "erro", "esgotou tentativas"


# ---------------------------------------------------------------- estado
SEEN_FILE = os.path.join(STATUS_DIR, "noticias_vistas.txt")
LAST_FILE = os.path.join(STATUS_DIR, "ultima_execucao.json")
MAIN_FILE = os.path.join(STATUS_DIR, "paginas_principais.json")


def load_main_state():
    try:
        with open(MAIN_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {}


def load_seen():
    if not os.path.exists(SEEN_FILE):
        return None
    with open(SEEN_FILE, encoding="utf-8") as f:
        return {l.strip() for l in f if l.strip()}


def save_seen(seen):
    os.makedirs(STATUS_DIR, exist_ok=True)
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(sorted(seen)) + "\n")


def last_run_time():
    try:
        with open(LAST_FILE, encoding="utf-8") as f:
            j = json.load(f)
        return dt.datetime.fromisoformat(j["data_utc"])
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------- main
def main():
    if not IA_ACCESS or not IA_SECRET:
        sys.exit("Faltam as chaves IA_ACCESS e IA_SECRET (secrets do repositório).")

    log(f"lendo sitemap: {SITEMAP_URL}")
    pages = read_sitemap(SITEMAP_URL)
    for u in read_extras():
        pages.setdefault(u, None)
    pages.setdefault(HOME_URL, None)

    now = dt.datetime.now(dt.timezone.utc)
    last = last_run_time() or (now - dt.timedelta(days=1))
    seen = load_seen()
    first_run = seen is None

    news = sorted(
        (u for u in pages if NEWS_MARK in u),
        key=lambda u: pages[u] or dt.datetime.min.replace(tzinfo=dt.timezone.utc),
        reverse=True,
    )
    main_pages = [u for u in pages if NEWS_MARK not in u and u != HOME_URL]
    log(f"{len(pages)} URLs no sitemap: {len(news)} notícias, {len(main_pages) + 1} páginas principais")

    if first_run:
        news_todo = news[:FIRST_NEWS]
        seen = set(news[FIRST_NEWS:])  # acervo antigo fica de fora
        log(f"primeira execução: salvando as {len(news_todo)} notícias mais recentes")
    else:
        news_todo = [u for u in news if u not in seen or (pages[u] and pages[u] > last)]
        log(f"{len(news_todo)} notícias novas ou atualizadas desde {last:%d/%m %H:%M} UTC")

    changed = [u for u in main_pages if pages[u] and pages[u] > last]
    changed_set = set(changed)
    # rodízio: a cada execução, as MAIN_PER_RUN salvas há mais tempo
    main_state = load_main_state()
    others = sorted(
        (u for u in main_pages if u not in changed_set),
        key=lambda u: main_state.get(u, ""),
    )[:MAIN_PER_RUN]
    log(f"páginas principais: {len(changed)} alteradas + {len(others)} do rodízio ({len(main_pages)} no total)")

    day = SKIP_IF_ARCHIVED_HOURS
    queue = (
        [(HOME_URL, True, day)]
        + [(u, True, day) for u in news_todo]
        + [(u, False, day) for u in changed]
        + [(u, False, day) for u in others]
    )
    main_pages_set = set(main_pages)
    log(f"fila desta execução: {len(queue)} URLs")

    counts = {"enviado": 0, "pulado": 0, "erro": 0}
    errors = []
    stopped = None
    done = 0
    daily_hits = 0
    for url, outlinks, skip_hours in queue:
        if elapsed_min() > MAX_MINUTES:
            stopped = f"tempo máximo ({MAX_MINUTES:g} min) atingido"
            break
        t0 = time.monotonic()
        try:
            status, info = spn_save(url, outlinks, skip_hours)
        except StopRun as e:
            stopped = str(e)
            break
        counts[status] += 1
        done += 1
        if NEWS_MARK in url and status != "erro":
            seen.add(url)
        elif status != "erro" and url in main_pages_set:
            main_state[url] = now.isoformat(timespec="seconds")
        if info == "limite diário desta URL":
            daily_hits += 1
            if daily_hits >= 10:
                stopped = "10 seguidas com limite diário; provável limite da conta, retomo amanhã"
                break
        else:
            daily_hits = 0
        if status == "erro":
            errors.append({"url": url, "erro": info})
        log(f"{done}/{len(queue)} {status:8} {url}" + (f" [{info}]" if status != "enviado" else "") + (" (+links)" if outlinks else ""))
        time.sleep(max(0.0, INTERVAL - (time.monotonic() - t0)))

    save_seen(seen)
    with open(MAIN_FILE, "w", encoding="utf-8") as f:
        json.dump(main_state, f, ensure_ascii=False, indent=0, sort_keys=True)
    resumo = {
        "data_utc": now.isoformat(timespec="seconds"),
        "fila": len(queue),
        "noticias_na_fila": len(news_todo),
        "processadas": done,
        **counts,
        "minutos": round(elapsed_min(), 1),
        "interrompido": stopped,
        "erros": errors[:200],
    }
    with open(LAST_FILE, "w", encoding="utf-8") as f:
        json.dump(resumo, f, ensure_ascii=False, indent=2)
    hist = os.path.join(STATUS_DIR, "execucoes.csv")
    new = not os.path.exists(hist)
    with open(hist, "a", encoding="utf-8") as f:
        if new:
            f.write("data_utc,fila,noticias,processadas,enviadas,puladas,erros,minutos,interrompido\n")
        f.write(
            f"{resumo['data_utc']},{len(queue)},{len(news_todo)},{done},{counts['enviado']},{counts['pulado']},"
            f"{counts['erro']},{resumo['minutos']},{(stopped or '').replace(',', ';')}\n"
        )

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("## Arquivamento SETI no Wayback\n\n")
            f.write("| fila | notícias | processadas | enviadas | puladas (já salvas) | erros | minutos |\n")
            f.write("|---|---|---|---|---|---|---|\n")
            f.write(f"| {len(queue)} | {len(news_todo)} | {done} | {counts['enviado']} | {counts['pulado']} | {counts['erro']} | {resumo['minutos']} |\n")
            if stopped:
                f.write(f"\nInterrompido: {stopped}\n")

    log(f"fim: {counts} em {resumo['minutos']} min" + (f" | parou: {stopped}" if stopped else ""))
    if stopped and stopped.startswith("credenciais"):
        sys.exit(1)


if __name__ == "__main__":
    main()
