"""Contract v2 between the scraper and the Pearls (Houzez) plugin: see docs/contract/README.md."""
from app.contract_v2.serializer import SCHEMA_VERSION, partner_profile, to_contract

__all__ = ["SCHEMA_VERSION", "to_contract", "partner_profile"]
