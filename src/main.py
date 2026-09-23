#!/usr/bin/env python3
# -*- encoding: utf-8 -*-
# *******************************************
# -*- CreateTime  :  2023/03/15 09:55:22
# -*- Author      :  Allen_Jol
# -*- FileName    :  main.py
# -*- Desc        :  Prometheus Alertmanager -> 飞书自定义机器人 webhook(支持多机器人)
# *******************************************

import sys
import json
import hashlib
import logging
import datetime
import threading

import arrow
import requests
import urllib3
from requests.adapters import HTTPAdapter
from flask import Flask, request, jsonify

from utils import gen_sign

# urllib3.disable_warnings()
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


app = Flask(__name__)

# 加载配置文件
app.config.from_object("config")

BOTS = app.config.get("APP_FS_BOTS") or []
ROUTE_ALL = app.config.get("APP_FS_ROUTE_ALL", False)

# 同一条告警(相同 labels+status)最多发送的次数; <=0 表示不限制
MAX_DUPLICATES = int(app.config.get("APP_FS_MAX_DUPLICATES", 2) or 0)

# 告警指纹 -> 已发送次数(进程内存, 重启归零)
_SEND_COUNTS = {}
_SEND_COUNTS_LOCK = threading.Lock()


@app.before_first_request
def before_first_request():
    app.logger.setLevel(logging.INFO)


def _pick_message(alert):
    """从 annotations 里取告警正文"""
    annotations = alert.get("annotations") or {}
    if annotations.get("message") is not None:
        return annotations.get("message")
    if annotations.get("description") is not None:
        return annotations.get("description")
    app.logger.error("Cannot get any alert info from annotations.message/description")
    return "null"


def _fmt_time(value):
    if not value:
        return "-"
    try:
        return arrow.get(value).to("Asia/Shanghai").format("YYYY-MM-DD HH:mm:ss")
    except Exception:
        return "-"


