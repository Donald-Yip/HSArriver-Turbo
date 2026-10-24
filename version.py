#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""项目版本号的**唯一来源**：网页控制台、日志浮窗、CLI 都从这里取。

以前 `hsctl.py` 里自己写着 ``HSCTL_VERSION = "1.0.0"``，网页写的是
``server_version = "HSLegendArriver/1.0"``，两边各写一份、很快就对不上。
现在只有本文件能改版本号。

**版本号就是发布日期**（用户定的口径），形如 ``2026.10.17``，界面上显示
``v2026.10.17``。不搞 1.2.3 那套，也**不在后面挂 git 短哈希**（用户明确不要）：

* 本项目是"每天改一点"的脚本，用户关心的只有"我手上这版是哪天的"；
* 日期一看就懂，再挂一串十六进制反而要多解释一遍。

发版时只改 ``VERSION`` 一处，浮窗、网页、`hsctl --version` 会同时变。
"""

from __future__ import annotations

from pathlib import Path

#: 项目大名，浮窗品牌行、网页标题、HTTP Server 头都用它。
NAME = "HSLegendArriver"

#: 版本号 = 发布日期，格式 ``YYYY.MM.DD``。**每次发版改成当天日期。**
VERSION = "2026.10.24"

ROOT = Path(__file__).resolve().parent


def version_label() -> str:
    """界面上的版本串，例如 ``v2026.10.17``。"""
    return f"v{VERSION}"


def version_info() -> dict:
    """给 HTTP 接口/CLI 用的结构化版本信息。"""
    return {
        "name": NAME,
        "version": VERSION,
        "label": version_label(),
    }
