# -*- coding: utf-8 -*-
"""VNEDU desktop control panel.

This launcher keeps the existing VNEDU tools intact and coordinates them from
one Tkinter dashboard. Each tool is executed in its own process so legacy
Tkinter mainloops and module-level state do not interfere with each other.
"""

from __future__ import annotations

import argparse
import atexit
import base64
import copy
import gzip
import hashlib
import importlib.util
import json
import logging
import logging.handlers
import os
from pathlib import Path
import re
import runpy
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
import tkinter as tk
import unicodedata
import uuid
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Callable
from urllib.parse import urlparse
from urllib.request import urlopen


APP_TITLE = "VNEDU Control Panel"
APP_VERSION = "1.0.0"
DEFAULT_VNEDU_URL = "https://vemzezsoasgdsoctrang.vnedu.vn/v5/"
DEFAULT_DEBUG_PORT = 9224
UI_FONT_FAMILY = "Segoe UI"
UI_FONT_SEMIBOLD = "Segoe UI Semibold"
SURFACE_COLOR = "#f4f7fb"
PANEL_COLOR = "#ffffff"
BORDER_COLOR = "#d8e0ea"
CARD_BORDER_COLOR = "#b8c6d6"
TEXT_COLOR = "#111827"
MUTED_TEXT_COLOR = "#475569"
SUBTLE_TEXT_COLOR = "#64748b"
BUTTON_BORDER_COLOR = "#b8c2cc"
BUTTON_HOVER_COLOR = "#eef3f8"
TOOL_ACCENTS = {
    "nhapdiem": "#2563eb",
    "nhanxet": "#16a34a",
    "locdiem": "#d97706",
    "sodaubai": "#7c3aed",
}
ACCENT_CHOICES = {
    "Xanh dương": "#2563eb",
    "Xanh lá": "#16a34a",
    "Cam": "#d97706",
    "Tím": "#7c3aed",
    "Xanh ngọc": "#0891b2",
    "Hồng": "#db2777",
}
TOOL_STATUS_COLORS = {
    "external": ("Đang dùng: File ngoài", "#e8f5ee", "#166534"),
    "embedded": ("Đang dùng: Bản nhúng", "#eef2ff", "#3730a3"),
    "missing": ("Đang dùng: Thiếu", "#fee2e2", "#991b1b"),
}
EMBEDDED_STATUS_COLORS = {
    "ok": ("Nhúng: OK", "#ecfdf5", "#047857"),
    "missing": ("Nhúng: Thiếu", "#fff7ed", "#9a3412"),
}
RUNNING_STATUS_COLORS = {
    "running": ("Đang mở", "#e0f2fe", "#0369a1"),
    "idle": ("Sẵn sàng", "#f8fafc", "#475569"),
    "missing": ("Không rõ", "#fee2e2", "#991b1b"),
}
CUSTOM_TOOLS_CONFIG_NAME = "custom_tools.json"
DEFAULT_TOOL_OVERRIDES_CONFIG_NAME = "default_tool_overrides.json"
APP_CONFIG_NAME = "app_config.json"
CUSTOM_TOOLS_DIR_NAME = "custom_tools"
CUSTOM_TOOL_ID_PREFIX = "custom_"
VALID_DASHBOARD_MODES = {"basic", "advanced"}
HEALTH_CHECK_CACHE_SECONDS = 60.0
HEALTH_CHECK_DELAY_MS = 350
TOOL_PROCESS_POLL_MS = 1000
SINGLE_INSTANCE_MUTEX_NAME = "Local\\VNEDUControlPanelSingleInstance_v1"
TOOL_PROCESS_MUTEX_PREFIX = "Local\\VNEDUControlPanelTool_v1_"
ALLOW_MULTIPLE_ENV = "VNEDU_ALLOW_MULTIPLE"

_logger_instance: logging.Logger | None = None


def _get_logger() -> logging.Logger:
    """Lazy-init centralized logger with rotation."""

    global _logger_instance  # noqa: PLW0603
    if _logger_instance is not None:
        return _logger_instance

    try:
        log_dir = app_data_dir() / "logs"
    except NameError:
        log_dir = Path.home() / ".vnedu" / "logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        handler: logging.Handler = logging.handlers.RotatingFileHandler(
            str(log_dir / "control_panel.log"),
            maxBytes=2 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
    except OSError:
        handler = logging.NullHandler()
    handler.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))

    _logger_instance = logging.getLogger("vnedu.panel")
    _logger_instance.setLevel(logging.DEBUG)
    _logger_instance.addHandler(handler)
    return _logger_instance


