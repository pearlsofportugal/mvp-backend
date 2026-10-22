# Contrato scraper → plugin Pearls (Houzez) — v2.0

## Princípios (longevidade)
1. **Só se acrescenta.** Dentro da v2.x nunca se renomeia/remove campos. Mudança que quebre = v3.0 e o endpoint serve `?schema=2` e `?schema=3` em paralelo durante 3 meses.
2. **`null` = desconhecido; `false`/`0` = confirmado que não tem.** Hoje os `has_*` quase todos `null` impedem preencher Features.
3. **Códigos estáveis, rótulos livres.** `property_type.code`, `features[]`, `partner_key`, `location.*.code` nunca mudam; o texto pode mudar. O plugin liga por código.
4. **Texto limpo, uma língua por entrada**, sem contactos/HTML/"nota para agentes".
5. **Fotos em URL estável, maior tamanho**, sem `_s`/`_m`, com `width/height`.
6. **`updated_at` só muda se o conteúdo público mudar** (o plugin salta o resto → sync rápido).
7. **O que desaparece não some**: `status=removed` + `removed_at` (o plugin marca vendido/arquivado em vez de apagar → SEO mantém-se).
8. **Envelope da lista** (`GET /listings`): `{ "schema_version":"2.0", "items":[...], "next_cursor":"...", "total":123, "generated_at":"..." }` com paginação por cursor e filtro `?updated_since=ISO`.

## Mapeamento → Houzez
| Campo API | Houzez / plugin |
|---|---|
| `id` | meta `_pearls_id` (chave de upsert) |
| `content.translations.*.title/description` | post_title / post_content (WPML/Polylang se multilingue) |
| `content.translations.*.meta_description` | Yoast meta description |
| `business_type` | taxonomia `property_status` (Venda/Arrendamento/Trespasse) |
| `property_type.code` | taxonomia `property_type` |
| `typology.label` | campo Fields Builder `tipologia` (`fave_tipologia`) |
| `price.amount`, `period`, `on_request` | `fave_property_price`, `fave_property_price_postfix`, "sob consulta" |
| `areas.useful_m2` / `land_m2` | `fave_property_size` / `fave_property_land` |
| `rooms.*` | `fave_property_bedrooms`, `_bathrooms`, `_garage`, andar |
| `building.construction_year`, `energy_class` | `fave_property_year`, `fave_energy_class` |
| `location.district/county/parish` (`code`) | `property_state` / `property_city` / `property_area` |
| `features[]` | taxonomia `property_feature` |
| `media[]` (photo, `position`, `is_cover`) | galeria + imagem de destaque (hotlink) |
| `media[type=floorplan]` | Floor plans |
| `media[type=video|virtual_tour]` | `fave_video_url`, `fave_virtual_tour` |
| `partner` | CPT `houzez_agency` (nome, logo, contactos) |
| `status`, `removed_at` | publish → draft/“vendido” |
| `quality.publishable` | publicar vs rascunho |
| `canonical_id` | noindex/redirect 301 para o principal |

## Obrigatório vs desejável
**Obrigatórios para publicar:** `id`, `status`, `updated_at`, `source.partner_key/url`, `business_type`, `property_type.code`, `location.district/county`, `price` (ou `on_request`), ≥1 `translations`, ≥1 foto.
**O que mais rende no Houzez** (hoje em falta no scraper): `features[]` (filtros), `typology`, `condition`, `partner` com logo, `media.width/height`, descrição sem contactos, `content_hash`.

## Manutenção
- Publicar o JSON Schema em `/schemas/listing/2.0.json` e validar **no scraper antes de gravar** (falha = `quality.issues`, não crashar o plugin).
- Dicionários versionados em `/taxonomies` (property_type, features, energy_class, places) — o plugin lê e cria termos.
- O plugin ignora campos desconhecidos (`additionalProperties: true`) e usa defaults se faltar um opcional.
- Endpoint de saúde `/health` com `schema_version`, contagens por parceiro e última execução.
- Teste de contrato no CI: um exemplo por parceiro validado contra o schema.

## Migração do formato atual
`description_clean`/`description` + `enriched_translations` → `content.translations`; `has_*` → `features[]`; `source_partner` → `source.partner_key`; `media_assets` → `media` (acrescentar width/height, `is_cover`).

---

# Implementação no scraper (mvp-backend)

