# Deploy, scrapes e agendamento

## Modelo

```
Cloud Scheduler (weekly-<site>)
      │  POST /api/v1/jobs/trigger/<site>
      ▼
 API  mvp-backend   (Cloud Run, CPU só durante os pedidos)
      │  cria o scrape job e enfileira uma Cloud Task
      ▼
 Cloud Tasks  scrape-jobs ──► API /internal/workers/scrape ──► inicia a execução do
                                                                Cloud Run Job mvp-scrape-worker
                                                                (python -m app.worker --scrape-job-id <id>)
```

- A **API** responde ao plugin e ao Scheduler e despacha trabalho. Não raspa.
- O **Cloud Run Job** `mvp-scrape-worker` usa a mesma imagem (2 CPU, 4 GiB) e só é cobrado enquanto raspa.
- Com `JOB_DISPATCH_MODE=local` (desenvolvimento) o scrape corre dentro da API; em produção isso exige
  `--no-cpu-throttling`, porque o trabalho continua depois de o pedido que o lançou responder.

## Deploy

Merge em `master` → acionador do Cloud Build → `cloudbuild.yaml`:

1. **Test**: `pytest` com as versões de `constraints.txt`.
2. **Build**: Kaniko constrói a imagem com as **mesmas** versões e faz um teste de fumo (`import app.main`).
3. **Deploy**: `gcloud run services update mvp-backend` (2 CPU, 4 GiB, concorrência 3, máximo de 10 instâncias,
   mínimo de 1, CPU só nos pedidos, `--cpu-boost`).
4. **DeployWorker**: atualiza a imagem do Job `mvp-scrape-worker` (ignorado se ainda não existe).

O acionador tem de usar o ficheiro do repositório: Cloud Build → Acionadores → o do `mvp-backend` →
Configuração = "Ficheiro de configuração do Cloud Build" → `cloudbuild.yaml`, com `_AR_PROJECT_ID` definida.
Um acionador com configuração própria (imagem `cloud-sdk:slim`) não aplica estas opções.

## Pôr os scrapes no Cloud Run Job (uma vez)

```bash
python scripts/setup_scrape_worker.py             # simulação: mostra tudo, sem valores secretos
python scripts/setup_scrape_worker.py --apply     # fase 1: fila, contas, permissões, segredo e Job (tráfego intacto)
# testar (abaixo) e só depois:
python scripts/setup_scrape_worker.py --activate --apply    # a API passa a despachar para o Job
python scripts/setup_scrape_worker.py --rollback --apply    # voltar a scrapes dentro da API (+ CPU permanente)
```

A fase 1 copia a imagem, a conta de serviço, a instância Cloud SQL, as variáveis e os segredos do serviço para o Job.

Teste do fluxo completo (depois de `--activate`):

```bash
python scripts/rescrape_sites.py --apply --only t2mais1
```

`GET /api/v1/jobs/<id>/events` deve mostrar `dispatched: Job enqueued in Cloud Tasks` e depois `worker_started`;
`gcloud run jobs executions list --job mvp-scrape-worker --region europe-west1` mostra a execução. Em
caso de falha, a execução mostra os logs do worker.

## Agendamento semanal

Um job `weekly-<site>` por site (hora de Lisboa, madrugada de domingo e segunda). Horários em `SLOTS` em
`scripts/schedule_sites.py`.

```bash
python scripts/schedule_sites.py                 # simulação: cron e comando gcloud de cada site
python scripts/schedule_sites.py --apply         # PATCH do URL inicial/páginas + cria/atualiza os jobs
```

Um job que morreu (execução do Job interrompida) deixaria o site "em curso" para sempre; por isso criar ou
disparar um job liberta primeiro os jobs sem sinal de vida (`running` sem heartbeat há mais de 5 min, `pending`
há mais de 1 h).

Disparar um site à mão: `gcloud scheduler jobs run weekly-<site> --location europe-west1`.

## Versões das dependências

`constraints.txt` fixa as versões exatas e usa-se no CI e na imagem. Para atualizar de propósito:

```bash
docker run --rm -v "<repo>:/src:ro" python:3.13-slim sh -c "cp -r /src /tmp/s && cd /tmp/s && \
  pip install -q pip-tools && pip-compile -q --extra=dev --strip-extras --no-emit-index-url \
  --no-annotate -o /tmp/c.txt pyproject.toml && cat /tmp/c.txt"
```

Cola o resultado (mantendo o cabeçalho de comentários) em `constraints.txt`, corre `pytest`, faz commit.

## Verificar um deploy

```bash
gcloud run services describe mvp-backend --region europe-west1 --project rock-voice-352108 \
  --format="yaml(spec.template.metadata.annotations,spec.template.spec.containerConcurrency,status.latestReadyRevisionName)"
```

Esperado: `cpu-throttling: 'true'`, `maxScale: '10'`, `containerConcurrency: 3`.

## Custos (ordem de grandeza, preços de lista; confirmar em Billing)

| | CPU só nos pedidos + Job | CPU permanente na API |
|---|---|---|
| API (1 instância mínima) | ~40 USD/mês em repouso | ~100–150 USD/mês |
| Scrapes (~10 h/semana) | ~10 USD/mês (Job) | incluído acima |

## Problemas conhecidos

- **"Executar de novo" um build falhado reconstrói o commit antigo.** Para construir o último `master`, usa
  Acionadores → Executar → ramo `master`. Não uses `gcloud builds submit` (perde as substituições do acionador).
- O histórico do Cloud Build mostra builds por região: confirma o filtro (`global` / `europe-west1`).
- Uma revisão que falha a arrancar deixa a anterior a servir; os logs estão em
  `resource.labels.revision_name="<revisão>"`.
- Sintoma de scrape sem CPU (modo `local` com CPU limitada): jobs muito lentos e
  `content_ready_selector ... never appeared` nos logs.
- No PowerShell 5.1 as aspas dentro de argumentos de programas externos perdem-se: usa `\"` dentro de texto entre plicas.