TOOL_FILES = {
    "nhapdiem": {
        "script": "nhapdiem21.py",
        "title": "Nhập điểm giọng nói",
        "description": "Đọc Sổ điểm, nhận giọng nói và ghi điểm.",
    },
    "nhanxet": {
        "script": "nhanxet_pro.py",
        "title": "Ghi nhận xét",
        "description": "Tạo nhận xét theo rule và ghi vào Sổ điểm.",
    },
    "locdiem": {
        "script": "locdiem.py",
        "title": "Lọc học lực",
        "description": "Lọc học sinh theo học lực.",
    },
    "sodaubai": {
        "script": "auto_danang8.py",
        "title": "Sổ đầu bài",
        "description": "Nhập Chi tiết sổ đầu bài qua Chrome CDP.",
    },
}
EMBEDDED_TOOL_PAYLOADS: dict[str, str] = {
    'nhapdiem': (
        'H4sIAAAAAAAC/+y9XZMb15Ug+D4R8x9SaG0LkFBgkbZsd8mQhi5SFtsUyWVRsnuLZTALyCqkCSBhJECyulwPHX5wTHR0RDt6'
        'JjY6NjrGWq+jt6fbYfe4JyZWfNgHKvw/uL9kz8f9/shMoIqiPC7PNFXIvHnuveeee+655/NoUUyTweBotVwtssEgyafzYrFM'
        '0tmsWKbLvJiV//7f/ft/J56WJ6X6e3kyz+jd4M5H1+/84OaDwb37dwd7dz+5v3sz6SdvtVqtT+/cvPFJcn21LJI745ef/3qW'
        '/ODFPy2TT6/1Hs4ezvYeZ5NsWcyS6cvn/0eeDMdFsiyKSTLjps+w6XKcFcn497+FFrPjnYezreT6cpkOx8kkf5Ilu2MYfJb8'
        'eJXKP29kTx4AjDK5tyiWxbCY9PCbBy+f/yv28l8SHtGTF79Ivvj5Fz+dHXNn82T28vP/d5UMX37+jzP64ou/ffn8b4bJcgFf'
        'wn9yeD1PhsX0sDgsniWPvjd++fzn+ZXbMKz5lY9f/NvsykfU/vHL5797lCxfPv9ngPX812lSvnz+n5NHe/jvFz/PXz7/6fQR'
        'wd8dr15+/k+z5PDl879OHiP4GXT24vMhoeGQJjyEiSEOyuE4m6YI8O+XNPTjcW4haTh+8evZGP7+bAi4+gxnAMhHFB/VLq5q'
        'NSxmy+zZcpIfylbiyTSdpcfZQjQbpYD+SVqWWSnbqUfd5CjPJiPdMlvmsCa6Gf1+OBMPflQWM/WjKMVn83Q5NgZxD36KNz9e'
        'ZSsF7eZ0vjzpJv8rPlNAFvrPcrxa5hP9c3U4XxTDrCzVo+V4kaWjfHasn5ijWz7OYfqLJC3hTzEC+Uw0mQI4QA1QRDdZ6kYn'
        'cwAq2+ymk0l6OMm6yY18uOwmt+D7dFksusntvITfD1bzSSa+XC0mMPPePF2Uap4/XhXLbDCfrAC78J7e2c0XGSCmXMoP4Gkx'
        'z2ZqXeeT9OTpIj8eL3vlyWw4SOe5wuFiUdAE76k29KgLWD+GEVN7/f1DAjrY271/696DwY1b92GX4/K0gcDyCZBXB8ZSFpMn'
        'WbuDk8hm8Mnu3Tsf3vru4MNbt5EpmB9fSVqzcTp7li0HT64NgNiO8uMe0gRQ7o2bH17/5PaDwf1Pbt8c3PzBvbv3ZYcRCNN0'
        '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'
        'EkqqRkXnpYmEoTPud5q6Cao4FM3nQpQ0N758iMs5A1sqmFAqGTa/IlZfyVDn6YhBf3Myr59LCc+/4IvJuOac3zD39RX1eq+o'
        '8xifv7Ib6qKtpmUX1IWYLb+K2yd3i5VcFHurJixfoH3KeCdYJvnKjFNfc5SL4ijyE/wrEoUvytSU5BSv3MxUxSj0aPu1cxJj'
        '+WYn3su34XSCuejFF79K+vOGpqgvf4TORj/poln+Z8cBA/CM4CWbk04YFqUKww9BXrCi+vKlacL8yvIVbF8VasvTZ8cuto7J'
        'Sq1xrC5ZzHIn7aWCRlLi2X8CNov7MSWOikz0Z+A18eUfwgIDjrvz4vnfw7d7A+Sz8OvZrwZNTelb+50Tsij+j+woQBN8AKqB'
        'zfmfP9MgoGYrGFtUbYIM783xoNfH5REsa2I0RsO3s93ZsMvlMgxZvPjib3BQNEPWe4DZPVCX5sjwf47uaX990jRLdsuwJhLB'
        'BnUPsyAlLKdHzQoywrZgp1QuBjICAtTDuEyTWVHZLJIzKZyEoAylNVdnIsENpRq0sk09x7WIwxRlnE1lnU1kniXMhUMVYHE+'
        '5sj4NFzCaBkiU3wKjUEpZ3zK8u1XJOMFGBWS+1ZL8DsnKqPKcWp/CLv8et52se4UKY0zM0nZmgq25Vx4ciievTeMXSXan0Yq'
        'HbW/XViLqI1pVZDT0imp2wHMHiiuPBq7KYrzgtUVgnXACZPRdvLkbErHTULwR8NKG8p4D9b23U4XrpqpX7ArHtyhhqKsVOG8'
        'kmOlJpdyKpj3zCRdM5LuGVYwHe2NkWd01YuxhRWG6Ks17aUnJakW0rmE2ChfXEjosfNEI55gisbndmSohtrIr4HRKOEk4YxQ'
        'sYPndpNq7Rj9W9kuCF0oT/vW//PuqNdXiYOGZL78oX5Z/kW2iQ9J8/okyYgEpKnnlro3OPt8BKmcQK6Csgy1xjmP1LncU6pp'
        'tqOdLfcrKfH8SHtoRPruYI9Sv8fua8E9XD2b9iu7fUOWkvQ5K/E7K/I9e2lWxZe6kLDK8UcTL/D50pWXXdti0njxpdkoJBKP'
        'CqqmaBao4HwXRwXfPvlCqD5Pi8z4Ukigobo/pYCIr4SiXyv250OPxX8J8WYy7xOu8sgb9q1s8eL+R/oF1JJntzYegsY8+3B9'
        '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'
        'zBK8O6LhqmjkqWm3n0HPeWRNZ+B5byZh/9mOp1GXXXH8hXtl5e3fvASICcctqQTHrXZu/54fjl+D2/1mflhOSWjzZwKKvwca'
        '9eM+vIwm59gCMxVGJ6om7b4SZryv9BK9L2m6VjiUCUOwhmrzrWpWGBee1S9wpnkFEvTHwjMszbkZzyoIiCJ+VaMJOv4eA9AW'
        's/xjsbQ76B/2FFeoR0srn4FumctTUAg51yQsMsunYdvmjQRWNcGUwlINk+RvV+D7HqbVwpKIFHPxgLcK4k0kNDnGykPIK7wP'
        'pUFQy2x1z3NKS/El68pK3nXjDCi4eIhOmUlfD2503O8K2XZdPqwqH7JypSG1Y3LUFRKrHwAZmqYqDW8eapYhuiXpo2XkYawh'
        'NRtM3FE4g6TBVzWfFqpVWeKu6yj/SKXkDcpCgy04+XJOvPJZ6vVR7nNOJJONfmVQboseI1e5pzs3Gkj4yYunyLYxz20w7/3V'
        'Fq+FtnyDmYU4x0LNzQ5iZmvHMHG57hDGXz64I6HblCLRB/496Wd4cDFNGR5oAjXtYBXcUfK331iNpi8qYNLnpzk7xq/rz9Qp'
        'do7faV4MKsmWvQ4uqyhsEhdYCBo4QpPQsFZXbqhuwP4hhn83m828UuXueZfm862SRgXLE2TLqgs8AvVM5fVV8uMnr5SYOBWP'
        '98jQiC/ytw5x6u/mJU5K4K/p8es9C/fMfSwkcnqlhAFzFYU9XnEqp+Rw54/9KFuHoG9OJh4rRZdp+orxFI/zsggSZi5gRpVi'
        'hf6YfLT9eDAZ7Awglu9kDhMra9E1CC5z4YhFWKDA/5UCopUvCQZkKbEQv7Qz0QruPJ8fE67c7JZJB2z0iHlYLeuQ/UpIpQtz'
        'OVjjb2jlpN2kX9aoP8baHnUErccjUnp3SL5UH1GWRSN+r65fQmoO/DvYRQ/6J6sq3A2VVC36twlvh13OLUI6K27dxCSBpGAj'
        '1svfQX5RyHozGE5V2m6uUpHVIHB5DIax2um2pGV28VENC6J/+2Q2oTg8r4Q6nUXME7hKZuJSmvE6G84KeImfUFWUzlrCd3aA'
        '55ogAbXsXJCqta5RoRpDFQ87J5TQxVUdq58VaJi46ADAwPR0kotOGG71VEA7TB9RLxyNG+mHy3FsFccVVlGsRJYHK1cte0I/'
        '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'
        'i5MA750NkUI3oRe+bTgJTFEuphBg7kK6BF3USaKrzIG3t686cJYI6Ernbm3ZuWVKrtG3sqNvzTW6c1Oi9zoDdOLm7LIC5+4s'
        'XjuVK1HFOANaWLDaB/FLYvjgMwhm6rZnw5lXQ8mq0V3O1ClEzsT04mIcXKrBYD5iJGnisXWOmRNuCJIZpxHnyVZJBhCcIUch'
        '258xrAo8rMkAHIeemg1qrKDWTFSmYU/YpCxDlJDFLeaXOBxSMtBdL5umi83rD2rZJ7JBnQIRv9dexanbyZNYC41kX4C5ZV7c'
        'CiwvpcI7zJCrncOX9LrVz95eqawG9x++hww+je/MujiN5JzEpallPZkml8OUFQw73ToVSP6UCo6j6n2fqldPO4iLbtpQNwTU'
        'e89xoqBHbSCumoA+Z9PpVPrhvLN3eOdB7c7enQf09/dqB6glvrDjKgNvJq+kbFITbsDnwL9ScvMZsHJN14l5WnZRgpEO4ZbD'
        'xMW/Frt4SfAlZ5oFDUpxMgfp54Vq8vtPn7FrV7NLReJFDW+R6hir+QvUERnPzqgu0McjeHzBpN2YcUZK4olIXkhF2JFWAx1g'
        'LEDlyqan9FITNb08a3Bk0/84r7LKHN0L6Z0S4C4I8Nn7SLTZl7ux050Wj85NkWmIkxRhXZDUZSw5ZJhpQrl/KWPjam5WjXTx'
        'zL1jdvIg3e9OJq5E7joq1CYuwlc08mAsbpXWrNdLd6m0KNJHzY6Y1cg5cm+THJCC5HDCVeeB8p+Jo7Q/8tEc+4ympjr861CN'
        'aIUS5pIKHN4pti01JUHBo11QxOd5nDi4ZN+TuWmG7GvPcjTj1dWrlmSXyPCuMTKVWkPxHkWDpUJSVxWpLRXfkpc4if2esYwn'
        '0pVkfmu//Wo/8kRjGe8Pe1SIsK69WEBuHwlWr0CC+afv8z1YeP7Xmrs4PIqOGf7gWKgFeYSCcB0+oWA4ecUVDwrUGrnnZGfL'
        'Qyc3jazVnJtIWYtf4uqXDnld2jnpZkx2I+N3borK2YPAroq5cn1KNoPXZujn4MS+E+E7EI5hfDSbl24GMCcBC3aMY/reY7Yg'
        'Y5yTDEVHb2FS5J7DDDnW57vW/S5RjtELKFfMU335YDzkyyPdlcjnJV2GfJWlHOe2ZNBcf9T895HRlSBmxaYj38zkYMe2jsLC'
        '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'
    ),
    'nhanxet': (
        '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'
    ),
    'locdiem': (
        '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'
    ),
    'sodaubai': (
        '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'
        'e6JNaLmuE4XoHTrd1me6Fcp3NKSmq36EPt1fgOz57tbWQ8JJ6h7GqB0NC+BZ+SCK+m7oxLAbBRqaCQLmXrKGbLoHjNatB/cp'
        'q+Igh1OkSh1poTio0x+MJiH2NR4MNRT8PYsW8XfZAdwjGfbMEJ6C27PKUxUYbwTH1PbpVREcosqzp0nyjBYx9Br0kK8jPJ+E'
        'M8SAfyyLsLKHvOBY+G7Q6ZD8ixc3HUYkCPujaBtP4dVSsMV3xNKOVaauQIUjJXlK2cuSKKTskpgCQcAlVLXEzYOaZ3xLuAow'
        'd9H2FKLfb1ZEI9AfdQrF25XCQlEFiIgG5zjQ+8K0n3BEGPVvPXn14n9tYmQakYIuyBtbDEkfjjZ/90VmOttc5SWLtQwpJyNC'
        'khVB3bKD7OwI+B92u5GMyFKyyP6cYG+CpKbhIs7RZP94RdcUGJYGCArIuxqTCY0skFE0xGp3o6woo6aJGGHaB0Pxs5IYH8cD'
        'rcgwsbApS0KexGFEKzJsLH2RrK38FsfxrKYvdtWJEk1ouR+ovC3SqDHNg+BfxoPVJoyMmnZiotQPe6XUFSDdGubqZfXImZxZ'
        'lCdwlNwzOaXnM0kKYe5CTnaI5avp9BCGEMIQQLp7mBlHeKgGEr76HkaqBwrJpvLt09X3jXGFQw77LqdifJlIccSJCrpURMZz'
        'FfGA9zk1FLHWGDP69YXyJWRDzN827z4LmYXKcGQPIXJuMwn2a9n+uD9EQHlLIdHfEkeYr8Grd0S2qt6hpRrvFBGgnv6T1fbA'
        'QMD5EDWGMlYQIDsKcTIZlODLWPB1RyTGZUGkBl6DIzQln4Y+sV4oPlcQBtXSGBoSw7ZjeOeYTg4x8DjkxUlnThiLXaw2C/CR'
        'niLAFFKm6ULDFlg+RXFj6B9CSgjoVfwMfcZROcPCwhhjqyIhpDqpzl4dZIJ+qBUdWz61vHnl+Ag4U3cd8EAp/AKKY0CmJY0f'
        'mseM9uYxo+cxo+cxo+cxo+cxo+cxo+cxo+cxo+cxo+cxo+cxo+cxo715zOh5zOh5zOh5zOh5zOh5zOh5zOh5zOh5zOh5zOh5'
        'zOh5zOh5zOh5zOh5zOh5zOh5zOh5zOh5zOh5zOh5zOh5zOh5zOh5zOh5zOh5zOh5zOh5zOh5zOh5zOh5zOgpEOAbEzH6GxEv'
        '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'
        'K6NnmrKXhtdL2ZdAyVJvOdbzX+Ytl6V6vvhguE9tr6ys3Fxplu6srWEtK1Wm50VA9C40UMk2UqtjCmm4zEW1gS85ycFkXkx5'
        'tGcDYC7oCJzBMa7nFKxJAzW8wYNTVcH744LcWrS37MXKI9y8BiyGDc6K/pweFq47QPEA6eX4Nv5Tx4mmBJHVnsBMuXYVPlS2'
        'NB985/tKPrdKS9XzfSjOVHXLOTzYHv/iyF3SZtncDbOsP/dMAvsPk4hURzXFyeTYt8B0S1em5CK+NKpIpPmrdoIOyII7Z7fd'
        'wyIgtg4JlFzUpVJHKh0XRXInAxK16NvcReIHyX34j9HlLLGVLiVr2jdGfb3+JRhgE9i89Y2xlyfcR/nVCpOkSTItPuZn7gBd'
        'r6XfQ7elrJxvDXxipRQjbGUz5Ucnstuw1IqwAOoSfiW/Oq+Wr666gMr65V9bZnG9tX0Cj4gPky99Ap8NT3rJqgS2ZidJAgA4'
        'mKF61n1vwqEcnjfTk6+13PVnHM8USKLlFVGwCaRFMm5cNOF9u+0bdW0tTJhnHawKV4NePGM3td6tstR6MtWnGtfxn8aLrNjj'
        'T54ifXsxGu9axZ8FORlOItg9kMLQ8Ww63h6cIOP65TeJb5GvzfwAmNkc/3rzPue+8/XpNNbt+YTAFQakLpxDhnUeVsBGuXcG'
        'aVxrPv5WAxPw7aPiBggI6h/B4kn+w/l0fx9g1qXRdP5Cp+cu/AuABke7YFs4Ge0foD34eLD7aG21rayLt6c89dwaPXz8/hwR'
        'CL/n2ReQ2wsu6gQSPhw1dind6gjU5KllmoIO0e28wnoFFjJaPJJQ/8DJqf270cBCBj2bn45haV6BpRnj8nQ3uNOwnL8R03jI'
        'fba0dPK7Etbi7RlMgLJO1prvFCoLQ8kPwHiTomJkmqUUvIEP8XcQP4jvXX0TwBCFzeG6H3axSoJID4a4SGs3AWxmDxDy11rN'
        'O3COpjAhoKvPtqPF9W1ancY7J4NjmOf0JHJ32t5fA/lkiP9DRzT8tEP/hAXZaLcN64avSdiS5lN79E9TX/qdE2AZeIaZptbN'
        '55aZ0/RkBP9da4JwO4dn6rhZKTGKRxCyfN1TlI7b+toaQ98pj8jWN+NB/XSqqnOIx8M9PMN7o/F4rbk9nR/Ah+GjY2wKobq1'
        'mWs8gOufBmbGgqLEvQUszcO3h1CSEtoGBPbyAZ1pU7brXji3IQurnv4ZINnAsAppmTFjKFQP/Bawu54k0EU5zQhW+LfRAeDf'
        'LCR443HsmSPobD44QqweTMiFn7tgdGyR0WkPP7aav/NK73de7f2Ol3uZgYF0lB8eE4ztg//zX/6/f/5m5EBnRool/u5rMf61'
        'DB5//s73tQZEQoPfv/sXTT03EI8PrXV2oCQH07KRcc1BQWAXEHCksMtxDsE5XPMcHui8H+xWkafZpMOyVzh2UUiVnA2HLePZ'
        'XKHf2CjOLPLlEd0rqDCZNG6trAAA7n+b7PsrYF3J8OUYDHd3+IiGcX11B82RoKWC0QMK16YXg4nxl0VDL2BHmoxYdcmiCoHD'
        'N+QR7q5kK5QtjtwdF/nHNISApXdee/jw3p2N+689bDx47eX7dy6xE4f1AF4eyB8ELwzyoerasIFx3hy78V2OBP0JWnqwCmTv'
        'HKIa6R8m+PD8M3PteyzpDZKK40yOJw20j/3TDjsGwSPW78D148DHLRCc0+eEQ1OLCPRugiWNjsgcMJ25XuaT3RGyJwUcYoqi'
        'wfyA9cbwYTh5ewTqQdZ5vH4y3QfAo5fARWPGJnX418vT6f6YLOwM0ox/3T5mzetoSv4nxbijZIGV+2s9ev659ofR6YPX7tx+'
        'cPv11+/e3rh9Cf1tBdknoV9UwRd7ECM1Uhl8uphxjmZwbQLuNfzdTiNV489Rfgx+1jn0jm4wJsW7SuyFcyoeQiKeHfTNQkL8'
        'BihV2PsKzO+KOIfGb9M42b3v3nvp9psPNvrQVP/1197YaEcDDEv4cgG81/q7w+0F+K+dTHERzFE9Hu3OtOE/QPX2nMXu+3fd'
        'E/Orv0IgQYYqX8CFcR0xSK7vjk4a1EHjZASneD81PTlGYPiGgeB1G/30pdl0oh479xblOWCna3Z3kVXvQHRdy/0C/2jdeeWN'
        '1169179778U3X+6//sZrL91/AJ/uv9FuA5wdhLvvAPe+Qd52b3nCBGqcwMVlHjtiNv83HD6e+5eH8+t3Rkf3J8DOEanoi6PJ'
        'Mzf7r/PkGtfhzMELuPFWE8GxoPi1YiWvvdWMFVnNdfINuP4aOwZIO/chjdsdFjgfwFX2H6JaMhzQK04nKAdvTK//Pqxj4zrU'
        'AoEBpBo9LiDyG8N2yG/b7E8X1AqK4hAB49+BcImD4Zj9ix9OX+ddoU8yWHysmkXU4ON2BseEQjhdzAF+bE3JVWMfh6nfQDSC'
        '2murKyX5ovdoal0+LTvoJfgpwKRMc4HQmedk8A6SATUym+9Cn1k3KIlpg1rVu5A0h3gAuqghnbWgetzuCPxQmdhalGUGfAR3'
        '5ho/k/Y28b9BV3jsE+nNjKcS1tISqx3tipdY4WrukKb1DlO9/nBJoIFeChBSy/IrnfbxNGunHX7LH2OfBCzjwMtDmk2F1CcB'
        'fWUJRSotFsIe12bWkCF6y9YGaHjv4aOh0/gCplKjv9ulfa4kbe/4+wsqVbsbb3y+4O+2DvDOkT548WD5HJagDl6+ueVdP8AB'
        'we8LDcjaHWR0INiTfwmh19R/RM/lX0jW0YYwJcljkbhxOsZAiV5TBw2KoP8efJRKNqS+7s2EvMZz1zbTCjaF5BPYCYR2JRMe'
        'HjY22IXhzyqFVeXBzIcRhPZwxFz4xkv0b7ixxVMQtziVhKAa8y1jwFkmnDCdEGbCvAVHZjDHQZ5gOtpmwZoprrKNto8UKTtL'
        'r4PHZpwGgHvDS92N/AgGwgVq+LpSqlfbaO8yjPUCaIHvoK+aEyD0i/LYGWwsA23abgFt8/kEAvsFHTEqIJPHrBvWpNikkOxR'
        'nTMbD4fHrZXu87EsW1T0pdjBAhSt/ekJ2LZB8jQi7AAgm/uz08mOJsf+6j8BEziiQPWpWcDbUEEem7FYTpGms9+8/z+ADYLL'
        'Q20mEUiqbvIr78ngzBqz7KBuCgrizwcAVwDQfmC3BK1f4D4Gqxol2ZFFo/NJ7XQabuZp2h4R9vFBZDMBBw/2dhSQ79TSu2QI'
        'fYu+4uK4ZZ/1n2rGQ5SbgeUSOSf5V4s/4KK+agAsuYKK2p6KCoSNo8HhEMSFWfIRgepbHOj0MFLgFgz8dTQcBId+UzlGxYqr'
        'QO7Xr58Mj8D59DqNdx+O8XVK4nSG/z5vJup4r7S1s9RE1PpQfTJ85zoAK+xO39EKfOHhvbtv9tfXX+ujculh/803HgSlwvtn'
        'B2yNyEb2xoP92ZqzSHfvbdy+88q9uzioO/fW1+E54/x65417tzfu9R/e+6L5vf/yG6+9+br3sPEv4X6R5fuZlXaOCd1SEl9V'
        'PJKpY5k9mvpB2hOtCDMhMswJIhDxEd5lCVJyWFRjNlg0Vm/NusGxIqYJpi7hm0JcRDoaq3wV+5MB3FUFK1RngPosQYWesu4P'
        'Fqd48H86cVnHardBOsAscyiK3+w2Po9hWsBUTga8BLQwyBeGZOrmpWixEo/d4ucHEMd16ORsGi8QB6xo9ZkuGDCwPLqvZeVI'
        'UXXMT4bTHvniW1nUPMDoc9H0s90GLSJH3EnbvGbcA5I8eJT6ahPutmjlVhcREn75XZ6xu88I3AAazJ8zdgJaRYt55tWWl3Nh'
        'ubr7F8kovNNY7VXapEu77QoRyp4WXlv3iISXeMddKcpbIdYg3a8DhiWJWgQKphWe4FKHjWKBbvZCon9y93KxOEvdybrnDjU6'
        '3h7TurDjjDFhJHQ1YD/7VkMZAO8XkPfj79vjIbp71ieCA8A3QIV1fEqor5MD1R0OOPvQmI5Poljf0p3JTEcG/81fSLaUQ1ia'
        '743saXN5IxORO5QpXTRNzWvAY8AZ80/sP7M4Rt12f7QLJiZ45838U+hKxQhf0gr8cFANzW4/DG5iuNguhRXleR/yGrqBjoh/'
        '4ccdy5VQKI4estFGTQ9lcHxy7IcFIqB5jKZUsm9fSYUFguS1GLUYh6RnDjBXl1blCJeOJvB50p3LKo2jMVwbZseivrysEMA+'
        'aSrxGcc0HG/80m67ygnaeOC/XSaBFqe1W3PpBVSHA7jJJyy4cvhqy/coUIfPChvDyIUjBqqbAbou7BySbHJ4MPKpzQC4hZNs'
        'gTsWD7xd5o0AFwPJVWLBc+6A6AUX+p/swXW+IdxpNAkY0Zx/2H787pTR54Lq2EWvQYhH1BvYW1Imw0rnPzYwV7z17spK+pN1'
        '37XmVVC4NlzCdacoVso46h7dB2fY3HnANE98xhdO9QEtnNmhvQG82HZ7RUthgBpTLFwfhmxBwatJus4NwzzOE9XQXEdhlTvj'
        '0fH2dHCy6+sAKr3pWU0uT60Ht998eOeV/p1X73b3cLvnknoX/tVxddJrSUNVzmRT3BZ2wOCiBlHDrXZ5QVEFoxI9VVi7g1Ib'
        'Rh7kuAPo1n60e64JWiXqJG//aY+UtvwHjkPB2nZ7GVx5p3GPIeAW5PTTBkugDZJAJYbH8CANprD/yu11tLKmRS/AoFiMh0ZM'
        'k+R/QHGHkUSUFrouJreEwm5FOcVZx0uUUio+KeS6wWdAIMaUvhiuVBYMYlYX3GKVrlDqusrlGXdX9+r0iNNQLBOpc1eGdAr3'
        'R57Rk8kCB54oJnsWiRlc4mhwcghW3QMQnJCfgW87YOPgOTxA0tqNxRtiR32KxWA7gqIpGYDZc02XjJrEkgE7ZuC97OXGRuHh'
        'b0cNGUS38crjH55KdPwmOxO/BIPckld5U++BszmCLPHusYHKoWiVAwKcvdF4/eDxDyR+f34iLzsUa2RHaDHg0Hfj5tvxZCGK'
        'Bc7iTmotgIn0XbdNv4RG7bwX8yEu8AnqNvfR5t+XhB8YPCORCVXvbf1dBxf5t77JUULOPvT0qx2GMJw0y4Zen+1EVYu0qtV9'
        '97AuKHGOa1filUbrPPzruHpdiv8wVCLxC7TmQy10TUI51lKZuRGfDxnK0XBtG9z8Ww4VYPP8EzffzvtFFIP78mK0c4iZNdGv'
        '2hthK1mHHmhArqNJhQoivaJHomFCriznyKh+7afIYHOdQifnhdjOcWI3OILXzWei9Y1aUNwzhuSb97FXxWWjQB6RbWbvXGv+'
        'U1S8tn2uYs8eWsTgzFieGIrQFfgz7f6FT2xO9L7Y6TTkXo/aS4j9YrRezilLKL0uoVeh8/TDRZFjnSTRLMp6ATsAl/azuXcJ'
        '4vvT1cZUISUpgxrsceru+NJ0243wyt4zJ0OI4oKLZsTW+CsXkBbykkJSSqDlprfwJFoisCLARe2ZBJBNQDBMQlCwF3x8mTed'
        'O98oDBBR7hj18j/aSckBDmFWXNLM1V3+EPjWN/iSjtciuqMHp832lSXOfOL1kLyLkw+I9E2crZK9hyvUBKZTvxKmkF+iK+JQ'
        'VevVuItr3cMe1SAf8mkmHofgB+j02UKKjKrQ6xdP4l1LBhQ1lni2xzey7sCBErhEhT/diO97UXl9YXJvd9FoFYFl2Ve8wiZL'
        '0wXQuDU9u6r95nc/KoFID/AifWTFj//OaiuK846AMIrywNwIbc2VKnzsFa3ctO9maYde37LgDEIGjFhptRh/17mP2innJ+ip'
        'V+LzpL/HZwfTd3wKwH+127o711LDrKjGPz5Bj7S95iaQ7xZEbYKsKePiJaQLm7XvlVQEFdXr8QKwgoCWwT8Ir7iIpVXk4AAi'
        'FFD0yF8VTr5Fmowz706PvUJawuAkJ1FCegl15i9A04BqqSDpIA4AkhH8yd9QKtLzpuLIYTiFXBBg8txXI4vu8+8IgfcjTDT3'
        '+P0dNEdDn0+eKzis71+/9zd/KSo4GbE7isDu/MnmK+JbLu14+/MRsxMzFlG44UDb7TSw1FIdsYhvqM5kQseuXOn+MtlY/VFJ'
        'O3Z0sPHpwV2SsdMfwdBq+A2chu37kjlosOfMQ2nnMzzUzSVvJNDYfidzIs8E8G2TbWbwmsXJ2ADX6Cf7KVjR+e+vY+zO9nR7'
        '+ggiBVvMkd6+5VlE5oj3sD2dcbwzN9+Er6linMV7z6mSMZ/QoOMREHRP0UD7vN1LOcSgF90OetEl+6vi1kxpBT/4u78GOw4v'
        '3Gj3WodywGFU1Jr5Fj/I98ncjCCCnxQ16NMdjOu91lmBanKTrr3VNCUggoZCK7jdt5rJ7Fs0e9V73d3OVzZefdD4HItOLzRa'
        'e6Kq92/HIScJKTZSvlO2UX4p20Pqdl1a5v2TmmWbN6PMF2ov+M/bIxwpKIn+mhIVzHi88O1oG3MNSX4C0CE1l9z1M2jrvPHU'
        '2Uzd+Fn1jQf01JmtwFCqysabAvIZkTC44Qvtu0gHzjE1mN9l+8fbd/dkegzuswBo0wI3VMUzqplCk32qsYHPRMdrb06fPfLi'
        'r0LqioiIWxLy4Top6kHKoZw/XCyUgYhmMLskwFYQ3QhC6MFg9oZ8a0gHPB7nzdTqaO7KOOmzeUAthLiyZr6mTw+zNLNHw7M1'
        '8AMTyuYZpHjaUj2dFUoI9uL27m5DoIcahCZQ/DjY3bVIBcXOwLcCZUTgdn4OSlsjjtiK9o6Qej74r3/baA26WHE+QgXrtL87'
        'WPS3ByMgqjPbXsZQ7ukVVM8IMTAKBhv6mprb/PoLjQ1CdGZoHvjIOjPU6aEW7edA0keUckFkmeUEbFeTQN69gbLzU8FZ0YTz'
        'UKPvaItZ15zuc11Kiu8vq/psQBmH4wBg1G/e/7GkW2aEvB1yXxzDu6O7zPh8fU/pIO/EA3CHy57QVQYScgt1HKGluKYivbKW'
        'b2klX1Jdh6YDJM+KNQurRR9R0Apll6+hlqtmxVdNz6fzwXjtGf/LY+CI0KEcFEgw46Y3Cey6u+A7MQKTr7e55vDd8M7ejUah'
        'rg5akSnK1jXTeme5nb73A3mwxkTdq3T0vVdt+KgFX+bR7CCym+WJjWwVumkuyzmWNu5e1MBbg/TKLMR5xW8FlYHVDkTWfvNG'
        'kpd79LufBCX6mUw3qbr2R0r0wOYb1YApBiDRPPZFOxm1Z1PbbW59jBQieIGpYT7FkE1o9Z7vdAYqce8Z3q4VsAqey5QZp9Og'
        '//Td/DzDyQxjeHn4feYhXDoTYsqlc3Cof/Dm4/+40cC8ZI27v/nl/914cP83v/z6m/VTLPCS8YAycbH+4tlZZmCvw6s0WC8h'
        '9k6j0EwZ7OXqKitSRZmm0oqi4GAFPS6nyKpyEUWGcrmYVjvZYupFFanDa1xcEeyEXGTm9ng0xQAciaWia2QXJZYZxPMf8I1S'
        '0mDyTlMc4Ovr+cKDG2r1gv1E/X+K6oCFdoiPRtSGhpddeQUaLsva9Rzpwe/pcQdJq/JR31BQ3oqSUjLRr/sAhDoEYVDMh5vw'
        'ckplW6G4+JOW0rn1qEnW3/rwD87Nj8nBcbLnSKocPjyMhZ1wS/xYnSGWFu+ui7XWHCWHlHwzWsS90TXWNYstZ8IkOYS9TjwH'
        'ZMcZxSTloaItt8u25f9o/kNlRiSmOHTuduWkNk0vmStPOZXLy/siVuTDWy5teetaeY55AoA2sLMOdxmxP7zRvfVunZDWHgAc'
        '11bauTxm0nCvjFvZtXQHUF7pkhYnlBggwqgUgjZkWCXk6C9tes3y+6QdVkeNYw6q29V5s4qpSBFovW5Sal1FleGajRADvwWW'
        'n34fdcT9PmOenMBXAHfyLMKdlALOwBsUtPSa9Am7xfS5FP6TYrHLoj3l4FMKmObZTMsJQM89WMnxqZi4Zq0UTBRh6zgCqcuM'
        'SpPoKLY0t7G0nO8KI5XYX5B90frbeqniUr0V5335vuCixhznk5ZtrKOxhCxz0nhy/uw5uhdMuKE/s3oSDc083jme4kABwAW6'
        'ziUYHp/AXqUZ2MTKV2rzLCAU9tFF38xWKd/TjjzjOIwpSNzGhrqZgBNCTzcRzuH1R/EgFOYngR1HmMoI8iT/rUlNvI3x6Bz6'
        'PI7dy9x0i3FASTc/gnbucrZYe2U75ZMO1nJ8iDPbm+IEiGqVOLL4U83zFRH29xvKHjsZpAyqCSJwbIuvLcfaejrrK1XJcAlZ'
        'v5xOCdkvsX7t0tpPU/3EGletru5DvnL7Y/KGuRNtrQsTgqjzHHG1PSI92RF5T3MG0/Hj7x/x2ar2puFzkHnRLO38g+ScB1MD'
        'oPgSWrda9I6rQ+84bL1klkVqoiv1mcwlnJRnPiYU9YrDP+ySOq9kSvCetIskaGfZtbWWjStXS5ykfLHtMj2foMnp+O1hYdwE'
        'm8HgaMayiGUd4gxlQjQcQYf9o1zr7xvcYgMABpPBjxCF6d3MJJiwZsLe3Bi4ii7p1yeDt0f7BPblxajEycsJJ9OOys83apLd'
        'XXGoiUQ3+1oC+T7RhikaNwKOQf3jI8kUkajV3z7tF1lPz86pkbPzeCBFuTjtBD5XDoenYKKnAZo5awn8UCSAop2GvTMmzji7'
        'eO/PlKg1qOJeEF7iiauJRbf6EI+K5MfgtRZsl09MfpL4QsOn4k07SZdlGKrY6WCudxqtK0l0TBl7QXgO1V47i0Z5zR0lOu6B'
        '88g1LchHJ3KDZIKUDV3+6qswAkyI996xJoZWFTtrSZeO6tnM5W30bsOUkt66bjbp++ZW8tBYAuD3TvAqsnoalRCo7cjSHR6F'
        'RAHn0GrWaTEahcWXIxC+Hix5OHwMzN2nGDWyzTj3mFnJIZ6m0uS1M3+JmaRcUioLPec4FaGG4s5yyYMdxVNxmcGi5M9wdvW1'
        'M2zLKDYCb3/CYVzuGXY2STvD3igzh3ivGexplf1TjjFjBToWK8YhIGEPIWTgG8MYBIiAdvZGsZ3pQEP9iPsxG17+G3c5NimC'
        'A9K8Nbfajeupn1EYwAJPuyDJLCgoGZLQSt7sqVAdYbKjovVeSd9qzfk0XY+mFNZCW1mPLlO/Bn4fl56Np3PM5ERXbkzq/LOR'
        'CcLKB7P+ZP9gpHQnbo7yOxwRSPQTVd8dDY+SdelHyhKk1MTHQ/8RLDtc+skWvEJsTAvbsWeEL4FeeGLt7ZCsSCxAqUffR9W8'
        'ewGq+fdEorB0Ed5b+R72RsPxLtTSRUX5OV6PY7zgUIRWic75OSYGyCuAOWf79M6B6s6Z/HSDc+BWpq7zCPiVTyLuYuiq6YR0'
        'grMxukepWWbQQ9OFsEKohI4vnUgMFF41BGt1B1jVnJ/doTQS+Ww+CffJAaM57GMChou5TTprVDqy20Wvy/lKxr5oS2dX1RPr'
        'QXRGFzXwMg7FZNK8wxkNEJxOdWEWVz7QvRwfIKCjlz6TJQ7Y8vdQtv0RYgoCdgDLLAztiJ4aIpAeSaIsWqNixyJAzlLHUsS6'
        'ldw3Fl3jbThQi775xYfYcG+p2QwzTcY1zS9c80rg2RhTRdqDtRzX4cl6hhZUWcUb1DnjF3f7fJGwAeTwO2eSRVIQi/90wakz'
        '/gsJ0oSMl+EcOTdQcVS8mvZUDJO1MmaW2GSbgZEPtAqu5+DVlOtglVwbDiqooDQWuFwXgSctfN+4cq+S06CboMA6TRTDOk/o'
        'xPnUaUkZ3GV0GrryxHxqSpzQstrCsjMRn41itWLEZ+by1gEz0VapX0xbcffKeJym/EyN1zL5m5oPsd8pPIrBBDuYnPYh8oZ1'
        'fxnSklq9izqkmoZq0telE83ND4FoGBjFrIIhk/oM7zLpCbYSeFqHGFvkOyMevjsHIxP1I0E/+lqaWzWxWnJzKuPK0BmMqwqN'
        '4XvFmUK7FjE5TN1p4xI8T/Zc0a+ybwml0vl370ni3iCl3iNJHGG3lY6qXMg4kAtZXEpSJbTW0fkvewcBJZiOvUjvTCBpbK7L'
        '5le6monuVwVYJSqmFg7Q0l4iT9xPweKJ+Ah6AaYVwNgV5/hGxK/Zg8Sxuz5Z94GVyzOKlt5csdgTTDVvWjdWeKOx/NYTNas7'
        'HP7KkoZ1OqYOz/Zm+iLlCudLm7IT4G9ZjUZ6BiXW3wwAR5pak50ZMq7IgQgXx8V1R2222Uoyt94Q7Q4+8f8e3vygYGnWnUf9'
        'Q1DBK6CU+OsQvie20aKV7XimsUpGf0U+q2rRj9i3qIXW6tj6S5sLnQEuMV38i7c37ryCTvvfobTxG2+89uDBvTcuPWH8JY54'
        'HROGvfngHp0Xdqf83cbDV37z/k9fb2y8cu81nMyfw5zu/ub97/9Ro4UPn/U7tx+CPeT1P7r95sZrLwOKNqDsP2hf+iTd5CxW'
        'gcp5WEzGU1H+Crop3fC+Q8bnOT2GqOG4ssWYlHQFRNQIBn588Phf0Nb1w2NiFiA6iBZPgCoFh9B1xPCU22hEHjxqoX6cRyZm'
        'QU8Bjpk5UbG92sa/HHV2gcKKuOFOffenZuhBsQ2coG/NUZg4oLsLGdnwj9YZZPh4m30iOvDHyGjLjR8EuT5gFme/i/O2mycc'
        'D/qwgGZoXQld/Ny6PGCA+DgGIHtCLlyRPL5XYue8uOLscARPjtrVWKjSahV/TRB7BO0UMIe4Bftrs+MtvWPYcEVsUE3D18wE'
        '041GpdzAN5Pemmr1UojkBp93PFdiecGBaj4ajPsB6XD17cVovNs3RVSwXyFSDYFX8hFE1SaDY1CwK+l33HbXtIHFVwX3vuYQ'
        'cEWYdhyQrC1kzT45DeOfh2MPKyOcBPfVu/RZ+2fPQ1XG3fZ2iYJham9Ucg0v6din1j8Ft/2RrJe6QstQV85MJ5TV56ydjlUq'
        'UaL7DmXEojymsT5AV17kG9wFi+jJ9LRVP5NwqLjQLZBi0UkWQ6TePqRQmGCumyAVjlaUgxcig0IpmLnBLhdFRwFVzpf58hYn'
        '/3WxPTjZbMLlPDpagEcFXrDupVxaU6z9eMGPJq3gVuz4jV3I8hU2rdjBvM5Um5ixIScsYl5qPl8yYtMV2C8xgRdAEXHalOMp'
        '3LvV7WUZ9xs+iYrjjfeD6nJzBGnNTcrxpgsZ3iy4hUBmbTuGuaYCu6o/PBXwVRVf61+/950/aVgz/Zkd17kgsgqGlIGAevwt'
        'HV6LQMRk2tfI4eHaFsBr8ZvshlRGmZwjgM9MUXDXuRZF7enOojGXJjEEWQDm7g1m54ora5HsEijjrMC0Zv/qXI0IZDFzKCSR'
        'ZjhgJukHX7A//PwLajMZdxrw42xNVrfT9p6HmZUKBlE8E+OETYM9yIfdWl1Z6di37di5vcGhhR6ivkeKy4M1XxTPoMwe4b/b'
        'sEqKIVk0vwHucrsMMYY+tlz4SSKFZZNr/Tt26SgSFdR16Lgsr6BP1cMnMwGirjdIIktLTEumXBkF4ZpBZlTiWbnxJbbX38yG'
        '0qik7gKExOEYv7HnoVu+BLEcZq+beFHsT7w0Sr35NFFrPg3riNjWmh92N3bG9/BIdRoEq0p/t9PLKrD94v/Fygn0mq11Notp'
        'vmCGnlqIjjM3+atT/OoRFN5ZMSoESYT4k0ZG+H0pCREE+QFmPvc2+FOgs8Qcbo/fmxNThCimVbkuu/UpbQlGEg9PFKgT1Koy'
        'XXoU+dZEufmblrcASb+4IIdnFLs4Qd0OeEibZjmYTwIk6iZpFhfbmELlB6ZQVJeAC27hbATOtXEV/NYpv+pUcH1p44rmV6ey'
        'oOyTQ/nOwa/A5jLZnwLcqeODttj+EsVTmDDyjv2K6D1y2rdgJgwcF1Zvea730c+6870Tzu8Eam+PpzuH8MEQUYpGfLODd10o'
        'OnNvdjkdSGgWisDcCnVVvBfFb+L0V5k3pbtYTcn6hodcd1iPJ82jv6/RmUYOwcQm2hGvAXC2GYaH4WHvfgneJMEhBtScMxCQ'
        '5wcLEJDxr+3FdHRtaxMcEfgHcP5A0dmBdcZmN3vPbaVirYrRAOd8LuRcxaAgadNeEzz4Gq2nz4o61587b1/o/SGPjcSzwukp'
        '826wV3eQH6LM+E+4uV8rHjruo6pnssmc2e09h/zmZ7Lt553wybPXjEbTaazTbQDf2XWMfANCSGE/LWqlIAzjI5ELvyhuv2Sk'
        'hbkL9aAKM6uOHkRB/+2kYiTkr44aBIH/6WSDHNyPnYzbfvGhk3fQt2ezKHceLLokrG1F3DTyltXNN6rhJvlaokKq876WOYwf'
        'RoXiorFv79Efza0nvbBliSv77VPqI3lKJTK/VXtQfRTPKZUuVI00CjulNojsPlEyo+YGk6t5EEnMH26BQ+EkgLLPirukFZ5E'
        'CcthbtTtf6uSmkqNlyCv1WadRhnqmWe2Km4SW1BUu6febmgBDepK2qvSunRBTkC77tAc/YCXHyS2fFSlEVM2APtPyygffP2X'
        'jcwtYMQUO0IQO7APyJDgjw2EU01qef31OxvIxL89YnX4mV2mhKjSyclYle5CcWHxYCo4Ps4zuIgHxGy6ONlBWGxMbwAIWD9B'
        '3uVfkn/0+B8W9JZcIF+GAu4a4e3nMBLY6jk7i8magtYxcyOWcFwJ0QscLxVrW2Frgktshq8TvSXW31Y0RPHSVLBvzbznUJ2M'
        'lVfqeWB+858FKl72wa42xj8TWXrZ5yAg6EK2JIocVwxI8fd17EaVxq4bi44H4Fu/WxJbBWf6m//ir5Ol19YZb+p5W6KlYqQr'
        'h1JS8qQ9PsFBuRv09uXFoEHnigLPtcxW+sn0D2NoBADDOMCN9BHy7Xgop5gJkTYmzLdFpRv3Zju9aDUm+4PTzBF2xd4Ln2HN'
        'qpuYPXhRvXeEI3ZvAmmmuQ3H89AP1c25dgWr8UW2Uoo0HLhxOWsg1kBUq6HghFo1+Ffh8TiXlQSy9VcmEVL35QLrw7PwxMpQ'
        'c8Pxy28rUI+Ipjll8AxKkw5Wt5p6+SaNVdQUo4D+q8t6lhUVrRuXxU6o4ujlrAl7cyVrx95ejn7IBCFW9PhyJFp85pKuZHgi'
        '9UOKLvf+CkLasQ/tB/WyX9o5zFk5bN5KVMlV8OUuR01PAy4+hQ07ElmmYU8Wi/YWjvpxkRjdIRp7Ci3FheoZf9i9YLYdrbQd'
        'Sy+YglqalrPnUUJYrqDcXkHnEQaGEGjPkHJYQMiwJ+QaKYeiPe41cob1c12BbyjPaIwcshN9vSlBOiPn50A7b4p5CiSnuI96'
        'EfCKABKjfTUJpOTW8oEt2kpfHvPScHSUnpQ6HiiGUoWALRJ1BPTCXX27ROQzh76Xj4xxlNTHj1B97K5jd3Y8HkGbX2mS95ot'
        'vRVdu37TwXUb95u0mGxF6WzYlbA4gS3vpHX851hCxD5T1D7hgY2abndStZyDa2vZESRryVmhu885xGoF9yRjBftZLV0caSwr'
        'n9SS9mxjQf6glqt7yKODTlvH057ORqQHKbYstVv4iHLero3POaYKxSdQNjdu17fKKEWxAFwA0YCHRyPZSZMylvx9OkEfHaPV'
        '6jA7Jj4QCbokY3WPF/MWY79ImwgZqO0h9cRXAXapFHlnODx0iDVLp1RWsHT2mp5hA6qo4Yo8yPFgezi2dgZUOw0CGzgXSURV'
        'kvTJWT0OFpIjvFNWFE1aVcuivStdVl0L9+bUsIcYSoh3FKGHZG8D6m63E+RyOgOuliYYae7fDmWY8BN8pE3FcndDEvakV5/e'
        '5xdfdx49air6mL0zAPSgXzGHxHg6PVygU9BZzJVoMvD+bAGFdhpIe7BdQFWJu4Pi16Go9SPqEAIX1gu+okbMVzG1oJqDBmhd'
        'yXEGYa+T6QS0vGBCDmbaCSfnV4vXhTHO8G8NU1RfqTDAFwqgSBA0rrBifM6vOUsLxTwuAIe1Yb8zx9370pzrphb1HYx2E7pA'
        'zTF8E63yyXAPTtNBsNLhKk9lPh1OZdv389ZTXhL6QYFVNVWVVVC2uGhetVqoA4nmRFHxnAwGptziK8hMdLTXPxrNZsbdPJxp'
        'uDMRg8ZdiFmx/21yc+BywGFBD8EWUT3Et43Wz1QRuweZ/+K56Jg82p6ldvxqBrkitYERQqXFo0Q7FIiqFo5yG42K++gcbcei'
        'JR2puz7SuVTriMYmIAeIqDg09ED7Ohu8PdwVsnBuuQZ9j52qssmTpQyU5Uz3vcon2lZJSXuXTX69Wh1tYi5r2myyXPme1145'
        'uvVoRnAxFHuijMd9/eFZ8N6JvSRJbVogbOwFPO/dhs5Bqey147ns2HAyt0rVnnA+br123hc1h4lkdE9TUkeFDDqVgjNawWT+'
        'TSNiCWRBIwY7mrPH5Taqm20SznYKOgYdolbDZVTTc8qY4TpYHIOj4QBMABzhM9hBtzSNOZnBQlHyEYbRWbcMkEGMVpjVyW0s'
        'YuLV2yqMx55RluFzhwDDJ7DwgVbKqmThPZTCeyH+sD3EyCTWrgXqNuOJSzq9tVDLR7ErK0mUkdo2M98AY9R18UH0TEex9i7W'
        'KgSvSDvhdg6gB9T+KQgVXllW6Whitl7LpwLxVgOwv1PwgpiQM8//a1qhY+3h67VTmfOCoyTQ3ibxZItU8EbTKmc6m7i1NGWr'
        'clI+jA3RHmR8+g2cuTtTapt8+L41Mq7L/JrJ5ONNbdQH3/2LhteSs0mmucJZqkLDboxatGC5BoBrzEcT36ScJ7Fv/gRIzAlG'
        'ODPml0siLzTddKxN598ucfE8k7Q1Dhb3MqjsQaLNjwepIWksKat/YkmiBAaMCYYemMWrugw8LGFFisMvmTbkrcJPlIKluS+V'
        'PApZHXL0+Fz1/j9+rDC7tnQT/3lDiWV05k8O6sVZbDRLmmx5vu7sTs3+74HCBX6l/4TsOJE9EwMBrHRmJT+PqDuOZj0nYl1U'
        'RKsjpi17UvNntFRQMzIuSrYUOWEazaSaBu/khZTfpNdyriw+nG1hekVnW4YHddE0vq63ssNmtax15cgo7GnUHRlQR7pql0F6'
        'WTdLx2R15bK376Is4EqyAXnD4jHqcOZM68VbT62WOyJOuyVnodJ15VwtjinqanlKS+dyqXSv1LhbDLlVKOasRpXSDpmVFC85'
        '5ertVNA/ykbO0CreQYkHvq5U6pO+fFaWVbc2MSxLEA5RgCAFvGEx2a1CEzXpogZt0IhMFqbHPz1iDcwpGRo4D5JIDZScoupY'
        'a9BQBTqqRkv6JKoRVUXCImUkqDR5B3H/55pzUq4q5VInBDdLoKy3Hezu9tEfmArAqK+vtqu1uAtZBJOtwh/3wS2iQlNGj4rP'
        'xul03IqasorWDs+3XbFRmVi2XSnjNl3eOBzzikcOHy12fqDSrVarYuNuB2au05PqdVsFWXyusUIa55a/r8Yww/Y/5wcoX2UX'
        'Kh6xdq9aU2CukTu5uJyXek6mzUHSTK/6KtZi3Rdl4Re63y/A05fg7VEeN91uRv71ZtUbZ+62ntedVE3mX4NCg8ug5maVPyo1'
        'KPLUPfPk19GYSWosYY011O6x8udgQpJXM7xc8KLVu9HMivWW1bGvqu+vJ0KxsUhq07XXG/3lyxBPSJ544rLFhylnaM+JOlda'
        'dNh6y1xsF7jXlrnWnBfKx/RSSz1cTKJPYb/Cm397j1V4LyXXbqmrq8bNVf44RwcJwyiqnR8CSgWFUb/w97B6OVAInwwwvDou'
        '4/KOdtVjrvVVi1Use9h9bW6dG0mJznNiW3v1Tm8uRrYe+yECQlKkXGGIQeCm+J5AQD3hiqpSGEZSN2v11v6QuapHJ8ty1vqM'
        'td5Q31h+9U2ALtSsOTuP3165/P0TbrsEs70wr/1I1vNJMGEdpztLfRKeWYunJfjpC2sekdTjUFrApNJJrTaDKFBtzE/XmLar'
        '6OQAUEhaiZ4aUaCP13P7w+NhspuWi31MGdge56EJTxuag4Mz1ThTNu285rSUJj7ZDOyb7wUM7ENdTsE4uXThMdDSVnx519Ys'
        'XkSraJ9ePEgCnq8j7C/x9qr57nKxoQiB4mmBhKLs5mjtrjPeJd5YFd9XlQ1GxUSq24osSV1Md/8xpkA7sY8Z9aEkdVhsnKfx'
        'usHaKQe++GNFijVmWfOJnxI5n8xC1dNLVztKlTXC9dfSoBfVW1DXi+wr3tLCR7r0zlyCqaXcLxBPni7EYx3c4aKa/jhqYzza'
        'OXQul1Y9iwGRz5r9q1OvtuV+a96nyz1tVZxUP07WQ7sjH2fj4R0cJBCpFp/zcVWrLqMoTTDTS5x+wUCrnuGa9jIwn6MUxgns'
        '8a/i8L8zGKFK/6Q/Bn4IIWP4a02ixYwu0wWQ/dqz3ZWaU6fkJSN8scKzYG2lu3qz86SMbeJHgBOsaWBbihVcBjtwWMIUEqL3'
        '64rSF2QLS7IGPjEvIZlxamd6Bjh4p3xMaDbLnJVl2UVNklnaGrOsRSbFaZ7gStb3J6jrU5DOQlf2j5VOppBIj9hSe+lGNC7X'
        'p5Z3l9hcn+mt1mZ6Kea38twSDdVclApp/EqHTWn+ah3YkhgP7R+DR4UQEvCIhmw4kBl3tDNs+UBVH6IMANEl3w2OJkr9a77U'
        '3ykJKdHbfvWVtTMPsQzaef3VtTMf+2zJxh/+4dqZWbXN3k3ISkFBKvWFlJKglpToMRqPRfSAvwrRAz8tI29Q8jcMcXfXvV1z'
        'OgJpt2ZSINSrbVZzzfxRsz7C5a1RgoWaUzfkwE8mnzpqtmXIjZvyiG/ZURHasPdpyTFRQ+6HJdshfL0171PdnZ6O+rsLCDBJ'
        'YCNd9tsEjit8BazmG42X4HyYCx7+PK8QVlYi+mKDnzzRF4f9iZF6kcPRC2uMYZb+9n0CJN1l5daU6Hq5y1H/ofxbEfS3Imhd'
        'bYm2abRb6NR/CkgshOW0xKYVG7bSfX6Jdb7wZtXcKLgycKYsu/Ff+O/+0dAFYqOvaH0QPWeJZUFXa7yf18Tr+onOSSCvaA7s'
        'BLw3GI+3B6BvpRQB7SWocWktwUU0BVQX/v/B13/cuP36fZqVse7sPv4Xch1in6EO+xmZeUJJNACh+vI6Vlq6c5MCYRmOUZ8S'
        'wUICEkX1yLrAuoLSCFs9ms26wQEG76xWSIOPRohtLEFZ7rzrRWKoUzfnuCa/rs8+vbF3nEF0/OUM2SwJD+jM298bgr/tkkfK'
        'oDssSdgXkBst8MDSdZfggD4uQeeiwsSz3VuXJEw88+RlCf+Bw3TWW270F6LXJSn1ovRyYZq5MN34tPPM0oKoRj83lyXEWu/j'
        'gkkvTT4ptM8lz/Fyj88Lvhdt7xaPtKD8D/scL3lbXs471nnPfuf7wXt2HYUsSuxD71njqy2BnuzOjUmzOXf2fAltbYg+VLAi'
        'gAe92ISKnFXtJe+IpR7Rl/qYfuKP6st8XF/qI/sCB+uSHt3+43spUdT4ONVWJ6a89V2Gt8SD2vfV99CUnl5miEt56i9x31yC'
        'cpV99wWe9iOSlJe/6NiZf/zrf1yAAw5Ywg/Ao/G/T5Zm+Re8N9vLU8rF7im4o/7ua74romKS7DVe+/wFOvFAjGs/HGv3ukzA'
        'o1e3L0kqlooy5FWle95VWaNEct5cqrWnG5BZEjxEvzA8Ge2dIsBVcaU3CZmseKITEnOz2a5PgbVr2ByKQCYmQD7QjLlFivj4'
        'ZeQ6t6XecrxiuZDWJxjeWjnUtUmquaNfv4vO1X9NCXEnjUeP391pHI0mB8sR1QWDthQrG+394Gh7tL+YQiabzvKvwaUCui4W'
        '3JXkAcs3s1zo6cXOZHQxXPD9EoPreYuDfuuSelZSuJ88fheuUvT+3zlouHiOF3p3GJNdfXa+1AXy5M7Hb0/Fv4dT8VFROz6v'
        'r1zSy/rKE3pUX7mM9/TNzpVLfEpf+ZBe0Zf0gi57PaOBkmWdXjlechpTN5eAE1H5StM+Xgw/OJv/88JIwheJhnaCv64sG+9V'
        'UjUbzHUlE7J8KQjoX/Pgzh9NyUvcDumcVZPHnVLk8zMZ+jnNoSNccnbO76KSkWiOauEBagxmjVDNm/Q3ywGUBzmE1IHBm85P'
        'zyPDwYMMC3Z6PGwN291+fzKAhNR9uBSaiWbO0PP0/y/v2nobObLzO4H5Dx3OS9Pm0JI8jh1haESR5BllNRphJN8gCESLbInt'
        'IdkML5K4hh6MfQgCI9gEwWIRGIussXAWRuIHxwgczCDIgxb5H/NP8p1TVV3VXVXN1oy8edgBbElk1anbqXM/p+LG0frqfQTS'
        '5nTi02QEH/vCfjBVEFYHwfBbVCUt7iVTz6NKNyBJdrQMpbxP4mGUkD2Y9GS7Y/HalTz8Ld8mCh601d/Od90swsHarsOd33A+'
        'mz6OFoM0IgzopuNFqxfHY/oltOgDV4FV+EL517nVNsSwtiNfYVEP33he+azydnel97srvOEtnxVNJRyhn3sX5esstdDsrViX'
        'iurqa77Mjs75I7itx4itZ5fzwxgYJ56pF5aEHDLwWZY8t268TMrP8rH8kG0CjBIo7UU7Y4b91Ov1fTQLsve8uBnQNw0U7wjm'
        'Y3h942kA7EPi3wIXGXQ2+HAH1kiKEmu06maYiX3NL/oJADvMETxWKx6OZwvnow0ikMPZiUrlj1KS3ly0gqxYROZEGMjRyrFL'
        'CKG6hlm7tohN8j05xlNAg5DArR5zlNjR2jHBoHcr8FcjeD9YkzYtwQ9cY4pX7sxRMw7tloDkg11i2JqbAc9HqolvRiv+VQ1O'
        'JI501EzoZbvT5CzkuHQqhoKqJsxfPaY6pSbN0llEGQ8kEp0CYWeMbK088JNoguf7ostkCJJ6TGUhVj0yjjz2WL4d0RkksNko'
        'MOGyVyLavC1+AYQn285N3d8YUf7TuJ0x1RKJqAd7YjJoc0BOyTNxuE7t+mQ+GvlLCDmekHAgT+Z6WS/ZQ3F/1SlTFwxNOkqJ'
        'J8UkZ22J9HfKBXBOVSnDwiVZBeI1aWNf3E0r3ivzGeuyzcnoimg7bUXgOqOevOiVTsGQ4KsegxqQ2I6qEq5IyzieTFFDSL7y'
        'UWF8IQquV6RcSmevtIssH5QSxWw5EAiTKX5XO1f2mO1NJeXxhIlK/ehg89H2Vmf/ye7ucbDNz5tAVL/K01r7iSCJUSxKiK8m'
        'aSpLX07BeWrWsuj7VnQKHhuuvrPSlL0cDLVhMlx7L6gfes+HCJpZFPjuJy9ffBcMrv+by41m/LefwsNHT5kjz76fvnz+Y5ff'
        'NR9S5SkYL/O8VitfguzKgYQwoQW4JmrtEKldMQ5FVTJT7MPqrsQ6V2dZucHXVQp7rp6mrEW83eyWk8PMqWbqkBAMc7OUQqOp'
        'mmTUqDiAIV7p1i5Pe66XLe0Zt9KDaTJyt3YzCuDSBASzMP/I8wvxaG3bQcxYcqQvDVHS0EWYGudBGYRHf9OoeTgyjSOKqAiw'
        'BaYst6LNG5EfqAu9SUyoLU7UOXmh4JRPhN+wBXXDI2LmVMIcQYDQQGdnILvDaCG/e9PE7TxNcRmqfBIOZxby88Rwe9nCSmFG'
        'zWCppFTzm5WXiHE1l0jZ9rB+Evf+Kzg0CI7bnvJ5bteU1aTuA/q5sanKpmK3bZRqxjeXZXIyTBX9TIsx2W+OVkpOGUecRNGs'
        'MGvivzX3XmeWmpl3082gD2xyzW++Ujusaq0qQ1Z+522BU6c3LMOz6gK5EsSLuF5zi+I31xpsSMvldCmfi4gac8chG2Ai2Ocm'
        'lwtDoRu4CBHq+M0wOEzwyPEYrV98Q7z4xb8Zxp3gjB3cI66L4xhPivoKVTy7a7v+ltOVm1GMV6MRNX8YziMtptyIQvwpEQhh'
        'XHt18oCHkL/Q9KHv2/E/AnlQQVFcmNiU/dqo+ihsHn+SFORSifBA+v+ZYUsR58JPdwoT2TN8ykL814gwkEViiegkfkpRYadd'
        'Za1NbY7UVypAyyVDMvuZ8Fqp7zJZixuFLqR0y5LNKk2FmKpbmrK8kKwNA3fB9vzGG7JJYaS61M/J1AohNXRr70XbcD23Geib'
        '+9tofHXHJ9JLusBz0qqBWoApIWqp8v1gxUHP4csF/U6YPnUBdsobNRW5FKHrqpA8m0PjDlWXIdLT7dMjuj2jV6b2kvcaS1yv'
        '2Xp3cY19lCHUVnKxsrCwQoVad4N7r/tPATrYfAoNPni0vbu//fTgFsEbujj58ifk3xIsbj6JpTLOTwUXVPFNM49h2p2QRXyC'
        'p4lhjSDlfPTyxT8nAVXGCMgHIQCD7kYLjiX6VRI8u/4OV3zWpyTUr7p5JV2eSoTDVwyXZ8PDiFHauTYnJ+klHjNCIYiCQR9r'
        'Ek0qLeoP/4BMjSHna6TmvAdzcIpggjWJbI7vKUOWwQahDIp6RkSL38pGs3+Ey6VkRXSTpBhhft65QK0VeuP2IunN+m3xQDP/'
        'bq1piBi8+KIfx4OyI5rThM2jGEcjvDNMM07UMhhSwKBKZrw4T2LCe9r/kLjGvdXgjSAUU+zFAyR8vxWsruFtQbDT+SgB0bHm'
        'PEnO+rPXm/kY6Pa7JAgVs2lUXIUY+rbWcoJjMtYBQgq2OwVjliu6INcsNFTQm94AEfz5tf0Vege6t2rF/iPRMzi//q00ZyEY'
        '9HfqU2BMENLl+Vt4nBaNlqmv5xBX3kbmovzSOaMt3cpunzaWrGTnGHEg32rFOM9evvjBHGd0/dvU9E+l6giaWYVgiPt/J997'
        'FRv7lrgrZ0D/biu3ZP2HGKJFOxjWHzymXfiYduH9ut4uw2Ge0raACdMT6LLrBXmKOvwpJB3LByY5t/eEuKM51q1T6s0nj/c3'
        'UOz3rWD7k/2Nva2fhlbP0rOzAVHq4RhPErl8lIfcIqBXznEkfz/ChLg8oKBijluSTBU48OzM9ag/dRmM9bd53nk3OFTjrgcv'
        'n387khdY3+hmcJbQwwBgCaCeXE1dBJl2r/9jJGY6ILkvK1NgH7NkVr1xa0z1GYAtZHxs+BpCZajW0BA0lnWQlIUX1zpDTIZu'
        'azemYtpyt3IOQ2gtv/4xeKwPp+4aCU6WwUKyCDx7niIOH5KHhCcMkHRhurHjrXOtM9Wzc4E3WleMKtGr75ozW1cyuTibWT9O'
        'WfH/lrT879WXrF35dlcYHYs7e2fZ8YZUFahdpzcKxyiz0g7hYrjfaNypMkwYjUBdJ+16XLW3whU56kk666NrfImD7sm9zuCs'
        'lMApopK1ivvcvwJi0SMB7RXKPR/Mh6P2KgygM5ToWLTrI1A5Ae8yg3cD5Lv+tb6rr4B4wlZdBfMMNMrj3k9Agvc+2HkYkKS8'
        'c3C4vbe5/dMQYc6JMAS5AgXe5fQztkGcYNmBaIlylcFfHzzZWxYCIlu3nZFGmu6q0B39icO8clfbRMLdly++hODdo7iUBvrO'
        '6OmVqSs+iNUojtg6naRDNdB5NOkUvmKnjzvGSDecpR4Is3RZfxTssPviw2X9qPABOnJI3rPGenAuOrBcgSr35yRWGKort28J'
        '9b5x5QdL15HAek334LlXnc8J2hXaHZnD8phYxLRDKvmxBwQahvzQLMGAGJp1KM6YpqJmbMO6KkeFza39gGseQovZJytO8E7D'
        'u2hVttE+CPXNstOQZRZtAPKLZf1F5MYsmswcc8i+WwZFFvS0QcgvlvWnqpx2Z/p0WU9VC9Turb5Zeo9iMntMtGnjlAwtw7QX'
        'ZxGAhfvl7SCGco51PgJ6ULWrCQW7mrPNf1MyW7bRuClH/qtKMAqUw/xiWX8XzplfOPtfFcNlBCkGM571QY9TYuWzfuszeDNC'
        '9Ucv4T0JO1S9EBHCuLSCC3U+2NnddvHEiwnubeczyqCIZukw6YbGME05ZqMYKuy2HmL0mSM5RIZgCB+134futDgzayta3Ki4'
        'HUKOn+UEtirRKFoMOK3vcmkMTnoUixQBKTrMJsdjyZ5WwmOh+NL7h5JZsquJmGszuP56TH6qbyBsQJ1OIXEU40Bu7UxVYRLZ'
        'TcbIGAO4wmWETLTJZdvJfSZXQA+kifJsQw5kIZ3oy1HfIambkGiLrLOi+6VOy9OpIkpIS2hpwGqCfaRHCPJYXJ+Q2DzqpmRB'
        'btfns9N77yFQFxhy6giR6p6SvENXokXTC0+twLW7zLTG6WSmn7yEQeIH7CGUEE+iIjdnbTMS/oYBeyBFaG7LgahEJqB3dKhj'
        'CxJSuLX9wcaHu4cdDI5IqqeHjnndTMDySlI0GrZBRuoUJLAmeWCWAwJZ9IEBKXUB4UI+mVhF0R75viSANSnx3EpdKHR1HKpD'
        'bKPZFTo6dpQeW0vI5EdO5JM5G43JSDZk+sFap2sZJDLZK2BREM67q8ISWBTEtZkIWYvqW7OMlYFS0tWNsjMgu3WexQsZ6MXw'
        'PcF92ELV2CGMluSQFZseSTDHvLtqLf6kkPAj8otzQGAz+Fm84N8apRkipQfEDn5Y4QMSSV3nQp/b58KytO9c4oU8GYg2WvzN'
        'oL3S0cAnNyMXnwTemo4HCIivd+qNKkfJvREc72nM2y5ai5arHgmfTUWiYShHaGa9/aiSdXOoASW4QpuZkBbBuB0jNSeeUMhX'
        'tq/Lqppi6ATPD1J8cnHYIzWn4yqlUf29jxKJuSTGEuZWRN0dKk//Wmh8CFPLGczhGYkhNyFs2rIcquAoFDGe4Xg8tZGV7KzY'
        'XI62/XB3u7O18emBjxpqJyUfuXJMAkQZY1muromrofQx+7JlOlwZLVeNlpPyTPHT9Fx95JyYlLnteSkpvWxass3yWSnJXk9K'
        'flLOOQ0N0sE8Dd1zORNWmqQDjtI+sdKV+hIwrFM6YLAGCgCrSyFkeiVBcYpbJtxMPwVsyDBfjakeCcnW/T/8YhiMztJotGzA'
        'En3TPQW+7UVBxa/lZkVmlkcIOfTV3F4WtFxGPe/iCkpr/lAKym4pfuR0Vx8Yj5DmVmAdQHLXySVCSy8Px5xY1y0DZtgY1b57'
        'CVrBHYVB/z/0FEOxUtGK8BYLhYR2IytUVVuivC7RXTkMp6C7ZsUT/1gLvzWTtoTz8c7e1pOPg8cbexsPtx9v7x3eGnwG9Je0'
        '8qQL50E/7Wn1vpvGcBzY3gWZZQbHLYV6dydxPJJ/gAhTJqP4A1E3CGRvr8IxgqjxznkyTU4GcZu89+u5gAI4vskLyaqiGkU5'
        'HBGRFuHVtBe/Ur5tWOu/5OBVOMZBA3+ZBEMy4vev/x1Kpcww6abSdS4iWH4jXKraoyqmfJlNfpH9dpH9RpaHIxIrzw3jsLFY'
        'HZAqF32RLb+f/XaZ/bZwgTO261ijPcL6OlSwGD/DtbUVPbfgXhCKTUVMxJrhmaIefUePvreHmrPZiUL79Vp4FnaPvqdHX/To'
        '2z0uZdYA9dLbos4AGQpigo3cZ8aSs6neUy2LIyzsEfSJLhwjLPQIfT1C3xihaD8ycd4ViVYRCZqiAlrNCAmlr+nHQvy4ED+c'
        '2GdO4jiXNAW6zkwYvbLFIKM/lPEOnUfbOw8fHWLVuHsaa3ApOYJJ97rI1msC5ThNtcFv08EruB/vbB0+amTru8gD78fkJ7VK'
        '+WUzlDGGapxasXyUjV/hOzQ67UsG5I0AD3LArdpwJNbw6jrDZDqMUJEeE4lOpiHvsoFXlNH5nj5vEdPYmSY/j0VZUTR+YOwV'
        'hb3wAT0w14hP84NlANPTU4Oi8OiM9JoioviCRn0J/hKTCt33wezpGOVcjUJIfn/FAL5QwBcG8MJVcAInDdPcFoAxV5X78/w2'
        'b8cgGlKYt0lB5I2pTj7UcRdphwK+KAC/EeUQmOAALrRGQqBsDX/WlidLp5ANLj9d1Ao7ZtKCpoaifyV6IUapaY5N23gOf8M8'
        'otQInuYJ4q17U2mez3nAwSEXgWx9T7QORGvJfLNwcATYP6ewMgp4m00kS5XPzY8oruSLucGFc7zWKoxFUv3ba7QzlJU7pZix'
        '3mDQEh/X8voHz8VVCZQogOjRehjPDhZTGJseQ2xIutPw3T+n4EBSzh93Pvlo5+nhhxu7iNDd3t67GZh3NZhPXwPMexrM5utM'
        '5y8MOCXzsZxSYhcpiR1R3JwyLD95+9iO6zYQULTyyuLrtaLxpojBHH7TNPKPRXSgQDQmlmHD97WgqqEKlhGRnRzxryhJQSYV'
        'wZ0q7oVyQfPIfnj9HcRDQ7jkAOeulDzZck2cTgJ4SwQT4QKbWQ45vC5w3WKQnmZtQTLl3FTB0YROLb5p3FwgFdqH/5Y7Bbwc'
        'sy4yXTEtEQ2JVPTDJ4cbu6KlS/IryBNOWERMM3iioeDdBt18l0K81lbcwuJSMXB1xSUBqq2z8LDa7gIXw2psykRKd/yVjY0q'
        'CEuq6iZqXn8x1ogpaCr5xjjQWLjPhODB8Xy8zSZS5p1qhrrEelI3lX62cpqsL2EvTqA7o9axgUo3uPoakMxbSxDJO4umz6Zm'
        '0KVPZSwk93iuutpW6xZZyzLlZS8bKRKgy9CVSOVquajcUlK7iq0z4ufNryw9kMKqCftqPw3ZQmvG6vWCt5nvFf3A9cG9WWj9'
        'guaUn2wmKOVKQ9uRrjbUptFZ5kO47RW+RDyeTTtnzbAtaBpV2yba2k4tvcC2aQXxHKHaOhYGxTLy63UWFNWYAiJLoni4SsbI'
        'snbRJbcj+su4ptcAkaAhNvSiIQh0scHbqkG/EAVbjoAWVchPKTsXBOPx6FeX/LN/9ebnl/hvceWy0y0lKqyMWbc8z7Asc2pV'
        '7ZO3g+tkOXRQ+WWpJmqfnERonnajCmlWR6k6Og8M3BFsVYG9EQ3XTC0eTZGKpe6RVAclU/OysUOKMadAj7nmZiMkjM5dTAkf'
        'fd+lBLUXv8yqwkzIYS1NeuccZTMidYKCCaCo5BhYMTeQbpGux1ZSDMeZNlgeQ10M4JU7wP83JQFVj1UCcihclAf048zcDhHr'
        'fwbNCRk8/Wt8fEIpOzKTytCoNJpvSnVLxmq3naJbEJ4j2Cpi2eD3wdo9jlRvtCwg3SjVgpO0ZNwL3lsZX1LqHMVr0TVDrjFE'
        'MYSwU5yQAeUjtsROrr9DAN81kpmG19+O5dxb5rpvY7uN4lG821RBEV9TwjU5gs8mJP92qDqoY+N/htXCSzabRCKBRRYRRb7L'
        'FyKbChsVibQpqgcoJCqoCD9QHYXxOKulEPT/9+tRXpLiioGd00F0RorqUc3ynxViAkUHo3JT4RtaekR55Bb7N6CZ3goLoP1l'
        'JZhG5RkLpPWdG+JxUfKORovQ3CGunkQfHpUmXYtCO01HG3NtJc2M+Za0+ps5UjTgyIwRDeIZdVmNR19rc56eDscmNnPxWdB2'
        'Uam7g7y4ETM5By5vgkD+RmHw6WA+NWv9E1X4p4SCsL5KSJX4/SJg1lGgna9CKuMBnFvkr2eDDBWgaHEVigYIhlGlQK6AvfFo'
        'HbFl1GjdaIBNrq6soPq3Tc1LLnWD0dGYxINgc/fJwXbn4dONze0Oskl2nmx1Hh9Urn3m23JncKWdd9uNBz63+mfpiamIiy49'
        'vKAySRc5zOglETkurTpQRuKKIx1+nnSIKHfG8+G4w8vCvG8kO4pOYhFhCdDbkfncsJVi4tJAeadC835Qojgdk+M+fGLYKCW9'
        'Xlc1cLIidG+aEc2lkkQFcUHJHAWVxYP+bRP7Xx+PPG3VGz1JL//2krd2oMuLnj30Y9YZhrdebKwI4RVR23WXYHuDK6ZDyDEd'
        'V+T4puDElOUPhz6iUh154xxKsAH2TDLjvyRZLjVFaU9fPv9PeGtJsMrFFtxxCeQYhMYQRdbu1O6iOMkt/CNA+49ePv/XveD+'
        'egCX/dNPg/0nO3uHtzjCnRoOWNU95wKXnQ5XRuioIpckvrSDDTDErWgPMg72SyUj4asWbX/jTu3/AAM+IIHOrQgA'
    ),
}
PAYLOAD_START = "EMBEDDED_TOOL_PAYLOADS: dict[str, str] = {"
PAYLOAD_END_MARKER = "\n}\nPAYLOAD_START"


