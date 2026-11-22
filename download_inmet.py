#!/usr/bin/env python3
"""Baixa os dados horários das estações automáticas do INMET nas capitais do Sul.

Fonte: Banco de Dados Históricos do INMET (portal.inmet.gov.br/dadoshistoricos),
um ZIP por ano com os CSVs de todas as ~600 estações automáticas (~95 MB/ano).

Para não baixar o ZIP inteiro, o script lê só o índice do ZIP e os 3 CSVs de
interesse com requisições HTTP Range (cerca de 0,8 MB por estação e ano).

Estações:
  A807  Curitiba (PR)
  A806  Florianópolis (SC)
  A801  Porto Alegre - Jardim Botânico (RS)

Usa apenas a biblioteca padrão. Os CSVs são salvos sem transformação em
<destino>/inmet/<ano>_<estacao>.csv. Arquivos já baixados são pulados.
"""

import argparse
import io
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

URL_ANO = "https://portal.inmet.gov.br/uploads/dadoshistoricos/{}.zip"
USER_AGENT = "utfpr-cidados-trab-final/1.0"
ESTACOES = {"A807": "curitiba", "A806": "florianopolis", "A801": "porto_alegre"}
PASTA = "inmet"


def log(msg):
    print(msg, flush=True)


def requisitar(url, metodo="GET", intervalo=None, tentativas=4, timeout=120):
    headers = {"User-Agent": USER_AGENT}
    if intervalo:
        headers["Range"] = "bytes={}-{}".format(*intervalo)
    req = urllib.request.Request(url, headers=headers, method=metodo)
    for i in range(tentativas):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if intervalo and resp.status != 206:
                    raise RuntimeError(f"servidor ignorou o Range em {url}")
                return resp.headers, resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 404 or i == tentativas - 1:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if i == tentativas - 1:
                raise
        time.sleep(2 ** i)


class ArquivoRemoto(io.RawIOBase):
    """Arquivo somente leitura sobre HTTP Range, para o zipfile ler só o necessário."""

    def __init__(self, url):
        self.url, self.pos = url, 0
        headers, _ = requisitar(url, metodo="HEAD")
        self.tamanho = int(headers["Content-Length"])

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, offset, whence=0):
        base = {0: 0, 1: self.pos, 2: self.tamanho}[whence]
        self.pos = base + offset
        return self.pos

    def readinto(self, buf):
        n = min(len(buf), self.tamanho - self.pos)
        if n <= 0:
            return 0
        _, dados = requisitar(self.url, intervalo=(self.pos, self.pos + n - 1))
        buf[: len(dados)] = dados
        self.pos += len(dados)
        return len(dados)


def baixar_ano(ano, destino, forcar):
    alvos = {cod: destino / f"{ano}_{nome}.csv" for cod, nome in ESTACOES.items()}
    faltando = {cod: p for cod, p in alvos.items() if forcar or not p.exists()}
    if not faltando:
        log(f"{ano}: já baixado")
        return
    # buffer grande: o zipfile faz muitas leituras pequenas no cabeçalho de cada membro
    remoto = io.BufferedReader(ArquivoRemoto(URL_ANO.format(ano)), buffer_size=1 << 20)
    with zipfile.ZipFile(remoto) as z:
        for cod, caminho in faltando.items():
            membros = [n for n in z.namelist() if f"_{cod}_" in n.upper()]
            if not membros:
                log(f"{ano}: estação {cod} ausente no ZIP")
                continue
            dados = z.read(membros[0])
            tmp = caminho.with_suffix(".part")
            tmp.write_bytes(dados)
            tmp.replace(caminho)
            log(f"{ano}: {caminho.name} ({len(dados) / 1e6:.1f} MB)")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("destino", type=Path, help="pasta base (ex.: dataset/)")
    p.add_argument("--inicio", type=int, default=2015)
    p.add_argument("--fim", type=int, default=2025)
    p.add_argument("--forcar", action="store_true", help="baixa de novo mesmo se já existir")
    args = p.parse_args()

    destino = args.destino / PASTA
    destino.mkdir(parents=True, exist_ok=True)
    erros = 0
    for ano in range(args.inicio, args.fim + 1):
        try:
            baixar_ano(ano, destino, args.forcar)
        except Exception as e:  # segue para o próximo ano e reporta no fim
            erros += 1
            log(f"{ano}: ERRO {e}")
    sys.exit(1 if erros else 0)


if __name__ == "__main__":
    main()
