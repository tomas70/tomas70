"""
AI analyst module — dual mode:

  Claude Desktop (default, no API key):
    build_analysis_context() formats the analysis as structured text.
    Claude Desktop reads this as an MCP tool result and applies its own
    SMC + Ross Hook expertise to decide confidence and skip/execute.

  Standalone mode (ANTHROPIC_API_KEY set):
    generate_setup_standalone() calls Claude API directly.
"""
import json
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

CLAUDE_MODEL      = os.getenv("CLAUDE_MODEL", "claude-opus-4-8")
CLAUDE_MAX_TOKENS = 1024

SYSTEM_PROMPT = """\
Tu esi profesionalus crypto trader naudojantis Joe Ross metodiką (1-2-3, Ross Hook, TTE) \
ir Smart Money Concepts (SMC) patvirtinimui.
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

Jei confidence < 7 arba setup'as silpnas — nustatyk "skip": true.
TTE įėjimas yra geresnis nei Hook įėjimas — vertink jį aukščiau.
Jei TP1 pažymėtas kaip "⚠️ NE struktūrinis" arba "⚠️ senas 4H lygis" — aukštas R:R \
nereiškia stipraus setup'o, nes tikslas nėra šviežia struktūrinė riba. Tokiu atveju \
vertink confidence kritiškiau, nebent kiti faktoriai (OB/FVG, hook amžius) kompensuoja."""


# ─── Context Builder ──────────────────────────────────────────────────────────

def build_analysis_context(analysis: dict, position: dict) -> str:
    """
    Formats full analysis + position data as readable text for Claude Desktop.
    """
    pair       = analysis.get("pair", "?")
    bias       = analysis.get("bias", "?").upper()
    price      = analysis.get("current_price", 0)
    entry      = analysis.get("entry", 0)
    sl         = analysis.get("sl", 0)
    tp1        = analysis.get("tp1", 0)
    tp2        = analysis.get("tp2", 0)
    rr         = analysis.get("rr_ratio", 0)
    sl_pct     = analysis.get("sl_pct", 0)
    tp1_pct    = analysis.get("tp1_pct", 0)
    entry_type = analysis.get("entry_type", "HOOK")

    ob      = analysis.get("order_block")
    fvg     = analysis.get("fvg")
    hook    = analysis.get("ross_hook", {}) or {}
    tte     = analysis.get("tte")
    struct  = analysis.get("structure_4h", {}) or {}
    pd_info = analysis.get("pd_zone", {}) or {}

    # dict-safe accessor (handles both dataclass instances and plain dicts)
    def _v(obj, key, default=None):
        if obj is None:
            return default
        return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)

    ob_range   = f"${_v(ob,'low',0):,.4f} – ${_v(ob,'high',0):,.4f}" if ob else "N/A"
    fvg_status = (
        f"${_v(fvg,'bottom',0):,.4f} – ${_v(fvg,'top',0):,.4f} (neužpildytas)"
        if fvg else "nėra (tik OB)"
    )
    hook_ago  = hook.get("candles_ago", 0)
    bos_label = "CHoCH" if struct.get("is_choch") else "BOS"
    pd_zone   = pd_info.get("zone", "?").upper()
    fib_pct   = (pd_info.get("fib_pct") or 0) * 100

    # TP1 freshness is informational only (not gated) — see multi_timeframe.py
    # module docstring for why an old 4H swing isn't treated as invalid.
    tp1_structural = analysis.get("tp1_structural", True)
    tp1_stale      = analysis.get("tp1_stale", False)
    tp1_age        = analysis.get("tp1_age_4h")
    if not tp1_structural:
        tp1_quality = "⚠️ NE struktūrinis — fallback %, RR spekuliatyvus"
    elif tp1_stale:
        tp1_quality = f"⚠️ senas 4H lygis (prieš {tp1_age} žvakių)"
    else:
        tp1_quality = f"šviežias 4H lygis (prieš {tp1_age} žvakių)"

    if tte:
        tte_line    = (
            f"  TTE įėjimas: ${_v(tte,'tte_entry',entry):,.4f}  "
            f"(signalas @ baro {_v(tte,'tte_bar_index','?')})"
        )
        tte_sl_line = f"  TTE SL:      ${_v(tte,'tte_sl',sl):,.4f}  (korekcijos žemuma)"
        entry_label = "TTE (Trader's Trick Entry)"
    else:
        tte_line    = "  TTE:         dar nesusiformavęs — laukiama signalo baro"
        tte_sl_line = f"  SL (Hook):   ${sl:,.4f}  (OB riba)"
        entry_label = "HOOK (klasikinis)"

    level      = position.get("level", 1)
    risk_usd   = position.get("risk_usd", 0)
    pos_usd    = position.get("position_usd", 0)
    leverage   = position.get("leverage", 1)
    margin_usd = position.get("margin_usd", 0)
    margin_ok  = position.get("margin_ok", True)
    remaining  = position.get("remaining_profit", 0)

    lines = [
        f"╔══ JOE ROSS + SMC ANALIZĖ: {pair} ══╗",
        f"",
        f"  Kaina dabar:   ${price:>12,.4f}",
        f"  Kryptis (4H):  {bias}  [{bos_label} patvirtintas]",
        f"  PD zona:       {pd_zone}  (Fib {fib_pct:.1f}%)",
        f"",
        f"  ── JOE ROSS 1-2-3 + HOOK (15m) ──",
        f"  Hook lygis:    ${hook.get('hook_level', 0):,.4f}",
        f"  Susiformavo:   prieš {hook_ago} žvakių",
        f"  Kryptis:       {hook.get('pattern','?').upper()}  ✓",
        f"",
        f"  ── TRADER'S TRICK ENTRY (TTE) ──",
        tte_line,
        tte_sl_line,
        f"  Įėjimo tipas:  {entry_label}",
        f"",
        f"  ── SMC PATVIRTINIMAS (1H) ──",
        f"  Order Block:   {ob_range}",
        f"  FVG:           {fvg_status}",
        f"",
        f"  ── TRADE PARAMETRAI ──",
        f"  Entry:  ${entry:>12,.4f}",
        f"  SL:     ${sl:>12,.4f}  (-{sl_pct:.2f}%)",
        f"  TP1:    ${tp1:>12,.4f}  (+{tp1_pct:.2f}%)  [{tp1_quality}]",
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
        f"╚{'═' * 42}╝",
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
    """
    api_key = os.getenv("ANTHROPIC_API_KEY") or ""
    if not api_key:
        return None

    try:
        import anthropic
        client  = anthropic.Anthropic(api_key=api_key)
        context = build_analysis_context(analysis, position)

        msg = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=CLAUDE_MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": context}],
        )

        if not msg.content or not hasattr(msg.content[0], "text"):
            logger.warning("Unexpected Claude response format")
            return None

        text  = msg.content[0].text.strip()
        start = text.find("{")
        end   = text.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(text[start:end])

        logger.warning("Claude response did not contain valid JSON")
    except Exception as exc:
        logger.error("Standalone Claude call failed: %s", exc)

    return None
