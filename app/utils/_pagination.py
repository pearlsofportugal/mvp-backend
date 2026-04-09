import math
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.base_schema import Meta

async def list_paginated(
    db: AsyncSession,
    model,
    filters: dict,
    apply_filters_fn,   # função que aplica filtros ao select
    sort_field_mapping: dict,
    sort_by: str = "created_at",
    sort_order: str = "desc",
    page: int = 1,
    page_size: int = 20,
):
    """
    Função genérica para listagem paginada de qualquer modelo.

    - db: AsyncSession
    - model: SQLAlchemy model (ex: Listing)
    - filters: dict com filtros recebidos da query
    - apply_filters_fn: função que aplica filtros ao select
    - sort_field_mapping: dict com campos de ordenação válidos {nome_param: model.campo}
    - sort_by / sort_order: ordenação
    - page / page_size: paginação
    """

    # Count total
    count_query = apply_filters_fn(select(func.count(model.id)), **filters)
    total = (await db.execute(count_query)).scalar_one()

    # Dados
    query = apply_filters_fn(select(model), **filters)
    sort_column = sort_field_mapping.get(sort_by, model.created_at)
    query = query.order_by(sort_column.desc() if sort_order == "desc" else sort_column.asc())
    query = query.offset((page - 1) * page_size).limit(page_size)

    result = await db.execute(query)
    items = result.scalars().all()

    pages = math.ceil(total / page_size) if total > 0 else 0
    meta = Meta(page=page, page_size=page_size, total=total, pages=pages)

    return items, meta