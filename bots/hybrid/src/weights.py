"""Generated network weights. Never edited by hand: tools/export_weights.py writes this file.

Each head is a spec for nn.from_spec, or None when no trained weights exist yet. Networks are
packed at import time, which is build time, so they cost no match fuel.
"""
import nn

MODE_SELECTOR_SPEC = None   # 64 -> 32 -> 16 -> 6
CAMP_SCORER_SPEC = None

MODE_SELECTOR = nn.from_spec(MODE_SELECTOR_SPEC)
CAMP_SCORER = nn.from_spec(CAMP_SCORER_SPEC)
