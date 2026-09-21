---
name: onboarding-partner
description: Gera a configuração SiteConfig (JSON) e o normalizer Python necessários para integrar um novo site imobiliário na pipeline de scraping do mvp-backend, a partir do HTML real das páginas de listagem e de detalhe. Usar quando o utilizador quiser adicionar/integrar um novo partner de scraping, colar HTML de um site imobiliário para gerar selectors, ou pedir "criar site config" / "onboard novo site".

disable-model-invocation: true
---

# Onboarding de um novo site imobiliário

Este skill produz DUAS coisas, não apenas uma — isto é o erro mais comum ao usar
a versão anterior deste prompt:

1. Um JSON `SiteConfig` pronto para `POST /api/v1/sites`.
2. Uma função `@partner_normalizer("$site_key")` em `app/services/mapper_service.py`.

**Sem o (2), o scrape falha em runtime** com `ValueError: No normalizer
registered for partner: '$site_key'` — `normalize_partner_payload()` faz
dispatch por `site_key` contra o dicionário `_PARTNER_NORMALIZERS`, e um
`SiteConfig` novo não regista lá nada sozinho. Isto não está documentado em
lado nenhum fora do próprio código, por isso não saltes este passo.

Consulta sempre [reference.md](reference.md) para a lista completa e
actualizada de nomes de selector, das normalizações automáticas, e do
catálogo de normalizers já registados. O código (`app/services/parser_service.py`,
`app/services/mapper_service.py`, `app/schemas/site_config_schema.py`) é
sempre a fonte de verdade — se o comportamento aqui descrito e o código
divergirem, confia no código e avisa o utilizador.

## Inputs

- `$base_url`, `$site_key`, `$site_name`, `$pagination_type`
  (`html_next` | `query_param` | `incremental_path` | `sitemap`)
- HTML da página de listagem (cards + paginação) e HTML da página de detalhe
  de um anúncio individual. Se não vierem na mesma mensagem, pede-os antes de
  continuar — nunca inventes selectors a partir de suposições.

## Processo

1. **Modo de extracção.** No HTML de detalhe: campos com elemento dedicado
   (classe/id/atributo único) → `direct`. Campos como pares nome/valor numa
   lista/tabela → `section`. Os dois modos podem coexistir — ver
   [reference.md § Combinar modos](reference.md#combinar-direct-e-section).
   Justifica a escolha em 1 frase.

2. **(Recomendado) Usa a API de sugestão já existente antes de analisares
   à mão.** Se o backend estiver acessível, chama
   `POST /api/v1/sites/preview/selector-suggestions` com o URL da página de
   detalhe — devolve candidatos ranqueados por JSON-LD/heurística
   (`app/crawler/selector_suggester.py`). Usa isso como rascunho e só refina
   manualmente os campos com confiança baixa ou vazios. Se o backend não
   estiver acessível, analisa o HTML directamente.

3. **Padrão de plataforma.** Antes de escreveres selectors do zero, verifica
   se o HTML corresponde a uma plataforma já suportada (ver
   [reference.md § Plataformas conhecidas](reference.md#plataformas-conhecidas)):
   EGO RealEstate (`.detailItem`, `.label`/`.value`) ou o layout de ícones da
   Habinédita (`[id*='modulodadosicones']`). Se corresponder, o normalizer é
   quase gratuito — ver passo 6.

4. **Análise da página de listagem.** `listing_link_selector`, `next_page_selector`
   (se `html_next`), `link_pattern` (regex a partir dos URLs reais de
   anúncios), padrão de URLs de imagens.

5. **Análise da página de detalhe.** Percorre a tabela de campos em
   [reference.md](reference.md) e atribui um selector (ou `null`) a cada um.
   Antes de exigires selector para title/price/area/land_area/property_type/
   typology/condition/business_type/district/county/parish, confirma se a
   página já tem `<script type="application/ld+json">` ou meta `og:` — nesse
   caso há fallback automático e o selector passa a opcional.

6. **Normalizer.** Escreve a função `@partner_normalizer("$site_key")` em
   `app/services/mapper_service.py`:
   - Se é plataforma EGO → 3 linhas, reaproveita `normalize_ego_platform_payload`
     (ver exemplos: `normalize_t2mais1_payload`, `normalize_escolhacerta_payload`).
   - Caso contrário → função dedicada usando `_build_base_schema`, seguindo o
     padrão de `normalize_pearls_payload` / `normalize_realkey_payload`.
   Propõe a edição ao ficheiro real (via diff) em vez de só mostrares o
   código — pede confirmação antes de aplicar.

7. **Monta o JSON** exactamente conforme o schema em
   [reference.md § JSON de saída](reference.md#json-de-saída). Usa sempre
   `business_type` — nunca `listing_type` (nome descontinuado ao nível da DB
   desde a migração `f4cab559a0b1`; ainda existem referências residuais a
   `listing_type` a corrigir noutros ficheiros do projecto, não repitas o erro
   aqui).

8. **Notas de validação** — inclui sempre:
   - Selectors de baixa confiança (classes CSS geradas: `.sc-abc123`, `.css-1x2y3z`)
   - Campos deixados `null` e porquê
   - O `curl` para criar o site (ver [reference.md](reference.md#testar-antes-de-activar))
   - A sequência de testes *read-only* a correr antes de activar o schedule
     (`test-listing-page`, `test-scrape`, `validate-selectors`) — nenhum
     escreve na base de dados.

## Regras

- Nunca inventes um selector — se não está no HTML fornecido, é `null`.
- Prefere selectors estáveis (`[data-testid=...]`, `[itemprop=...]`,
  `.price-value`) a classes CSS-in-JS geradas.
- `use_js_render: true` só se o conteúdo principal vier de `<script>` JSON ou
  de uma shell `#__next`/`#app` sem dados reais no HTML estático.
- `pagination_type: sitemap` → `listing_link_selector`/`next_page_selector`
  ficam irrelevantes; `link_pattern` filtra as entradas do sitemap XML e
  `start_url` do job deve apontar para o próprio sitemap.
- Se o site tiver texto próprio para anúncios vendidos/reservados fora dos
  termos PT/EN habituais, define `selectors.sold_keywords`.