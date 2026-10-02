# -*- coding: utf-8 -*-
import json, os, sys
sys.path.insert(0, os.path.abspath("douyin-downloader"))
from app.core.browser_bridge import fetch_aweme_detail
PROFILE = os.path.abspath(r"douyin-downloader\dist\edge_profile")
bit = fetch_aweme_detail("7686440597854440730", PROFILE)
v = bit["video"]
br = v.get("bit_rate") or []
print("条目数:", len(br))
print("单条的全部键:", sorted(br[0].keys()))
print("play_addr 键:", sorted((br[0].get("play_addr") or {}).keys()))
print()
for i, e in enumerate(br):
    pa = e.get("play_addr") or {}
    print("%2d gear=%-22s qt=%-4s FPS=%-3s rate=%-8s %sx%s fmt=%s HDR=%s size=%s" % (
        i, e.get("gear_name"), e.get("quality_type"), e.get("FPS"), e.get("bit_rate"),
        pa.get("width"), pa.get("height"), e.get("format"), e.get("is_hdr"),
        e.get("data_size") or pa.get("data_size")))
