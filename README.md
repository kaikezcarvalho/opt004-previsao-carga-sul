# Previsão da carga máxima diária do subsistema Sul

Projeto final de Ciência de Dados, UTFPR.

## Reprodução

Requer Python 3.10 ou mais novo e acesso à internet.

```bash
# Ambiente
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt        # Windows: .venv\Scripts\pip

# Dados brutos do ONS, 2015–2025 (~150 MB, 4–5 min)
.venv/bin/python download_ons.py dataset/

# Preparação e integração: baixa clima na primeira execução e gera processado/*.parquet
.venv/bin/jupyter nbconvert --execute --to notebook --inplace 02_preparacao.ipynb
#    ou abrir o notebook com: .venv/bin/jupyter lab
```

## Estrutura

| Caminho | Conteúdo |
|---|---|
| `download_ons.py` | Download dos dados brutos do Portal de Dados Abertos do ONS (`--help` mostra as opções) |
| `02_preparacao.ipynb` | Etapa 2: limpeza, clima, feriados, agregações, validação e exportação |
| `dataset/` | Dados brutos, fora do Git: arquivos do ONS e `clima_open_meteo/` (cache da API) |
| `processado/` | Bases finais em Parquet |

### Bases geradas em `processado/`

| Arquivo | Chave | Linhas |
|---|---|---|
| `base_horaria.parquet` | `instante` (hora local) | 96.432 |
| `base_diaria.parquet` | `data` | 4.018 |
| `base_semanal.parquet` | `semana_inicio` (segunda-feira, semana ISO) | 575 |
| `base_mensal.parquet` | `mes` (1º dia) | 132 |
| `benchmark_ons.parquet` | `data` | 1.762 (desde 05/03/2021) |

## Fontes

- **ONS:** Curva de Carga Horária (fonte principal), Carga de Energia Diária e Mensal, Demanda Máxima Diária, Carga de Energia Programada e Valores da Programação Diária.
- **Clima:** Open-Meteo, API histórica (`archive-api.open-meteo.com`). Curitiba, Florianópolis e Porto Alegre, ponderadas pela população de cada estado (IBGE, Censo 2022).
- **Feriados:** biblioteca `holidays`, com os feriados nacionais e os estaduais de PR, SC e RS.

## Decisões técnicas da preparação

- **Período e alvo:** 2015–2025 (2026 está incompleto). O alvo é `carga_max`, o maior dos 24 valores horários do dia, em MWmed.
- **Relógio:** o ONS registra a **hora local de Brasília, com horário de verão**. Nas trocas de horário de 2015 a 2019, o perfil de carga não se desloca, e a hora 00:00 dos dias de início do horário de verão vem vazia ou zerada. Essa hora é marcada `hora_inexistente_dst` e não é imputada. O clima, baixado em UTC, é convertido para `America/Sao_Paulo`.
- **Qualidade da série do Sul:**
  - não há duplicados nem horas ausentes da grade;
  - o dia **09/04/2015** está inteiro vazio no arquivo do ONS. Ele fica com `carga_max` NaN e `dia_incompleto = True`, sem imputação;
  - lacunas de até 3 horas seriam interpoladas, mas não ocorreram.
- **Outliers:** são mantidos. Variações hora a hora extremas só recebem a flag `suspeito`.
- **Feriados:** Carnaval (segunda e terça) e Corpus Christi contam como `feriado_nacional`. O Dia da Consciência Negra é nacional a partir de 2024. Na biblioteca, o PR não tem feriado estadual no período.
- **Regimes do ONS:** `regime_ons` vale 0 até 28/02/2021, 1 de 01/03/2021 a 28/04/2023 e 2 a partir de 29/04/2023 (inclusão da MMGD estimada).
- **`pandemia`:** de 16/03/2020 a 31/12/2020. O intervalo é configurável no topo do notebook.
- **Validação:**
  - a média diária calculada coincide com a Carga de Energia Diária do ONS;
  - a média mensal fica de 1% a 5% abaixo da Carga de Energia Mensal;
  - `carga_max` fica perto da demanda integralizada da Demanda Máxima Diária (diferença mediana de −0,2%) e cerca de 1,4% abaixo do pico instantâneo.
- **Valores da Programação Diária:** traz a geração programada por usina, e não a carga. Por isso não foi usado no benchmark.