Base: `https://<host>/api/v2`. Todas as rotas pedem o cabeçalho `X-API-Key`, exceto o schema (público) e o `/health` da raiz.
O formato v1 (`/api/v1`) não foi tocado.

| Rota | Para quê |
|---|---|
| `GET /api/v2/listings?updated_since=&cursor=&limit=&partner=&status=` | O feed. `limit` 1–200 (50 por omissão); `status` = `active`, `removed` ou `all` (omissão: `all`, para o plugin arquivar o que desapareceu). |
| `GET /api/v2/listings/{id}` | Um imóvel (o objeto do contrato, sem envelope). |
| `GET /api/v2/taxonomies` | Tipos e subtipos, condições, classes energéticas, features (26), estados, tipos de negócio, parceiros. Códigos estáveis; `label_pt`/`label_en`. |
| `GET /api/v2/taxonomies/places?level=district\|county\|parish&parent=` | Distritos, concelhos e freguesias oficiais (DICOFRE). `parent` = código do distrito (2 dígitos) ou do concelho (4). |
| `GET /api/v2/schemas/listing/2.0.json` | O JSON Schema (público). |
| `GET /api/v2/health` | `schema_version`, imóveis ativos/removidos por parceiro e a última execução de cada site. |

O `/health` da raiz também passa a trazer `schema_version`.

## Como sincronizar
1. Primeira vez: percorrer `GET /listings?limit=200` seguindo `next_cursor` até vir `null`. Guardar o `generated_at` **da primeira página**.
2. Sincronizações seguintes: `GET /listings?updated_since=<generated_at guardado>` e o mesmo ciclo de cursor.
3. `updated_since` é **inclusivo** (`>=`); repetir um imóvel é inofensivo, perder um não é possível. A ordem é `(updated_at, id)` ascendente e o cursor guarda ambos, por isso imóveis com o mesmo `updated_at` não se perdem entre páginas.
4. `status=removed` chega com `removed_at` e `removed_reason: "not_found"`: arquivar/marcar vendido em vez de apagar.

## Regras de mapeamento aplicadas (as que não são óbvias)
- **Classe energética:** a escala portuguesa acaba no F; um `G` perdido vira `F`. `Isento`/`Exempted` → `exempt`; "em curso" → `pending`; `Unavailable`/vazio → `null` (desconhecido, não uma classe).
- **Estado (`condition`):** `new`, `under_construction`, `used`, `renovated`, `to_renovate`, `ruin`; texto que não reconhecemos → `null`.
- **Tipo:** passa sempre pelo vocabulário canónico (`Moradia Isolada`, `Lote`, `Quinta/Herdade`… caem no código certo); `villa`, `townhouse` e `hotel` e os subtipos vêm do texto.
- **Features:** só entram positivas e com evidência clara — flag `has_*` verdadeira ou texto sem negação nem dúvida ("sem varanda", "possibilidade de piscina" não contam).
- **Título (5–120) e descrição (≥ 40):** cortados em palavra / sintetizados ("Apartamento T2 em Canidelo") quando a fonte é curta demais; telefones e emails removidos da descrição (`contact_in_text` fica em `quality.issues` se a fonte os tinha).
- **Fotos:** `thumb_url` por regra de anfitrião; a capa é a primeira foto; `has_watermark` quando o URL tem `/watermark/`; máximo 60; `width`/`height` só quando conhecidos.
- **Qualidade:** `publishable` = ≥1 foto, preço (ou "sob consulta"), distrito/concelho e descrição `pt` válida. Os `issues` explicam porque não.
- **Parceiro:** o nome vem de `site_configs.name`, o `website` do `base_url`; `logo_url`, `legal_name`, `ami_licence`, `phone`, `email`, `address` preenchem-se por `PATCH /api/v1/sites/{key}`.

## Fora desta entrega (fase 2)
`status` `reserved`/`sold`/`rented` (hoje só `active`/`removed`), `canonical_id` e `duplicate_group_id` (duplicados entre agências; vão a `null`) e `width`/`height` reais das fotos.

## Verificar em produção
```
set MVP_API_URL=https://<host>/api/v1
set MVP_API_KEY=<chave>
python scripts/validate_contract.py
```
Percorre o feed todo, valida cada imóvel contra o schema publicado e imprime, por parceiro, violações, imóveis publicáveis e os `quality.issues` mais frequentes. Sai com código 1 se houver violações ou imóveis repetidos entre páginas.
