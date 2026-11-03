"""Print a new MANDATE_SIGNING_KEY for .env. Generate ONE key per database, not per person.

Everyone and every server using the same database (Sahil, Anurag, Render) must use the same key:
mandates are signed with it, and under a different key every payment blocks. Never commit it.

cd backend && uv run python -m scripts.gen_mandate_key
"""

from app.mandates import generate_signing_key

if __name__ == "__main__":
    print(f"MANDATE_SIGNING_KEY={generate_signing_key()}")