def app_data_dir() -> Path:
    """Return the writable app directory used for bundled tool scripts/configs."""

    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "VNEDU-Control-Panel"


def bundled_base_dir() -> Path:
    """Return the source directory for bundled data files."""

    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent


def tool_workspace_dir() -> Path:
    """Return the writable folder where tool scripts run and store sidecar files."""

    if getattr(sys, "frozen", False):
        target_dir = app_data_dir() / "tools"
        target_dir.mkdir(parents=True, exist_ok=True)
        source_dir = bundled_base_dir()
        overrides = load_default_tool_overrides()
        for tool_name, metadata in TOOL_FILES.items():
            source = source_dir / metadata["script"]
            target = target_dir / metadata["script"]
            override = overrides.get(tool_name)
            if override and override.get("payload"):
                try:
                    override_bytes = decode_payload_bytes(override["payload"])
                    if not target.exists() or target.read_bytes() != override_bytes:
                        target.write_bytes(override_bytes)
                    continue
                except Exception:
                    pass
            if not source.exists():
                continue
            # Only seed defaults on first run. Once a user has replaced a tool,
            # the packaged app must not silently overwrite it on later launches.
            if not target.exists():
                shutil.copy2(source, target)
        return target_dir
    return Path(__file__).resolve().parent


def embedded_tool_dir() -> Path:
    """Return the writable folder used when external tool files are missing."""

    target_dir = app_data_dir() / "embedded_tools"
    target_dir.mkdir(parents=True, exist_ok=True)
    return target_dir


def custom_tools_config_path() -> Path:
    """Return the JSON file that stores user-added tool metadata and payloads."""

    return app_data_dir() / CUSTOM_TOOLS_CONFIG_NAME


def default_tool_overrides_path() -> Path:
    """Return the JSON file that stores replaceable fallback payloads for bundled tools."""

    return app_data_dir() / DEFAULT_TOOL_OVERRIDES_CONFIG_NAME


def app_config_path() -> Path:
    """Return the JSON file that stores small dashboard preferences."""

    return app_data_dir() / APP_CONFIG_NAME


def load_app_config() -> dict[str, str]:
    """Load dashboard preferences with conservative defaults."""

    path = app_config_path()
    if not path.exists():
        return {"dashboard_mode": "basic"}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"dashboard_mode": "basic"}
    if not isinstance(raw, dict):
        return {"dashboard_mode": "basic"}
    mode = str(raw.get("dashboard_mode") or "basic").strip().lower()
    if mode not in VALID_DASHBOARD_MODES:
        mode = "basic"
    return {"dashboard_mode": mode}


