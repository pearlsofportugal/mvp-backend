"""Create configuration tables and seed with default values.

Revision ID: 003_config_tables
Revises: 002_seed_site_configs
Create Date: 2025-01-15 12:00:00.000000

This migration:
1. Creates enrichment_configs table for scoring keywords and thresholds
2. Creates field_mappings table for field name translations
3. Creates character_mappings table for mojibake fixes and currency symbols
4. Seeds all tables with values previously hardcoded in services
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '003_config_tables'
down_revision = '002_seed_site_configs'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ========================================
    # Create enrichment_configs table
    # ========================================
    op.create_table(
        'enrichment_configs',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('key', sa.String(50), unique=True, index=True, nullable=False),
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('location_keywords', sa.JSON(), nullable=False, server_default='[]'),
        sa.Column('rooms_keywords', sa.JSON(), nullable=False, server_default='[]'),
        sa.Column('condition_keywords', sa.JSON(), nullable=False, server_default='[]'),
        sa.Column('amenity_keywords', sa.JSON(), nullable=False, server_default='[]'),
        sa.Column('area_keywords', sa.JSON(), nullable=False, server_default='[]'),
        sa.Column('marketing_phrases', sa.JSON(), nullable=False, server_default='[]'),
        sa.Column('scoring_weights', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('grade_thresholds', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('penalties', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('min_length_short', sa.Integer(), nullable=False, server_default='100'),
        sa.Column('min_length_good', sa.Integer(), nullable=False, server_default='300'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
    )

    # ========================================
    # Create field_mappings table
    # ========================================
    op.create_table(
        'field_mappings',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('source_name', sa.String(100), nullable=False, index=True),
        sa.Column('target_field', sa.String(50), nullable=False, index=True),
        sa.Column('mapping_type', sa.String(20), nullable=False, server_default='field'),
        sa.Column('language', sa.String(5), nullable=False, server_default='pt'),
        sa.Column('site_key', sa.String(50), nullable=True, index=True),
        sa.Column('priority', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
    )

    # ========================================
    # Create character_mappings table
    # ========================================
    op.create_table(
        'character_mappings',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('source_chars', sa.String(20), unique=True, nullable=False, index=True),
        sa.Column('target_chars', sa.String(20), nullable=False),
        sa.Column('category', sa.String(20), nullable=False, server_default='mojibake'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
    )

    # ========================================
    # Seed enrichment_configs with default
    # ========================================
    op.execute("""
        INSERT INTO enrichment_configs (key, name, location_keywords, rooms_keywords, condition_keywords, 
                                        amenity_keywords, area_keywords, marketing_phrases, 
                                        scoring_weights, grade_thresholds, penalties)
        VALUES (
            'default',
            'Default Enrichment Config',
            '["localizado", "situado", "zona", "bairro", "centro", "próximo", "perto", "junto", "acessos", "transportes", "metro", "autoestrada", "praia", "vista", "exposição solar"]',
            '["quarto", "quartos", "suite", "suites", "wc", "casa de banho", "cozinha", "sala", "varanda", "marquise", "despensa", "arrumos", "escritório"]',
            '["renovado", "remodelado", "novo", "recuperado", "restaurado", "bom estado", "para recuperar", "usado", "como novo", "primeira mão"]',
            '["garagem", "estacionamento", "parking", "box", "arrecadação", "piscina", "jardim", "terraço", "churrasqueira", "lareira", "ar condicionado", "aquecimento central", "painéis solares", "elevador", "portaria", "condomínio", "segurança"]',
            '["m2", "m²", "metros", "metros quadrados", "área", "área útil", "área bruta", "área total"]',
            '["oportunidade", "única", "único", "excelente", "fantástico", "fantástica", "maravilhoso", "maravilhosa", "imperdível", "não perca", "aproveite", "negócio", "investimento", "rentabilidade"]',
            '{"location": 15, "rooms": 10, "condition": 10, "amenity": 10, "area": 10, "length_bonus": 5, "marketing_penalty": -5}',
            '{"A+": 90, "A": 80, "B": 70, "C": 60, "D": 50, "E": 30, "F": 0}',
            '{"short_description": -20, "no_location": -15, "no_area": -10, "all_caps": -10, "contact_info": -5}'
        )
    """)

    # ========================================
    # Seed field_mappings - Field translations
    # ========================================
    field_mappings = [
        # Price fields
        ("preço", "price", "field", "pt"),
        ("price", "price", "field", "en"),
        ("valor", "price", "field", "pt"),
        ("precio", "price", "field", "es"),
        
        # Typology
        ("tipologia", "typology", "field", "pt"),
        ("typology", "typology", "field", "en"),
        ("tipo", "typology", "field", "pt"),
        
        # Bedrooms
        ("quartos", "bedrooms", "field", "pt"),
        ("bedrooms", "bedrooms", "field", "en"),
        ("assoalhadas", "bedrooms", "field", "pt"),
        ("t0", "bedrooms", "field", "pt"),
        ("t1", "bedrooms", "field", "pt"),
        ("t2", "bedrooms", "field", "pt"),
        ("t3", "bedrooms", "field", "pt"),
        ("t4", "bedrooms", "field", "pt"),
        ("t5", "bedrooms", "field", "pt"),
        
        # Bathrooms
        ("casas de banho", "bathrooms", "field", "pt"),
        ("wc", "bathrooms", "field", "pt"),
        ("bathrooms", "bathrooms", "field", "en"),
        
        # Area
        ("área útil", "area_useful", "field", "pt"),
        ("área bruta", "area_gross", "field", "pt"),
        ("área", "area_useful", "field", "pt"),
        ("area", "area_useful", "field", "en"),
        ("m2", "area_useful", "field", "pt"),
        ("m²", "area_useful", "field", "pt"),
        
        # Floor
        ("andar", "floor", "field", "pt"),
        ("piso", "floor", "field", "pt"),
        ("floor", "floor", "field", "en"),
        
        # Energy certificate
        ("certificado energético", "energy_certificate", "field", "pt"),
        ("classe energética", "energy_certificate", "field", "pt"),
        ("energy certificate", "energy_certificate", "field", "en"),
        ("energy class", "energy_certificate", "field", "en"),
        
        # Construction year
        ("ano de construção", "construction_year", "field", "pt"),
        ("ano", "construction_year", "field", "pt"),
        ("construction year", "construction_year", "field", "en"),
        ("built", "construction_year", "field", "en"),
        
        # Location
        ("localização", "location", "field", "pt"),
        ("morada", "full_address", "field", "pt"),
        ("location", "location", "field", "en"),
        ("address", "full_address", "field", "en"),
        ("distrito", "district", "field", "pt"),
        ("concelho", "county", "field", "pt"),
        ("freguesia", "parish", "field", "pt"),
        
        # Property type
        ("tipo de imóvel", "property_type", "field", "pt"),
        ("property type", "property_type", "field", "en"),
        ("apartamento", "property_type", "field", "pt"),
        ("moradia", "property_type", "field", "pt"),
        ("terreno", "property_type", "field", "pt"),
        
        # Listing type
        ("tipo de negócio", "listing_type", "field", "pt"),
        ("venda", "listing_type", "field", "pt"),
        ("arrendamento", "listing_type", "field", "pt"),
        ("sale", "listing_type", "field", "en"),
        ("rent", "listing_type", "field", "en"),
    ]
    
    for source, target, mtype, lang in field_mappings:
        op.execute(f"""
            INSERT INTO field_mappings (source_name, target_field, mapping_type, language)
            VALUES ('{source}', '{target}', '{mtype}', '{lang}')
        """)

    # ========================================
    # Seed field_mappings - Feature detection
    # ========================================
    feature_mappings = [
        # Garage
        ("garagem", "has_garage", "pt"),
        ("garage", "has_garage", "en"),
        ("parking", "has_garage", "en"),
        ("estacionamento", "has_garage", "pt"),
        ("box", "has_garage", "pt"),
        
        # Elevator
        ("elevador", "has_elevator", "pt"),
        ("elevator", "has_elevator", "en"),
        ("lift", "has_elevator", "en"),
        
        # Balcony
        ("varanda", "has_balcony", "pt"),
        ("balcony", "has_balcony", "en"),
        ("terraço", "has_balcony", "pt"),
        ("terrace", "has_balcony", "en"),
        ("marquise", "has_balcony", "pt"),
        
        # Air conditioning
        ("ar condicionado", "has_air_conditioning", "pt"),
        ("air conditioning", "has_air_conditioning", "en"),
        ("a/c", "has_air_conditioning", "en"),
        ("ac", "has_air_conditioning", "en"),
        ("climatização", "has_air_conditioning", "pt"),
        
        # Pool
        ("piscina", "has_pool", "pt"),
        ("pool", "has_pool", "en"),
        ("swimming pool", "has_pool", "en"),
    ]
    
    for source, target, lang in feature_mappings:
        op.execute(f"""
            INSERT INTO field_mappings (source_name, target_field, mapping_type, language)
            VALUES ('{source}', '{target}', 'feature', '{lang}')
        """)

    # ========================================
    # Seed character_mappings - Mojibake fixes
    # ========================================
    mojibake_mappings = [
        ("Ã¡", "á"),
        ("Ã©", "é"),
        ("Ã­", "í"),
        ("Ã³", "ó"),
        ("Ãº", "ú"),
        ("Ã£", "ã"),
        ("Ãµ", "õ"),
        ("Ã§", "ç"),
        ("Ã¢", "â"),
        ("Ãª", "ê"),
        ("Ã´", "ô"),
        ("Ã ", "à"),
        ("Ã¼", "ü"),
        ("Ã±", "ñ"),
        ("Ã", "Á"),
        ("Ã‰", "É"),
        ('Ã"', "Ó"),
        ("Ãš", "Ú"),
        ("Ãƒ", "Ã"),
        ("Ã•", "Õ"),
        ("Ã‡", "Ç"),
        ("Âº", "º"),
        ("Âª", "ª"),
    ]
    
    for source, target in mojibake_mappings:
        # Escape single quotes for SQL
        source_escaped = source.replace("'", "''")
        target_escaped = target.replace("'", "''")
        op.execute(f"""
            INSERT INTO character_mappings (source_chars, target_chars, category)
            VALUES ('{source_escaped}', '{target_escaped}', 'mojibake')
        """)

    # ========================================
    # Seed character_mappings - Currency symbols
    # ========================================
    currency_mappings = [
        ("€", "EUR"),
        ("R$", "BRL"),
        ("$", "USD"),
        ("£", "GBP"),
        ("¥", "JPY"),
        ("CHF", "CHF"),
        ("kr", "SEK"),
        ("zł", "PLN"),
        ("Kč", "CZK"),
    ]
    
    for symbol, code in currency_mappings:
        op.execute(f"""
            INSERT INTO character_mappings (source_chars, target_chars, category)
            VALUES ('{symbol}', '{code}', 'currency')
        """)


def downgrade() -> None:
    op.drop_table('character_mappings')
    op.drop_table('field_mappings')
    op.drop_table('enrichment_configs')
