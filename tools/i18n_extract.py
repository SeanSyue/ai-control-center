r"""把一個檔案裡寫死的中文搬進 `app/i18n.py`。

    .venv\Scripts\python.exe tools\i18n_extract.py app\ui\main_window.py 對照表.json

`對照表.json` 是 `{"那句中文": "要給它的 key"}`。沒有列在裡面的不動——
**一次搬一批，每一批跑一次測試**，比一次全搬安全得多。

## 它只搬「單純字串」，不碰 f-string

2026-08-24 量到：1,491 段裡有 **578 段夾在 f-string 裡**（39%）。那種不能
機械抽出來——`f"專案敏感度已設定為：{level}"` 要變成一句帶 `{level}` 的模板，
而模板裡的變數名要人取。這支工具**看到就跳過並且講出來**，不會假裝搬好了。

## 為什麼在位元組上動刀

`ast` 的 `col_offset` 是 **UTF-8 位元組偏移，不是字元偏移**。中文一個字三個
位元組，用字元偏移去切會切在字的中間——2026-08-24 第一版就是這樣把
`outcomes.py` 整個切爛的（幸好有版控）。
"""
from __future__ import annotations

import ast
import codecs
import json
import re
import sys
from pathlib import Path

CJK = re.compile(r"[\u4e00-\u9fff]")
I18N = Path("app/i18n.py")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def _docstring_ids(tree: ast.AST) -> set[int]:
    out: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            continue
        body = getattr(node, "body", None)
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            out.add(id(body[0].value))
    return out


def _inside_fstrings(tree: ast.AST) -> set[int]:
    out: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            for child in ast.walk(node):
                out.add(id(child))
    return out


def _module_level_ids(tree: ast.AST) -> set[int]:
    """在函式外面的那些。

    **模組層不准改成 `t(...)`**：那會在 import 的當下把那句話凍住，而
    `tests/test_no_new_hardcoded_chinese.py` 有一條專門擋這件事。
    那種字要改的是「存 key 不存字」，形狀不一樣，這支工具不碰。
    """
    inside: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            for child in ast.walk(node):
                inside.add(id(child))
    everything = {id(n) for n in ast.walk(tree)}
    return everything - inside


def extract(path: Path, mapping: dict[str, str]) -> tuple[int, list[str]]:
    raw = path.read_bytes()
    # **BOM 要先切掉再算偏移。**`utf-8-sig` 解碼會把那三個位元組吃掉，而
    # `ast` 的偏移是相對於解碼後的文字——留著 BOM 去改寫，每一刀都會偏 3 個
    # 位元組，切在字的中間。（2026-08-24 真的把 `ai_settings_panel.py` 切爛過，
    # 而症狀是 `UnicodeDecodeError`，跟「搬錯字串」完全不像。）
    bom = codecs.BOM_UTF8
    prefix = b""
    if raw.startswith(bom):
        prefix, raw = bom, raw[len(bom):]
    text = raw.decode("utf-8")
    tree = ast.parse(text)

    skip = _docstring_ids(tree) | _inside_fstrings(tree)
    outside = _module_level_ids(tree)

    starts: list[int] = []
    position = 0
    for line in text.encode("utf-8").splitlines(keepends=True):
        starts.append(position)
        position += len(line)

    def at(lineno: int, col: int) -> int:
        return starts[lineno - 1] + col

    edits: list[tuple[int, int, bytes]] = []
    skipped: list[str] = []
    used: dict[str, str] = {}

    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if not CJK.search(node.value) or node.value not in mapping:
            continue
        if id(node) in skip:
            skipped.append(f"{path.name}:{node.lineno} f-string／docstring 裡：{node.value[:20]}")
            continue
        if id(node) in outside:
            skipped.append(f"{path.name}:{node.lineno} 在函式外面：{node.value[:20]}")
            continue
        key = mapping[node.value]
        used[key] = node.value
        edits.append(
            (
                at(node.lineno, node.col_offset),
                at(node.end_lineno, node.end_col_offset),
                f't({json.dumps(key, ensure_ascii=False)})'.encode("utf-8"),
            )
        )

    if edits:
        # **先確認 key 收得下，再動原始碼。**
        #
        # 這裡本來的順序是反的：寫完檔案才去加 key，而 `_add_keys` 撞到
        # 「同一個 key 內容不一樣」會 `SystemExit`——那時候原始碼已經改成
        # `t("那個 key")` 了，而目錄裡沒有它。留下的是一個**指向不存在的
        # key 的檔案**，而它要等到有人跑到那一行才炸。
        #
        # （2026-08-24 真的發生了一次：`project.folder_missing` 在兩個檔案
        # 裡是兩句不同的話。）
        _add_keys(used)
        out = raw
        for start, end, replacement in sorted(edits, reverse=True):
            out = out[:start] + replacement + out[end:]
        path.write_bytes(prefix + out)
        _ensure_import(path)

    return len(edits), skipped


