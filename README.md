# Arquivamento diário da SETI no Wayback Machine

Todo dia, às 03:23 (Brasília), o GitHub roda o `arquivar.py`, que:

1. Lê o `sitemap.xml` da SETI (hoje são 7 páginas de sitemap, com todas as notícias e páginas do site).
2. Envia primeiro a home e as páginas alteradas nos últimos 3 dias, pedindo para salvar também os links delas (PDFs de editais, anexos etc.).
3. Envia todo o resto do site, em ordem aleatória.
4. Pula o que já foi salvo nas últimas 20 horas, para não duplicar.
5. Grava um registro em `status/ultima_execucao.json` e `status/historico.csv`.

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
| `SKIP_IF_ARCHIVED_HOURS` | 20 | Não reenvia página salva há menos de X horas |
| `OUTLINKS_DAYS` | 3 | Páginas alteradas nesse período também salvam os links (PDFs) |
| `INTERVAL_SECONDS` | 5 | Intervalo entre envios (o limite do Archive é ~15 por minuto) |
| `MAX_MINUTES` | 320 | Tempo máximo por execução |

Se o histórico mostrar muitos erros ou o aviso de limite diário, aumente `SKIP_IF_ARCHIVED_HOURS` (por exemplo para 168, uma semana): as novidades continuam sendo salvas todo dia e o acervo antigo passa a ser revisitado semanalmente.

Para incluir páginas que não estão no sitemap, coloque uma URL por linha em `urls_extras.txt`.

## Conferir o resultado

- Histórico de capturas: https://web.archive.org/web/*/seti.pr.gov.br*
- Registro de cada execução: pasta `status/` deste repositório.

O commit diário do registro também mantém o repositório "ativo", evitando que o GitHub desligue o agendamento após 60 dias sem movimento.
