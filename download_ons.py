#!/usr/bin/env python3
"""Baixa os dados brutos do Portal de Dados Abertos do ONS.

Datasets:
  - Curva de Carga Horária               (curva-carga)              arquivos anuais
  - Carga de Energia Diária              (carga-energia)            arquivos anuais
  - Demanda Máxima Diária por Subsistema (demanda_maxima_di)        arquivos anuais (desde 2024)
  - Carga de Energia Programada          (carga-energia-programada) API semi-horária (desde 05/03/2021)
  - Valores da Programação Diária        (programacao_diaria)       arquivos diários (desde 10/2024)
  - Carga de Energia Mensal              (carga-mensal)             arquivo único com todo o histórico

Usa apenas a biblioteca padrão. Os arquivos ficam em <destino>/<dataset>/, sem
nenhuma transformação. O período padrão é 2015–2025 (2026 está incompleto).
Arquivos já baixados são pulados, exceto os do ano corrente, que o ONS continua
atualizando.
"""

import argparse
import csv
import json
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path

CKAN_API = "https://dados.ons.org.br/api/3/action/package_show?id={}"
CARGA_API = "https://apicarga.ons.org.br/prd/cargaprogramada"
CARGA_PROGRAMADA_INICIO = date(2021, 3, 5)
USER_AGENT = "utfpr-cidados-trab-final/1.0"

# slug no portal -> pasta de destino
DATASETS_ARQUIVO = {
    "curva-carga": "curva_carga_horaria",
    "carga-energia": "carga_energia_diaria",
    "demanda_maxima_di": "demanda_maxima_diaria",
    "programacao_diaria": "programacao_diaria",
    "carga-mensal": "carga_energia_mensal",
}
PASTA_PROGRAMADA = "carga_energia_programada"

# CARGA_ENERGIA_2025.parquet, PROGRAMACAO_DIARIA_2024_10_01.csv, CARGA_MENSAL.csv
RE_DATA_ARQUIVO = re.compile(r"_(\d{4})(?:_(\d{2})_(\d{2}))?\.[a-z]+$", re.IGNORECASE)


def log(msg):
    print(msg, flush=True)