def _add_keys(pairs: dict[str, str]) -> None:
    source = I18N.read_text(encoding="utf-8")
    existing = ast.literal_eval(
        ast.get_source_segment(source, _strings_node(source)) or "{}"
    )
    lines: list[str] = []
    for key in sorted(pairs):
        if key in existing:
            if existing[key] != pairs[key]:
                raise SystemExit(
                    f"key {key!r} 已經存在，而且內容不一樣：\n"
                    f"  舊：{existing[key]!r}\n  新：{pairs[key]!r}"
                )
            continue
        lines.append(
            f"    {json.dumps(key, ensure_ascii=False)}: "
            f"{json.dumps(pairs[key], ensure_ascii=False)},"
        )
    if not lines:
        return
    marker = "\n}"
    index = source.rindex(marker, 0, source.index("class MissingText"))
    I18N.write_text(
        source[:index] + "\n" + "\n".join(lines) + source[index:], encoding="utf-8"
    )


def _strings_node(source: str):
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "STRINGS":
            return node.value
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "STRINGS":
            return node.value
    raise SystemExit("app/i18n.py 裡找不到 STRINGS")


def _ensure_import(path: Path) -> None:
    """補上 `from app.i18n import t`。

    **BOM 要用 `utf-8-sig` 讀掉再寫回去。**用 `utf-8` 讀的話那三個位元組會
    變成文字開頭的一個 U+FEFF 字元，而插入的 import 會被放到它前面——
    於是 BOM 跑到第三行，Python 直接拒絕整個檔案。
    （2026-08-24 真的發生過，而症狀是「invalid non-printable character」，
    跟「搬錯字串」完全不像。）
    """
    raw = path.read_bytes()
    has_bom = raw.startswith(codecs.BOM_UTF8)
    text = raw.decode("utf-8-sig")
    if re.search(r"^from app[.]i18n import t\r?$", text, re.M):
        return
    # 這裡的 \r? 是必要的：這個專案的檔案是 CRLF，而 $ 在 MULTILINE 下
    # 比的是換行前面——行尾那個 \r 會讓它比不到，於是 import 被插到
    # from __future__ **前面**，而 Python 直接拒絕整個檔案。
    match = re.search(r"^from __future__ import annotations\r?$", text, re.M)
    where = text.index(chr(10), match.end()) + 1 if match else 0
    text = text[:where] + "\nfrom app.i18n import t\n" + text[where:]
    body = text.encode("utf-8")
    path.write_bytes(codecs.BOM_UTF8 + body if has_bom else body)


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    path = Path(sys.argv[1])
    mapping = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    count, skipped = extract(path, mapping)
    print(f"搬了 {count} 段")
    if skipped:
        print(f"\n跳過 {len(skipped)} 段（**沒有假裝搬好**）：")
        for line in skipped[:20]:
            print("  " + line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
