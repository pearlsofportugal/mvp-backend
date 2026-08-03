# scripts/audit_and_fix_image_filters.py
import asyncio
from app.database import async_session_factory
from app.models.site_config_model import SiteConfig
from sqlalchemy import select

async def main():
    async with async_session_factory() as db:
        # 1. Fetch all configurations once
        sites = (await db.execute(select(SiteConfig))).scalars().all()
        total_fixed = 0
        
        for s in sites:
            site_was_updated = False
            
            for field_name in ("image_filter", "image_exclude_filter"):
                value = getattr(s, field_name)
                if not value:
                    continue
                
                # Check conditions
                has_double_backslash = "\\\\" in value
                # Check for wrapping quotes (e.g., '"images.com"' or "'images.com'")
                has_stray_quotes = (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'"))
                
                if has_double_backslash or has_stray_quotes:
                    print(f"[SUSPEITO] {s.key}.{field_name} original: {value!r}")
                    
                    fixed_value = value
                    if has_double_backslash:
                        fixed_value = fixed_value.replace("\\\\", "\\")
                    if has_stray_quotes:
                        fixed_value = fixed_value.strip('"').strip("'")
                    
                    # Update the model instance directly
                    setattr(s, field_name, fixed_value)
                    print(f"    -> [CORRIGIDO]: {fixed_value!r}")
                    site_was_updated = True
            
            if site_was_updated:
                total_fixed += 1
        
        # 2. Commit all changes at once if updates were made
        if total_fixed > 0:
            await db.commit()
            print(f"\n[SUCESSO] {total_fixed} sites foram limpos e salvos no banco.")
        else:
            print("\n[OK] Nenhum filtro corrompido encontrado.")

if __name__ == "__main__":
    asyncio.run(main())