def save_app_config(config: dict[str, str]) -> None:
    """Persist dashboard preferences without touching tool configuration."""

    mode = str(config.get("dashboard_mode") or "basic").strip().lower()
    if mode not in VALID_DASHBOARD_MODES:
        mode = "basic"
    path = app_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps({"version": 1, "dashboard_mode": mode}, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def custom_tool_storage_dir() -> Path:
    """Return the writable folder where user-added external tool copies are stored."""

    target_dir = tool_workspace_dir() / CUSTOM_TOOLS_DIR_NAME
    target_dir.mkdir(parents=True, exist_ok=True)
    return target_dir


def slugify_tool_text(text: str) -> str:
    """Convert a display title into a stable ASCII slug for file/id names."""

    normalized = unicodedata.normalize("NFKD", text.strip()).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", normalized).strip("_").lower()
    return slug or "tool"


def custom_tool_id_for_title(title: str) -> str:
    """Build a collision-resistant custom tool id."""

    return f"{CUSTOM_TOOL_ID_PREFIX}{slugify_tool_text(title)}_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"


def normalize_custom_script_path(script: str) -> str:
    """Return a safe custom tool script path relative to the tool workspace."""

    normalized = script.replace("\\", "/").strip()
    candidate = Path(normalized)
    parts = candidate.parts
    if (
        candidate.is_absolute()
        or len(parts) != 2
        or parts[0] != CUSTOM_TOOLS_DIR_NAME
        or parts[1] in {"", ".", ".."}
        or Path(parts[1]).name != parts[1]
        or not parts[1].lower().endswith(".py")
    ):
        raise ValueError("Đường dẫn tool tùy chỉnh không hợp lệ.")
    return f"{CUSTOM_TOOLS_DIR_NAME}/{parts[1]}"


def sanitize_custom_tools(raw_tools: object) -> dict[str, dict[str, str]]:
    """Normalize custom-tool config data and drop malformed entries."""

    tools: dict[str, dict[str, str]] = {}
    if not isinstance(raw_tools, dict):
        return tools
    for tool_id, metadata in raw_tools.items():
        if not isinstance(tool_id, str) or not isinstance(metadata, dict):
            continue
        title = str(metadata.get("title") or "").strip()
        try:
            script = normalize_custom_script_path(str(metadata.get("script") or ""))
        except ValueError:
            continue
        if not tool_id.startswith(CUSTOM_TOOL_ID_PREFIX) or not title:
            continue
        tools[tool_id] = {
            "title": title,
            "description": str(metadata.get("description") or "").strip(),
            "script": script,
            "accent": str(metadata.get("accent") or ACCENT_CHOICES["Xanh ngọc"]).strip(),
            "payload": str(metadata.get("payload") or "").strip(),
            "created_at": str(metadata.get("created_at") or ""),
            "updated_at": str(metadata.get("updated_at") or ""),
        }
    return tools


def load_custom_tools() -> dict[str, dict[str, str]]:
    """Load user-added tools from app config, ignoring malformed entries."""

    path = custom_tools_config_path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}

    raw_tools = raw.get("tools", {})
    return sanitize_custom_tools(raw_tools)


def save_custom_tools(tools: dict[str, dict[str, str]]) -> None:
    """Persist user-added tool metadata and embedded payloads atomically."""

    path = custom_tools_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "tools": tools}
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def sanitize_default_tool_overrides(raw_overrides: object) -> dict[str, dict[str, str]]:
    """Normalize persisted fallback payloads for default tools."""

    overrides: dict[str, dict[str, str]] = {}
    if not isinstance(raw_overrides, dict):
        return overrides
    for tool_name, metadata in raw_overrides.items():
        if tool_name not in TOOL_FILES or not isinstance(metadata, dict):
            continue
        payload = str(metadata.get("payload") or "").strip()
        if not payload:
            continue
        overrides[tool_name] = {
            "payload": payload,
            "source_hash": str(metadata.get("source_hash") or "").strip(),
            "source_name": str(metadata.get("source_name") or "").strip(),
            "updated_at": str(metadata.get("updated_at") or ""),
        }
    return overrides


def load_default_tool_overrides() -> dict[str, dict[str, str]]:
    """Load default-tool fallback payload overrides from app data."""

    path = default_tool_overrides_path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return sanitize_default_tool_overrides(raw.get("tools", {}))


def save_default_tool_overrides(overrides: dict[str, dict[str, str]]) -> None:
    """Persist default-tool fallback payload overrides atomically."""

    path = default_tool_overrides_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "tools": sanitize_default_tool_overrides(overrides)}
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def default_tool_payload(tool_name: str) -> str:
    """Return the active fallback payload for one default tool."""

    if getattr(sys, "frozen", False):
        override = load_default_tool_overrides().get(tool_name)
        if override and override.get("payload"):
            return override["payload"].strip()
    return EMBEDDED_TOOL_PAYLOADS.get(tool_name, "").strip()


def custom_tool_external_path(metadata: dict[str, str]) -> Path:
    """Return the external script path for one user-added tool."""

    return tool_workspace_dir() / normalize_custom_script_path(metadata["script"])


def decode_payload_bytes(payload: str) -> bytes:
    """Decode one base64+gzip source payload as exact bytes."""

    compressed = base64.b64decode(payload.encode("ascii"))
    return gzip.decompress(compressed)


def decode_custom_tool_bytes(tool_id: str, metadata: dict[str, str]) -> bytes:
    """Decode one custom tool fallback payload as exact bytes."""

    payload = metadata.get("payload", "").strip()
    if not payload:
        raise FileNotFoundError(f"Tool tùy chỉnh chưa có bản nhúng: {metadata.get('title') or tool_id}")
    try:
        return decode_payload_bytes(payload)
    except Exception as error:  # noqa: BLE001 - user-added payload corruption needs a clear message.
        raise RuntimeError(f"Bản nhúng của tool tùy chỉnh {tool_id} bị lỗi: {error}") from error


def decode_custom_tool_source(tool_id: str, metadata: dict[str, str]) -> str:
    """Decode one custom tool fallback payload."""

    return decode_custom_tool_bytes(tool_id, metadata).decode("utf-8-sig")


def extract_custom_tool_script(tool_id: str, metadata: dict[str, str]) -> Path:
    """Write a custom tool payload to the embedded runtime folder and return it."""

    relative_script = Path(normalize_custom_script_path(metadata["script"]))
    target = embedded_tool_dir() / CUSTOM_TOOLS_DIR_NAME / relative_script.name
    target.parent.mkdir(parents=True, exist_ok=True)
    source = decode_custom_tool_bytes(tool_id, metadata)
    existing = b""
    if target.exists():
        try:
            existing = target.read_bytes()
        except OSError:
            existing = b""
    if existing != source:
        tmp_path = target.with_suffix(target.suffix + ".tmp")
        tmp_path.write_bytes(source)
        tmp_path.replace(target)
    return target


def resolve_custom_tool_script(tool_id: str, allow_external: bool = True) -> Path:
    """Return the script path for a user-added tool, falling back to its payload."""

    metadata = load_custom_tools().get(tool_id)
    if metadata is None:
        raise ValueError(f"Tool tùy chỉnh không tồn tại: {tool_id}")
    script_path = custom_tool_external_path(metadata)
    if allow_external and script_path.exists():
        return script_path
    return extract_custom_tool_script(tool_id, metadata)


def decode_embedded_tool_source(tool_name: str) -> str:
    """Decode one embedded legacy tool source payload."""

    return decode_embedded_tool_bytes(tool_name).decode("utf-8-sig")


def decode_embedded_tool_bytes(tool_name: str) -> bytes:
    """Decode one embedded legacy tool source payload as exact bytes."""

    payload = default_tool_payload(tool_name)
    if not payload:
        raise FileNotFoundError(f"Không có mã nhúng dự phòng cho tool: {tool_name}")
    try:
        return decode_payload_bytes(payload)
    except Exception as error:  # noqa: BLE001 - payload corruption is a startup/runtime issue.
        raise RuntimeError(f"Mã nhúng của tool {tool_name} bị lỗi: {error}") from error


def extract_embedded_tool_script(tool_name: str, script_name: str) -> Path:
    """Write an embedded tool script to app data and return its path."""

    target = embedded_tool_dir() / script_name
    source = decode_embedded_tool_bytes(tool_name)
    existing = b""
    if target.exists():
        try:
            existing = target.read_bytes()
        except OSError:
            existing = b""
    if existing != source:
        tmp_path = target.with_suffix(target.suffix + ".tmp")
        tmp_path.write_bytes(source)
        tmp_path.replace(target)
    return target


def encode_embedded_tool_source(source: bytes) -> str:
    """Return a compact base64+gzip payload for one tool source file."""

    return base64.b64encode(gzip.compress(source, compresslevel=9)).decode("ascii")


def sha256_hex(data: bytes) -> str:
    """Return a stable SHA-256 hex digest for one byte payload."""

    return hashlib.sha256(data).hexdigest()


def source_bytes_for_embedding(tool_name: str) -> bytes:
    """Read current source for a tool, falling back to the existing embedded snapshot."""

    metadata = TOOL_FILES.get(tool_name)
    if metadata is None:
        raise ValueError(f"Tool không hợp lệ: {tool_name}")
    source_path = tool_workspace_dir() / metadata["script"]
    if source_path.exists():
        return source_path.read_bytes()
    return decode_embedded_tool_bytes(tool_name)


def build_embedded_payload_block() -> tuple[str, dict[str, str]]:
    """Build the Python source block for the current embedded tool snapshots."""

    payloads: dict[str, str] = {}
    lines = [PAYLOAD_START]
    for tool_name in TOOL_FILES:
        payload = encode_embedded_tool_source(source_bytes_for_embedding(tool_name))
        payloads[tool_name] = payload
        lines.append(f"    {tool_name!r}: (")
        for index in range(0, len(payload), 96):
            lines.append(f"        {payload[index:index + 96]!r}")
        lines.append("    ),")
    lines.append("}")
    return "\n".join(lines), payloads


def refresh_embedded_payloads_in_control_panel() -> None:
    """Refresh embedded fallback snapshots in this control-panel source file."""

    if getattr(sys, "frozen", False):
        raise RuntimeError("Bản .exe không thể tự cập nhật mã nhúng. Hãy chạy file .py để đổi file tool.")

    control_panel_path = Path(__file__).resolve()
    source = control_panel_path.read_text(encoding="utf-8")
    start = source.index(PAYLOAD_START)
    end = source.index(PAYLOAD_END_MARKER, start) + len("\n}")
    payload_block, payloads = build_embedded_payload_block()
    tmp_path = control_panel_path.with_suffix(control_panel_path.suffix + ".tmp")
    tmp_path.write_text(source[:start] + payload_block + source[end:], encoding="utf-8")
    tmp_path.replace(control_panel_path)
    EMBEDDED_TOOL_PAYLOADS.clear()
    EMBEDDED_TOOL_PAYLOADS.update(payloads)


def external_tool_script_path(tool_name: str) -> Path:
    """Return the expected external script path for one logical tool."""

    metadata = TOOL_FILES.get(tool_name)
    if metadata is None:
        raise ValueError(f"Tool không hợp lệ: {tool_name}")
    return tool_workspace_dir() / metadata["script"]


def resolve_tool_script(tool_name: str, allow_external: bool = True) -> Path:
    """Return one validated tool script path."""

    metadata = TOOL_FILES.get(tool_name)
    if metadata is None:
        raise ValueError(f"Tool không hợp lệ: {tool_name}")
    script_path = external_tool_script_path(tool_name)
    if allow_external and script_path.exists():
        return script_path
    return extract_embedded_tool_script(tool_name, metadata["script"])


def embedded_tool_text(tool_name: str) -> str:
    """Return the normalized embedded source text for one default tool."""

    return decode_embedded_tool_source(tool_name)


def tool_runtime_key(tool_name: str, *, custom: bool = False) -> str:
    """Return the stable runtime key used to prevent duplicate tool sessions."""

    scope = "custom" if custom else "default"
    return f"{scope}:{tool_name}"


def tool_process_mutex_name(runtime_key: str) -> str:
    """Return a Windows-safe mutex name for one running tool."""

    safe_key = re.sub(r"[^A-Za-z0-9_.-]+", "_", runtime_key).strip("_")
    return f"{TOOL_PROCESS_MUTEX_PREFIX}{safe_key or 'tool'}"


_kernel32_cache: tuple[object, object] | None = None


def _setup_kernel32_mutex():
    """Setup kernel32 WinDLL once, cache for reuse across all mutex calls."""

    global _kernel32_cache  # noqa: PLW0603
    if _kernel32_cache is not None:
        return _kernel32_cache

    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.OpenMutexW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.OpenMutexW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32_cache = (kernel32, ctypes)
    return _kernel32_cache


class ToolProcessGuard:
    """Hold a named Windows mutex while one dashboard tool is running."""

    def __init__(self, runtime_key: str) -> None:
        self.runtime_key = runtime_key
        self._handle: int | None = None
        self._kernel32: object | None = None

    def acquire(self) -> bool:
        """Return True when this process owns the per-tool runtime lock."""

        if sys.platform != "win32":
            return True

        kernel32, ctypes = _setup_kernel32_mutex()
        ctypes.set_last_error(0)
        handle = kernel32.CreateMutexW(None, False, tool_process_mutex_name(self.runtime_key))
        if not handle:
            raise OSError(ctypes.get_last_error(), "Không tạo được khóa tool.")
        already_running = ctypes.get_last_error() == 183
        if already_running:
            kernel32.CloseHandle(handle)
            return False
        self._kernel32 = kernel32
        self._handle = handle
        return True

    def release(self) -> None:
        """Release the per-tool runtime lock."""

        if self._handle is None or self._kernel32 is None:
            return
        try:
            self._kernel32.CloseHandle(self._handle)  # type: ignore[attr-defined]
        finally:
            self._handle = None
            self._kernel32 = None


def is_tool_process_mutex_active(runtime_key: str) -> bool:
    """Return whether another wrapped process currently owns one tool lock."""

    if sys.platform != "win32":
        return False

    kernel32, _ctypes = _setup_kernel32_mutex()
    synchronize = 0x00100000
    handle = kernel32.OpenMutexW(synchronize, False, tool_process_mutex_name(runtime_key))
    if not handle:
        return False
    try:
        return True
    finally:
        kernel32.CloseHandle(handle)


def run_tool_process(tool_name: str, extra_args: list[str] | None = None) -> int:
    """Run a bundled legacy tool as if it was started directly."""

    guard = ToolProcessGuard(tool_runtime_key(tool_name, custom=False))
    if not guard.acquire():
        print(f"{TOOL_FILES[tool_name]['title']} đang mở. Không mở thêm phiên thứ hai.")
        return 0

    old_argv = sys.argv[:]
    old_cwd = Path.cwd()
    try:
        script_path = resolve_tool_script(tool_name)
        os.chdir(script_path.parent)
        sys.argv = [str(script_path), *(extra_args or [])]
        runpy.run_path(str(script_path), run_name="__main__")
    finally:
        sys.argv = old_argv
        os.chdir(old_cwd)
        guard.release()
    return 0


def run_custom_tool_process(tool_id: str, extra_args: list[str] | None = None) -> int:
    """Run a user-added tool as if it was started directly."""

    guard = ToolProcessGuard(tool_runtime_key(tool_id, custom=True))
    if not guard.acquire():
        title = load_custom_tools().get(tool_id, {}).get("title", tool_id)
        print(f"{title} đang mở. Không mở thêm phiên thứ hai.")
        return 0

    old_argv = sys.argv[:]
    old_cwd = Path.cwd()
    try:
        script_path = resolve_custom_tool_script(tool_id)
        os.chdir(script_path.parent)
        sys.argv = [str(script_path), *(extra_args or [])]
        runpy.run_path(str(script_path), run_name="__main__")
    finally:
        sys.argv = old_argv
        os.chdir(old_cwd)
        guard.release()
    return 0


class SingleInstanceGuard:
    """Hold a process-wide lock so only one login/dashboard window can run."""

    def __init__(self) -> None:
        self._handle: int | None = None
        self._kernel32: object | None = None

    def acquire(self) -> bool:
        """Return True when this process owns the dashboard lock."""

        if os.environ.get(ALLOW_MULTIPLE_ENV) == "1":
            return True
        if sys.platform != "win32":
            return True

        kernel32, ctypes = _setup_kernel32_mutex()
        ctypes.set_last_error(0)
        handle = kernel32.CreateMutexW(None, False, SINGLE_INSTANCE_MUTEX_NAME)
        if not handle:
            raise OSError(ctypes.get_last_error(), "Không tạo được khóa phiên VNEDU Control Panel.")
        already_running = ctypes.get_last_error() == 183
        if already_running:
            kernel32.CloseHandle(handle)
            return False
        self._kernel32 = kernel32
        self._handle = handle
        return True

    def release(self) -> None:
        """Release the dashboard lock when the GUI exits."""

        if self._handle is None or self._kernel32 is None:
            return
        try:
            self._kernel32.CloseHandle(self._handle)  # type: ignore[attr-defined]
        finally:
            self._handle = None
            self._kernel32 = None


def focus_existing_control_panel_window() -> bool:
    """Bring the already-open control panel window to the foreground on Windows."""

    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        user32.FindWindowW.restype = wintypes.HWND
        user32.IsIconic.argtypes = [wintypes.HWND]
        user32.IsIconic.restype = wintypes.BOOL
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.ShowWindow.restype = wintypes.BOOL
        user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        user32.SetForegroundWindow.restype = wintypes.BOOL

        hwnd = user32.FindWindowW(None, f"{APP_TITLE} v{APP_VERSION}")
        if not hwnd:
            return False
        user32.ShowWindow(hwnd, 9 if user32.IsIconic(hwnd) else 5)
        user32.SetForegroundWindow(hwnd)
        return True
    except Exception:  # noqa: BLE001 - duplicate launch must exit even if focusing fails.
        return False


def write_json_if_changed(path: Path, payload: dict[str, object]) -> None:
    """Write JSON only when content differs to avoid needless config churn."""

    existing: dict[str, object] = {}
    if path.exists():
        try:
            existing_raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(existing_raw, dict):
                existing = existing_raw
        except (OSError, json.JSONDecodeError):
            existing = {}

    merged = {**existing, **payload}
    if merged == existing:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def sync_tool_configs(username: str, target_url: str, debug_port: int) -> None:
    """Seed shared tool config files without persisting the password."""

    shared_payload = {
        "debug_port": str(debug_port),
        "target_url": target_url,
        "cdp_url": target_url,
        "username": username,
        "show_password": False,
    }
    for workspace in (tool_workspace_dir(), embedded_tool_dir()):
        write_json_if_changed(workspace / "vnedu_standalone_config.json", shared_payload)
        write_json_if_changed(
            workspace / "nhanxet_v2_config.json",
            {
                "debug_port": str(debug_port),
                "target_url": target_url,
                "username": username,
                "show_password": False,
            },
        )
        write_json_if_changed(
            workspace / "auto_danang_config.json",
            {
                "vnedu_username": username,
                "vnedu_password": "",
                "debug_port": str(debug_port),
            },
        )


def validate_target_url(raw_url: str) -> str:
    """Validate the VNEDU URL used by the automation session."""

    url = raw_url.strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("URL vnEdu phải bắt đầu bằng http:// hoặc https://.")
    if "vnedu" not in parsed.netloc.lower():
        raise ValueError("URL không giống tên miền vnEdu.")
    return url


def parse_debug_port(raw_port: str) -> int:
    """Parse and validate the local Chrome DevTools port."""

    try:
        port = int(str(raw_port).strip())
    except ValueError as error:
        raise ValueError("Cổng CDP phải là số.") from error
    if port < 1024 or port > 65535:
        raise ValueError("Cổng CDP phải nằm trong khoảng 1024..65535.")
    return port


class _EventBus:
    """Thread-safe in-process pub/sub for dashboard state changes."""

    def __init__(self, root: tk.Tk) -> None:
        self._root = root
        self._subs: dict[str, list[Callable[..., None]]] = {}

    def on(self, event: str, handler: Callable[..., None]) -> None:
        """Subscribe a handler to an event name."""
        self._subs.setdefault(event, []).append(handler)

    def off(self, event: str, handler: Callable[..., None]) -> None:
        """Unsubscribe a handler from an event name."""
        handlers = self._subs.get(event, [])
        if handler in handlers:
            handlers.remove(handler)

    def emit(self, event: str, **kwargs: object) -> None:
        """Thread-safe: schedules dispatch on Tk main thread."""
        try:
            self._root.after(0, lambda: self._dispatch(event, kwargs))
        except (RuntimeError, tk.TclError):
            pass

    def _dispatch(self, event: str, kwargs: dict[str, object]) -> None:
        for handler in self._subs.get(event, []):
            try:
                handler(**kwargs)
            except Exception:  # noqa: BLE001 - event handlers must not crash the dispatcher.
                _get_logger().error("Event handler error: %s", event, exc_info=True)


def login_to_vnedu(
    username: str,
    password: str,
    target_url: str,
    debug_port: int,
    progress_callback: Callable[[float, str], None] | None = None,
) -> str:
    """Open the configured VNEDU URL and perform login through existing automation."""

    import_errors: list[str] = []
    old_sys_path = sys.path[:]
    module_names = ("vnedu_login_nhapdiem", "vnedu_login_nhanxet")
    try:
        for tool_name, module_name, class_name in (
            ("nhapdiem", module_names[0], "VnEduScoreEntryAutomation"),
            ("nhanxet", module_names[1], "VnEduScoreAutomation"),
        ):
            try:
                script_path = resolve_tool_script(tool_name)
                sys.path.insert(0, str(script_path.parent))
                spec = importlib.util.spec_from_file_location(module_name, script_path)
                if spec is None or spec.loader is None:
                    raise ImportError(f"Không nạp được module từ {script_path}")
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                spec.loader.exec_module(module)
                automation_class = getattr(module, class_name)
                break
            except Exception as error:  # noqa: BLE001 - try the alternate automation source below.
                import_errors.append(f"{tool_name}: {error}")
        else:
            raise RuntimeError("Không nạp được automation đăng nhập VNEDU:\n" + "\n".join(import_errors))

        automation = automation_class(debug_port=debug_port, target_url=target_url)
        with automation._open_page() as page:  # Reuse the project automation contract.
            return automation._login_if_needed_on_page(
                page,
                username=username,
                password=password,
                progress_callback=progress_callback,
            )
    finally:
        sys.path = old_sys_path
        for module_name in module_names:
            sys.modules.pop(module_name, None)


