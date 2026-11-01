#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""兼容层：让「只装 requirements.txt、没装项目包」的旧式部署也能 import hslegend。

背景
----
仓库根的 ``click.py`` / ``FSM_action.py`` / ``manual_controller.py`` 会
``from hslegend...`` 导入 **本项目自己的包**（源码在 ``src/hslegend/``）。

* 用 uv 的机器：``uv sync`` 会把项目包以可编辑方式装进 ``.venv``；
* 用 pip 的机器：``pip install -e .`` 才有同样的效果；
* 但群友一直以来的装法是「conda 3.12 + ``pip install -r requirements.txt``」，
  **没有**装项目包这一步，于是启动就会
  ``ModuleNotFoundError: No module named 'hslegend'``（战网拉起、开始对战都会中招）。

这里在**任何 hslegend 导入之前**把仓库内的 ``src/`` 塞进 ``sys.path``：

* 没装项目包 → 直接用源码，功能与 ``pip install -e .`` 等价；
* 已经装过（uv / pip）→ 源码目录与可编辑安装指向同一份代码，行为一致；
* 只拷了部分 .py、没有 ``src/`` → 什么都不做，交给解释器正常报错。

用法：只允许在**进程入口**（web_ui.py / hsctl.py / main.py / mcp_server.py）
最开始 ``import _srcpath``，不要扩散到其它模块 —— 这个文件只负责一次性的路径修正。
"""

from __future__ import annotations

import sys
from pathlib import Path

#: 仓库根下的源码目录（``src/hslegend/...``）。
SRC_DIR = Path(__file__).resolve().parent / "src"


def ensure_src_on_path() -> bool:
    """把 ``src/`` 插到 ``sys.path`` 最前面；目录不存在则返回 False（不报错）。"""
    if not SRC_DIR.is_dir():
        return False
    path = str(SRC_DIR)
    if path not in sys.path:
        sys.path.insert(0, path)
    return True


ensure_src_on_path()
