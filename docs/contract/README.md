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
