"""Write one network head into bots/hybrid/src/weights.py without touching the other heads.

weights.py holds one `<NAME>_SPEC = {...}` block and one `<NAME>_MARGIN = ...` line per head; the
trainers call write_head() to replace just theirs (a selector retrain must not wipe the camp
scorer, and vice versa). Each head also keeps one note line in the module docstring.
"""
import re


def spec_literal(spec_layers, extra=None):
    def lit(layer):
        raw = bytes(int(v) & 0xFF for v in layer["weights"].reshape(-1))
        return ("    {\"n_in\": %d, \"n_out\": %d, \"mult\": %d, \"shift\": %d,\n     \"bias\": %s,\n     \"weights\": %r}"
                % (layer["n_in"], layer["n_out"], layer["mult"], layer["shift"], [int(v) for v in layer["bias"]], raw))
    head = "".join("\"%s\": %r, " % (k, v) for k, v in (extra or {}).items())
    return "{%s\"layers\": [\n%s\n]}" % (head, ",\n".join(lit(l) for l in spec_layers))


def write_head(path, name, spec_layers, margin, margin_note, note, extra=None):
    """Replace `<name>_SPEC`, `<name>_MARGIN` and the head's note line in weights.py."""
    text = open(path, encoding="utf-8").read()
    spec = "%s_SPEC = %s\n" % (name, spec_literal(spec_layers, extra))
    text, n = re.subn(r"^%s_SPEC = .*?(?=^[A-Z_]+ = |^[A-Za-z_]+ = nn\.)" % name, lambda m: spec, text,
                      count=1, flags=re.S | re.M)
    assert n == 1, f"{name}_SPEC block not found in {path}"
    margin_line = "%s_MARGIN = %d   # %s\n" % (name, margin, margin_note)
    text, n = re.subn(r"^%s_MARGIN = .*\n" % name, lambda m: margin_line, text, count=1, flags=re.M)
    if n == 0:
        text = text.replace(spec, spec + margin_line, 1)
    note_line = "%s: %s" % (name, note)
    if re.search(r"^%s: .*$" % name, text, flags=re.M):
        text = re.sub(r"^%s: .*$" % name, lambda m: note_line, text, count=1, flags=re.M)
    else:
        text = text.replace('"""\nimport nn', note_line + '\n"""\nimport nn', 1)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
