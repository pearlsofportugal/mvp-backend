# Deploy e agendamento

## Como o deploy funciona

Merge em `master` → acionador do Cloud Build → `cloudbuild.yaml`:

1. **Test**: `pytest` com as versões de `constraints.txt`.
2. **Build**: Kaniko constrói a imagem com as **mesmas** versões (`constraints.txt` é copiado para o
   `Dockerfile`) e faz um teste de fumo (`import app.main`). Se a app não importar, o build falha aqui.
3. **Deploy**: `gcloud run services update mvp-backend` com as opções do ficheiro:
   2 CPU, 4 GiB, concorrência 3, máximo de 10 instâncias, mínimo de 1, **CPU sempre alocada**.

O acionador tem de usar o ficheiro do repositório: Cloud Build → Acionadores → o do `mvp-backend` →
Configuração = "Ficheiro de configuração do Cloud Build" → `cloudbuild.yaml`, com a substituição
`_AR_PROJECT_ID` definida. Um acionador com configuração própria (imagem `cloud-sdk:slim`) não aplica estas
opções e o serviço volta a CPU limitada, concorrência 10 e máximo de 3 instâncias.

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

Esperado: `cpu-throttling: 'false'`, `maxScale: '10'`, `containerConcurrency: 3`.
Sintoma de CPU limitada: jobs em segundo plano muito lentos e `content_ready_selector ... never appeared`
nos logs.

## Problemas conhecidos

- **"Executar de novo" um build falhado reconstrói o commit antigo.** Para construir o último `master`, usa
  Acionadores → Executar → ramo `master`. Não uses `gcloud builds submit` (perde as substituições do acionador).
- O histórico do Cloud Build mostra builds por região: confirma o filtro (`global` / `europe-west1`).
- Uma revisão que falha a arrancar deixa a anterior a servir; os logs estão em
  `resource.labels.revision_name="<revisão>"`.

## Agendamento semanal

Cada site tem um job `scrape-<site>` no Cloud Scheduler (chama `POST /api/v1/jobs/trigger/<site>`),
escalonado ao fim de semana de madrugada (hora de Lisboa). Define-se com:

```bash
python scripts/schedule_sites.py            # simulação: mostra os crons
python scripts/schedule_sites.py --apply    # grava (precisa de MVP_API_URL e MVP_API_KEY)
```

Os horários estão em `SLOTS` nesse script. Requisitos no serviço: `GOOGLE_CLOUD_PROJECT`, `BACKEND_URL` e
permissão para gerir o Cloud Scheduler. Sem isso o `PATCH` ao site responde 200 mas não cria o job; o script
avisa quando não existe próxima execução. O endpoint de trigger responde logo e o scrape continua em segundo
plano no próprio serviço, por isso a CPU permanente é obrigatória.

Para correr um site à mão: `gcloud scheduler jobs run scrape-<site> --location europe-west1`.