def _alert_fingerprint(alert):
    """同一条告警的唯一指纹: labels + status 归一化后取 sha256"""
    basis = json.dumps(
        {"labels": alert.get("labels") or {}, "status": alert.get("status", "")},
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def _duplicate_exceeded(alert):
    """判断该告警是否已超过发送上限; 未超过则计数 +1

    返回 (exceeded, seen_count): exceeded=True 表示应丢弃,
    seen_count 为本次之前已发送的次数。
    """
    if MAX_DUPLICATES <= 0:
        return False, 0
    fingerprint = _alert_fingerprint(alert)
    with _SEND_COUNTS_LOCK:
        count = _SEND_COUNTS.get(fingerprint, 0)
        if count >= MAX_DUPLICATES:
            return True, count
        _SEND_COUNTS[fingerprint] = count + 1
        return False, count


def _bots_by_name():
    return {bot["name"]: bot for bot in BOTS}


def _default_bots():
    default = [bot for bot in BOTS if bot.get("default")]
    return default or BOTS[:1]


def _select_bots(bot_label):
    """按告警标签 feishu_bot 选择目标机器人

    - ROUTE_ALL=true            -> 全部机器人
    - 标签值为 all / *          -> 全部机器人
    - 标签值为逗号分隔的多个名字 -> 命中的机器人
    - 标签为空 / 名字都不存在    -> 默认机器人(标记 default 的, 否则第一个)
    """
    if ROUTE_ALL:
        return list(BOTS)
    if bot_label:
        value = str(bot_label).strip()
        if value in ("all", "*"):
            return list(BOTS)
        wanted = [x.strip() for x in value.split(",") if x.strip()]
        mapping = _bots_by_name()
        selected = [mapping[name] for name in wanted if name in mapping]
        unknown = [name for name in wanted if name not in mapping]
        if unknown:
            app.logger.warning("Unknown bot name(s) in label feishu_bot: %s", unknown)
        if selected:
            return selected
    return _default_bots()


def build_payload(alert, bot):
    """根据告警内容和机器人配置构造飞书消息体"""
    labels = alert.get("labels") or {}
    alertname = labels.get("alertname", "unknown")
    severity = labels.get("severity", "unknown")
    instance = labels.get("instance", "unknown")
    status = alert.get("status", "")
    message = _pick_message(alert)

    title = "平台监控告警通知: %s" % alertname
    warning_status = "当前状态: %s \n" % status
    warning_name = "告警名称: %s \n" % alertname
    warning_level = severity
    warning_level_text = "告警等级: %s \n" % severity
    warning_instance = "告警实例: %s \n" % instance
    warning_info = "告警信息: %s" % str(message).replace(",", "\n").replace(":", ":  ")
    warning_end_time = "结束时间: %s \n" % _fmt_time(alert.get("endsAt"))
    warning_start_time = "告警时间: %s \n" % _fmt_time(alert.get("startsAt"))

    now_time = datetime.datetime.now().replace(microsecond=0)
    try:
        start_time_struct = datetime.datetime.strptime(_fmt_time(alert.get("startsAt")), "%Y-%m-%d %H:%M:%S")
        warning_last_time = "持续时间: %s \n" % (now_time - start_time_struct)
    except Exception:
        warning_last_time = "持续时间: - \n"

    timestamp = int(datetime.datetime.now().timestamp())
    secret = bot.get("secret") or ""
    sign = gen_sign.gen_sign(timestamp, secret) if secret else None

    alert_type = bot.get("alert_type", "post")

    if alert_type == "interactive":
        card_title = "%s告警通知" % warning_level
        send_data = {
            "msg_type": "interactive",
            "timestamp": timestamp,
            "card": {
                "config": {"wide_screen_mode": True},
                "elements": [
                    {
                        "tag": "div",
                        "text": {"tag": "plain_text", "content": warning_name, "lines": 1},
                        "fields": [
                            {"text": {"tag": "lark_md", "content": warning_instance}},
                            {"text": {"tag": "lark_md", "content": warning_info}},
                            {"text": {"tag": "lark_md", "content": warning_start_time}},
                            {
                                "text": {
                                    "tag": "lark_md",
                                    "content": warning_last_time if status == "firing" else warning_end_time,
                                }
                            },
                            {
                                "text": {
                                    "tag": "lark_md",
                                    "content": "<at id=all></at>" if status == "firing" and warning_level == "P0" else "",
                                }
                            },
                        ],
                    }
                ],
                "header": {
                    "template": "red"
                    if status == "firing" and warning_level == "P0"
                    else "orange"
                    if status == "firing" and warning_level == "P1"
                    else "yellow"
                    if status == "firing"
                    else "green",
                    "title": {
                        "content": card_title if status == "firing" else "告警恢复",
                        "tag": "plain_text",
                    },
                },
            },
        }
    else:
        # 默认 post(富文本)
        send_data = {
            "timestamp": timestamp,
            "msg_type": "post",
            "content": {
                "post": {
                    "zh_cn": {
                        "title": title,
                        "content": [
                            [
                                {"tag": "text", "text": warning_instance},
                                {"tag": "text", "text": warning_start_time},
                                {"tag": "text", "text": warning_end_time},
                                {"tag": "text", "text": warning_level_text},
                                {"tag": "text", "text": warning_info},
                                {"tag": "text", "text": warning_status},
                            ]
                        ],
                    }
                }
            },
        }

    if sign:
        send_data["sign"] = sign
    return send_data


def _send_to_bot(bot, send_data):
    headers = {"Content-Type": "application/json; charset=utf-8"}
    # 利用 requests 封装好的方法来设置 http 请求的重试次数
    session = requests.Session()
    session.mount("http://", HTTPAdapter(max_retries=3))
    session.mount("https://", HTTPAdapter(max_retries=3))
    try:
        resp = session.post(
            bot["webhook"],
            data=json.dumps(send_data),
            headers=headers,
            timeout=5,
            verify=False,
        )
        # 打印飞书返回体, 便于排查(如 code!=0 表示签名/频率/被限流等问题)
        app.logger.info(
            "Sent to bot '%s' -> HTTP %s body=%s",
            bot["name"],
            resp.status_code,
            resp.text[:300].replace("\n", " "),
        )
        return resp
    except requests.exceptions.RequestException as e:
        app.logger.error("Send to bot '%s' failed: %s", bot["name"], e)
        return None


@app.route("/healthz", methods=["GET"])
def healch_check():
    current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    data = {"time": current_time, "status": "OK", "status_code": 200}
    return jsonify(data)


@app.route("/bots", methods=["GET"])
def list_bots():
    """列出已加载的机器人(不返回签名密钥, webhook 做打码处理)"""

    def mask(url):
        url = url or ""
        if len(url) <= 12:
            return "****"
        return url[:-8] + "********"

    return jsonify(
        {
            "route_all": ROUTE_ALL,
            "count": len(BOTS),
            "bots": [
                {
                    "name": bot["name"],
                    "default": bool(bot.get("default")),
                    "alert_type": bot.get("alert_type"),
                    "sign": bool(bot.get("secret")),
                    "webhook": mask(bot.get("webhook")),
                }
                for bot in BOTS
            ],
        }
    )


@app.route("/send", methods=["POST"])
@app.route("/send/<bot_name>", methods=["POST"])
def send(bot_name=None):
    if not BOTS:
        return jsonify({"status": "error", "msg": "no feishu bot configured"}), 500

    raw = request.data or b""
    try:
        data = json.loads(raw) if raw else {}
    except json.JSONDecodeError as e:
        app.logger.error("Invalid json body: %s", e)
        return jsonify({"status": "error", "msg": "invalid json body"}), 400

    # 指定了 /send/<bot_name> 则只发给该机器人
    fixed_target = None
    if bot_name is not None:
        mapping = _bots_by_name()
        if bot_name not in mapping:
            return (
                jsonify({"status": "error", "msg": "unknown bot '%s'" % bot_name, "bots": list(mapping)}),
                404,
            )
        fixed_target = [mapping[bot_name]]

    alerts = data.get("alerts") or []
    app.logger.info("Receive %d alert(s) from %s", len(alerts), request.remote_addr)

    sent = 0
    skipped = 0
    for alert in alerts:
        # 同一条告警(相同 labels+status)最多发送 MAX_DUPLICATES 次
        exceeded, seen = _duplicate_exceeded(alert)
        if exceeded:
            alertname = (alert.get("labels") or {}).get("alertname", "unknown")
            app.logger.info(
                "Skip duplicated alert '%s' (already sent %d time(s), limit=%d)",
                alertname,
                seen,
                MAX_DUPLICATES,
            )
            skipped += 1
            continue
        label = (alert.get("labels") or {}).get("feishu_bot")
        targets = fixed_target if fixed_target is not None else _select_bots(label)
        # 同一条告警对相同 (alert_type, secret) 只构造一次消息体
        cache = {}
        for bot in targets:
            key = (bot.get("alert_type", "post"), bot.get("secret", ""))
            if key not in cache:
                cache[key] = build_payload(alert, bot)
            _send_to_bot(bot, cache[key])
            sent += 1

    return jsonify({"status": "ok", "alerts": len(alerts), "sent": sent, "skipped": skipped}), 200


if __name__ == "__main__":
    app.logger.info("Prometheus Python webhook start...")
    app.run(
        host=app.config.get("APP_HOST"),
        port=int(app.config.get("APP_PORT")),
        debug=app.config.get("DEBUG"),
    )
