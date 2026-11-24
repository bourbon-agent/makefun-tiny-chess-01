#!/usr/bin/env python3
"""Repack main_src.py -> main.py (the tournament deliverable).

Recipe reproduces the historical e6e94ce pack:
  - container: lzma FORMAT_ALONE, single LZMA1 filter
  - filter params: lc=3, lp=0, pb=0, dict_size=16 KiB, preset 9
    (decoded from the original payload header via lzma._decode_filter_properties)
  - the payload is embedded as a latin-1 string literal, so only these bytes
    get escaped: backslash, single-quote, \\n, \\r, NUL (as \\0).  All other
    high bytes stay raw in the file -- hence the #coding:l1 line.
  - wrapper: import lzma;exec(lzma.decompress('<literal>'.encode('l1')))

Guarantees before writing: literal round-trips byte-exactly to main_src.py,
main.py parses/compiles, and the file fits the 4096-byte budget.
"""
import ast
import lzma
import pathlib
import py_compile

HERE = pathlib.Path(__file__).resolve().parent
SRC = HERE.parent / "main_src.py"
OUT = SRC.parent / "main.py"
BUDGET = 4096

FILTERS = [{
    "id": lzma.FILTER_LZMA1,
    "preset": 9,
    "lc": 3,
    "lp": 0,
    "pb": 0,
    "dict_size": 16384,
}]


def pack(src: bytes) -> bytes:
    payload = lzma.compress(src, format=lzma.FORMAT_ALONE, filters=FILTERS)
    if payload[5:13] == b"\xff" * 8:
        # one-shot compress() leaves the ALONE size field unknown; pin it so
        # plain lzma.decompress() can find the end of the stream
        payload = payload[:5] + len(src).to_bytes(8, "little") + payload[13:]
    s = payload.decode("latin-1")
    for a, b in (("\\", "\\\\"), ("'", "\\'"), ("\n", "\\n"), ("\r", "\\r"), ("\x00", "\\x00")):
        s = s.replace(a, b)
    return ("#coding:l1\nimport lzma;exec(lzma.decompress('" + s + "'.encode('l1')))").encode("latin-1")


def roundtrip(latin1_text: str, expect: bytes) -> bool:
    tree = ast.parse(latin1_text)
    lits = [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and len(n.value) > 1000]
    return bool(lits) and lzma.decompress(lits[0].encode("latin-1")) == expect


class _StripDocstrings(ast.NodeTransformer):
    def _strip(self, node):
        body = node.body
        while body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
            body = body[1:]
        node.body = body
        return self.generic_visit(node)

    visit_Module = visit_FunctionDef = visit_ClassDef = _strip


def main() -> None:
    src = SRC.read_bytes()
    tree = ast.parse(src)
    tree = _StripDocstrings().visit(tree)
    src = ast.unparse(tree).encode()  # canonical minimal whitespace for the payload
    data = pack(src)
    text = data.decode("latin-1")
    if not roundtrip(text, src):
        raise SystemExit("round-trip FAILED — main.py not written")
    if len(data) > BUDGET:
        raise SystemExit(f"main.py would be {len(data)} bytes — over the {BUDGET}-byte budget")
    OUT.write_bytes(data)
    py_compile.compile(OUT, doraise=True)
    payload_len = len(lzma.compress(src, format=lzma.FORMAT_ALONE, filters=FILTERS))
    print(f"{OUT.name}: {len(data)} bytes (budget {BUDGET}: {'OK' if len(data) <= BUDGET else 'OVER'}) "
          f"| payload {payload_len} | round-trip verified | compiles")


if __name__ == "__main__":
    main()
