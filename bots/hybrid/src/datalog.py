"""Decision logging for offline training. Debug builds only: the whole call runs inside
cambc.debug_block, which costs no fuel locally and never runs on the platform.

One line per body per turn, parsed by tools/build_dataset.py:

    nnlog r=<round> s=<slot> k=<kind> m=<mode chosen> src=<script|nn> mask=<6 bits> \
          sc=<6 script scores x100> gems=<carried> bank=<team gems banked> hp=<ratio x100> \
          f=<66 features as 2-digit hex>

Enable with config.DATALOG = True and run the harness with --debug --log-limit 16000000.
"""
import camp_features
import features


def write_camp(w, turn, decider, cands, script_pick, source):
    """One line per retarget, parsed by tools/replay_camp_dataset.py --check:

        camplog r=<round> s=<slot> pick=<index the script chose, -1 none> src=<script|nn> \
                g=<group index per candidate> S=<state hex> C=<candidate hex, '|' separated>
    """
    try:
        state, rows = camp_features.build(w, turn, decider, cands)
    except Exception as exc:
        print("camplog-error %r" % (exc,), flush=True)
        return
    print("camplog r=%d s=%d pick=%d src=%s g=%s S=%s C=%s" % (
        w.round, w.slot, script_pick, source, ",".join(str(c.group.index) for c in cands),
        "".join("%02x" % v for v in state), "|".join("".join("%02x" % v for v in row) for row in rows)), flush=True)


def write(w, turn, decider, mode, mask, scores, prev_mode, prev_mode_turns):
    """Features are built exactly as the network would see them before the decision: with the
    previous turn's mode, not the one just chosen (which would leak the label)."""
    cur_mode, cur_turns = decider.mode, decider.mode_turns
    decider.mode, decider.mode_turns = prev_mode, prev_mode_turns
    try:
        x = features.build(w, turn, decider)
    except Exception as exc:                     # never let logging break a turn
        print("nnlog-error %r" % (exc,), flush=True)
        return
    finally:
        decider.mode, decider.mode_turns = cur_mode, cur_turns
    print("nnlog r=%d s=%d k=%d m=%d src=%s mask=%s sc=%s gems=%d bank=%d hp=%d f=%s" % (
        w.round, w.slot, w.kind, mode, decider.last_source,
        "".join("1" if ok else "0" for ok in mask),
        ",".join(str(int(round(s * 100))) for s in (scores or [0.0] * 6)),
        w.gems, w.team_gems_banked(), int(w.hp_ratio * 100),
        "".join("%02x" % v for v in x)), flush=True)
