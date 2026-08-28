#!/usr/bin/env python3
"""成品复核工具：对任意已做好的 .key（不限于本技能生成的）做只读检查前置。

只读、不保存：
- 打开前检查该文件是否已被用户打开，已打开则中止（安全铁律第3条）。
- 全程 `close ... saving no`，绝不写回原文件。

两个子命令：
  styles        导出每页文字的字号/字体/颜色（结构化数据，用于字号规范核对）。
                 局限：Keynote AppleScript 读不到 shape 的填充色、也读不到图片的
                 原始像素分辨率——这两项（颜色一致性、图片清晰度）无法靠这个脚本
                 自动判定，必须配合 export-images 导出的每页图，由人/AI 目检。
  export-images 把每页导出成独立 PNG（默认分辨率 1920x1080，与画布一致），
                 供颜色一致性 / 命名口径 / 图片清晰度 / 版式的目检使用。
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

APP_NAME = "Keynote Creator Studio"


def q(value: Path | str) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def run_osascript(script: str) -> str:
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        timeout=600,
    )
    if result.returncode != 0:
        raise RuntimeError(f"osascript failed: {result.stderr.strip()}")
    return result.stdout


def assert_not_already_open(path: Path) -> None:
    """安全铁律第3条：用户打开着的文件绝不碰。用文件名做匹配（够用且不依赖脆弱的路径转换）。"""
    script = f'''
    tell application "{APP_NAME}"
        set nameList to {{}}
        repeat with d in documents
            set end of nameList to (name of d)
        end repeat
        return nameList
    end tell
    '''
    out = run_osascript(script)
    open_names = [n.strip() for n in out.split(",")]
    if path.name in open_names or path.stem in open_names:
        raise RuntimeError(
            f"「{path.name}」当前已在 Keynote 中打开，按安全铁律不能自动读取——"
            "请先让正在编辑的人保存并关闭，或确认这不是同一份文件后重试。"
        )


def extract_text_styles(path: Path) -> list[dict]:
    assert_not_already_open(path)
    script = f'''
    tell application "{APP_NAME}"
        with timeout of 600 seconds
            activate
            set theDoc to open POSIX file "{q(path)}"
            try
                set outText to ""
                set slideCount to count of slides of theDoc
                repeat with i from 1 to slideCount
                    tell slide i of theDoc
                        set tCount to count of text items
                        repeat with j from 1 to tCount
                            set ti to text item j
                            set sz to "?"
                            set fnt to "?"
                            set clr to "?"
                            try
                                set sz to (size of object text of ti) as string
                            end try
                            try
                                set fnt to (font of object text of ti) as string
                            end try
                            try
                                set clrList to color of object text of ti
                                set clr to ((item 1 of clrList) as string) & "," & ((item 2 of clrList) as string) & "," & ((item 3 of clrList) as string)
                            end try
                            set txt to ""
                            try
                                set txt to (object text of ti) as string
                            end try
                            if length of txt > 24 then
                                set txt to (text 1 thru 24 of txt) & "…"
                            end if
                            set outText to outText & i & "|" & j & "|" & sz & "|" & fnt & "|" & clr & "|" & txt & "\\n"
                        end repeat
                    end tell
                end repeat
            on error errMsg
                close theDoc saving no
                error errMsg
            end try
            close theDoc saving no
            return outText
        end timeout
    end tell
    '''
    raw = run_osascript(script)
    rows = []
    for line in raw.splitlines():
        parts = line.split("|", 5)
        if len(parts) != 6:
            continue
        slide, item, size, font, color, text = parts
        rows.append(
            {
                "slide": int(slide),
                "item": int(item),
                "size": size,
                "font": font,
                "color_hex": _to_hex(color),
                "text": text,
            }
        )
    return rows


def _to_hex(color_16bit: str) -> str:
    """Keynote 的颜色是 0-65535 每通道，转成常见的 #RRGGBB 方便对照品牌色板。"""
    try:
        r, g, b = (int(c) for c in color_16bit.split(","))
        return "#{:02X}{:02X}{:02X}".format(
            round(r / 65535 * 255), round(g / 65535 * 255), round(b / 65535 * 255)
        )
    except (ValueError, AttributeError):
        return color_16bit


def export_slide_images(path: Path, out_dir: Path) -> list[Path]:
    assert_not_already_open(path)
    out_dir.mkdir(parents=True, exist_ok=True)
    prefix = out_dir / path.stem
    script = f'''
    tell application "{APP_NAME}"
        with timeout of 600 seconds
            activate
            set theDoc to open POSIX file "{q(path)}"
            try
                export theDoc to POSIX file "{q(prefix)}" as slide images with properties {{image format:PNG, all stages:false}}
            on error errMsg
                close theDoc saving no
                error errMsg
            end try
            close theDoc saving no
        end timeout
    end tell
    '''
    run_osascript(script)
    return sorted(out_dir.glob(f"{path.stem}.*.png"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    styles = sub.add_parser("styles", help="导出每页文字的字号/字体/颜色 JSON")
    styles.add_argument("deck", type=Path)
    styles.add_argument("--out", type=Path, help="不填则打印到 stdout")

    export = sub.add_parser("export-images", help="把每页导出成独立 PNG，供目检")
    export.add_argument("deck", type=Path)
    export.add_argument("--out-dir", type=Path, required=True)

    args = parser.parse_args()
    deck = args.deck.expanduser().resolve()

    if args.cmd == "styles":
        rows = extract_text_styles(deck)
        payload = json.dumps(rows, ensure_ascii=False, indent=2)
        if args.out:
            args.out.write_text(payload, encoding="utf-8")
            print(f"写入 {args.out}（{len(rows)} 条文字对象）")
        else:
            print(payload)
    elif args.cmd == "export-images":
        out_dir = args.out_dir.expanduser().resolve()
        pngs = export_slide_images(deck, out_dir)
        print(f"导出 {len(pngs)} 页到 {out_dir}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
