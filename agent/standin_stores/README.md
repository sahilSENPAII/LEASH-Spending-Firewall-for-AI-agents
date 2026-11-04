# Stand-in store pages

Temporary copies of SoundHub and MegaDeals with the layout agreed with Sahil (product list, one page
per product, sku and price as text and in `data-sku` / `data-price`, hidden injection on a MegaDeals
product page). They let the agent be built and tested before the real stores in `stores/` exist.
Serve them with `uv run python -m leash_agent attack --standin`. Delete this folder once the real
stores run on :8101 / :8102.
