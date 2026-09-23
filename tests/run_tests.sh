#!/bin/bash
# 多机器人功能测试: 在容器里跑 flask app + 假飞书端点, 从宿主断言
set -u
IMAGE=python:3.11-slim
NAME=fwtest
HERE="$(cd "$(dirname "$0")/.." && pwd)"   # 仓库根目录
SRC=$HERE
CAP=$(mktemp -d)
PASS=0; FAIL=0

cleanup() { docker rm -f $NAME >/dev/null 2>&1; }
cleanup
rm -rf $CAP; mkdir -p $CAP

docker run -d --name $NAME \
  -v $SRC:/data/feishu-webhook \
  -v $CAP:/tmp/fw_capture \
  -p 127.0.0.1:18080:8080 \
  -e APP_ENV=dev -e APP_HOST=0.0.0.0 -e APP_PORT=8080 \
  -e APP_FS_BOTS_FILE=/data/feishu-webhook/tests/bots.test.json \
  $IMAGE bash -c "pip install -q -r /data/feishu-webhook/src/requirements.txt && cd /data/feishu-webhook/src && (python /data/feishu-webhook/tests/capture_server.py >/tmp/cap.log 2>&1 &) && sleep 1 && python main.py" >/dev/null

echo "== 等待服务就绪(装依赖+启动) =="
for i in $(seq 1 90); do
  code=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:18080/healthz 2>/dev/null)
  [ "$code" = "200" ] && break
  sleep 2
done
if [ "$code" != "200" ]; then echo "服务未就绪, 容器日志:"; docker logs --tail 30 $NAME; cleanup; exit 1; fi
echo "healthz OK"

cnt() { ls $CAP 2>/dev/null | grep -c "^$1_"; }
req() { curl -s -o /dev/null -w '%{http_code}' -XPOST -H 'Content-Type: application/json' -d "$1" "$2"; }
ALERT='{"version":"4","status":"firing","alerts":[{"status":"firing","labels":{"alertname":"NodeDown","severity":"P1","instance":"10.0.0.1:9100"},"annotations":{"description":"node down","message":"node is down"},"startsAt":"2026-09-22T13:00:00Z","endsAt":"0001-01-01T00:00:00Z"}]}'
ALERT_OPS='{"alerts":[{"status":"firing","labels":{"alertname":"T","severity":"P0","instance":"1.1.1.1","feishu_bot":"ops,dev"},"annotations":{"message":"m"},"startsAt":"2026-09-22T13:00:00Z","endsAt":"0001-01-01T00:00:00Z"}]}'
ALERT_ALL='{"alerts":[{"status":"firing","labels":{"alertname":"T","severity":"P2","instance":"1.1.1.1","feishu_bot":"all"},"annotations":{"message":"m"},"startsAt":"2026-09-22T13:00:00Z","endsAt":"0001-01-01T00:00:00Z"}]}'
ALERT_DEV='{"alerts":[{"status":"firing","labels":{"alertname":"T","severity":"P1","instance":"1.1.1.1","feishu_bot":"dev"},"annotations":{"message":"m"},"startsAt":"2026-09-22T13:00:00Z","endsAt":"0001-01-01T00:00:00Z"}]}'
ALERT_UNKNOWN='{"alerts":[{"status":"firing","labels":{"alertname":"T","severity":"P1","instance":"1.1.1.1","feishu_bot":"nope"},"annotations":{"message":"m"},"startsAt":"2026-09-22T13:00:00Z","endsAt":"0001-01-01T00:00:00Z"}]}'

chk() { # chk <desc> <expr-expected> <actual>
  if [ "$2" = "$3" ]; then echo "  PASS  $1 ($3)"; PASS=$((PASS+1)); else echo "  FAIL  $1 预期=$2 实际=$3"; FAIL=$((FAIL+1)); fi
}

