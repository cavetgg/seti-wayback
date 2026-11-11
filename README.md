# Arquivamento diário da SETI no Wayback Machine

Três vezes por dia (03:23, 11:23 e 19:23, horário de Brasília), o GitHub roda o `arquivar.py`, que lê o `sitemap.xml` da SETI e salva no Wayback Machine:

1. **Home**: a cada execução, junto com os links dela (PDFs, anexos).
2. **Notícias**: na primeira execução, as 50 mais recentes. Depois, só as novas ou atualizadas desde a última execução, também com os links.
3. **Páginas principais** (tudo que não é notícia, ~175): as alteradas na hora; as demais em rodízio, 25 por execução (o ciclo completo leva uns 2 a 3 dias).

O acervo antigo de notícias fica de fora de propósito, para manter a rotina leve.

Arquivos de controle em `status/`:
- `noticias_vistas.txt`: notícias já tratadas (apague para recomeçar do zero).
- `paginas_principais.json`: data em que cada página principal foi enviada.
- `ultima_execucao.json` e `execucoes.csv`: resumo de cada execução.

## Configuração (uma vez só, uns 10 minutos)

### 1. Chaves do Internet Archive
1. Crie uma conta grátis em https://archive.org/account/signup (ou entre na sua).
2. Acesse https://archive.org/account/s3.php e gere as chaves.
3. Anote a **Access Key** e a **Secret Key**.

### 2. Repositório no GitHub
1. Em https://github.com/new crie um repositório **público** chamado `seti-wayback`.
   Público porque o Actions é ilimitado nesse caso; em repo privado a cota grátis (2.000 min/mês) não aguenta rodar todo dia. As chaves ficam em Secrets, que nunca aparecem publicamente.
2. Na página do repo, clique em **Add file > Upload files** e arraste todo o conteúdo desta pasta (inclusive a pasta `.github`). Se o navegador esconder a `.github`, crie o arquivo pelo botão **Add file > Create new file** com o nome `.github/workflows/arquivar.yml` e cole o conteúdo.
3. Vá em **Settings > Secrets and variables > Actions > New repository secret** e crie:
   - `IA_ACCESS` = sua Access Key
   - `IA_SECRET` = sua Secret Key

### 3. Primeiro teste
Vá em **Actions > Arquivar SETI no Wayback Machine > Run workflow**. Acompanhe o log; ao final aparece um resumo com quantas páginas foram enviadas, puladas e com erro.

## Ajustes (opcionais)

Variáveis de ambiente que dá para colocar no passo `env:` do workflow:

| Variável | Padrão | O que faz |
|---|---|---|
| `SKIP_IF_ARCHIVED_HOURS` | 4 | Não reenvia página salva há menos de X horas |
| `MAIN_PER_RUN` | 25 | Páginas principais do rodízio em cada execução |
| `FIRST_NEWS` | 50 | Notícias salvas na primeira execução |
| `INTERVAL_SECONDS` | 5 | Intervalo entre envios (o limite do Archive é ~15 por minuto) |
| `MAX_MINUTES` | 320 | Tempo máximo por execução |

Para incluir páginas que não estão no sitemap, coloque uma URL por linha em `urls_extras.txt`.

## Conferir o resultado

- Histórico de capturas: https://web.archive.org/web/*/seti.pr.gov.br*
- Registro de cada execução: pasta `status/` deste repositório.

O commit diário do registro também mantém o repositório "ativo", evitando que o GitHub desligue o agendamento após 60 dias sem movimento.
