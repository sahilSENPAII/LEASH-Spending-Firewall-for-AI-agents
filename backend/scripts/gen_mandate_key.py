"""Print a new MANDATE_SIGNING_KEY for your .env (each person and Render gets their own).

cd backend && uv run python -m scripts.gen_mandate_key
"""

from app.mandates import generate_signing_key

if __name__ == "__main__":
    print(f"MANDATE_SIGNING_KEY={generate_signing_key()}")