echo "== 1. GET /bots =="
B=$(curl -s http://127.0.0.1:18080/bots)
echo "  $B"
chk "bots count=3" 3 "$(echo $B | python3 -c 'import sys,json;print(json.load(sys.stdin)["count"])')"
chk "bots 名称" "['ops', 'dev', 'noSign']" "$(echo $B | python3 -c 'import sys,json;print([b["name"] for b in json.load(sys.stdin)["bots"]])')"

echo "== 2. 无标签 -> 默认机器人 ops =="
chk "POST /send 200" 200 "$(req "$ALERT" http://127.0.0.1:18080/send)"
sleep 1
chk "ops 收到 1" 1 "$(cnt ops)"
chk "dev 未收到" 0 "$(cnt dev)"

echo "== 3. feishu_bot=dev -> 仅 dev =="
req "$ALERT_DEV" http://127.0.0.1:18080/send >/dev/null; sleep 1
chk "dev +1=1" 1 "$(cnt dev)"
chk "ops 仍 1" 1 "$(cnt ops)"

echo "== 4. feishu_bot=ops,dev -> 两个都收到 =="
req "$ALERT_OPS" http://127.0.0.1:18080/send >/dev/null; sleep 1
chk "ops +1=2" 2 "$(cnt ops)"
chk "dev +1=2" 2 "$(cnt dev)"

echo "== 5. feishu_bot=all -> 全部 3 个 =="
req "$ALERT_ALL" http://127.0.0.1:18080/send >/dev/null; sleep 1
chk "ops +1=3" 3 "$(cnt ops)"
chk "dev +1=3" 3 "$(cnt dev)"
chk "noSign +1=1" 1 "$(cnt nosign)"

echo "== 6. 未知标签回退默认 =="
req "$ALERT_UNKNOWN" http://127.0.0.1:18080/send >/dev/null; sleep 1
chk "ops 回退+1=4" 4 "$(cnt ops)"

echo "== 7. URL 路由 /send/dev =="
req "$ALERT" http://127.0.0.1:18080/send/dev >/dev/null; sleep 1
chk "dev +1=4" 4 "$(cnt dev)"
chk "ops 不变 4" 4 "$(cnt ops)"

echo "== 8. URL 路由 /send/不存在 -> 404 =="
chk "404" 404 "$(req "$ALERT" http://127.0.0.1:18080/send/nobody)"

echo "== 9. 消息体检查(签名/卡片/富文本) =="
CAP="$CAP" python3 - <<'PY'
import json,glob,os,sys
cap=os.environ["CAP"]
def one(prefix):
    f=sorted(glob.glob(os.path.join(cap,prefix+"_*.json")))[0]
    return json.load(open(f))
ops=one("ops"); dev=one("dev"); ns=one("nosign")
ok=[]
ok.append(("ops 是 interactive", ops.get("msg_type")=="interactive"))
ok.append(("ops 带 sign", bool(ops.get("sign"))))
ok.append(("ops 带 timestamp", isinstance(ops.get("timestamp"),int)))
ok.append(("dev 是 post", dev.get("msg_type")=="post"))
ok.append(("dev 带 sign", bool(dev.get("sign"))))
ok.append(("noSign 无 sign 字段", "sign" not in ns))
ok.append(("ops/dev 签名不同", ops.get("sign")!=dev.get("sign")))
bad=[n for n,v in ok if not v]
for n,v in ok: print(("  PASS  " if v else "  FAIL  ")+n)
sys.exit(1 if bad else 0)
PY
if [ $? -eq 0 ]; then PASS=$((PASS+1)); else FAIL=$((FAIL+1)); fi

echo "== 10. 容器日志(错误检查) =="
if docker logs $NAME 2>&1 | grep -iE "traceback|error" | grep -v "Cannot get any alert info" | head -5; then
  echo "  (见上)"
fi

echo "==================== 结果: PASS=$PASS FAIL=$FAIL ===================="
cleanup
[ $FAIL -eq 0 ]