class ControlPanelApp:
    """Main dashboard that launches each VNEDU utility."""

    _active_scroll_canvas: tk.Canvas | None = None

    def __init__(
        self,
        root: tk.Tk,
        session: dict[str, object] | None = None,
        *,
        skip_login: bool = False,
    ) -> None:
        self.root = root
        self.session = session or {
            "username": "",
            "target_url": DEFAULT_VNEDU_URL,
            "debug_port": DEFAULT_DEBUG_PORT,
            "message": "Sẵn sàng đăng nhập.",
        }
        self.processes: list[subprocess.Popen[bytes]] = []
        self.tool_processes: dict[str, subprocess.Popen[bytes]] = {}
        self._dashboard_cards_frame: ttk.Frame | None = None
        self._tool_process_poll_after_id: str | None = None
        self._running_tool_keys_snapshot: set[str] = set()
        self.skip_login = skip_login
        self._busy = False
        self.app_config = load_app_config()
        self.custom_tools = load_custom_tools()
        self.health_result: dict[str, object] = {}
        self._health_check_token = 0
        self._health_cache_at = 0.0
        self._health_cache_key = ""
        self._health_in_progress = False
        self._health_after_id: str | None = None
        self.bus = _EventBus(root)
        self._file_mtimes: dict[str, int] = {}
        self._prev_card_fingerprint: str = ""

        self.url_var = tk.StringVar(value=str(self.session.get("target_url") or DEFAULT_VNEDU_URL))
        self.port_var = tk.StringVar(value=str(self.session.get("debug_port") or DEFAULT_DEBUG_PORT))
        self.username_var = tk.StringVar(value=str(self.session.get("username") or ""))
        self.password_var = tk.StringVar()
        self.show_password_var = tk.BooleanVar(value=False)
        initial_message = "Chế độ xem giao diện: chưa đăng nhập VNEDU." if skip_login else str(
            self.session.get("message") or "Sẵn sàng đăng nhập."
        )
        self.status_var = tk.StringVar(value=initial_message)
        self.smart_status_var = tk.StringVar(value="VNEDU: ĐANG KIỂM TRA | CDP: ĐANG KIỂM TRA | TOOL: ĐANG KIỂM TRA")
        self.session_caption_var = tk.StringVar()
        self.tool_filter_var = tk.StringVar()
        self.tool_count_var = tk.StringVar()
        self.advanced_mode_var = tk.BooleanVar(value=self.app_config.get("dashboard_mode") == "advanced")

        self.root.title(f"{APP_TITLE} v{APP_VERSION}")
        self.root.minsize(520, 320)
        if skip_login:
            self._build_dashboard_screen()
        else:
            self._build_login_screen()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        atexit.register(self._cleanup_all_children)

    def _safe_after(self, ms: int, callback: Callable[..., None]) -> str | None:
        """Schedule callback on Tk main thread, suppress TclError if root destroyed."""

        try:
            if self.root.winfo_exists():
                return self.root.after(ms, callback)
        except tk.TclError:
            pass
        return None

    def _clear_root(self) -> None:
        """Remove the current screen before switching login/dashboard views."""

        for child in self.root.winfo_children():
            child.destroy()

    def _outlined_panel(
        self,
        parent: tk.Misc,
        *,
        padding: tuple[int, int, int, int] = (16, 14, 16, 14),
        height: int | None = None,
    ) -> tuple[tk.Frame, ttk.Frame]:
        """Create a bordered white panel and return its outer and inner frames."""

        outer = tk.Frame(
            parent,
            bg=PANEL_COLOR,
            highlightthickness=1,
            highlightbackground=BORDER_COLOR,
            highlightcolor=BORDER_COLOR,
        )
        if height is not None:
            outer.configure(height=height)
            outer.grid_propagate(False)
            outer.pack_propagate(False)
        inner = ttk.Frame(outer, style="Panel.TFrame", padding=padding)
        inner.pack(fill="both", expand=True)
        return outer, inner

    def _flat_button(
        self,
        parent: tk.Misc,
        *,
        text: str,
        command: Callable[[], None],
        width: int | None = None,
        primary: bool = False,
        compact: bool = False,
        disabled: bool = False,
    ) -> tk.Frame:
        """Create a bordered lightweight button that matches the older dashboard look."""

        bg = "#f1f5f9" if disabled else ("#ffffff" if not primary else "#eef4ff")
        fg = "#64748b" if disabled else ("#0f172a" if not primary else "#1d4ed8")
        hover_bg = bg if disabled else (BUTTON_HOVER_COLOR if not primary else "#e0ecff")
        display_text = text.upper()
        button = tk.Frame(
            parent,
            bg=bg,
            highlightthickness=1,
            highlightbackground=BUTTON_BORDER_COLOR,
            highlightcolor=BUTTON_BORDER_COLOR,
            cursor="arrow" if disabled else "hand2",
        )
        label = tk.Label(
            button,
            text=display_text,
            width=width or 0,
            bg=bg,
            fg=fg,
            font=(UI_FONT_FAMILY, 10, "bold" if primary else "normal"),
            padx=7 if compact else 14,
            pady=3 if compact else 7,
            cursor="arrow" if disabled else "hand2",
        )
        label.pack(fill="both", expand=True)

        def set_bg(color: str) -> None:
            button.configure(bg=color)
            label.configure(bg=color)

        def invoke(_event: object | None = None) -> None:
            if disabled:
                return
            command()

        for widget in (button, label):
            widget.bind("<Button-1>", invoke)
            widget.bind("<Enter>", lambda _event: set_bg(hover_bg))
            widget.bind("<Leave>", lambda _event: set_bg(bg))
        return button

    def _chip(self, parent: tk.Misc, text: str, column: int) -> None:
        """Render one compact session chip in the dashboard header."""

        label = tk.Label(
            parent,
            text=text,
            bg=PANEL_COLOR,
            fg=MUTED_TEXT_COLOR,
            font=(UI_FONT_FAMILY, 9),
            padx=10,
            pady=4,
            highlightthickness=1,
            highlightbackground=BORDER_COLOR,
            highlightcolor=BORDER_COLOR,
        )
        label.grid(row=0, column=column, sticky="w", padx=(0, 6), pady=(7, 0))

    def _styled_info(
        self,
        title: str,
        message: str,
        *,
        parent: tk.Misc | None = None,
        accent: str = "#16a34a",
    ) -> None:
        """Show a styled info dialog matching the dashboard visual language."""

        anchor = parent or self.root
        dlg = tk.Toplevel(anchor)
        dlg.title(title)
        dlg.resizable(False, False)
        dlg.configure(background=SURFACE_COLOR)
        dlg.transient(anchor)
        dlg.grab_set()

        # --- accent bar ---
        tk.Frame(dlg, bg=accent, height=4).pack(fill="x")

        shell = ttk.Frame(dlg, style="App.TFrame", padding=(26, 20, 26, 20))
        shell.pack(fill="both", expand=True)

        # --- icon + title row ---
        header = ttk.Frame(shell, style="App.TFrame")
        header.pack(fill="x")
        icon_canvas = tk.Canvas(header, width=28, height=28, bg=SURFACE_COLOR, highlightthickness=0, bd=0)
        icon_canvas.pack(side="left", padx=(0, 12))
        icon_canvas.create_oval(2, 2, 26, 26, fill=accent, outline=accent)
        icon_canvas.create_text(14, 14, text="✓", fill="#ffffff", font=(UI_FONT_FAMILY, 14, "bold"))
        tk.Label(
            header,
            text=title,
            bg=SURFACE_COLOR,
            fg=TEXT_COLOR,
            font=(UI_FONT_SEMIBOLD, 14, "bold"),
            anchor="w",
        ).pack(side="left", fill="x", expand=True)

        # --- message body ---
        tk.Frame(shell, bg=BORDER_COLOR, height=1).pack(fill="x", pady=(14, 14))
        tk.Label(
            shell,
            text=message,
            bg=SURFACE_COLOR,
            fg=TEXT_COLOR,
            font=(UI_FONT_FAMILY, 11),
            justify="left",
            anchor="nw",
            wraplength=380,
        ).pack(fill="x")

        # --- OK button ---
        btn_row = ttk.Frame(shell, style="App.TFrame")
        btn_row.pack(fill="x", pady=(18, 0))
        self._flat_button(btn_row, text="OK", command=dlg.destroy, primary=True, width=10).pack(side="right")

        dlg.update_idletasks()
        x = anchor.winfo_rootx() + max((anchor.winfo_width() - dlg.winfo_width()) // 2, 0)
        y = anchor.winfo_rooty() + max((anchor.winfo_height() - dlg.winfo_height()) // 3, 0)
        dlg.geometry(f"+{x}+{y}")
        dlg.bind("<Return>", lambda _e: dlg.destroy())
        dlg.bind("<Escape>", lambda _e: dlg.destroy())
        dlg.wait_window()

    def _styled_ask_string(
        self,
        title: str,
        prompt: str,
        *,
        parent: tk.Misc | None = None,
        show: str = "",
    ) -> str | None:
        """Show a styled text-input dialog matching the dashboard visual language."""

        anchor = parent or self.root
        dlg = tk.Toplevel(anchor)
        dlg.title(title)
        dlg.resizable(False, False)
        dlg.configure(background=SURFACE_COLOR)
        dlg.transient(anchor)
        dlg.grab_set()

        result: dict[str, str | None] = {"value": None}

        # --- accent bar ---
        tk.Frame(dlg, bg="#2563eb", height=4).pack(fill="x")

        shell = ttk.Frame(dlg, style="App.TFrame", padding=(26, 20, 26, 20))
        shell.pack(fill="both", expand=True)

        # --- title ---
        tk.Label(
            shell,
            text=title,
            bg=SURFACE_COLOR,
            fg=TEXT_COLOR,
            font=(UI_FONT_SEMIBOLD, 14, "bold"),
            anchor="w",
        ).pack(fill="x")

        # --- prompt ---
        tk.Label(
            shell,
            text=prompt,
            bg=SURFACE_COLOR,
            fg=MUTED_TEXT_COLOR,
            font=(UI_FONT_FAMILY, 10),
            anchor="w",
            wraplength=340,
        ).pack(fill="x", pady=(8, 10))

        # --- input ---
        entry_var = tk.StringVar()
        entry = ttk.Entry(shell, textvariable=entry_var, width=40, show=show)
        entry.pack(fill="x")
        entry.focus_set()

        def on_ok(_event: object | None = None) -> None:
            result["value"] = entry_var.get()
            dlg.destroy()

        def on_cancel(_event: object | None = None) -> None:
            dlg.destroy()

        # --- buttons ---
        btn_row = ttk.Frame(shell, style="App.TFrame")
        btn_row.pack(fill="x", pady=(18, 0))
        self._flat_button(btn_row, text="Hủy", command=on_cancel, width=8).pack(side="right", padx=(8, 0))
        self._flat_button(btn_row, text="OK", command=on_ok, primary=True, width=8).pack(side="right")

        entry.bind("<Return>", on_ok)
        dlg.bind("<Escape>", on_cancel)

        dlg.update_idletasks()
        x = anchor.winfo_rootx() + max((anchor.winfo_width() - dlg.winfo_width()) // 2, 0)
        y = anchor.winfo_rooty() + max((anchor.winfo_height() - dlg.winfo_height()) // 3, 0)
        dlg.geometry(f"+{x}+{y}")
        dlg.wait_window()
        return result["value"]

    def _login_title_canvas(self, parent: tk.Misc) -> tk.Canvas:
        """Create the centered login title with a subtle text outline."""

        canvas = tk.Canvas(parent, width=410, height=38, bg=SURFACE_COLOR, highlightthickness=0, bd=0)
        title = "ĐĂNG NHẬP VNEDU"
        font = (UI_FONT_SEMIBOLD, 18, "bold")
        center_x = 205
        center_y = 21
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            canvas.create_text(center_x + dx, center_y + dy, text=title, fill="#c8d8ee", font=font, anchor="center")
        canvas.create_text(center_x, center_y, text=title, fill="#0b1f3a", font=font, anchor="center")
        return canvas

    def _lock_icon(self, parent: tk.Misc) -> tk.Canvas:
        """Create a small vector lock icon for the login subtitle."""

        canvas = tk.Canvas(parent, width=18, height=18, bg=SURFACE_COLOR, highlightthickness=0, bd=0)
        canvas.create_arc(4, 2, 14, 13, start=0, extent=180, outline="#2563eb", width=2, style="arc")
        canvas.create_rectangle(3, 8, 15, 16, outline="#2563eb", fill="#e8f1ff", width=1)
        canvas.create_oval(8, 11, 10, 13, outline="#1d4ed8", fill="#1d4ed8")
        return canvas

    def _tool_status_kind(self, tool_name: str, *, custom: bool = False) -> str:
        """Return whether a tool is using the external file, embedded fallback, or is missing."""

        try:
            if custom:
                metadata = self.custom_tools.get(tool_name)
                if metadata is None:
                    return "missing"
                if custom_tool_external_path(metadata).exists():
                    return "external"
                return "embedded" if metadata.get("payload") else "missing"
            if external_tool_script_path(tool_name).exists():
                return "external"
        except Exception:  # noqa: BLE001 - status display must never break the dashboard.
            return "missing"
        return "embedded" if tool_name in EMBEDDED_TOOL_PAYLOADS else "missing"

    def _embedded_status_kind(self, tool_name: str, *, custom: bool = False) -> str:
        """Return whether the embedded fallback payload exists for this tool."""

        if custom:
            metadata = self.custom_tools.get(tool_name)
            return "ok" if metadata and metadata.get("payload") else "missing"
        return "ok" if default_tool_payload(tool_name) else "missing"

    def _check_one_tool_health(
        self,
        tool_name: str,
        metadata: dict[str, str],
        *,
        custom: bool = False,
        deep: bool = False,
    ) -> tuple[bool, list[str]]:
        """Validate external and embedded source for one tool without showing dialogs."""

        errors: list[str] = []
        title = metadata.get("title", tool_name)
        external_ok = False
        try:
            external_path = custom_tool_external_path(metadata) if custom else external_tool_script_path(tool_name)
            if external_path.exists():
                if deep:
                    compile(external_path.read_bytes(), str(external_path), "exec")
                external_ok = True
        except Exception as error:  # noqa: BLE001 - background health check must keep going.
            errors.append(f"{title}: file ngoài lỗi ({error})")

        embedded_ok = False
        try:
            if custom:
                payload = metadata.get("payload", "").strip()
                if deep:
                    embedded_source = decode_custom_tool_source(tool_name, metadata)
                    compile(embedded_source, f"<embedded {title}>", "exec")
                    embedded_ok = True
                else:
                    embedded_ok = bool(payload)
            else:
                payload = EMBEDDED_TOOL_PAYLOADS.get(tool_name, "").strip()
                if deep:
                    embedded_source = decode_embedded_tool_source(tool_name)
                    compile(embedded_source, f"<embedded {title}>", "exec")
                    embedded_ok = True
                else:
                    embedded_ok = bool(payload)
        except Exception as error:  # noqa: BLE001 - corrupted fallback should be visible in footer status.
            errors.append(f"{title}: bản nhúng lỗi ({error})")
        if not external_ok and not embedded_ok:
            errors.append(f"{title}: không có nguồn chạy hợp lệ")
        return external_ok or embedded_ok, errors

    def _check_cdp_health(self, debug_port: int) -> tuple[bool, str]:
        """Return whether Chrome DevTools Protocol is reachable on the configured port."""

        # Fast socket pre-check (50ms) to avoid slow urlopen timeout when Chrome is not running.
        try:
            sock = socket.create_connection(("127.0.0.1", debug_port), timeout=0.05)
            sock.close()
        except (OSError, ConnectionRefusedError):
            return False, "Chrome chưa mở CDP."
        try:
            with urlopen(f"http://127.0.0.1:{debug_port}/json/version", timeout=0.5) as response:
                if response.status != 200:
                    return False, f"CDP trả mã {response.status}"
                response.read(256)
        except Exception as error:  # noqa: BLE001 - health status is informational, not fatal.
            return False, str(error).strip() or type(error).__name__
        return True, "OK"

    def _collect_dashboard_health(
        self,
        custom_tools: dict[str, dict[str, str]],
        debug_port: int,
        *,
        deep: bool = False,
    ) -> dict[str, object]:
        """Collect dashboard health data in a worker thread."""

        ready_count = 0
        total_count = len(TOOL_FILES) + len(custom_tools)
        errors: list[str] = []
        for tool_name, metadata in TOOL_FILES.items():
            ready, tool_errors = self._check_one_tool_health(tool_name, metadata, custom=False, deep=deep)
            ready_count += 1 if ready else 0
            errors.extend(tool_errors)
        for tool_id, metadata in custom_tools.items():
            ready, tool_errors = self._check_one_tool_health(tool_id, metadata, custom=True, deep=deep)
            ready_count += 1 if ready else 0
            errors.extend(tool_errors)
        cdp_ok, cdp_detail = self._check_cdp_health(debug_port)
        return {
            "ready_count": ready_count,
            "total_count": total_count,
            "errors": errors,
            "cdp_ok": cdp_ok,
            "cdp_detail": cdp_detail,
        }

    def _format_smart_status(self, health: dict[str, object] | None = None) -> str:
        """Build the compact dashboard footer status text."""

        username = self.username_var.get().strip()
        vnedu_text = "ĐÃ ĐĂNG NHẬP" if username else "CHƯA ĐĂNG NHẬP"
        if not health:
            return f"VNEDU: {vnedu_text} | CDP: ĐANG KIỂM TRA | TOOL: ĐANG KIỂM TRA"
        cdp_text = "OK" if health.get("cdp_ok") else "LỖI"
        ready_count = int(health.get("ready_count") or 0)
        total_count = int(health.get("total_count") or 0)
        return f"VNEDU: {vnedu_text} | CDP: {cdp_text} | TOOL: {ready_count}/{total_count} SẴN SÀNG"

    def _tool_health_cache_key(self, custom_tools: dict[str, dict[str, str]], debug_port: int) -> str:
        """Build a cheap cache key for automatic dashboard health checks."""

        parts = [f"cdp={debug_port}"]
        for tool_name, metadata in TOOL_FILES.items():
            try:
                external_path = external_tool_script_path(tool_name)
                stat = external_path.stat() if external_path.exists() else None
                external_sig = f"{stat.st_mtime_ns}:{stat.st_size}" if stat else "missing"
            except OSError:
                external_sig = "error"
            parts.append(
                f"default:{tool_name}:{external_sig}:payload={len(EMBEDDED_TOOL_PAYLOADS.get(tool_name, ''))}"
            )
        for tool_id, metadata in sorted(custom_tools.items()):
            try:
                external_path = custom_tool_external_path(metadata)
                stat = external_path.stat() if external_path.exists() else None
                external_sig = f"{stat.st_mtime_ns}:{stat.st_size}" if stat else "missing"
            except (OSError, ValueError):
                external_sig = "error"
            parts.append(f"custom:{tool_id}:{external_sig}:payload={len(metadata.get('payload', ''))}")
        return "|".join(parts)

    def _use_cached_health_if_fresh(self, cache_key: str) -> bool:
        """Reuse recent health results so mode switches do not restart heavy checks."""

        if not self.health_result or self._health_cache_key != cache_key:
            return False
        if time.monotonic() - self._health_cache_at > HEALTH_CHECK_CACHE_SECONDS:
            return False
        self.smart_status_var.set(self._format_smart_status(self.health_result))
        return True

    def _schedule_dashboard_health_check(self, *, force: bool = False) -> None:
        """Start a silent background check after the dashboard is rendered."""

        try:
            debug_port = parse_debug_port(self.port_var.get())
        except ValueError:
            debug_port = DEFAULT_DEBUG_PORT
        custom_tools = copy.deepcopy(self.custom_tools)
        cache_key = self._tool_health_cache_key(custom_tools, debug_port)
        if not force and self._use_cached_health_if_fresh(cache_key):
            return
        if self._health_after_id is not None:
            try:
                self.root.after_cancel(self._health_after_id)
            except tk.TclError:
                pass
            self._health_after_id = None
        if self._health_in_progress and not force:
            if self.health_result:
                self.smart_status_var.set(self._format_smart_status(self.health_result))
            else:
                self.smart_status_var.set(self._format_smart_status(None))
            return

        self._health_check_token += 1
        token = self._health_check_token
        self.smart_status_var.set(self._format_smart_status(None))

        def worker() -> None:
            health = self._collect_dashboard_health(custom_tools, debug_port, deep=False)
            self._safe_after(0, lambda: self._apply_dashboard_health_result(token, health, cache_key))

        def start_worker() -> None:
            self._health_after_id = None
            self._health_in_progress = True
            threading.Thread(target=worker, daemon=True, name="VNEDUDashboardHealthCheck").start()

        self._health_after_id = self._safe_after(HEALTH_CHECK_DELAY_MS, start_worker)

    def _apply_dashboard_health_result(self, token: int, health: dict[str, object], cache_key: str) -> None:
        """Apply the latest silent health-check result to the footer."""

        self._health_in_progress = False
        if token != self._health_check_token:
            return
        self.health_result = health
        self._health_cache_key = cache_key
        self._health_cache_at = time.monotonic()
        self.smart_status_var.set(self._format_smart_status(health))
        if health.get("errors"):
            self.status_var.set("Có lỗi nhẹ trong tool. Mở CÔNG CỤ > KIỂM TRA LẠI TOOL để xem chi tiết.")

    def _status_pill(
        self,
        parent: tk.Misc,
        kind: str,
        *,
        palette: dict[str, tuple[str, str, str]] | None = None,
    ) -> tk.Label:
        """Create a small status badge for a tool card."""

        colors = palette or TOOL_STATUS_COLORS
        text, bg, fg = colors.get(kind, colors["missing"])
        return tk.Label(
            parent,
            text=text,
            bg=bg,
            fg=fg,
            font=(UI_FONT_FAMILY, 8, "bold"),
            padx=8,
            pady=2,
        )

    def _tool_runtime_key(self, tool_name: str, *, custom: bool = False) -> str:
        """Return the runtime key used by dashboard cards and child locks."""

        return tool_runtime_key(tool_name, custom=custom)

    def _cleanup_finished_tool_processes(self) -> bool:
        """Drop finished child processes from the in-memory dashboard registry."""

        changed = False
        for runtime_key, process in list(self.tool_processes.items()):
            if process.poll() is None:
                continue
            self.tool_processes.pop(runtime_key, None)
            changed = True
        self.processes = [process for process in self.processes if process.poll() is None]
        return changed

    def _is_tool_running(self, tool_name: str, *, custom: bool = False) -> bool:
        """Return True when this tool is already open through the dashboard wrapper."""

        runtime_key = self._tool_runtime_key(tool_name, custom=custom)
        process = self.tool_processes.get(runtime_key)
        if process is not None:
            if process.poll() is None:
                return True
            self.tool_processes.pop(runtime_key, None)
        return is_tool_process_mutex_active(runtime_key)

    def _collect_running_tool_keys(self) -> set[str]:
        """Return the set of dashboard tools currently known as running."""

        self._cleanup_finished_tool_processes()
        running_keys: set[str] = set()
        for tool_name in TOOL_FILES:
            runtime_key = self._tool_runtime_key(tool_name, custom=False)
            if self._is_tool_running(tool_name, custom=False):
                running_keys.add(runtime_key)
        for tool_id in self.custom_tools:
            runtime_key = self._tool_runtime_key(tool_id, custom=True)
            if self._is_tool_running(tool_id, custom=True):
                running_keys.add(runtime_key)
        return running_keys

    def _refresh_tool_cards(self) -> None:
        """Refresh only the card list after a tool starts or exits."""

        frame = self._dashboard_cards_frame
        if frame is None:
            return
        try:
            if frame.winfo_exists():
                self._render_dashboard_cards(frame)
        except tk.TclError:
            self._dashboard_cards_frame = None

    def _start_tool_process_poll(self) -> None:
        """Start a lightweight poll that keeps card running badges accurate."""

        if self._tool_process_poll_after_id is not None:
            return
        self._tool_process_poll_after_id = self._safe_after(TOOL_PROCESS_POLL_MS, self._poll_tool_processes)

    def _poll_tool_processes(self) -> None:
        """Refresh running indicators when a child tool exits or file changes."""

        self._tool_process_poll_after_id = None
        running_keys = self._collect_running_tool_keys()
        running_changed = running_keys != self._running_tool_keys_snapshot
        file_changed = self._detect_tool_file_changes()
        if running_changed or file_changed:
            self._running_tool_keys_snapshot = running_keys
            self._refresh_tool_cards()
        self._tool_process_poll_after_id = self._safe_after(TOOL_PROCESS_POLL_MS, self._poll_tool_processes)

    def _detect_tool_file_changes(self) -> bool:
        """Cheap stat()-based check for external tool file modifications."""

        changed = False
        for tool_name in TOOL_FILES:
            try:
                path = external_tool_script_path(tool_name)
                mtime = path.stat().st_mtime_ns if path.exists() else 0
            except OSError:
                mtime = 0
            key = f"default:{tool_name}"
            if self._file_mtimes.get(key) != mtime:
                self._file_mtimes[key] = mtime
                changed = True
        for tool_id, metadata in self.custom_tools.items():
            try:
                path = custom_tool_external_path(metadata)
                mtime = path.stat().st_mtime_ns if path.exists() else 0
            except (OSError, ValueError):
                mtime = 0
            key = f"custom:{tool_id}"
            if self._file_mtimes.get(key) != mtime:
                self._file_mtimes[key] = mtime
                changed = True
        return changed

    def _dashboard_tool_items(self) -> list[tuple[str, dict[str, str], bool]]:
        """Return built-in and user-added tools in dashboard display order."""

        items: list[tuple[str, dict[str, str], bool]] = [
            (tool_name, metadata, False) for tool_name, metadata in TOOL_FILES.items()
        ]
        for tool_id, metadata in self.custom_tools.items():
            items.append((tool_id, metadata, True))
        return items

    def _normalized_search_text(self, text: str) -> str:
        """Normalize Vietnamese text for loose dashboard filtering."""

        text = text.replace("đ", "d").replace("Đ", "D")
        decomposed = unicodedata.normalize("NFKD", text)
        ascii_text = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
        return ascii_text.casefold()

    def _tool_matches_filter(self, tool_name: str, metadata: dict[str, str], *, custom: bool) -> bool:
        """Return whether one tool should be visible for the current search filter."""

        query = self._normalized_search_text(self.tool_filter_var.get().strip())
        if not query:
            return True
        haystack = " ".join(
            [
                metadata.get("title", ""),
                metadata.get("description", ""),
                metadata.get("script", ""),
                "tool them moi" if custom else "tool mac dinh",
                tool_name,
            ]
        )
        return query in self._normalized_search_text(haystack)

    def _dashboard_tool_groups(self) -> list[tuple[str, list[tuple[str, dict[str, str], bool]]]]:
        """Return grouped and filtered dashboard tools."""

        default_tools: list[tuple[str, dict[str, str], bool]] = []
        custom_tools: list[tuple[str, dict[str, str], bool]] = []
        for tool_name, metadata in TOOL_FILES.items():
            if self._tool_matches_filter(tool_name, metadata, custom=False):
                default_tools.append((tool_name, metadata, False))
        for tool_id, metadata in self.custom_tools.items():
            if self._tool_matches_filter(tool_id, metadata, custom=True):
                custom_tools.append((tool_id, metadata, True))
        return [("TOOL MẶC ĐỊNH", default_tools), ("TOOL THÊM MỚI", custom_tools)]

    def _card_state_fingerprint(self) -> str:
        """Build a lightweight fingerprint of card-relevant state to skip no-op rebuilds."""

        parts: list[str] = []
        for tool_name in TOOL_FILES:
            rk = self._tool_runtime_key(tool_name, custom=False)
            running = "R" if rk in self._running_tool_keys_snapshot else "_"
            parts.append(f"d:{tool_name}:{running}")
        for tool_id in self.custom_tools:
            rk = self._tool_runtime_key(tool_id, custom=True)
            running = "R" if rk in self._running_tool_keys_snapshot else "_"
            parts.append(f"c:{tool_id}:{running}")
        parts.append(f"filter:{self.tool_filter_var.get()}")
        parts.append(f"mode:{self.advanced_mode_var.get()}")
        return "|".join(parts)

    def _render_dashboard_cards(self, cards_frame: ttk.Frame) -> None:
        """Render grouped dashboard cards, skipping rebuild when state is unchanged."""

        fingerprint = self._card_state_fingerprint()
        if fingerprint == self._prev_card_fingerprint:
            return
        self._prev_card_fingerprint = fingerprint

        for child in cards_frame.winfo_children():
            child.destroy()
        cards_frame.columnconfigure(0, weight=1)
        cards_frame.columnconfigure(1, weight=1)

        row = 0
        visible_count = 0
        for group_title, items in self._dashboard_tool_groups():
            if not items:
                continue
            for index, (tool_name, metadata, is_custom) in enumerate(items):
                self._tool_card(cards_frame, tool_name, row + index // 2, index % 2, metadata=metadata, custom=is_custom)
                visible_count += 1
            row += (len(items) + 1) // 2

        if visible_count == 0:
            empty_panel, empty_frame = self._outlined_panel(cards_frame, padding=(18, 16, 18, 16), height=82)
            empty_panel.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(8, 0))
            ttk.Label(
                empty_frame,
                text="Không có tool phù hợp.",
                style="PanelMuted.TLabel",
                font=(UI_FONT_FAMILY, 10, "bold"),
            ).pack(anchor="w")
        self.tool_count_var.set(f"{visible_count} tool")

    def _scrollable_tool_area(self, parent: ttk.Frame) -> tuple[ttk.Frame, ttk.Frame]:
        """Create a fixed-height, mouse-wheel-scrollable area for tool cards."""

        container = ttk.Frame(parent, style="App.TFrame")
        container.columnconfigure(0, weight=1)
        area_height = 242 if self.advanced_mode_var.get() else 218
        canvas = tk.Canvas(container, bg=SURFACE_COLOR, height=area_height, highlightthickness=0, bd=0)
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        container.rowconfigure(0, weight=1)

        inner = ttk.Frame(canvas, style="App.TFrame")
        window_id = canvas.create_window((0, 0), window=inner, anchor="nw")
        _scroll_debounce_id: list[str | None] = [None]

        def _do_update_scroll() -> None:
            _scroll_debounce_id[0] = None
            bbox = canvas.bbox("all")
            canvas.configure(scrollregion=bbox)
            content_height = 0 if bbox is None else bbox[3] - bbox[1]
            if content_height > canvas.winfo_height() + 4:
                scrollbar.grid(row=0, column=1, sticky="ns", padx=(6, 0))
            else:
                scrollbar.grid_remove()

        def update_scroll_region(_event: object | None = None) -> None:
            if _scroll_debounce_id[0] is not None:
                try:
                    canvas.after_cancel(_scroll_debounce_id[0])
                except tk.TclError:
                    pass
            _scroll_debounce_id[0] = canvas.after(16, _do_update_scroll)

        def resize_inner(event: tk.Event) -> None:
            canvas.itemconfigure(window_id, width=event.width)
            update_scroll_region()

        def on_mouse_wheel(event: tk.Event) -> None:
            delta = -1 if event.delta > 0 else 1
            canvas.yview_scroll(delta, "units")

        def _scoped_mouse_wheel(event: tk.Event) -> None:
            if self._active_scroll_canvas is canvas:
                delta = -1 if event.delta > 0 else 1
                canvas.yview_scroll(delta, "units")

        def bind_mouse_wheel(_event: object) -> None:
            self._active_scroll_canvas = canvas
            canvas.bind_all("<MouseWheel>", _scoped_mouse_wheel)

        def unbind_mouse_wheel(_event: object) -> None:
            if self._active_scroll_canvas is canvas:
                self._active_scroll_canvas = None

        inner.bind("<Configure>", update_scroll_region)
        canvas.bind("<Configure>", resize_inner)
        canvas.bind("<Enter>", bind_mouse_wheel)
        canvas.bind("<Leave>", unbind_mouse_wheel)
        inner.bind("<Enter>", bind_mouse_wheel)
        inner.bind("<Leave>", unbind_mouse_wheel)
        return container, inner

    def _build_login_screen(self) -> None:
        """Build the first screen with only VNEDU login controls."""

        self._clear_root()
        login_width = 455
        login_height = 352
        login_x = max((self.root.winfo_screenwidth() - login_width) // 2, 0)
        login_y = max((self.root.winfo_screenheight() - login_height) // 2, 0)
        self.root.geometry(f"{login_width}x{login_height}+{login_x}+{login_y}")
        self.root.minsize(445, 342)
        self.root.resizable(False, False)
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        shell = ttk.Frame(self.root, style="App.TFrame", padding=(20, 15, 20, 16))
        shell.grid(row=0, column=0, sticky="nsew")
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(2, weight=0)

        login_header = ttk.Frame(shell, style="App.TFrame")
        login_header.grid(row=0, column=0, sticky="ew", pady=(0, 2))
        login_header.columnconfigure(0, weight=1)
        self._login_title_canvas(login_header).grid(row=0, column=0, sticky="")
        subtitle = ttk.Frame(login_header, style="App.TFrame")
        subtitle.grid(row=1, column=0, sticky="", pady=(2, 0))
        self._lock_icon(subtitle).grid(row=0, column=0, sticky="e", padx=(0, 5))
        tk.Label(
            subtitle,
            text="MẬT KHẨU KHÔNG ĐƯỢC LƯU",
            bg=SURFACE_COLOR,
            fg="#48627f",
            font=(UI_FONT_FAMILY, 9, "bold"),
            anchor="center",
            justify="center",
        ).grid(row=0, column=1, sticky="w")
        tk.Frame(login_header, width=92, height=2, bg="#c7dbff").grid(row=2, column=0, pady=(6, 0))

        panel, frame = self._outlined_panel(shell, padding=(18, 13, 18, 15))
        panel.grid(row=2, column=0, sticky="w", pady=(10, 0))
        frame.columnconfigure(0, weight=1)

        ttk.Label(frame, text="THÔNG TIN ĐĂNG NHẬP", style="PanelTitle.TLabel", font=(UI_FONT_FAMILY, 10, "bold")).grid(
            row=0, column=0, sticky="w", pady=(0, 10)
        )

        form = ttk.Frame(frame, style="Panel.TFrame")
        form.grid(row=1, column=0, sticky="w")
        form.columnconfigure(0, minsize=92)

        ttk.Label(form, text="URL VNEDU", style="DialogFieldTitle.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 14), pady=(0, 8)
        )
        ttk.Entry(form, textvariable=self.url_var, width=39).grid(row=0, column=1, sticky="w", pady=(0, 8))

        ttk.Label(form, text="CDP", style="DialogFieldTitle.TLabel").grid(
            row=1, column=0, sticky="w", padx=(0, 14), pady=(0, 8)
        )
        ttk.Entry(form, textvariable=self.port_var, width=10).grid(row=1, column=1, sticky="w", pady=(0, 8))

        ttk.Label(form, text="TÀI KHOẢN", style="DialogFieldTitle.TLabel").grid(
            row=2, column=0, sticky="w", padx=(0, 14), pady=(0, 8)
        )
        self.username_entry = ttk.Entry(form, textvariable=self.username_var, width=32)
        self.username_entry.grid(row=2, column=1, sticky="w", pady=(0, 8))

        ttk.Label(form, text="MẬT KHẨU", style="DialogFieldTitle.TLabel").grid(
            row=3, column=0, sticky="w", padx=(0, 14)
        )
        password_row = ttk.Frame(form, style="Panel.TFrame")
        password_row.grid(row=3, column=1, sticky="w")
        self.password_entry = ttk.Entry(password_row, textvariable=self.password_var, show="*", width=32)
        self.password_entry.grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(
            password_row,
            text="Hiện",
            variable=self.show_password_var,
            command=self._toggle_password,
        ).grid(row=0, column=1, padx=(8, 0))

        action_row = ttk.Frame(frame, style="Panel.TFrame")
        action_row.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        action_row.columnconfigure(1, weight=1)
        status_dot = tk.Canvas(action_row, width=12, height=12, bg=PANEL_COLOR, highlightthickness=0)
        status_dot.create_oval(2, 2, 10, 10, fill="#f59e0b", outline="")
        status_dot.grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Label(action_row, textvariable=self.status_var, style="PanelStatus.TLabel", wraplength=260).grid(
            row=0, column=1, sticky="w"
        )
        self.login_button = self._flat_button(
            action_row,
            text="Đăng nhập",
            command=self._start_login,
            width=9,
            primary=True,
        )
        self.login_button.grid(row=0, column=2, sticky="e", padx=(12, 0))
        tk.Label(
            shell,
            text="Develop by Vu Hao",
            bg=SURFACE_COLOR,
            fg="#708093",
            font=(UI_FONT_FAMILY, 8, "bold"),
            anchor="center",
            justify="center",
        ).grid(row=3, column=0, sticky="ew", pady=(9, 0))
        self.root.bind("<Return>", lambda _event: self._start_login())
        self.username_entry.focus_set()

    def _build_dashboard_screen(self) -> None:
        """Build the dashboard after VNEDU login succeeds."""

        self._clear_root()
        self.root.unbind("<Return>")
        if self.advanced_mode_var.get():
            self.root.geometry("900x395+70+65")
            self.root.minsize(860, 390)
        else:
            self.root.geometry("900x370+70+65")
            self.root.minsize(860, 365)
        self.root.resizable(False, False)
        self.custom_tools = load_custom_tools()
        self._refresh_session_caption()
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=0)
        self.root.rowconfigure(1, weight=0)
        self.root.rowconfigure(2, weight=0)

        header = ttk.Frame(self.root, style="App.TFrame", padding=(22, 18, 22, 10))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(
            header,
            text="VNEDU Control Panel",
            style="DashboardTitle.TLabel",
            font=(UI_FONT_FAMILY, 18, "bold"),
        ).grid(row=0, column=0, sticky="w")
        header_actions = ttk.Frame(header, style="App.TFrame")
        header_actions.grid(row=0, column=1, rowspan=2, sticky="e", padx=(16, 0))
        self._flat_button(header_actions, text="Mở vnEdu", command=self._reopen_vnedu, width=8).grid(
            row=0, column=0, padx=(0, 8)
        )
        self._flat_button(header_actions, text="Thêm tool", command=self._open_add_tool_dialog, width=9).grid(
            row=0, column=1, padx=(0, 8)
        )
        self._flat_button(
            header_actions,
            text="Đổi file",
            command=self._open_replace_tool_dialog,
            width=8,
        ).grid(row=0, column=2, padx=(0, 8))
        tools_button = self._flat_button(
            header_actions,
            text="Công cụ",
            command=lambda: self._open_tools_menu(tools_button),
            width=7,
        )
        tools_button.grid(row=0, column=3)
        chip_frame = ttk.Frame(header, style="App.TFrame")
        chip_frame.grid(row=1, column=0, sticky="w")
        host = urlparse(self.url_var.get().strip()).netloc or self.url_var.get().strip()
        username = self.username_var.get().strip() or "(chưa nhập)"
        self._chip(chip_frame, f"URL  {host}", 0)
        self._chip(chip_frame, f"CDP  {self.port_var.get().strip()}", 1)
        self._chip(chip_frame, f"TK  {username}", 2)

        body = ttk.Frame(self.root, style="App.TFrame", padding=(22, 0, 22, 10))
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=0)

        cards_area, cards_frame = self._scrollable_tool_area(body)
        cards_area.grid(row=0, column=0, sticky="ew")
        self._dashboard_cards_frame = cards_frame
        self._running_tool_keys_snapshot = self._collect_running_tool_keys()
        self._render_dashboard_cards(cards_frame)
        self._start_tool_process_poll()

        footer = ttk.Frame(self.root, style="App.TFrame", padding=(22, 0, 22, 12))
        footer.grid(row=2, column=0, sticky="ew")
        footer.columnconfigure(1, weight=1)
        status_dot = tk.Canvas(footer, width=12, height=12, bg=SURFACE_COLOR, highlightthickness=0)
        status_dot.create_oval(2, 2, 10, 10, fill="#22c55e" if username != "(chưa nhập)" else "#f59e0b", outline="")
        status_dot.grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Label(
            footer,
            textvariable=self.smart_status_var,
            style="Status.TLabel",
            wraplength=620,
        ).grid(row=0, column=1, sticky="w")
        mode_text = "Nâng cao" if self.advanced_mode_var.get() else "Cơ bản"
        self._flat_button(footer, text=mode_text, command=self._toggle_dashboard_mode, compact=True, width=8).grid(
            row=0, column=2, sticky="e", padx=(14, 10)
        )
        ttk.Label(
            footer,
            text="Mật khẩu không được lưu.",
            style="Footer.TLabel",
        ).grid(row=0, column=3, sticky="e")
        self._schedule_dashboard_health_check()

        # --- Keyboard shortcuts ---
        self.root.bind("<Control-r>", lambda _: self._reopen_vnedu())
        self.root.bind("<Control-t>", lambda _: self._open_add_tool_dialog())
        self.root.bind("<Control-m>", lambda _: self._open_tool_manager_dialog())
        self.root.bind("<F5>", lambda _: self._schedule_dashboard_health_check(force=True))
        _get_logger().info("Dashboard built. Tools: %d default + %d custom.", len(TOOL_FILES), len(self.custom_tools))

    def _toggle_password(self) -> None:
        self.password_entry.configure(show="" if self.show_password_var.get() else "*")

    def _refresh_session_caption(self) -> None:
        username = self.username_var.get().strip() or "(chưa nhập tài khoản)"
        self.session_caption_var.set(f"{self.url_var.get().strip()}  |  CDP {self.port_var.get().strip()}  |  {username}")

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        if hasattr(self, "login_button"):
            try:
                self.login_button.configure(state="disabled" if busy else "normal")
            except tk.TclError:
                pass

    def _mark_ui_preview(self) -> None:
        self.status_var.set("Đang xem dashboard. Các tool vẫn mở được, nhưng chưa có phiên đăng nhập chung.")

    def _start_login(self) -> None:
        if self._busy:
            return
        try:
            target_url = validate_target_url(self.url_var.get())
            debug_port = parse_debug_port(self.port_var.get())
        except ValueError as error:
            messagebox.showerror("Dữ liệu chưa hợp lệ", str(error), parent=self.root)
            return

        username = self.username_var.get().strip()
        password = self.password_var.get()
        if not username or not password:
            messagebox.showwarning("Thiếu thông tin", "Hãy nhập đủ tài khoản và mật khẩu VNEDU.", parent=self.root)
            return

        self._set_busy(True)
        self.status_var.set("Đang mở Chrome và đăng nhập VNEDU...")
        threading.Thread(
            target=self._login_worker,
            args=(username, password, target_url, debug_port),
            daemon=True,
            name="VNEDUDashboardLogin",
        ).start()

    def _login_worker(self, username: str, password: str, target_url: str, debug_port: int) -> None:
        try:
            message = login_to_vnedu(username, password, target_url, debug_port, self._progress)
        except Exception as error:  # noqa: BLE001 - user-facing login path.
            self.root.after(0, lambda: self._login_failed(error))
            return
        self.root.after(0, lambda: self._login_done(username, target_url, debug_port, message))

    def _progress(self, _value: float, message: str = "") -> None:
        if message:
            self.root.after(0, lambda: self.status_var.set(message))

    def _login_failed(self, error: Exception) -> None:
        self._set_busy(False)
        detail = str(error).strip() or type(error).__name__
        self.status_var.set(f"Đăng nhập chưa hoàn tất: {detail}")
        messagebox.showerror("Không đăng nhập được", detail, parent=self.root)

    def _login_done(self, username: str, target_url: str, debug_port: int, message: str) -> None:
        self._set_busy(False)
        self.session.update(
            {
                "username": username,
                "target_url": target_url,
                "debug_port": debug_port,
                "message": message,
            }
        )
        self.password_var.set("")
        sync_tool_configs(username=username, target_url=target_url, debug_port=debug_port)
        self._refresh_session_caption()
        self.status_var.set(message or "Phiên VNEDU đã sẵn sàng.")
        self._build_dashboard_screen()

    def _open_add_tool_dialog(self) -> None:
        """Open a step-by-step wizard used to add a new custom Python tool card."""

        dialog = tk.Toplevel(self.root)
        dialog.title("Thêm tool")
        dialog.resizable(False, False)
        dialog.configure(background=SURFACE_COLOR)
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.columnconfigure(0, weight=1)

        title_var = tk.StringVar()
        description_var = tk.StringVar()
        color_var = tk.StringVar(value="Xanh ngọc")
        selected_file = tk.StringVar(value="Chưa chọn file .py")
        wizard_status_var = tk.StringVar(value="Bước 1/5: chọn file Python cần thêm.")
        current_step = tk.IntVar(value=0)
        selected_path: dict[str, Path | None] = {"path": None}
        validation_state: dict[str, object] = {"ok": False, "messages": []}

        shell = ttk.Frame(dialog, style="App.TFrame", padding=(18, 16, 18, 16))
        shell.grid(row=0, column=0, sticky="nsew")
        shell.columnconfigure(0, weight=1)

        ttk.Label(
            shell,
            text="Thêm tool vào dashboard",
            style="DialogTitle.TLabel",
            font=(UI_FONT_FAMILY, 17, "bold"),
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            shell,
            text="Chọn file, nhập thông tin, kiểm tra rồi lưu. Tool sẽ có bản nhúng dự phòng để dùng khi mất file ngoài.",
            style="Muted.TLabel",
            wraplength=560,
        ).grid(row=1, column=0, sticky="w", pady=(5, 14))

        panel, frame = self._outlined_panel(shell, padding=(18, 14, 18, 14))
        panel.grid(row=2, column=0, sticky="ew")
        frame.columnconfigure(1, weight=1)

        def choose_file() -> None:
            path = filedialog.askopenfilename(
                parent=dialog,
                title="Chọn file Python cho tool mới",
                filetypes=[("Python file", "*.py"), ("Tất cả file", "*.*")],
            )
            if path:
                selected_path["path"] = Path(path)
                selected_file.set(path)
                if not title_var.get().strip():
                    title_var.set(Path(path).stem.replace("_", " ").title())
                validation_state["ok"] = False
                validation_state["messages"] = []
                render_step()

        def validate_current_tool() -> bool:
            messages: list[str] = []
            path = selected_path["path"]
            if path is None:
                messages.append("Chưa chọn file .py.")
            elif not path.exists() or not path.is_file():
                messages.append(f"Không tìm thấy file: {path}")
            elif path.suffix.lower() != ".py":
                messages.append("File được chọn không phải .py.")
            if not title_var.get().strip():
                messages.append("Tên card chưa được nhập.")
            try:
                if path is not None and path.exists() and path.is_file() and path.suffix.lower() == ".py":
                    source_bytes = path.read_bytes()
                    compile(source_bytes, str(path), "exec")
                    encoded = encode_embedded_tool_source(source_bytes)
                    if not encoded:
                        messages.append("Không tạo được bản nhúng dự phòng.")
            except Exception as error:  # noqa: BLE001 - user-facing wizard validation.
                messages.append(f"File Python chưa hợp lệ: {error}")
            validation_state["ok"] = not messages
            validation_state["messages"] = messages or ["Kiểm tra OK. File có thể nhúng và tạo card."]
            return bool(validation_state["ok"])

        def apply_add() -> None:
            if not bool(validation_state.get("ok")) and not validate_current_tool():
                current_step.set(3)
                render_step()
                return
            path = selected_path["path"]
            if path is None:
                current_step.set(0)
                render_step()
                return
            try:
                tool_id = self._add_custom_tool(
                    title=title_var.get(),
                    description=description_var.get(),
                    accent=ACCENT_CHOICES.get(color_var.get(), ACCENT_CHOICES["Xanh ngọc"]),
                    source_path=path,
                )
            except Exception as error:  # noqa: BLE001 - user-facing add-tool path.
                wizard_status_var.set(f"Không lưu được tool: {error}")
                validation_state["ok"] = False
                validation_state["messages"] = [str(error)]
                current_step.set(3)
                render_step()
                return
            self.custom_tools = load_custom_tools()
            self.status_var.set(f"Đã thêm tool: {self.custom_tools[tool_id]['title']}.")
            dialog.destroy()
            self._build_dashboard_screen()

        def clear_frame() -> None:
            for child in frame.winfo_children():
                child.destroy()

        def reset_validation() -> None:
            validation_state["ok"] = False
            validation_state["messages"] = []

        def render_step() -> None:
            clear_frame()
            step = current_step.get()
            step_titles = [
                "BƯỚC 1/5 - CHỌN FILE",
                "BƯỚC 2/5 - THÔNG TIN CARD",
                "BƯỚC 3/5 - MÀU CARD",
                "BƯỚC 4/5 - KIỂM TRA",
                "BƯỚC 5/5 - LƯU TOOL",
            ]
            step_messages = [
                "Chọn file .py của tool cần đưa vào dashboard.",
                "Tên card nên ngắn, mô tả nên đủ để người dùng hiểu chức năng.",
                "Chọn màu nhận diện cho card trên dashboard.",
                "App sẽ kiểm tra file và bản nhúng trước khi cho lưu.",
                "Xác nhận thông tin, sau đó lưu tool vào dashboard.",
            ]
            wizard_status_var.set(step_messages[step])
            ttk.Label(frame, text=step_titles[step], style="DialogFieldTitle.TLabel").grid(
                row=0, column=0, columnspan=2, sticky="w", pady=(0, 12)
            )
            if step == 0:
                ttk.Label(frame, text="FILE .PY", style="DialogFieldTitle.TLabel").grid(
                    row=1, column=0, sticky="w", padx=(0, 12), pady=(0, 10)
                )
                ttk.Label(frame, textvariable=selected_file, style="PanelMuted.TLabel", wraplength=420).grid(
                    row=1, column=1, sticky="w", pady=(0, 10)
                )
                self._flat_button(frame, text="Chọn file .py", command=choose_file, primary=True).grid(
                    row=2, column=1, sticky="w"
                )
            elif step == 1:
                ttk.Label(frame, text="TÊN TOOL", style="DialogFieldTitle.TLabel").grid(
                    row=1, column=0, sticky="w", padx=(0, 12), pady=(0, 10)
                )
                title_entry = ttk.Entry(frame, textvariable=title_var, width=46)
                title_entry.grid(row=1, column=1, sticky="ew", pady=(0, 10))
                ttk.Label(frame, text="MÔ TẢ", style="DialogFieldTitle.TLabel").grid(
                    row=2, column=0, sticky="w", padx=(0, 12), pady=(0, 10)
                )
                ttk.Entry(frame, textvariable=description_var, width=46).grid(row=2, column=1, sticky="ew", pady=(0, 10))
                title_entry.focus_set()
            elif step == 2:
                ttk.Label(frame, text="MÀU CARD", style="DialogFieldTitle.TLabel").grid(
                    row=1, column=0, sticky="w", padx=(0, 12), pady=(0, 10)
                )
                color_row = ttk.Frame(frame, style="Panel.TFrame")
                color_row.grid(row=1, column=1, sticky="ew", pady=(0, 10))
                color_combo = ttk.Combobox(
                    color_row,
                    textvariable=color_var,
                    values=list(ACCENT_CHOICES),
                    state="readonly",
                    width=18,
                )
                color_combo.grid(row=0, column=0, sticky="w")
                color_preview = tk.Frame(
                    color_row,
                    width=40,
                    height=24,
                    bg=ACCENT_CHOICES.get(color_var.get(), ACCENT_CHOICES["Xanh ngọc"]),
                )
                color_preview.grid(row=0, column=1, padx=(10, 0))
                color_preview.grid_propagate(False)

                def update_color_preview(_event: object | None = None) -> None:
                    reset_validation()
                    color_preview.configure(bg=ACCENT_CHOICES.get(color_var.get(), ACCENT_CHOICES["Xanh ngọc"]))

                color_combo.bind("<<ComboboxSelected>>", update_color_preview)
            elif step == 3:
                ok = validate_current_tool()
                color = "#047857" if ok else "#b91c1c"
                ttk.Label(
                    frame,
                    text="KẾT QUẢ",
                    style="DialogFieldTitle.TLabel",
                ).grid(row=1, column=0, sticky="nw", padx=(0, 12), pady=(0, 10))
                result_text = "\n".join(f"- {message}" for message in validation_state["messages"])
                tk.Label(
                    frame,
                    text=result_text,
                    bg=PANEL_COLOR,
                    fg=color,
                    font=(UI_FONT_FAMILY, 10, "bold"),
                    justify="left",
                    wraplength=440,
                ).grid(row=1, column=1, sticky="w", pady=(0, 10))
                self._flat_button(frame, text="Kiểm tra lại", command=render_step).grid(row=2, column=1, sticky="w")
            else:
                summary = (
                    f"Tên: {title_var.get().strip()}\n"
                    f"Mô tả: {description_var.get().strip() or 'Tool tùy chỉnh.'}\n"
                    f"Màu: {color_var.get()}\n"
                    f"File: {selected_path['path']}"
                )
                tk.Label(
                    frame,
                    text=summary,
                    bg=PANEL_COLOR,
                    fg=TEXT_COLOR,
                    font=(UI_FONT_FAMILY, 10),
                    justify="left",
                    wraplength=470,
                ).grid(row=1, column=0, columnspan=2, sticky="w")
            render_buttons()

        def can_leave_step(step: int) -> bool:
            if step == 0 and selected_path["path"] is None:
                wizard_status_var.set("Hãy chọn file .py trước.")
                return False
            if step == 1 and not title_var.get().strip():
                wizard_status_var.set("Hãy nhập tên card.")
                return False
            if step == 3 and not validate_current_tool():
                render_step()
                return False
            return True

        def go_previous() -> None:
            if current_step.get() > 0:
                current_step.set(current_step.get() - 1)
                render_step()

        def go_next() -> None:
            step = current_step.get()
            if not can_leave_step(step):
                return
            if step < 4:
                current_step.set(step + 1)
                render_step()

        button_row = ttk.Frame(shell, style="App.TFrame")
        button_row.grid(row=3, column=0, sticky="ew", pady=(14, 0))
        button_row.columnconfigure(0, weight=1)
        status_label = ttk.Label(shell, textvariable=wizard_status_var, style="Footer.TLabel")
        status_label.grid(row=4, column=0, sticky="w", pady=(8, 0))

        def render_buttons() -> None:
            for child in button_row.winfo_children():
                child.destroy()
            if current_step.get() > 0:
                self._flat_button(button_row, text="Quay lại", command=go_previous).grid(row=0, column=1, padx=(0, 8))
            if current_step.get() < 4:
                self._flat_button(button_row, text="Tiếp", command=go_next, primary=True, width=7).grid(
                    row=0, column=2, padx=(0, 8)
                )
            else:
                self._flat_button(button_row, text="Lưu tool", command=apply_add, primary=True, width=8).grid(
                    row=0, column=2, padx=(0, 8)
                )
            self._flat_button(button_row, text="Hủy", command=dialog.destroy, width=7).grid(row=0, column=3)

        render_step()

        dialog.update_idletasks()
        x = self.root.winfo_rootx() + max((self.root.winfo_width() - dialog.winfo_width()) // 2, 0)
        y = self.root.winfo_rooty() + max((self.root.winfo_height() - dialog.winfo_height()) // 3, 0)
        dialog.geometry(f"+{x}+{y}")

    def _open_replace_tool_dialog(self, initial_tool_name: str | None = None) -> None:
        """Open a dialog for replacing tool files and editing custom tool cards."""

        self.custom_tools = load_custom_tools()
        dialog = tk.Toplevel(self.root)
        dialog.title("Đổi file tool")
        dialog.resizable(False, False)
        dialog.configure(background=SURFACE_COLOR)
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(0, weight=1)

        tool_labels: dict[str, tuple[str, bool]] = {}
        for tool_name, metadata in TOOL_FILES.items():
            tool_labels[f"Mặc định | {metadata['title']}  [{metadata['script']}]"] = (tool_name, False)
        for tool_id, metadata in self.custom_tools.items():
            tool_labels[f"Thêm mới | {metadata['title']}  [{Path(metadata['script']).name}]"] = (tool_id, True)

        initial_label = next(
            (label for label, (tool_name, _is_custom) in tool_labels.items() if tool_name == initial_tool_name),
            next(iter(tool_labels)),
        )
        selected_tool_label = tk.StringVar(value=initial_label)
        selected_file = tk.StringVar(value="Chưa chọn file .py")
        selected_path: dict[str, Path | None] = {"path": None}
        tool_detail_var = tk.StringVar()
        title_var = tk.StringVar()
        description_var = tk.StringVar()
        color_var = tk.StringVar(value="Xanh ngọc")

        def color_name_for_hex(color: str) -> str:
            for name, value in ACCENT_CHOICES.items():
                if value.lower() == color.lower():
                    return name
            return "Xanh ngọc"

        shell = ttk.Frame(dialog, style="App.TFrame", padding=(22, 18, 22, 18))
        shell.grid(row=0, column=0, sticky="nsew")
        shell.columnconfigure(0, weight=1)

        ttk.Label(
            shell,
            text="Đổi file tool",
            style="DialogTitle.TLabel",
            font=(UI_FONT_FAMILY, 17, "bold"),
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            shell,
            text="Tool mặc định chỉ đổi file. Tool thêm mới có thể đổi tên, mô tả, màu card và file nhúng.",
            style="Muted.TLabel",
            wraplength=600,
        ).grid(row=1, column=0, sticky="w", pady=(5, 14))

        panel, frame = self._outlined_panel(shell, padding=(18, 14, 18, 14))
        panel.grid(row=2, column=0, sticky="ew")
        frame.columnconfigure(1, weight=1)

        ttk.Label(frame, text="CHỨC NĂNG", style="DialogFieldTitle.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 12), pady=(0, 10)
        )
        combo = ttk.Combobox(
            frame,
            textvariable=selected_tool_label,
            values=list(tool_labels),
            state="readonly",
            width=52,
        )
        combo.grid(row=0, column=1, sticky="ew", pady=(0, 10))

        ttk.Label(frame, text="TÊN HIỂN THỊ", style="DialogFieldTitle.TLabel").grid(
            row=1, column=0, sticky="w", padx=(0, 12), pady=(0, 10)
        )
        title_entry = ttk.Entry(frame, textvariable=title_var, width=46)
        title_entry.grid(row=1, column=1, sticky="ew", pady=(0, 10))

        ttk.Label(frame, text="MÔ TẢ", style="DialogFieldTitle.TLabel").grid(
            row=2, column=0, sticky="w", padx=(0, 12), pady=(0, 10)
        )
        description_entry = ttk.Entry(frame, textvariable=description_var, width=46)
        description_entry.grid(row=2, column=1, sticky="ew", pady=(0, 10))

        ttk.Label(frame, text="MÀU CARD", style="DialogFieldTitle.TLabel").grid(
            row=3, column=0, sticky="w", padx=(0, 12), pady=(0, 10)
        )
        color_row = ttk.Frame(frame, style="Panel.TFrame")
        color_row.grid(row=3, column=1, sticky="ew", pady=(0, 10))
        color_combo = ttk.Combobox(
            color_row,
            textvariable=color_var,
            values=list(ACCENT_CHOICES),
            state="readonly",
            width=18,
        )
        color_combo.grid(row=0, column=0, sticky="w")
        color_preview = tk.Frame(color_row, width=34, height=22, bg=ACCENT_CHOICES[color_var.get()])
        color_preview.grid(row=0, column=1, padx=(10, 0))
        color_preview.grid_propagate(False)

        ttk.Label(frame, text="FILE MỚI", style="DialogFieldTitle.TLabel").grid(
            row=4, column=0, sticky="w", padx=(0, 12), pady=(2, 0)
        )
        ttk.Label(frame, textvariable=selected_file, style="PanelMuted.TLabel", wraplength=450).grid(
            row=4, column=1, sticky="w", pady=(2, 0)
        )
        ttk.Label(frame, text="ĐANG DÙNG", style="DialogFieldTitle.TLabel").grid(
            row=5, column=0, sticky="w", padx=(0, 12), pady=(12, 0)
        )
        ttk.Label(frame, textvariable=tool_detail_var, style="PanelMuted.TLabel", wraplength=450).grid(
            row=5, column=1, sticky="w", pady=(12, 0)
        )

        def update_color_preview(_event: object | None = None) -> None:
            color_preview.configure(bg=ACCENT_CHOICES.get(color_var.get(), ACCENT_CHOICES["Xanh ngọc"]))

        def set_custom_fields_enabled(enabled: bool) -> None:
            entry_state = "normal" if enabled else "disabled"
            title_entry.configure(state=entry_state)
            description_entry.configure(state=entry_state)
            color_combo.configure(state="readonly" if enabled else "disabled")

        def refresh_tool_detail(_event: object | None = None) -> None:
            tool_name, is_custom = tool_labels[selected_tool_label.get()]
            if is_custom:
                metadata = self.custom_tools[tool_name]
                title_var.set(metadata["title"])
                description_var.set(metadata.get("description", ""))
                color_var.set(color_name_for_hex(metadata.get("accent", ACCENT_CHOICES["Xanh ngọc"])))
                set_custom_fields_enabled(True)
                run_text, _run_bg, _run_fg = TOOL_STATUS_COLORS[self._tool_status_kind(tool_name, custom=True)]
                embedded_text, _embedded_bg, _embedded_fg = EMBEDDED_STATUS_COLORS[
                    self._embedded_status_kind(tool_name, custom=True)
                ]
                detail = f"File: {Path(metadata['script']).name}  |  {run_text}  |  {embedded_text}"
            else:
                metadata = TOOL_FILES[tool_name]
                title_var.set(metadata["title"])
                description_var.set(metadata["description"])
                color_var.set(color_name_for_hex(TOOL_ACCENTS.get(tool_name, ACCENT_CHOICES["Xanh ngọc"])))
                set_custom_fields_enabled(False)
                run_text, _run_bg, _run_fg = TOOL_STATUS_COLORS[self._tool_status_kind(tool_name)]
                embedded_text, _embedded_bg, _embedded_fg = EMBEDDED_STATUS_COLORS[
                    self._embedded_status_kind(tool_name)
                ]
                override = load_default_tool_overrides().get(tool_name) or {}
                override_source = override.get("source_name", "").strip()
                file_label = f"File chuẩn: {metadata['script']}"
                if override_source and override_source != metadata["script"]:
                    file_label += f"  (nguồn gốc: {override_source})"
                detail = f"{file_label}  |  {run_text}  |  {embedded_text}"
            tool_detail_var.set(detail)
            update_color_preview()

        def on_tool_selected(_event: object | None = None) -> None:
            selected_path["path"] = None
            selected_file.set("Chưa chọn file .py")
            refresh_tool_detail()

        combo.bind("<<ComboboxSelected>>", on_tool_selected)
        color_combo.bind("<<ComboboxSelected>>", update_color_preview)
        refresh_tool_detail()

        def choose_file() -> None:
            path = filedialog.askopenfilename(
                parent=dialog,
                title="Chọn file Python mới cho tool",
                filetypes=[("Python file", "*.py"), ("Tất cả file", "*.*")],
            )
            if path:
                selected_path["path"] = Path(path)
                selected_file.set(path)

        def apply_change() -> None:
            tool_name, is_custom = tool_labels[selected_tool_label.get()]
            source_path = selected_path["path"]
            if source_path is None:
                messagebox.showwarning("Thiếu file", "Hãy chọn một file .py trước.", parent=dialog)
                return
            try:
                if is_custom:
                    target_path = self._update_custom_tool(
                        tool_name,
                        title=title_var.get(),
                        description=description_var.get(),
                        accent=ACCENT_CHOICES.get(color_var.get(), ACCENT_CHOICES["Xanh ngọc"]),
                        source_path=source_path,
                    )
                    self.custom_tools = load_custom_tools()
                    updated_title = self.custom_tools[tool_name]["title"]
                    reloaded_payload = self.custom_tools[tool_name].get("payload", "").strip()
                    expected_payload = encode_embedded_tool_source(source_path.read_bytes())
                    if reloaded_payload != expected_payload:
                        messagebox.showerror(
                            "Lỗi nhúng tool",
                            f"File đã copy nhưng bản nhúng không khớp. Hãy thử lại.\nTool: {updated_title}",
                            parent=dialog,
                        )
                        return
                    short_source = f"...\\{source_path.parent.name}\\{source_path.name}"
                    self._styled_info(
                        "Đã cập nhật",
                        (
                            f"Tool: {updated_title}\n"
                            f"Thay thế: {target_path.name}\n"
                            f"Bằng: {short_source}"
                        ),
                        parent=dialog,
                    )
                    self.status_var.set(f"Đã cập nhật tool và nhúng thành công: {updated_title}.")
                    dialog.destroy()
                    self._build_dashboard_screen()
                    return
                replace_result = self._replace_tool_source(tool_name, source_path)
            except Exception as error:  # noqa: BLE001 - user-facing file replacement path.
                messagebox.showerror("Không cập nhật được tool", str(error), parent=dialog)
                return
            target_path = Path(str(replace_result["target_path"]))
            external_changed = bool(replace_result["external_changed"])
            embedded_changed = bool(replace_result["embedded_changed"])
            if external_changed and embedded_changed:
                detail_text = "Đã đổi file tool và cập nhật bản dự phòng nhúng."
            elif external_changed:
                detail_text = "Đã đổi file tool. Bản nhúng đã trùng sẵn với file mới."
            elif embedded_changed:
                detail_text = "File đang dùng đã trùng sẵn. Đã đồng bộ lại bản dự phòng nhúng."
            else:
                detail_text = "File đã chọn trùng hoàn toàn với file đang dùng. Không có thay đổi nội dung."
            short_source = f"...\\{source_path.parent.name}\\{source_path.name}"
            self.status_var.set(f"Đã cập nhật {TOOL_FILES[tool_name]['title']}: {target_path.name} ← {source_path.name}")
            refresh_tool_detail()
            self._styled_info(
                "Đã cập nhật",
                (
                    f"Tool: {TOOL_FILES[tool_name]['title']}\n"
                    f"Thay thế: {target_path.name}\n"
                    f"Bằng: {short_source}"
                ),
                parent=dialog,
            )
            dialog.destroy()

        button_row = ttk.Frame(shell, style="App.TFrame")
        button_row.grid(row=3, column=0, sticky="ew", pady=(14, 0))
        button_row.columnconfigure(1, weight=1)
        self._flat_button(button_row, text="Chọn file .py", command=choose_file).grid(row=0, column=0, sticky="w")
        self._flat_button(button_row, text="Cập nhật", command=apply_change, primary=True).grid(
            row=0, column=2, padx=(8, 0)
        )
        self._flat_button(button_row, text="Đóng", command=dialog.destroy).grid(row=0, column=3, padx=(8, 0))

        dialog.update_idletasks()
        x = self.root.winfo_rootx() + max((self.root.winfo_width() - dialog.winfo_width()) // 2, 0)
        y = self.root.winfo_rooty() + max((self.root.winfo_height() - dialog.winfo_height()) // 3, 0)
        dialog.geometry(f"+{x}+{y}")

    def _replace_tool_source(self, tool_name: str, source_path: Path) -> dict[str, object]:
        """Replace one logical tool source and verify both file and embedded snapshot."""

        metadata = TOOL_FILES.get(tool_name)
        if metadata is None:
            raise ValueError(f"Tool không hợp lệ: {tool_name}")
        if not source_path.exists() or not source_path.is_file():
            raise FileNotFoundError(f"Không tìm thấy file đã chọn: {source_path}")
        if source_path.suffix.lower() != ".py":
            raise ValueError("Chỉ hỗ trợ đổi bằng file .py.")

        source_bytes = source_path.read_bytes()
        compile(source_bytes, str(source_path), "exec")
        source_hash = sha256_hex(source_bytes)
        expected_payload = encode_embedded_tool_source(source_bytes)

        target_path = tool_workspace_dir() / metadata["script"]
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_before = target_path.read_bytes() if target_path.exists() else b""
        target_before_hash = sha256_hex(target_before) if target_before else ""
        embedded_before_payload = default_tool_payload(tool_name)
        embedded_before_hash = sha256_hex(embedded_before_payload.encode("ascii")) if embedded_before_payload else ""

        try:
            same_file = source_path.resolve() == target_path.resolve()
        except OSError:
            same_file = False
        external_changed = target_before != source_bytes
        if not same_file and external_changed:
            if target_path.exists():
                backup_dir = target_path.parent / "tool_backups"
                backup_dir.mkdir(parents=True, exist_ok=True)
                backup_path = backup_dir / f"{target_path.stem}_{time.strftime('%Y%m%d_%H%M%S')}{target_path.suffix}.bak"
                shutil.copy2(target_path, backup_path)
            shutil.copy2(source_path, target_path)

        if getattr(sys, "frozen", False):
            overrides = load_default_tool_overrides()
            overrides[tool_name] = {
                "payload": expected_payload,
                "source_hash": source_hash,
                "source_name": source_path.name,
                "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            save_default_tool_overrides(overrides)
        else:
            refresh_embedded_payloads_in_control_panel()
        target_after = target_path.read_bytes()
        if target_after != source_bytes:
            raise RuntimeError(
                "Đổi file chưa hoàn tất: file đang dùng sau cập nhật không khớp file đã chọn."
            )

        embedded_after_payload = default_tool_payload(tool_name)
        if embedded_after_payload != expected_payload:
            raise RuntimeError(
                "Đổi file chưa hoàn tất: bản nhúng dự phòng sau cập nhật không khớp file đã chọn."
            )

        target_after_hash = sha256_hex(target_after)
        embedded_after_hash = sha256_hex(embedded_after_payload.encode("ascii")) if embedded_after_payload else ""
        embedded_changed = embedded_before_payload != embedded_after_payload
        return {
            "target_path": target_path,
            "external_changed": external_changed,
            "embedded_changed": embedded_changed,
            "source_hash": source_hash,
            "target_before_hash": target_before_hash,
            "target_after_hash": target_after_hash,
            "embedded_before_hash": embedded_before_hash,
            "embedded_after_hash": embedded_after_hash,
        }

    def _add_custom_tool(self, title: str, description: str, accent: str, source_path: Path) -> str:
        """Add one user-selected Python file as a custom dashboard tool."""

        clean_title = title.strip()
        if not clean_title:
            raise ValueError("Hãy nhập tên tool.")
        if not source_path.exists() or not source_path.is_file():
            raise FileNotFoundError(f"Không tìm thấy file đã chọn: {source_path}")
        if source_path.suffix.lower() != ".py":
            raise ValueError("Chỉ hỗ trợ thêm file .py.")

        source_bytes = source_path.read_bytes()
        compile(source_bytes, str(source_path), "exec")

        tool_id = custom_tool_id_for_title(clean_title)
        script_name = f"{tool_id}.py"
        relative_script = f"{CUSTOM_TOOLS_DIR_NAME}/{script_name}"
        target_path = tool_workspace_dir() / relative_script
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, target_path)

        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        tools = load_custom_tools()
        tools[tool_id] = {
            "title": clean_title,
            "description": description.strip() or "Tool tùy chỉnh.",
            "script": relative_script,
            "accent": accent if accent.startswith("#") else ACCENT_CHOICES["Xanh ngọc"],
            "payload": encode_embedded_tool_source(source_bytes),
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        save_custom_tools(tools)
        return tool_id

    def _update_custom_tool(
        self,
        tool_id: str,
        *,
        title: str,
        description: str,
        accent: str,
        source_path: Path | None = None,
    ) -> Path:
        """Update display metadata and optionally replace the source of a custom tool."""

        tools = load_custom_tools()
        metadata = tools.get(tool_id)
        if metadata is None:
            raise ValueError(f"Tool tùy chỉnh không tồn tại: {tool_id}")

        clean_title = title.strip()
        if not clean_title:
            raise ValueError("Hãy nhập tên tool.")

        target_path = custom_tool_external_path(metadata)
        if source_path is not None:
            if not source_path.exists() or not source_path.is_file():
                raise FileNotFoundError(f"Không tìm thấy file đã chọn: {source_path}")
            if source_path.suffix.lower() != ".py":
                raise ValueError("Chỉ hỗ trợ file .py.")
            source_bytes = source_path.read_bytes()
            compile(source_bytes, str(source_path), "exec")
            expected_payload = encode_embedded_tool_source(source_bytes)

            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_before = target_path.read_bytes() if target_path.exists() else b""
            try:
                same_file = source_path.resolve() == target_path.resolve()
            except OSError:
                same_file = False
            if not same_file and target_before != source_bytes:
                if target_path.exists():
                    backup_dir = target_path.parent / "tool_backups"
                    backup_dir.mkdir(parents=True, exist_ok=True)
                    backup_path = backup_dir / f"{target_path.stem}_{time.strftime('%Y%m%d_%H%M%S')}.py.bak"
                    shutil.copy2(target_path, backup_path)
                shutil.copy2(source_path, target_path)
            if target_path.read_bytes() != source_bytes:
                raise RuntimeError("Cập nhật tool tùy chỉnh chưa hoàn tất: file đang dùng không khớp file đã chọn.")
            metadata["payload"] = expected_payload

        metadata["title"] = clean_title
        metadata["description"] = description.strip() or "Tool tùy chỉnh."
        metadata["accent"] = accent if accent.startswith("#") else ACCENT_CHOICES["Xanh ngọc"]
        metadata["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        tools[tool_id] = metadata
        save_custom_tools(tools)
        return target_path

    def _tool_card(
        self,
        parent: ttk.Frame,
        tool_name: str,
        row: int,
        column: int,
        *,
        metadata: dict[str, str] | None = None,
        custom: bool = False,
    ) -> None:
        metadata = metadata if metadata is not None else TOOL_FILES[tool_name]
        accent_color = metadata.get("accent") if custom else TOOL_ACCENTS.get(tool_name, "#2563eb")
        if not accent_color:
            accent_color = "#2563eb"
        advanced = self.advanced_mode_var.get()
        is_running = self._is_tool_running(tool_name, custom=custom)
        card = tk.Frame(
            parent,
            bg=PANEL_COLOR,
            height=108 if advanced else 96,
            highlightthickness=2,
            highlightbackground=CARD_BORDER_COLOR,
            highlightcolor=CARD_BORDER_COLOR,
        )
        card.grid_propagate(False)
        card.pack_propagate(False)
        card.grid(
            row=row,
            column=column,
            sticky="nsew",
            padx=(0 if column == 0 else 6, 6 if column == 0 else 0),
            pady=5,
        )
        tk.Frame(card, bg=accent_color, width=5).pack(side="left", fill="y")
        frame = ttk.Frame(card, style="Panel.TFrame", padding=(16, 10, 14, 10))
        frame.pack(side="left", fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=0)
        ttk.Label(
            frame,
            text=metadata["title"].upper(),
            style="CardTitle.TLabel",
            font=(UI_FONT_FAMILY, 12, "bold"),
        ).grid(
            row=0, column=0, sticky="w", padx=(0, 12)
        )
        ttk.Label(frame, text=metadata["description"], wraplength=320, style="PanelMuted.TLabel").grid(
            row=1, column=0, sticky="w", padx=(0, 12), pady=(5, 0)
        )
        row_span = 3 if advanced else 2
        if advanced:
            status_badges = ttk.Frame(frame, style="Panel.TFrame")
            status_badges.grid(row=2, column=0, sticky="w", pady=(8, 0))
            badge_column = 0
            if is_running:
                self._status_pill(status_badges, "running", palette=RUNNING_STATUS_COLORS).grid(
                    row=0, column=badge_column, sticky="w"
                )
                badge_column += 1
            self._status_pill(status_badges, self._tool_status_kind(tool_name, custom=custom)).grid(
                row=0, column=badge_column, sticky="w", padx=(6 if badge_column else 0, 0)
            )
            badge_column += 1
            self._status_pill(
                status_badges,
                self._embedded_status_kind(tool_name, custom=custom),
                palette=EMBEDDED_STATUS_COLORS,
            ).grid(
                row=0, column=badge_column, sticky="w", padx=(6, 0)
            )
        action_frame = ttk.Frame(frame, style="Panel.TFrame")
        action_frame.grid(row=0, column=1, rowspan=row_span, sticky="e")
        self._flat_button(
            action_frame,
            text="Đang mở" if is_running else "Mở",
            command=lambda: self._launch_tool(tool_name, custom=custom),
            width=8 if is_running else 7,
            primary=not is_running,
            disabled=is_running,
        ).grid(row=0, column=0, sticky="e")

    def _launch_tool(self, tool_name: str, *, custom: bool = False) -> None:
        logger = _get_logger()
        try:
            if custom:
                metadata = self.custom_tools.get(tool_name)
                if metadata is None:
                    raise ValueError(f"Tool tùy chỉnh không tồn tại: {tool_name}")
                title = metadata["title"]
                command_flag = "--custom-tool"
            else:
                title = TOOL_FILES[tool_name]["title"]
                command_flag = "--tool"

            runtime_key = self._tool_runtime_key(tool_name, custom=custom)
            if self._is_tool_running(tool_name, custom=custom):
                self.status_var.set(f"{title} đang mở; không mở thêm phiên thứ hai.")
                self._running_tool_keys_snapshot = self._collect_running_tool_keys()
                self._refresh_tool_cards()
                return

            # Loading indicator after duplicate check (avoid unnecessary UI flush)
            self.status_var.set(f"Đang khởi chạy {title}...")
            self.root.update_idletasks()

            self._refresh_session_caption()
            try:
                target_url = validate_target_url(self.url_var.get())
                debug_port = parse_debug_port(self.port_var.get())
                sync_tool_configs(
                    username=self.username_var.get().strip(),
                    target_url=target_url,
                    debug_port=debug_port,
                )
            except ValueError:
                pass
            command = [self._launcher_executable(), str(self._launcher_script_or_arg()), command_flag, tool_name]
            if getattr(sys, "frozen", False):
                command = [sys.executable, command_flag, tool_name]
            process = subprocess.Popen(
                command,
                cwd=str(tool_workspace_dir()),
                env={**os.environ, "VNEDU_CONTROL_PANEL": "1"},
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            )
            self.processes.append(process)
            self.tool_processes[runtime_key] = process
            self._running_tool_keys_snapshot = self._collect_running_tool_keys()
            self._refresh_tool_cards()
            self._start_tool_process_poll()
        except Exception as error:  # noqa: BLE001
            fallback_title = (
                self.custom_tools.get(tool_name, {}).get("title", tool_name)
                if custom
                else TOOL_FILES[tool_name]["title"]
            )
            self._log_error(f"Lỗi mở {fallback_title}", error)
            return
        self.status_var.set(f"Đã mở {title}.")
        logger.info("Launched tool: %s (custom=%s, pid=%s)", tool_name, custom, process.pid)
        self.bus.emit("tool:started", tool_name=tool_name, custom=custom)

    def _open_tool_card_menu(self, anchor: tk.Widget, tool_name: str, *, custom: bool = False) -> None:
        """Show compact per-card actions without cluttering the card surface."""

        menu = tk.Menu(
            self.root,
            tearoff=False,
            bg=PANEL_COLOR,
            fg=TEXT_COLOR,
            activebackground=BUTTON_HOVER_COLOR,
            activeforeground=TEXT_COLOR,
            font=(UI_FONT_FAMILY, 10),
        )
        menu.add_command(label="Xem trạng thái", command=lambda: self._show_tool_status(tool_name, custom=custom))
        menu.add_command(label="Đổi thông tin/file" if custom else "Đổi file", command=lambda: self._open_replace_tool_dialog(tool_name))
        if custom:
            menu.add_separator()
            menu.add_command(label="Xóa card", command=lambda: self._delete_custom_tool_card(tool_name))
        try:
            menu.tk_popup(anchor.winfo_rootx(), anchor.winfo_rooty() + anchor.winfo_height() + 4)
        finally:
            menu.grab_release()

    def _show_tool_status(self, tool_name: str, *, custom: bool = False, parent: tk.Misc | None = None) -> None:
        """Show where a tool is loaded from and whether its fallback exists."""

        try:
            if custom:
                metadata = self.custom_tools.get(tool_name)
                if metadata is None:
                    raise ValueError(f"Tool tùy chỉnh không tồn tại: {tool_name}")
                title = metadata["title"]
                external_path = custom_tool_external_path(metadata)
                relative_script = Path(normalize_custom_script_path(metadata["script"]))
                embedded_path = embedded_tool_dir() / CUSTOM_TOOLS_DIR_NAME / relative_script.name
            else:
                metadata = TOOL_FILES[tool_name]
                title = metadata["title"]
                external_path = external_tool_script_path(tool_name)
                embedded_path = embedded_tool_dir() / metadata["script"]
            run_text, _run_bg, _run_fg = TOOL_STATUS_COLORS[self._tool_status_kind(tool_name, custom=custom)]
            embedded_text, _embedded_bg, _embedded_fg = EMBEDDED_STATUS_COLORS[
                self._embedded_status_kind(tool_name, custom=custom)
            ]
            open_text = "ĐANG MỞ" if self._is_tool_running(tool_name, custom=custom) else "CHƯA MỞ"
            source_info = ""
            if not custom:
                override = load_default_tool_overrides().get(tool_name) or {}
                override_source = override.get("source_name", "").strip()
                override_time = override.get("updated_at", "").strip()
                if override_source:
                    source_info = f"\nFile nguồn gốc: {override_source}"
                    if override_time:
                        source_info += f" (cập nhật: {override_time})"
            message = (
                f"Tool: {title}\n"
                f"Trạng thái mở: {open_text}\n"
                f"Nguồn đang dùng: {run_text}\n"
                f"Bản nhúng: {embedded_text}"
                f"{source_info}\n\n"
                f"File ngoài:\n{external_path}\n\n"
                f"File nhúng dự phòng:\n{embedded_path}"
            )
            messagebox.showinfo("Trạng thái tool", message, parent=parent or self.root)
        except Exception as error:  # noqa: BLE001
            self._log_error("Không đọc được trạng thái tool", error)

    def _toggle_dashboard_mode(self) -> None:
        """Switch between simple and advanced dashboard controls."""

        if self.advanced_mode_var.get():
            self.advanced_mode_var.set(False)
            mode = "basic"
            self.status_var.set("Đã bật chế độ cơ bản.")
        else:
            self.advanced_mode_var.set(True)
            mode = "advanced"
            self.status_var.set("Đã bật chế độ nâng cao.")
        self.app_config["dashboard_mode"] = mode
        try:
            save_app_config(self.app_config)
        except OSError as error:
            self._log_error("Không lưu được chế độ giao diện", error)
            return
        self._build_dashboard_screen()

    def _open_tool_manager_dialog(self) -> None:
        """Open a focused management surface for all built-in and custom tools."""

        self.custom_tools = load_custom_tools()
        dialog = tk.Toplevel(self.root)
        dialog.title("Quản lý tool")
        dialog.resizable(False, False)
        dialog.configure(background=SURFACE_COLOR)
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(0, weight=1)

        manager_filter_var = tk.StringVar(value=self.tool_filter_var.get().strip())
        manager_count_var = tk.StringVar()

        shell = ttk.Frame(dialog, style="App.TFrame", padding=(22, 18, 22, 18))
        shell.grid(row=0, column=0, sticky="nsew")
        shell.columnconfigure(0, weight=1)

        header = ttk.Frame(shell, style="App.TFrame")
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(
            header,
            text="Quản lý tool",
            style="DialogTitle.TLabel",
            font=(UI_FONT_FAMILY, 17, "bold"),
        ).grid(row=0, column=0, sticky="w")
        self._flat_button(
            header,
            text="Thêm tool",
            command=lambda: (dialog.destroy(), self._open_add_tool_dialog()),
            width=8,
        ).grid(row=0, column=1, sticky="e")
        ttk.Label(
            shell,
            text="Theo dõi file ngoài, bản nhúng và chỉnh tool thêm mới tại một nơi.",
            style="Muted.TLabel",
            wraplength=560,
        ).grid(row=1, column=0, sticky="w", pady=(5, 12))

        filter_row = ttk.Frame(shell, style="App.TFrame")
        filter_row.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        filter_row.columnconfigure(1, weight=1)
        ttk.Label(filter_row, text="TÌM TOOL", style="Muted.TLabel", font=(UI_FONT_FAMILY, 9, "bold")).grid(
            row=0, column=0, sticky="w", padx=(0, 8)
        )
        manager_entry = ttk.Entry(filter_row, textvariable=manager_filter_var, width=28)
        manager_entry.grid(row=0, column=1, sticky="w")
        ttk.Label(filter_row, textvariable=manager_count_var, style="Footer.TLabel").grid(row=0, column=2, sticky="e")

        list_container = ttk.Frame(shell, style="App.TFrame")
        list_container.grid(row=3, column=0, sticky="ew")
        list_container.columnconfigure(0, weight=1)
        canvas = tk.Canvas(list_container, bg=SURFACE_COLOR, height=245, highlightthickness=0, bd=0)
        scrollbar = ttk.Scrollbar(list_container, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.grid(row=0, column=0, sticky="ew")
        inner = ttk.Frame(canvas, style="App.TFrame")
        window_id = canvas.create_window((0, 0), window=inner, anchor="nw")

        def update_scroll_region(_event: object | None = None) -> None:
            bbox = canvas.bbox("all")
            canvas.configure(scrollregion=bbox)
            content_height = 0 if bbox is None else bbox[3] - bbox[1]
            if content_height > canvas.winfo_height() + 4:
                scrollbar.grid(row=0, column=1, sticky="ns", padx=(6, 0))
            else:
                scrollbar.grid_remove()

        def resize_inner(event: tk.Event) -> None:
            canvas.itemconfigure(window_id, width=event.width)
            update_scroll_region()

        canvas.bind("<Configure>", resize_inner)
        inner.bind("<Configure>", update_scroll_region)

        def item_matches(tool_id: str, metadata: dict[str, str], *, custom: bool) -> bool:
            query = self._normalized_search_text(manager_filter_var.get().strip())
            if not query:
                return True
            haystack = " ".join(
                [
                    metadata.get("title", ""),
                    metadata.get("description", ""),
                    metadata.get("script", ""),
                    tool_id,
                    "tool them moi" if custom else "tool mac dinh",
                ]
            )
            return query in self._normalized_search_text(haystack)

        def open_replace(tool_id: str) -> None:
            dialog.destroy()
            self._open_replace_tool_dialog(tool_id)

        def delete_custom(tool_id: str) -> None:
            metadata = self.custom_tools.get(tool_id)
            if metadata is None:
                messagebox.showwarning("Không tìm thấy card", "Card tool này không còn tồn tại.", parent=dialog)
                return
            title = metadata.get("title", tool_id)
            confirmed = messagebox.askyesno(
                "Xóa card tool",
                (
                    f"Xóa card \"{title}\" khỏi dashboard?\n\n"
                    "App chỉ xóa bản copy và bản nhúng do dashboard quản lý. File gốc bạn đã chọn sẽ không bị xóa."
                ),
                parent=dialog,
            )
            if not confirmed:
                return
            try:
                removed_title, cleanup_errors = self._remove_custom_tool(tool_id)
            except Exception as error:  # noqa: BLE001
                self._log_error(f"Không xóa được {title}", error)
                return
            self.custom_tools = load_custom_tools()
            self.status_var.set(f"Đã xóa card tool: {removed_title}.")
            dialog.destroy()
            self._build_dashboard_screen()
            if cleanup_errors:
                messagebox.showwarning(
                    "Đã xóa card",
                    "Card đã được xóa, nhưng có file phụ chưa dọn được:\n\n" + "\n".join(cleanup_errors),
                    parent=self.root,
                )

        def render_rows(_event: object | None = None) -> None:
            for child in inner.winfo_children():
                child.destroy()
            inner.columnconfigure(0, weight=1)
            visible = 0
            row = 0
            groups = [
                ("TOOL MẶC ĐỊNH", [(tool_id, metadata, False) for tool_id, metadata in TOOL_FILES.items()]),
                ("TOOL THÊM MỚI", [(tool_id, metadata, True) for tool_id, metadata in self.custom_tools.items()]),
            ]
            for group_title, raw_items in groups:
                items = [
                    (tool_id, metadata, custom)
                    for tool_id, metadata, custom in raw_items
                    if item_matches(tool_id, metadata, custom=custom)
                ]
                if not items:
                    continue
                ttk.Label(
                    inner,
                    text=group_title,
                    style="Muted.TLabel",
                    font=(UI_FONT_FAMILY, 9, "bold"),
                ).grid(row=row, column=0, sticky="w", pady=(4 if row else 0, 4))
                row += 1
                for tool_id, metadata, custom in items:
                    row_panel, row_frame = self._outlined_panel(inner, padding=(14, 10, 14, 10), height=104)
                    row_panel.grid(row=row, column=0, sticky="ew", pady=(0, 8))
                    row_frame.columnconfigure(0, weight=1)
                    ttk.Label(
                        row_frame,
                        text=metadata["title"].upper(),
                        style="CardTitle.TLabel",
                        font=(UI_FONT_FAMILY, 11, "bold"),
                    ).grid(row=0, column=0, sticky="w")
                    script_label = Path(metadata["script"]).name
                    type_text = "Thêm mới" if custom else "Mặc định"
                    ttk.Label(
                        row_frame,
                        text=f"{type_text} | {script_label}",
                        style="PanelMuted.TLabel",
                    ).grid(row=1, column=0, sticky="w", pady=(4, 0))
                    status_row = ttk.Frame(row_frame, style="Panel.TFrame")
                    status_row.grid(row=2, column=0, sticky="w", pady=(7, 0))
                    self._status_pill(status_row, self._tool_status_kind(tool_id, custom=custom)).grid(row=0, column=0)
                    self._status_pill(
                        status_row,
                        self._embedded_status_kind(tool_id, custom=custom),
                        palette=EMBEDDED_STATUS_COLORS,
                    ).grid(row=0, column=1, padx=(6, 0))
                    actions = ttk.Frame(row_frame, style="Panel.TFrame")
                    actions.grid(row=0, column=1, rowspan=3, sticky="e")
                    self._flat_button(
                        actions,
                        text="Mở",
                        command=lambda item_id=tool_id, item_custom=custom: self._launch_tool(item_id, custom=item_custom),
                        width=6,
                        primary=True,
                    ).grid(row=0, column=0, padx=(0, 6))
                    self._flat_button(
                        actions,
                        text="Sửa" if custom else "Đổi file",
                        command=lambda item_id=tool_id: open_replace(item_id),
                        width=7,
                    ).grid(row=0, column=1)
                    self._flat_button(
                        actions,
                        text="Trạng thái",
                        command=lambda item_id=tool_id, item_custom=custom: self._show_tool_status(
                            item_id,
                            custom=item_custom,
                            parent=dialog,
                        ),
                        width=8,
                    ).grid(row=1, column=0, padx=(0, 6), pady=(7, 0))
                    if custom:
                        self._flat_button(
                            actions,
                            text="Xóa",
                            command=lambda item_id=tool_id: delete_custom(item_id),
                            width=5,
                        ).grid(row=1, column=1, sticky="ew", pady=(7, 0))
                    visible += 1
                    row += 1
            if visible == 0:
                empty_panel, empty_frame = self._outlined_panel(inner, padding=(14, 14, 14, 14), height=70)
                empty_panel.grid(row=0, column=0, sticky="ew")
                ttk.Label(empty_frame, text="Không có tool phù hợp.", style="PanelMuted.TLabel").pack(anchor="w")
            manager_count_var.set(f"{visible} tool")

        manager_entry.bind("<KeyRelease>", render_rows)
        manager_entry.bind("<<Paste>>", lambda _event: self.root.after(1, render_rows))
        render_rows()

        footer = ttk.Frame(shell, style="App.TFrame")
        footer.grid(row=4, column=0, sticky="ew", pady=(14, 0))
        footer.columnconfigure(0, weight=1)
        self._flat_button(footer, text="Kiểm tra", command=self._self_check, width=8).grid(row=0, column=1, padx=(0, 8))
        self._flat_button(footer, text="Đóng", command=dialog.destroy, width=8).grid(row=0, column=2)

        dialog.update_idletasks()
        dialog.geometry("640x470")
        x = self.root.winfo_rootx() + max((self.root.winfo_width() - dialog.winfo_width()) // 2, 0)
        y = self.root.winfo_rooty() + max((self.root.winfo_height() - dialog.winfo_height()) // 4, 0)
        dialog.geometry(f"+{x}+{y}")

    def _remove_custom_tool(self, tool_id: str) -> tuple[str, list[str]]:
        """Remove a user-added card and its dashboard-managed copies."""

        tools = load_custom_tools()
        metadata = tools.pop(tool_id, None)
        if metadata is None:
            raise ValueError(f"Tool tùy chỉnh không tồn tại: {tool_id}")

        save_custom_tools(tools)
        title = metadata.get("title", tool_id)
        cleanup_errors: list[str] = []
        candidate_paths: list[Path] = []
        try:
            candidate_paths.append(custom_tool_external_path(metadata))
        except Exception as error:  # noqa: BLE001 - the card is already removed; report cleanup only.
            cleanup_errors.append(f"Không xác định được file ngoài: {error}")
        try:
            relative_script = Path(normalize_custom_script_path(metadata["script"]))
            candidate_paths.append(embedded_tool_dir() / CUSTOM_TOOLS_DIR_NAME / relative_script.name)
        except Exception as error:  # noqa: BLE001
            cleanup_errors.append(f"Không xác định được file nhúng: {error}")

        for path in dict.fromkeys(candidate_paths):
            try:
                path.unlink()
            except FileNotFoundError:
                continue
            except OSError as error:
                cleanup_errors.append(f"{path}: {error}")
        return title, cleanup_errors

    def _delete_custom_tool_card(self, tool_id: str) -> None:
        """Ask for confirmation and delete one user-added dashboard card."""

        self.custom_tools = load_custom_tools()
        metadata = self.custom_tools.get(tool_id)
        if metadata is None:
            messagebox.showwarning("Không tìm thấy card", "Card tool này không còn tồn tại.", parent=self.root)
            self._build_dashboard_screen()
            return

        title = metadata.get("title", tool_id)
        confirmed = messagebox.askyesno(
            "Xóa card tool",
            (
                f"Xóa card \"{title}\" khỏi dashboard?\n\n"
                "App chỉ xóa bản copy và bản nhúng do dashboard quản lý. "
                "File gốc bạn đã chọn sẽ không bị xóa."
            ),
            parent=self.root,
        )
        if not confirmed:
            return

        try:
            removed_title, cleanup_errors = self._remove_custom_tool(tool_id)
        except Exception as error:  # noqa: BLE001 - user-facing deletion path.
            self._log_error(f"Không xóa được {title}", error)
            return

        self.custom_tools = load_custom_tools()
        self.status_var.set(f"Đã xóa card tool: {removed_title}.")
        self._build_dashboard_screen()
        if cleanup_errors:
            messagebox.showwarning(
                "Đã xóa card",
                "Card đã được xóa, nhưng có file phụ chưa dọn được:\n\n" + "\n".join(cleanup_errors),
                parent=self.root,
            )

    def _launcher_executable(self) -> str:
        return sys.executable

    def _launcher_script_or_arg(self) -> Path:
        return Path(__file__).resolve()

    def _reopen_vnedu(self) -> None:
        password = self.password_var.get()
        if not password:
            password = self._styled_ask_string(
                "Mật khẩu VNEDU",
                "Nhập mật khẩu VNEDU để mở lại phiên đăng nhập:",
                show="*",
            )
            if not password:
                self.status_var.set("Hủy mở lại vnEdu: chưa nhập mật khẩu.")
                return
        self.status_var.set("Đang mở lại trang vnEdu...")
        threading.Thread(target=self._reopen_worker, args=(password,), daemon=True, name="VNEDUReopen").start()

    def _reopen_worker(self, password: str) -> None:
        try:
            target_url = str(self.session.get("target_url") or DEFAULT_VNEDU_URL)
            debug_port = int(self.session.get("debug_port") or DEFAULT_DEBUG_PORT)
            username = str(self.session.get("username") or self.username_var.get().strip())
            message = login_to_vnedu(username, password, target_url, debug_port)
        except Exception as error:  # noqa: BLE001
            self.root.after(0, lambda: self._log_error("Lỗi mở lại vnEdu", error))
            return
        self.root.after(0, lambda: self.status_var.set(message or "Đã mở lại trang vnEdu."))

    def _open_tool_folder(self) -> None:
        try:
            os.startfile(str(tool_workspace_dir()))  # type: ignore[attr-defined]
        except Exception as error:  # noqa: BLE001
            self._log_error("Không mở được thư mục tool", error)

    def _open_app_data_folder(self) -> None:
        """Open the folder that stores embedded tools, backups, and logs."""

        try:
            target = app_data_dir()
            target.mkdir(parents=True, exist_ok=True)
            os.startfile(str(target))  # type: ignore[attr-defined]
        except Exception as error:  # noqa: BLE001
            self._log_error("Không mở được thư mục dữ liệu app", error)

    def _backup_tool_config(self) -> None:
        """Export custom-tool configuration, including embedded payloads, to JSON."""

        self.custom_tools = load_custom_tools()
        default_name = f"vnedu_tool_backup_{time.strftime('%Y%m%d_%H%M%S')}.json"
        target = filedialog.asksaveasfilename(
            parent=self.root,
            title="Sao lưu cấu hình tool",
            initialfile=default_name,
            defaultextension=".json",
            filetypes=[("JSON backup", "*.json"), ("All files", "*.*")],
        )
        if not target:
            return
        backup = {
            "backup_type": "vnedu_control_panel_tools",
            "version": 1,
            "app_version": APP_VERSION,
            "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "custom_tools": {"version": 1, "tools": self.custom_tools},
        }
        try:
            Path(target).write_text(json.dumps(backup, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as error:
            self._log_error("Không sao lưu được cấu hình tool", error)
            return
        self.status_var.set(f"Đã sao lưu cấu hình tool: {Path(target).name}")
        messagebox.showinfo("Sao lưu cấu hình tool", "Đã sao lưu xong cấu hình tool.", parent=self.root)

    def _restore_tool_config(self) -> None:
        """Restore custom-tool configuration from a dashboard backup JSON file."""

        source = filedialog.askopenfilename(
            parent=self.root,
            title="Khôi phục cấu hình tool",
            filetypes=[("JSON backup", "*.json"), ("All files", "*.*")],
        )
        if not source:
            return
        try:
            raw = json.loads(Path(source).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            self._log_error("File sao lưu không hợp lệ", error)
            return
        if not isinstance(raw, dict):
            messagebox.showerror("File sao lưu không hợp lệ", "File sao lưu phải là dữ liệu JSON dạng object.", parent=self.root)
            return

        raw_tools: object
        custom_tools_payload = raw.get("custom_tools")
        if isinstance(custom_tools_payload, dict):
            raw_tools = custom_tools_payload.get("tools", {})
        else:
            raw_tools = raw.get("tools", {})
        restored_tools = sanitize_custom_tools(raw_tools)
        if raw_tools and not restored_tools:
            messagebox.showerror(
                "File sao lưu không hợp lệ",
                "Không tìm thấy card tool hợp lệ trong file sao lưu.",
                parent=self.root,
            )
            return
        confirmed = messagebox.askyesno(
            "Khôi phục cấu hình tool",
            (
                f"Khôi phục {len(restored_tools)} card tool từ file sao lưu?\n\n"
                "Cấu hình tool thêm mới hiện tại sẽ được thay thế."
            ),
            parent=self.root,
        )
        if not confirmed:
            return
        try:
            save_custom_tools(restored_tools)
        except OSError as error:
            self._log_error("Không khôi phục được cấu hình tool", error)
            return
        self.custom_tools = load_custom_tools()
        self.status_var.set(f"Đã khôi phục {len(self.custom_tools)} card tool.")
        self._build_dashboard_screen()
        messagebox.showinfo("Khôi phục cấu hình tool", "Đã khôi phục xong cấu hình tool.", parent=self.root)

    def _tool_report_text(self) -> str:
        """Build a plain-text diagnostic report for built-in and custom tools."""

        lines = [
            f"{APP_TITLE} v{APP_VERSION}",
            f"Báo cáo tool: {time.strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            f"Thư mục app: {app_data_dir()}",
            f"Thư mục chạy tool: {tool_workspace_dir()}",
            f"Thư mục nhúng dự phòng: {embedded_tool_dir()}",
            "",
            "TOOL MẶC ĐỊNH",
        ]
        for tool_name, metadata in TOOL_FILES.items():
            external_path = external_tool_script_path(tool_name)
            embedded_path = embedded_tool_dir() / metadata["script"]
            run_text = TOOL_STATUS_COLORS[self._tool_status_kind(tool_name)][0]
            embedded_text = EMBEDDED_STATUS_COLORS[self._embedded_status_kind(tool_name)][0]
            lines.extend(
                [
                    f"- {metadata['title']} [{tool_name}]",
                    f"  File chuẩn: {metadata['script']}",
                    f"  Trạng thái chạy: {run_text}",
                    f"  Bản nhúng: {embedded_text}",
                    f"  File ngoài: {external_path} | tồn tại: {'có' if external_path.exists() else 'không'}",
                    f"  File nhúng runtime: {embedded_path} | tồn tại: {'có' if embedded_path.exists() else 'không'}",
                ]
            )

        self.custom_tools = load_custom_tools()
        lines.extend(["", f"TOOL THÊM MỚI ({len(self.custom_tools)})"])
        if not self.custom_tools:
            lines.append("- Chưa có tool thêm mới.")
        for tool_id, metadata in self.custom_tools.items():
            try:
                external_path = custom_tool_external_path(metadata)
                ext_exists = external_path.exists()
            except Exception as error:  # noqa: BLE001
                external_path = f"<lỗi đường dẫn: {error}>"
                ext_exists = False
            embedded_path = embedded_tool_dir() / CUSTOM_TOOLS_DIR_NAME / Path(metadata.get("script", "")).name
            run_text = TOOL_STATUS_COLORS[self._tool_status_kind(tool_id, custom=True)][0]
            embedded_text = EMBEDDED_STATUS_COLORS[self._embedded_status_kind(tool_id, custom=True)][0]
            try:
                emb_exists = embedded_path.exists()
            except OSError:
                emb_exists = False
            lines.extend(
                [
                    f"- {metadata.get('title', tool_id)} [{tool_id}]",
                    f"  File: {metadata.get('script', '')}",
                    f"  Trạng thái chạy: {run_text}",
                    f"  Bản nhúng: {embedded_text}",
                    f"  File ngoài: {external_path} | tồn tại: {'có' if ext_exists else 'không'}",
                    f"  File nhúng runtime: {embedded_path} | tồn tại: {'có' if emb_exists else 'không'}",
                ]
            )
        return "\n".join(lines) + "\n"

    def _export_tool_report(self) -> None:
        """Save a readable tool-status report for troubleshooting."""

        default_name = f"vnedu_tool_report_{time.strftime('%Y%m%d_%H%M%S')}.txt"
        target = filedialog.asksaveasfilename(
            parent=self.root,
            title="Xuất báo cáo tool",
            initialfile=default_name,
            defaultextension=".txt",
            filetypes=[("Text report", "*.txt"), ("All files", "*.*")],
        )
        if not target:
            return
        try:
            Path(target).write_text(self._tool_report_text(), encoding="utf-8")
        except OSError as error:
            self._log_error("Không xuất được báo cáo tool", error)
            return
        self.status_var.set(f"Đã xuất báo cáo tool: {Path(target).name}")
        messagebox.showinfo("Xuất báo cáo tool", "Đã xuất xong báo cáo tool.", parent=self.root)

    def _open_tools_menu(self, anchor: tk.Widget) -> None:
        """Show secondary maintenance actions without taking dashboard space."""

        menu = tk.Menu(
            self.root,
            tearoff=False,
            bg=PANEL_COLOR,
            fg=TEXT_COLOR,
            activebackground=BUTTON_HOVER_COLOR,
            activeforeground=TEXT_COLOR,
            font=(UI_FONT_FAMILY, 10),
        )
        menu.add_command(label="Quản lý tool", command=self._open_tool_manager_dialog)
        menu.add_command(label="Đổi file nhanh", command=self._open_replace_tool_dialog)
        menu.add_separator()
        menu.add_command(label="Kiểm tra lại tool", command=self._self_check)
        menu.add_command(label="Xuất báo cáo tool", command=self._export_tool_report)
        menu.add_separator()
        menu.add_command(label="Sao lưu cấu hình tool", command=self._backup_tool_config)
        menu.add_command(label="Khôi phục cấu hình tool", command=self._restore_tool_config)
        menu.add_separator()
        menu.add_command(label="Mở thư mục tool", command=self._open_tool_folder)
        menu.add_command(label="Mở thư mục dữ liệu app", command=self._open_app_data_folder)
        try:
            menu.tk_popup(anchor.winfo_rootx(), anchor.winfo_rooty() + anchor.winfo_height() + 4)
        finally:
            menu.grab_release()

    def _self_check(self) -> None:
        errors: list[str] = []
        for tool_name in TOOL_FILES:
            try:
                resolve_tool_script(tool_name)
            except Exception as error:  # noqa: BLE001
                errors.append(f"{tool_name}: {error}")
        self.custom_tools = load_custom_tools()
        for tool_id, metadata in self.custom_tools.items():
            try:
                resolve_custom_tool_script(tool_id)
            except Exception as error:  # noqa: BLE001
                errors.append(f"{metadata.get('title', tool_id)}: {error}")
        if errors:
            messagebox.showerror("Kiểm tra tool", "\n".join(errors), parent=self.root)
            return
        custom_count = len(self.custom_tools)
        if custom_count:
            message = f"Đã tìm thấy đủ 4 tool mặc định và {custom_count} tool thêm mới."
        else:
            message = "Đã tìm thấy đủ 4 tool mặc định."
        messagebox.showinfo("Kiểm tra tool", message, parent=self.root)

    def _log_error(self, title: str, error: Exception) -> None:
        detail = str(error).strip() or type(error).__name__
        self.status_var.set(detail)
        _get_logger().error("%s: %s", title, error, exc_info=True)
        messagebox.showerror(title, detail, parent=self.root)

    def _toast(self, message: str, duration_ms: int = 2500) -> None:
        """Non-blocking status toast that auto-dismisses."""

        toast = tk.Toplevel(self.root)
        toast.overrideredirect(True)
        toast.attributes("-topmost", True)
        toast.configure(bg="#1e293b")
        label = tk.Label(
            toast, text=message, bg="#1e293b", fg="#f8fafc",
            font=(UI_FONT_FAMILY, 10), padx=18, pady=10,
        )
        label.pack()
        toast.update_idletasks()
        x = self.root.winfo_rootx() + self.root.winfo_width() - toast.winfo_width() - 16
        y = self.root.winfo_rooty() + self.root.winfo_height() - toast.winfo_height() - 16
        toast.geometry(f"+{x}+{y}")
        # Fade-in effect
        toast.attributes("-alpha", 0.0)
        def _fade_in(step: int = 0) -> None:
            alpha = min(1.0, step * 0.15)
            try:
                toast.attributes("-alpha", alpha)
                if alpha < 1.0:
                    toast.after(30, lambda: _fade_in(step + 1))
            except tk.TclError:
                pass
        _fade_in()
        toast.after(duration_ms, lambda: self._dismiss_toast(toast))

    def _dismiss_toast(self, toast: tk.Toplevel) -> None:
        """Fade-out and destroy a toast notification."""
        def _fade_out(step: int = 7) -> None:
            alpha = max(0.0, step * 0.15)
            try:
                toast.attributes("-alpha", alpha)
                if alpha > 0.0:
                    toast.after(30, lambda: _fade_out(step - 1))
                else:
                    toast.destroy()
            except tk.TclError:
                pass
        _fade_out()

    def _on_close(self) -> None:
        running = [k for k, p in self.tool_processes.items() if p.poll() is None]
        if running:
            if not messagebox.askyesno(
                "Thoát dashboard",
                f"Có {len(running)} tool đang chạy. Bạn có muốn thoát?",
                parent=self.root,
            ):
                return
        if self._tool_process_poll_after_id is not None:
            try:
                self.root.after_cancel(self._tool_process_poll_after_id)
            except tk.TclError:
                pass
            self._tool_process_poll_after_id = None
        if self._health_after_id is not None:
            try:
                self.root.after_cancel(self._health_after_id)
            except tk.TclError:
                pass
            self._health_after_id = None
        _get_logger().info("Dashboard closed.")
        self._cleanup_all_children()
        self.root.destroy()

    def _cleanup_all_children(self) -> None:
        """Best-effort cleanup of all child tool processes."""

        for process in self.processes:
            try:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    process.kill()
                except OSError:
                    pass
            except Exception:  # noqa: BLE001 - cleanup must not crash.
                pass


def configure_style(root: tk.Tk) -> None:
    """Apply conservative ttk styling for the control-panel dashboard."""

    root.configure(background=SURFACE_COLOR)
    root.option_add("*Font", f"{{{UI_FONT_FAMILY}}} 10")

    style = ttk.Style(root)
    if "vista" in style.theme_names():
        style.theme_use("vista")
    style.configure(".", font=(UI_FONT_FAMILY, 10), foreground=TEXT_COLOR)
    style.configure("TFrame", background=SURFACE_COLOR)
    style.configure("App.TFrame", background=SURFACE_COLOR)
    style.configure("Panel.TFrame", background=PANEL_COLOR)
    style.configure("TLabel", background=SURFACE_COLOR, foreground=TEXT_COLOR, font=(UI_FONT_FAMILY, 10))
    style.configure("Hero.TLabel", background=SURFACE_COLOR, foreground=TEXT_COLOR, font=(UI_FONT_FAMILY, 20, "bold"))
    style.configure(
        "DashboardTitle.TLabel",
        background=SURFACE_COLOR,
        foreground=TEXT_COLOR,
        font=(UI_FONT_FAMILY, 19, "bold"),
    )
    style.configure("Muted.TLabel", background=SURFACE_COLOR, foreground=MUTED_TEXT_COLOR, font=(UI_FONT_FAMILY, 10))
    style.configure("Status.TLabel", background=SURFACE_COLOR, foreground="#334155", font=(UI_FONT_FAMILY, 10))
    style.configure("Footer.TLabel", background=SURFACE_COLOR, foreground=SUBTLE_TEXT_COLOR, font=(UI_FONT_FAMILY, 9))
    style.configure("Panel.TLabel", background=PANEL_COLOR, foreground=TEXT_COLOR, font=(UI_FONT_FAMILY, 10))
    style.configure(
        "PanelTitle.TLabel",
        background=PANEL_COLOR,
        foreground=TEXT_COLOR,
        font=(UI_FONT_FAMILY, 12, "bold"),
    )
    style.configure(
        "CardTitle.TLabel",
        background=PANEL_COLOR,
        foreground="#0f172a",
        font=(UI_FONT_FAMILY, 11, "bold"),
    )
    style.configure(
        "DialogTitle.TLabel",
        background=SURFACE_COLOR,
        foreground=TEXT_COLOR,
        font=(UI_FONT_FAMILY, 17, "bold"),
    )
    style.configure(
        "DialogFieldTitle.TLabel",
        background=PANEL_COLOR,
        foreground=TEXT_COLOR,
        font=(UI_FONT_FAMILY, 10, "bold"),
    )
    style.configure(
        "PanelMuted.TLabel",
        background=PANEL_COLOR,
        foreground=MUTED_TEXT_COLOR,
        font=(UI_FONT_FAMILY, 10),
    )
    style.configure(
        "PanelStatus.TLabel",
        background=PANEL_COLOR,
        foreground="#334155",
        font=(UI_FONT_FAMILY, 10),
    )
    style.configure("TButton", font=(UI_FONT_FAMILY, 10), padding=(10, 6))
    style.configure("Primary.TButton", font=(UI_FONT_FAMILY, 10, "bold"), padding=(16, 7))
    style.configure("Secondary.TButton", font=(UI_FONT_FAMILY, 10), padding=(12, 6))
    style.configure("Tool.TButton", font=(UI_FONT_FAMILY, 10, "bold"), padding=(12, 6))
    style.configure("TCheckbutton", background=PANEL_COLOR, font=(UI_FONT_FAMILY, 10))
    style.configure("App.TCheckbutton", background=SURFACE_COLOR, font=(UI_FONT_FAMILY, 10))
    style.configure("TLabelframe.Label", font=(UI_FONT_FAMILY, 10, "bold"))


def launch_dashboard(skip_login: bool = False) -> int:
    """Launch the dashboard immediately; login is handled inside the window."""

    single_instance = SingleInstanceGuard()
    if not single_instance.acquire():
        focus_existing_control_panel_window()
        return 0
    tool_workspace_dir()
    try:
        root = tk.Tk()
        configure_style(root)
        ControlPanelApp(root, skip_login=skip_login)
        root.mainloop()
        return 0
    finally:
        single_instance.release()


def run_self_test() -> int:
    """Run lightweight checks for dashboard packaging and tool presence."""

    failures: list[str] = []
    expected_tools = set(TOOL_FILES)
    embedded_tools = set(EMBEDDED_TOOL_PAYLOADS)
    if embedded_tools != expected_tools:
        missing = sorted(expected_tools - embedded_tools)
        extra = sorted(embedded_tools - expected_tools)
        if missing:
            failures.append(f"Thiếu payload nhúng cho: {', '.join(missing)}")
        if extra:
            failures.append(f"Payload nhúng thừa: {', '.join(extra)}")

    guard: ToolProcessGuard | None = None
    try:
        guard = ToolProcessGuard("self_test:tool_process_guard")
        if not guard.acquire():
            failures.append("Khóa chống mở trùng tool đang bị chiếm bất thường.")
        elif sys.platform == "win32" and not is_tool_process_mutex_active("self_test:tool_process_guard"):
            failures.append("Không đọc được trạng thái khóa chống mở trùng tool.")
    except Exception as error:  # noqa: BLE001
        failures.append(f"Khóa chống mở trùng tool: {error}")
    finally:
        if guard is not None:
            guard.release()

    for tool_name, metadata in TOOL_FILES.items():
        try:
            external_path = external_tool_script_path(tool_name)
            if external_path.exists():
                compile(external_path.read_text(encoding="utf-8-sig"), str(external_path), "exec")

            embedded_script_path = resolve_tool_script(tool_name, allow_external=False)
            if not embedded_script_path.exists():
                raise FileNotFoundError(f"Không bung được file nhúng: {embedded_script_path}")
            compile(embedded_script_path.read_text(encoding="utf-8-sig"), str(embedded_script_path), "exec")
            compile(
                decode_embedded_tool_source(tool_name),
                f"<embedded {metadata['script']}>",
                "exec",
            )
        except Exception as error:  # noqa: BLE001
            failures.append(f"{metadata['script']}: {error}")

    for tool_id, metadata in load_custom_tools().items():
        try:
            external_path = custom_tool_external_path(metadata)
            if external_path.exists():
                compile(external_path.read_text(encoding="utf-8-sig"), str(external_path), "exec")

            embedded_script_path = resolve_custom_tool_script(tool_id, allow_external=False)
            if not embedded_script_path.exists():
                raise FileNotFoundError(f"Không bung được file nhúng: {embedded_script_path}")
            compile(embedded_script_path.read_text(encoding="utf-8-sig"), str(embedded_script_path), "exec")
            compile(
                decode_custom_tool_source(tool_id, metadata),
                f"<embedded custom {metadata['script']}>",
                "exec",
            )
        except Exception as error:  # noqa: BLE001
            failures.append(f"{metadata.get('title', tool_id)}: {error}")
    if failures:
        print("SELF-TEST FAILED")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("SELF-TEST OK")
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    """Build command-line parser."""

    parser = argparse.ArgumentParser(description=APP_TITLE)
    parser.add_argument("--tool", choices=sorted(TOOL_FILES), help="Chạy một tool legacy trong tiến trình con.")
    parser.add_argument("--custom-tool", help="Chạy một tool tùy chỉnh trong tiến trình con.")
    parser.add_argument("--self-test", action="store_true", help="Kiểm tra nhanh dashboard và file tool.")
    parser.add_argument("--skip-login", action="store_true", help="Mở dashboard để xem giao diện, không đăng nhập trước.")
    return parser


def configure_cli_streams() -> None:
    """Make command-line help/errors safe with Vietnamese text on Windows consoles."""

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            continue


def main() -> int:
    """Program entry point."""

    configure_cli_streams()
    parser = build_arg_parser()
    args, extra_args = parser.parse_known_args()
    try:
        if args.self_test:
            return run_self_test()
        if args.custom_tool:
            return run_custom_tool_process(args.custom_tool, extra_args)
        if args.tool:
            return run_tool_process(args.tool, extra_args)
        return launch_dashboard(skip_login=args.skip_login)
    except KeyboardInterrupt:
        return 130
    except Exception as error:  # noqa: BLE001 - crash report for desktop app startup.
        crash_path = app_data_dir() / "vnedu_control_panel_crash.txt"
        crash_path.parent.mkdir(parents=True, exist_ok=True)
        crash_path.write_text(
            f"{time.strftime('%Y-%m-%d %H:%M:%S')}\n{traceback.format_exc()}",
            encoding="utf-8",
        )
        try:
            messagebox.showerror("Lỗi VNEDU Control Panel", f"{error}\n\nCrash report: {crash_path}")
        except Exception:
            print(f"Lỗi: {error}", file=sys.stderr)
            print(f"Crash report: {crash_path}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
