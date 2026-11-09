#!/usr/bin/env python3
"""
Arquiva o site completo da SETI/PR no Wayback Machine (Internet Archive).

Fluxo:
  1. Lê o sitemap.xml do site (índice + todas as páginas filhas).
  2. Monta a fila: home e páginas alteradas recentemente primeiro
     (essas também capturam os links que contêm, como PDFs de editais),
     depois todo o resto em ordem aleatória.
  3. Envia cada URL para o Save Page Now 2 (SPN2), respeitando o limite
     de requisições, e pula o que já foi arquivado nas últimas horas.
  4. Grava um registro em status/ para conferência.

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

# Não reenvia uma página arquivada há menos de X horas (evita duplicar)
SKIP_IF_ARCHIVED_HOURS = float(os.environ.get("SKIP_IF_ARCHIVED_HOURS", "20"))
# Páginas alteradas nos últimos N dias também salvam os links delas (PDFs etc.)
OUTLINKS_DAYS = float(os.environ.get("OUTLINKS_DAYS", "3"))
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


def spn_save(url, outlinks):
    data = {
        "url": url,
        "capture_all": "1",
        "if_not_archived_within": f"{int(SKIP_IF_ARCHIVED_HOURS * 3600)}",
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
                wait = 60 * (attempt + 1)
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
            raise StopRun("limite diário de capturas do Internet Archive atingido")
        if j.get("job_id"):
            return "enviado", j["job_id"]
        if "same snapshot" in msg.lower() or "already" in msg.lower():
            return "pulado", msg[:120]
        return "erro", (ext or msg or txt)[:160]
    return "erro", "esgotou tentativas"


# ---------------------------------------------------------------- main
def main():
    if not IA_ACCESS or not IA_SECRET:
        sys.exit("Faltam as chaves IA_ACCESS e IA_SECRET (secrets do repositório).")

    log(f"lendo sitemap: {SITEMAP_URL}")
    pages = read_sitemap(SITEMAP_URL)
    for u in read_extras():
        pages.setdefault(u, None)
    pages.setdefault(HOME_URL, None)
    log(f"{len(pages)} URLs no total")

    now = dt.datetime.now(dt.timezone.utc)
    recent_cut = now - dt.timedelta(days=OUTLINKS_DAYS)
    priority = [HOME_URL] + sorted(
        (u for u, lm in pages.items() if lm and lm >= recent_cut and u != HOME_URL),
        key=lambda u: pages[u],
        reverse=True,
    )
    prio_set = set(priority)
    rest = [u for u in pages if u not in prio_set]
    random.shuffle(rest)  # se o tempo acabar, cada dia cobre uma parte diferente
    queue = [(u, True) for u in priority] + [(u, False) for u in rest]
    log(f"{len(priority)} prioritárias (home + alteradas em {OUTLINKS_DAYS:g} dias), {len(rest)} restantes")

    counts = {"enviado": 0, "pulado": 0, "erro": 0}
    errors = []
    stopped = None
    done = 0
    for url, outlinks in queue:
        if elapsed_min() > MAX_MINUTES:
            stopped = f"tempo máximo ({MAX_MINUTES:g} min) atingido"
            break
        t0 = time.monotonic()
        try:
            status, info = spn_save(url, outlinks)
        except StopRun as e:
            stopped = str(e)
            break
        counts[status] += 1
        done += 1
        if status == "erro":
            errors.append({"url": url, "erro": info})
        log(f"{done}/{len(queue)} {status:8} {url}" + (" (+links)" if outlinks else ""))
        time.sleep(max(0.0, INTERVAL - (time.monotonic() - t0)))

    resumo = {
        "data_utc": now.isoformat(timespec="seconds"),
        "total_urls": len(queue),
        "processadas": done,
        **counts,
        "minutos": round(elapsed_min(), 1),
        "interrompido": stopped,
        "erros": errors[:200],
    }
    os.makedirs(STATUS_DIR, exist_ok=True)
    with open(os.path.join(STATUS_DIR, "ultima_execucao.json"), "w", encoding="utf-8") as f:
        json.dump(resumo, f, ensure_ascii=False, indent=2)
    hist = os.path.join(STATUS_DIR, "historico.csv")
    new = not os.path.exists(hist)
    with open(hist, "a", encoding="utf-8") as f:
        if new:
            f.write("data_utc,total_urls,processadas,enviadas,puladas,erros,minutos,interrompido\n")
        f.write(
            f"{resumo['data_utc']},{len(queue)},{done},{counts['enviado']},{counts['pulado']},"
            f"{counts['erro']},{resumo['minutos']},{(stopped or '').replace(',', ';')}\n"
        )

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("## Arquivamento SETI no Wayback\n\n")
            f.write("| URLs | processadas | enviadas | puladas (já salvas) | erros | minutos |\n")
            f.write("|---|---|---|---|---|---|\n")
            f.write(f"| {len(queue)} | {done} | {counts['enviado']} | {counts['pulado']} | {counts['erro']} | {resumo['minutos']} |\n")
            if stopped:
                f.write(f"\nInterrompido: {stopped}\n")

    log(f"fim: {counts} em {resumo['minutos']} min" + (f" | parou: {stopped}" if stopped else ""))
    if stopped and stopped.startswith("credenciais"):
        sys.exit(1)


if __name__ == "__main__":
    main()