def baixar(url, tentativas=4, timeout=120):
    """Retorna o conteúdo da URL, com novas tentativas e espera crescente."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for i in range(tentativas):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 404 or i == tentativas - 1:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if i == tentativas - 1:
                raise
        time.sleep(2 ** i)


def salvar(caminho, conteudo):
    tmp = caminho.with_suffix(caminho.suffix + ".part")
    tmp.write_bytes(conteudo)
    tmp.replace(caminho)


def precisa_baixar(caminho, ano, forcar):
    """ano=None marca arquivos que nunca ficam prontos (ex.: histórico completo)."""
    return forcar or not caminho.exists() or ano is None or ano >= date.today().year


def listar_arquivos(slug, formato, ano_inicio, ano_fim):
    """Lista (url, nome, ano) dos recursos do dataset no formato pedido.

    Se uma data não tiver o formato pedido, usa CSV no lugar.
    """
    pacote = json.loads(baixar(CKAN_API.format(slug)))["result"]
    por_chave = {}  # data do arquivo (ou None) -> {formato: url}
    for r in pacote["resources"]:
        url = r.get("url", "")
        nome = url.rsplit("/", 1)[-1]
        fmt = nome.rsplit(".", 1)[-1].lower() if "." in nome else ""
        if fmt not in ("parquet", "csv", "xlsx") or nome.lower().startswith("dicionario"):
            continue
        m = RE_DATA_ARQUIVO.search(nome)
        chave = m.groups() if m else None
        por_chave.setdefault(chave, {})[fmt] = url

    arquivos = []
    for chave, urls in sorted(por_chave.items(), key=lambda kv: kv[0] or ()):
        ano = int(chave[0]) if chave else None
        if ano is not None and not ano_inicio <= ano <= ano_fim:
            continue
        if chave and chave[1]:
            ano = 0  # arquivo de um único dia: não muda depois de publicado
        url = urls.get(formato) or urls.get("csv")
        if url:
            arquivos.append((url, url.rsplit("/", 1)[-1], ano))
    return arquivos


def baixar_dataset_arquivos(slug, pasta, formato, ano_inicio, ano_fim, forcar, workers):
    pasta.mkdir(parents=True, exist_ok=True)
    arquivos = listar_arquivos(slug, formato, ano_inicio, ano_fim)
    pendentes = [(u, pasta / n, a) for u, n, a in arquivos
                 if precisa_baixar(pasta / n, a, forcar)]
    log(f"[{slug}] {len(arquivos)} arquivos no período, {len(pendentes)} a baixar")

    falhas = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futuros = {ex.submit(baixar, u): (u, c) for u, c, _ in pendentes}
        for i, f in enumerate(as_completed(futuros), 1):
            url, caminho = futuros[f]
            try:
                salvar(caminho, f.result())
                if len(pendentes) <= 30 or i % 50 == 0 or i == len(pendentes):
                    log(f"  [{i}/{len(pendentes)}] {caminho.name}")
            except Exception as e:
                falhas += 1
                log(f"  ERRO {url}: {e}")
    return falhas


def trimestres(inicio, fim):
    """Intervalos de até 3 meses (limite da API), um trimestre civil por vez."""
    atual = inicio
    while atual <= fim:
        mes_fim = ((atual.month - 1) // 3 + 1) * 3
        ultimo = (date(atual.year + mes_fim // 12, mes_fim % 12 + 1, 1) - timedelta(days=1))
        yield atual, min(ultimo, fim)
        atual = ultimo + timedelta(days=1)


def baixar_carga_programada(pasta, areas, ano_inicio, ano_fim, forcar):
    """Baixa a API de carga programada e grava um CSV por área e ano."""
    pasta.mkdir(parents=True, exist_ok=True)
    inicio = max(date(ano_inicio, 1, 1), CARGA_PROGRAMADA_INICIO)
    # a programação de amanhã já sai hoje
    fim = min(date(ano_fim, 12, 31), date.today() + timedelta(days=1))
    colunas = ["cod_areacarga", "dat_referencia", "din_referenciautc", "val_cargaglobalprogramada"]
    falhas = 0

    for area in areas:
        for ano in range(inicio.year, fim.year + 1):
            caminho = pasta / f"CARGA_PROGRAMADA_{area}_{ano}.csv"
            if not precisa_baixar(caminho, ano, forcar):
                log(f"[carga-energia-programada] {caminho.name} já existe")
                continue
            linhas = []
            ini_ano, fim_ano = max(inicio, date(ano, 1, 1)), min(fim, date(ano, 12, 31))
            for a, b in trimestres(ini_ano, fim_ano):
                url = f"{CARGA_API}?dat_inicio={a}&dat_fim={b}&cod_areacarga={area}"
                try:
                    linhas.extend(json.loads(baixar(url, timeout=300)))
                except urllib.error.HTTPError as e:
                    if e.code != 404:  # 404 = sem dados no intervalo
                        falhas += 1
                        log(f"  ERRO {url}: {e}")
                except Exception as e:
                    falhas += 1
                    log(f"  ERRO {url}: {e}")
            if not linhas:
                continue
            tmp = caminho.with_suffix(".csv.part")
            with tmp.open("w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=colunas, delimiter=";", extrasaction="ignore")
                w.writeheader()
                w.writerows(linhas)
            tmp.replace(caminho)
            log(f"[carga-energia-programada] {caminho.name}: {len(linhas)} registros")
    return falhas


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("destino", type=Path, help="pasta onde os dados serão gravados")
    p.add_argument("--inicio", type=int, default=2015, help="primeiro ano (padrão: 2015)")
    p.add_argument("--fim", type=int, default=2025, help="último ano, inclusive (padrão: 2025)")
    p.add_argument("--formato", choices=["parquet", "csv", "xlsx"], default="parquet",
                   help="formato dos arquivos do portal (padrão: parquet)")
    p.add_argument("--areas", default="S,PR,SC,RS",
                   help="áreas de carga da API de carga programada (padrão: S,PR,SC,RS)")
    p.add_argument("--workers", type=int, default=8, help="downloads em paralelo")
    p.add_argument("--forcar", action="store_true", help="baixa de novo arquivos já existentes")
    args = p.parse_args()

    ano_inicio, ano_fim = args.inicio, args.fim
    if ano_inicio > ano_fim:
        p.error("--inicio deve ser menor ou igual a --fim")
    destino = args.destino.expanduser().resolve()
    log(f"Destino: {destino}\nPeríodo: {ano_inicio} a {ano_fim}")

    falhas = 0
    for slug, pasta in DATASETS_ARQUIVO.items():
        try:
            falhas += baixar_dataset_arquivos(slug, destino / pasta, args.formato,
                                              ano_inicio, ano_fim, args.forcar, args.workers)
        except Exception as e:
            falhas += 1
            log(f"[{slug}] ERRO ao listar recursos: {e}")
    areas = [a.strip().upper() for a in args.areas.split(",") if a.strip()]
    falhas += baixar_carga_programada(destino / PASTA_PROGRAMADA, areas, ano_inicio, ano_fim, args.forcar)

    log(f"\nConcluído com {falhas} falha(s)." if falhas else "\nConcluído sem falhas.")
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())
