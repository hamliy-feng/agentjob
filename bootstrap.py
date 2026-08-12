from __future__ import annotations
import shutil
from pathlib import Path

ROOT=Path(__file__).resolve().parent
EX=ROOT/'examples'
MAP={
    '我的资料.example.txt':'我的资料.txt',
    '求职要求.example.txt':'求职要求.txt',
    '补充资料.example.txt':'补充资料.txt',
    '系统配置.example.json':'系统配置.json',
    'radar_urls.example.txt':'radar_urls.txt',
}
for src,dst in MAP.items():
    target=ROOT/dst
    if not target.exists():
        shutil.copy2(EX/src,target)
        print('created',dst)
(ROOT/'data').mkdir(exist_ok=True)
(ROOT/'data'/'.gitkeep').touch()
print('bootstrap complete')
