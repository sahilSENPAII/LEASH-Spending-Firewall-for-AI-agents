# M0 spikes

Throwaway scripts that answer the risky questions before we build anything. Run them on your Mac
(PayPal's API is not reachable from Claude's sandbox).

## Setup (once)
```bash
cd ~/leash
python3 -m venv .venv && source .venv/bin/activate
pip install -r spikes/requirements.txt
playwright install chromium
```

## 1. PayPal vault -> authorize -> capture / void
```bash
python spikes/01_paypal_vault.py
```
A browser tab opens: log in as the **sandbox buyer** and approve. You land on example.com; go back to
Terminal and press Enter. Paste the `=== RESULTS ===` block back to Claude.

## 2. MCP from Claude Desktop
Add this to `~/Library/Application Support/Claude/claude_desktop_config.json`
(create the file if missing; merge if it already has `mcpServers`), then fully quit and reopen Claude:
```json
{
  "mcpServers": {
    "leash-spike": {
      "command": "/Users/sahilray/leash/.venv/bin/python",
      "args": ["/Users/sahilray/leash/spikes/02_mcp_hello.py"]
    }
  }
}
```
In a new Claude chat: "Use leash to list my mandates, then request a $179 payment to SoundHub, then a $300 one."
Expect ALLOW then BLOCK.

## 3. Headless browser reads hidden text
```bash
python spikes/03_playwright.py
```
Expect `Hidden injection visible to agent: True`.
