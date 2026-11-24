#!/usr/bin/env python3
"""Differential tests for the engine internals vs python-chess.

Run:  python3 tests/difftest.py [path/to/main_src.py]

Checks, against python-chess ground truth:
  1. attacked(square, byw)  on random sparse positions (both colours, all squares)
  2. parse(): KX placement and board[] contents on random positions
  3. legal move set: gen()+post-make legality filter vs chess.legal_moves
  4. mate-in-1 spot positions
Exits 0 with counts, prints up to 10 mismatches per category.
"""
import pathlib
import random
import sys

import chess

target = sys.argv[1] if len(sys.argv) > 1 else None
if target is None:
    target = str(pathlib.Path(__file__).resolve().parent.parent / "main_src.py")
src = pathlib.Path(target).read_text()
if src.rstrip().endswith("main()"):
    src = src.rstrip()[:-len("main()")]
ns = {}
exec(compile(src, target, "exec"), ns)
parse, attacked, gen, mk, unmk, think, uci, boardkey = (
    ns["parse"], ns["attacked"], ns["gen"], ns["mk"], ns["unmk"],
    ns["think"], ns["uci"], ns["boardkey"],
)
KX, TT, TURN0 = ns["KX"], ns["TT"], ns.get("TURN")


def to88(sq):
    return (sq >> 3) * 16 + (sq & 7)


def to64(i):
    return ((i >> 4) & 7) * 8 + (i & 7)


rng = random.Random(2026)
bad_att, bad_parse, bad_moves, bad_mate = [], [], [], []

MATE1 = [
    "7k/5ppp/8/8/8/8/5PPP/R3K2R w KQ - 0 1",   # Ra8#
    "6k1/5ppp/8/8/8/8/5PPP/R3K2R w KQ - 0 1",   # Ra8+ then? mate? no: Kg7 escapes? Ra8+ Kh7? g7 pawn... check only
    "3qk3/8/8/8/8/8/8/3QK3 w - - 0 1",          # not mate-in-1 (sanity: engine must NOT claim mate)
    "k7/8/1K6/8/8/8/8/7R w - - 0 1",           # Ra8#? Kb8... Ra8#? king a8?? occupied... Rh8+? Ka7 covered by Kb6 -> Rh8#
]

# ---- 1/2/3: random-position differentials ----
for trial in range(400):
    board = chess.Board(fen=None)
    try:
        wk, bk = rng.choice(chess.SQUARES), rng.choice(chess.SQUARES)
        if wk == bk:
            continue
        board.set_piece_at(wk, chess.Piece(chess.KING, chess.WHITE))
        board.set_piece_at(bk, chess.Piece(chess.KING, chess.BLACK))
    except ValueError:
        continue
    ok = True
    for _ in range(rng.randrange(2, 10)):
        pt = chess.Piece(rng.choice([chess.PAWN, chess.KNIGHT, chess.BISHOP,
                                     chess.ROOK, chess.QUEEN]),
                         rng.choice([chess.WHITE, chess.BLACK]))
        sq = rng.choice(chess.SQUARES)
        if pt.piece_type == chess.PAWN and chess.square_rank(sq) in (0, 7):
            continue
        try:
            board.set_piece_at(sq, pt)
        except ValueError:
            pass
    board.turn = rng.choice([True, False])
    board.ep_square = None
    board.castling_rights = 0
    board.halfmove_clock = 0
    if not board.king(chess.WHITE) or not board.king(chess.BLACK):
        continue
    fen = board.fen()
    try:
        parse(fen)
    except Exception as e:
        bad_parse.append((fen, "exception", repr(e)))
        continue
    want_wk, want_bk = to88(board.king(chess.WHITE)), to88(board.king(chess.BLACK))
    if KX[0] != want_wk or KX[1] != want_bk:
        bad_parse.append((fen, "KX", KX[0], KX[1], want_wk, want_bk))
        continue
    if ns["TURN"] != (1 if board.turn else -1):
        bad_parse.append((fen, "TURN", ns["TURN"]))
        continue
    # board[] equality
    mism_sq = None
    for sq in chess.SQUARES:
        pc = board.piece_map().get(sq)
        val = 0
        if pc:
            v = {chess.PAWN: 1, chess.KNIGHT: 2, chess.BISHOP: 3,
                 chess.ROOK: 4, chess.QUEEN: 5, chess.KING: 6}[pc.piece_type]
            val = v if pc.color else -v
        if ns["B"][to88(sq)] != val:
            mism_sq = (sq, val, ns["B"][to88(sq)])
            break
    if mism_sq:
        bad_parse.append((fen, "B", mism_sq))
        continue
    # 1. attacked
    for sq in rng.sample(chess.SQUARES, 40):
        for byw in (True, False):
            want = board.is_attacked_by(chess.WHITE if byw else chess.BLACK, sq)
            got = attacked(to88(sq), byw)
            if want != got:
                bad_att.append((fen, to88(sq), byw, want, got))
                break
        else:
            continue
        break
    # 3. legal moves
    ms = []
    gen(ms)
    eng_legal = []
    h = boardkey()
    ns["PLY"] = [h]
    for m in ms:
        saved, dup = mk(m, h)
        T = ns["TURN"]
        # EXACTLY the engine's own root filter (main_src.py line 281):
        if not attacked(KX[0 if T > 0 else 1], T < 0):
            eng_legal.append(uci(m))
        unmk(m, saved)
    want = set(m.uci() for m in board.legal_moves)
    got = set(eng_legal)
    if want != got:
        bad_moves.append((fen, sorted(want - got)[:6], sorted(got - want)[:6]))

# 4. mate-in-1: engine must find it when it exists
for fen in MATE1:
    TT.clear()
    try:
        mv = think(fen, 300000)
    except Exception as e:
        bad_mate.append((fen, "exception", repr(e)))
        continue
    got = uci(mv) if mv else None
    b = chess.Board(fen)
    has_mate = any((b.push(m), b.is_checkmate(), b.pop())[1] for m in b.legal_moves)
    if has_mate:
        b.push(chess.Move.from_uci(got))
        solved = b.is_checkmate()
        b.pop()
        if not solved:
            bad_mate.append((fen, "missed mate", got))
    else:
        # no mate available: engine must still pick a legal move
        if got not in [m.uci() for m in b.legal_moves]:
            bad_mate.append((fen, "illegal pick", got))

for name, lst in (("attacked", bad_att), ("parse", bad_parse),
                  ("moves", bad_moves), ("mate", bad_mate)):
    print(f"{name}: {len(lst)} mismatches")
    for x in lst[:8]:
        print("   ", x)
sys.exit(1 if (bad_att or bad_parse or bad_moves or bad_mate) else 0)
