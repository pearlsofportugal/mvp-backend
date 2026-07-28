# Workers escaláveis

O mesmo contentor suporta dois processos:

- API: comando por defeito (`uvicorn app.main:app`).
- Worker de scraping: `python -m app.worker --scrape-job-id <UUID>`.

## Configuração

Em desenvolvimento, mantenha `JOB_DISPATCH_MODE=local`. A API usa uma tarefa
local apenas como fallback de desenvolvimento.

Em produção, defina `JOB_DISPATCH_MODE=cloud_tasks` e configure:

```text
CLOUD_TASKS_PROJECT=<project-id>
CLOUD_TASKS_LOCATION=europe-west1
CLOUD_TASKS_QUEUE=scrape-jobs
WORKER_DISPATCH_URL=https://<api-interna>
WORKER_DISPATCH_TOKEN=<secret-do-secret-manager>
WORKER_DISPATCH_SERVICE_ACCOUNT=<tasks-invoker@project.iam.gserviceaccount.com>
CLOUD_RUN_SCRAPE_JOB_NAME=mvp-scrape-worker
```

O endpoint interno inicia uma execução do Cloud Run Job e passa o UUID como
argumento. O serviço API deve usar ingress interno e o service account de
Cloud Tasks precisa de `run.invoker`; a identidade que chama a API Cloud Run
precisa de `run.developer` para iniciar execuções.

## Operação

Executar a migração antes do deploy:

```bash
alembic upgrade head
```

Os eventos de execução estão disponíveis em
`GET /api/v1/jobs/{job_id}/events`. Os campos `attempt_count`,
`dispatch_attempts` e `execution_id` permitem auditar novas tentativas e a
execução Cloud Run associada.
