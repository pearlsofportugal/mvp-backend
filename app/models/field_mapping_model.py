"""FieldMapping SQLAlchemy model — configurable field name translations for parser."""
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class FieldMapping(Base):
    """Maps raw field names to canonical field names.
    
    Used by parser_service to translate field labels from different
    languages and sites to standardized field names.
    
    Examples:
    - "preço" → "price"
    - "quartos" → "bedrooms"
    - "garagem" → "has_garage" (feature detection)
    """
    __tablename__ = "field_mappings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    
    # Source field name (what appears in HTML)
    source_name: Mapped[str] = mapped_column(
        String(100), 
        index=True,
        comment="Raw field name from HTML (e.g., 'preço', 'price', 'quartos')"
    )
    
    # Target canonical field
    target_field: Mapped[str] = mapped_column(
        String(50),
        index=True,
        comment="Canonical field name (e.g., 'price', 'bedrooms', 'has_garage')"
    )
    
    # Mapping type
    mapping_type: Mapped[str] = mapped_column(
        String(20),
        default="field",
        comment="Type: 'field' (data field) or 'feature' (boolean amenity)"
    )
    
    # Language/locale
    language: Mapped[str] = mapped_column(
        String(5),
        default="pt",
        comment="Language code: 'pt', 'en', 'es', etc."
    )
    
    # Optional site-specific mapping
    site_key: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
        index=True,
        comment="Optional site key for site-specific mappings (NULL = global)"
    )
    
    # Priority for conflict resolution
    priority: Mapped[int] = mapped_column(
        default=0,
        comment="Higher priority mappings override lower ones"
    )
    
    # Active flag
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    def __repr__(self) -> str:
        return f"<FieldMapping('{self.source_name}' → '{self.target_field}', type={self.mapping_type})>"


class CharacterMapping(Base):
    """Maps corrupted characters (mojibake) to correct characters.
    
    Also used for currency symbol to ISO code mappings.
    
    Examples:
    - "Ã¡" → "á" (mojibake fix)
    - "€" → "EUR" (currency)
    """
    __tablename__ = "character_mappings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    
    # Source string (corrupted or symbol)
    source_chars: Mapped[str] = mapped_column(
        String(20),
        unique=True,
        index=True,
        comment="Source characters to replace"
    )
    
    # Target string (correct)
    target_chars: Mapped[str] = mapped_column(
        String(20),
        comment="Replacement characters"
    )
    
    # Mapping category
    category: Mapped[str] = mapped_column(
        String(20),
        default="mojibake",
        comment="Category: 'mojibake', 'currency', 'symbol'"
    )
    
    # Active flag
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    def __repr__(self) -> str:
        return f"<CharacterMapping('{self.source_chars}' → '{self.target_chars}', category={self.category})>"
