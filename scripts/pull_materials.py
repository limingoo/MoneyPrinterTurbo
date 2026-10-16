#!/usr/bin/env python3
"""把服务器上的素材批量下载到本机。

只依赖 Python 标准库，本机装有 Python 3 即可直接运行。
从服务器前端（默认 :8502）的 HTTP 接口拉取素材清单，
再逐个下载到本地目录，不往服务器写任何东西。

用法示例：
    python pull_materials.py --base-url http://<服务器IP>:8502 --out ./materials
    python pull_materials.py --base-url http://127.0.0.1:8502 --out ./materials --status ok
    python pull_materials.py --base-url http://<服务器IP>:8502 --out ./materials --trash

若服务器 config.toml 里 app.api_key 非空，需加 --api-key。
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

CHUNK = 1024 * 256


def _build_opener(api_key: str):
    opener = urllib.request.build_opener()
    if api_key:
        opener.addheaders = [("x-api-key", api_key)]
    return opener


def _get_json(opener, url: str):
    with opener.open(url, timeout=60) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if payload.get("status") != 200:
        raise RuntimeError(payload.get("message") or f"接口返回异常: {payload}")
    return payload.get("data") or {}


def list_files(opener, base_url: str, trash: bool) -> list:
    if trash:
        data = _get_json(opener, f"{base_url}/api/v1/material_trash")
    else:
        data = _get_json(opener, f"{base_url}/api/v1/material_library")
    return data.get("files", [])


def download(opener, base_url: str, filename: str, out_dir: str, retries: int = 3) -> str:
    target = os.path.join(out_dir, filename)
    query = urllib.parse.urlencode({"filename": filename})
    url = f"{base_url}/api/v1/material_preview?{query}"

    for attempt in range(1, retries + 1):
        try:
            with opener.open(url, timeout=300) as resp:
                total = resp.headers.get("Content-Length")
                total = int(total) if total else None
                tmp = target + ".part"
                done = 0
                with open(tmp, "wb") as fh:
                    while True:
                        chunk = resp.read(CHUNK)
                        if not chunk:
                            break
                        fh.write(chunk)
                        done += len(chunk)
                        if total:
                            pct = done * 100 // total
                            sys.stdout.write(f"\r    {pct:3d}%  {done // 1024}KB")
                            sys.stdout.flush()
                os.replace(tmp, target)
                if total:
                    sys.stdout.write("\r" + " " * 30 + "\r")
                return target
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if attempt == retries:
                raise
            print(f"    第 {attempt} 次失败（{exc}），重试…")
            time.sleep(2 * attempt)
    return target


def fmt_size(n) -> str:
    if n is None:
        return "?"
    return f"{n / 1024 / 1024:.1f}MB" if n > 1024 * 1024 else f"{n / 1024:.0f}KB"


def main() -> int:
    parser = argparse.ArgumentParser(description="批量下载服务器素材到本机")
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8502",
        help="服务器地址，例如 http://1.2.3.4:8502（前端端口）",
    )
    parser.add_argument("--out", default="./materials", help="本地保存目录")
    parser.add_argument("--api-key", default="", help="服务器 app.api_key（若已配置）")
    parser.add_argument(
        "--status",
        default="",
        help="只下载指定状态，逗号分隔，如 ok,low_quality；默认全部",
    )
    parser.add_argument("--trash", action="store_true", help="改从隔离区下载")
    parser.add_argument("--overwrite", action="store_true", help="已存在的同名同大小文件也重下")
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    os.makedirs(args.out, exist_ok=True)
    opener = _build_opener(args.api_key)

    try:
        files = list_files(opener, base_url, args.trash)
    except Exception as exc:  # noqa: BLE001
        print(f"拉取素材清单失败：{exc}", file=sys.stderr)
        return 1

    wanted = {s.strip() for s in args.status.split(",") if s.strip()}
    if wanted:
        files = [f for f in files if (f.get("status") or "ok") in wanted]

    if not files:
        print("没有符合条件的素材。")
        return 0

    print(f"共 {len(files)} 个素材 → {os.path.abspath(args.out)}")
    ok = skipped = failed = 0
    for i, item in enumerate(files, 1):
        name = item.get("file")
        size = item.get("size")
        print(f"[{i}/{len(files)}] {name}  ({fmt_size(size)})")
        target = os.path.join(args.out, name)
        if os.path.isfile(target) and not args.overwrite:
            if size is None or os.path.getsize(target) == size:
                print("    已存在，跳过")
                skipped += 1
                continue
        try:
            download(opener, base_url, name, args.out)
            ok += 1
        except Exception as exc:  # noqa: BLE001
            print(f"    下载失败：{exc}", file=sys.stderr)
            failed += 1

    print(f"\n完成：成功 {ok}，跳过 {skipped}，失败 {failed}")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
