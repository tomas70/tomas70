"""
AI analyst module — dual mode:

  Claude Desktop (default, no API key):
    build_analysis_context() formats the analysis as structured text.
    Claude Desktop reads this as an MCP tool result and applies its own
    SMC + Ross Hook expertise to decide confidence and skip/execute.

  Standalone mode (ANTHROPIC_API_KEY set):
    generate_setup_standalone() calls Claude API directly and returns a
    parsed decision dict.
"""
import json
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

CLAUDE_MODEL     = os.getenv("CLAUDE_MODEL", "claude-opus-4-8")
CLAUDE_MAX_TOKENS = 1024

SYSTEM_PROMPT = """\
Tu esi profesionalus crypto trader naudojantis SMC ir Ross Hook metodiką.
Gauni rinkos analizės duomenis ir grąžini TIKTAI JSON objektą (be jokio kito teksto).

Privalomi laukai:
{
  "bias":         "long" | "short",
  "entry":        number,
  "sl":           number,
  "tp1":          number,
  "tp2":          number,
  "rr_ratio":     number,
  "confidence":   1–10,
  "invalidation": "sąlyga, kuri panaikina setup'ą",
  "skip":         true | false,
  "reasoning":    "trumpas pagrindimas"
}

Jei confidence < 7 arba setup'as silpnas — nustatyk "skip": true."""


# ─── Claude Desktop mode ──────────────────────────────────────────────────────

def build_analysis_context(analysis: dict, position: dict) -> str:
    """
    Formats the full analysis + position data as structured readable text.
    This is the string Claude Desktop sees as the MCP tool result — Claude
    then applies its own reasoning to assign confidence and decide skip/execute.
    """
    pair    = analysis.get("pair", "?")
    bias    = analysis.get("bias", "?").upper()
    price   = analysis.get("current_price", 0)
    entry   = analysis.get("entry", 0)
    sl      = analysis.get("sl", 0)
    tp1     = analysis.get("tp1", 0)
    tp2     = analysis.get("tp2", 0)
    rr      = analysis.get("rr_ratio", 0)
    sl_pct  = analysis.get("sl_pct", 0)
    tp1_pct = analysis.get("tp1_pct", 0)

    ob       = analysis.get("order_block")
    fvg      = analysis.get("fvg")
    hook     = analysis.get("ross_hook", {})
    struct   = analysis.get("structure_4h", {})
    pd_info  = analysis.get("pd_zone", {})

    ob_range    = f"${ob.low:,.4f} – ${ob.high:,.4f}" if ob else "N/A"
    fvg_status  = f"${fvg.bottom:,.4f} – ${fvg.top:,.4f} (neužpildytas)" if fvg else "nėra"
    hook_ago    = hook.get("candles_ago", 0)
    bos_label   = "CHoCH" if struct.get("is_choch") else "BOS"
    pd_zone     = pd_info.get("zone", "?").upper()
    fib_pct     = (pd_info.get("fib_pct") or 0) * 100

    level       = position.get("level", 1)
    risk_usd    = position.get("risk_usd", 0)
    pos_usd     = position.get("position_usd", 0)
    leverage    = position.get("leverage", 1)
    margin_usd  = position.get("margin_usd", 0)
    margin_ok   = position.get("margin_ok", True)
    remaining   = position.get("remaining_profit", 0)

    lines = [
        f"╔══ SMC + ROSS HOOK ANALIZĖ: {pair} ══╗",
        f"",
        f"  Kaina dabar:  ${price:>12,.4f}",
        f"  Kryptis (4H): {bias}",
        f"  {bos_label} patvirtintas: ✓",
        f"  PD zona:      {pd_zone} (Fib {fib_pct:.1f}%)",
        f"",
        f"  ── SMC STRUKTŪRA (4H / 1H) ──",
        f"  Order Block:  {ob_range}",
        f"  FVG:          {fvg_status}",
        f"  FVG ↔ OB overlap: ✓",
        f"",
        f"  ── ROSS HOOK (15m) ──",
        f"  Hook lygis:   ${entry:,.4f}",
        f"  Susiformavo:  prieš {hook_ago} žvakių",
        f"  Kryptis:      {hook.get('pattern', '?').upper()}  ✓",
        f"",
        f"  ── TRADE PARAMETRAI ──",
        f"  Entry:  ${entry:>12,.4f}",
        f"  SL:     ${sl:>12,.4f}  (-{sl_pct:.2f}%)",
        f"  TP1:    ${tp1:>12,.4f}  (+{tp1_pct:.2f}%)",
        f"  TP2:    ${tp2:>12,.4f}",
        f"  R:R  =  1:{rr:.1f}",
        f"",
        f"  ── RIZIKA (Level {level}) ──",
        f"  Rizika:   ${risk_usd:.2f}  (30%)",
        f"  Notional: ~${pos_usd:.0f}",
        f"  Leverage: {leverage:.0f}x",
        f"  Margin:   ${margin_usd:.2f}  {'✓' if margin_ok else '⚠️ viršija balansą'}",
        f"  Iki kito lygio: ${remaining:.2f}",
        f"",
        f"╚{'═' * 40}╝",
    ]
    return "\n".join(lines)


# ─── Standalone mode (optional ANTHROPIC_API_KEY) ─────────────────────────────

def generate_setup_standalone(
    analysis: dict,
    position: dict,
) -> Optional[dict]:
    """
    Calls Claude API directly when ANTHROPIC_API_KEY is set.
    Returns parsed decision dict or None if key unavailable / call fails.
    Used in main.py standalone scheduler mode.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY") or ""
    if not api_key:
        return None

    try:
        import anthropic  # optional dependency
        client  = anthropic.Anthropic(api_key=api_key)
        context = build_analysis_context(analysis, position)

        msg = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=CLAUDE_MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": context}],
        )

        text  = msg.content[0].text.strip()
        start = text.find("{")
        end   = text.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(text[start:end])

        logger.warning("Claude response did not contain valid JSON")
    except Exception as exc:
        logger.error("Standalone Claude call failed: %s", exc)

    return None
