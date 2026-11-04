# Stand-in store pages

The agent's own copy of SoundHub and MegaDeals, in the same layout as the real stores (`stores/`,
`python -m stores.serve`): a product list, one page per product, sku and price as text and in
`data-sku` / `data-price`, and the hidden injection on the MegaDeals Sony page.

Demos use the real stores. These pages are kept so the agent's tests (and `--standin` runs) work
without starting the stores.
