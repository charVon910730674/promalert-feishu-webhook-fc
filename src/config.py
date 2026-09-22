#!/usr/bin/env python3
# -*- encoding: utf-8 -*-
# *******************************************
# -*- CreateTime  :  2023/03/15 13:14:36
# -*- Author      :  Allen_Jol
# -*- FileName    :  config.py
# -*- Desc        :  多飞书机器人配置加载
# *******************************************

import os
import sys
import json

"""
配置说明
--------
1. 支持配置多个飞书自定义机器人(webhook + 签名密钥 + 消息类型)。
2. 机器人列表的来源优先级:
   a) APP_FS_BOTS_FILE : 指向一个 JSON 文件, 数组或对象形式均可(推荐, 便于管理多个机器人);
   b) APP_FS_BOTS      : 直接放在环境变量里的 JSON(适合少量机器人);
   c) APP_FS_WEBHOOK / APP_FS_SECRET / APP_FS_ALERT_TYPE : 兼容旧版单机器人写法。
3. 每个机器人字段:
   - name       : 机器人名(必填, 唯一), 用于路由(URL /send/<name> 或告警标签 feishu_bot)
   - webhook    : 飞书自定义机器人 webhook 地址(必填)
   - secret     : 签名校验密钥(可空, 机器人若开启"签名校验"则必填)
   - alert_type : post(富文本) 或 interactive(消息卡片), 默认 post
   - default    : 布尔, 无标签告警默认投递的机器人; 都不填则第一个为默认
   - enabled    : 布尔, false 表示跳过该机器人, 默认 true
4. APP_FS_ROUTE_ALL=true 时, 每条告警都会广播给所有机器人。
"""


def _fail(msg):
    print(msg)
    sys.exit(1)


# APP_ENV: dev 开启 DEBUG, prod 关闭
if os.getenv("APP_ENV") == "dev":
    DEBUG = True
elif os.getenv("APP_ENV") == "prod":
    DEBUG = False
else:
    print("Environment APP_ENV value false, please set to dev or prod!")
    sys.exit(1)

# 项目侦听的地址
APP_HOST = os.getenv("APP_HOST", "0.0.0.0")

# 项目使用的端口
APP_PORT = os.getenv("APP_PORT", "8080")

# 是否把每条告警广播到所有机器人(默认否, 走标签路由)
APP_FS_ROUTE_ALL = os.getenv("APP_FS_ROUTE_ALL", "false").lower() in ("1", "true", "yes", "on")


def _load_raw_bots():
    """返回原始机器人配置(list 或 dict), 以及来源描述"""
    bots_file = os.getenv("APP_FS_BOTS_FILE")
    if bots_file:
        if not os.path.isfile(bots_file):
            _fail("APP_FS_BOTS_FILE not found: %s" % bots_file)
        try:
            with open(bots_file, "r", encoding="utf-8") as f:
                return json.load(f), "APP_FS_BOTS_FILE(%s)" % bots_file
        except Exception as e:
            _fail("Failed to parse APP_FS_BOTS_FILE %s: %s" % (bots_file, e))

    if os.getenv("APP_FS_BOTS"):
        try:
            return json.loads(os.getenv("APP_FS_BOTS")), "APP_FS_BOTS"
        except Exception as e:
            _fail("Invalid APP_FS_BOTS json: %s" % e)

    # 兼容旧版单机器人
    webhook = os.getenv("APP_FS_WEBHOOK")
    secret = os.getenv("APP_FS_SECRET")
    alert_type = os.getenv("APP_FS_ALERT_TYPE", "post")
    if webhook:
        return [{
            "name": "default",
            "webhook": webhook,
            "secret": secret or "",
            "alert_type": alert_type,
            "default": True,
        }], "APP_FS_WEBHOOK/APP_FS_SECRET"

    _fail("Require bot config: set APP_FS_BOTS_FILE, APP_FS_BOTS, or APP_FS_WEBHOOK(+APP_FS_SECRET)")


def _normalize(raw):
    """把 dict/list 形式的配置统一成 list[dict]"""
    if isinstance(raw, dict):
        items = []
        for name, cfg in raw.items():
            if not isinstance(cfg, dict):
                cfg = {"webhook": cfg}
            cfg = dict(cfg)
            cfg.setdefault("name", name)
            items.append(cfg)
        return items
    if isinstance(raw, list):
        return raw
    _fail("Bot config must be a JSON array or object")


def _load_bots():
    raw, source = _load_raw_bots()
    bots = []
    seen = set()
    for item in _normalize(raw):
        if not isinstance(item, dict):
            _fail("Invalid bot item: %r" % (item,))
        name = str(item.get("name") or "").strip()
        webhook = item.get("webhook") or item.get("APP_FS_WEBHOOK")
        secret = item.get("secret") or item.get("APP_FS_SECRET") or ""
        alert_type = item.get("alert_type") or item.get("type") or "post"
        enabled = item.get("enabled", True)
        is_default = bool(item.get("default", False))

        if not name:
            _fail("Bot item missing 'name': %r" % (item,))
        if name in seen:
            _fail("Duplicate bot name: %s" % name)
        seen.add(name)
        if not enabled:
            print("Bot '%s' disabled, skipped" % name)
            continue
        if not webhook:
            _fail("Bot '%s' missing 'webhook'" % name)
        if alert_type not in ("post", "interactive"):
            _fail("Bot '%s' alert_type must be 'post' or 'interactive'" % name)

        bots.append({
            "name": name,
            "webhook": webhook,
            "secret": secret,
            "alert_type": alert_type,
            "default": is_default,
        })

    if not bots:
        _fail("No enabled bots configured")

    if not any(b["default"] for b in bots):
        bots[0]["default"] = True

    print("Loaded %d feishu bot(s) from %s: %s" % (len(bots), source, [b["name"] for b in bots]))
    return bots


# 机器人列表, 供 main.py 使用
APP_FS_BOTS = _load_bots()
