# promalert-feishu-webhook

python3.11 + flask 编写了一个对接飞书 API 实现告警的 webhook。
**本版本在原作者基础上扩展为支持同时接入多个飞书机器人（多 webhook / 多群）。**

## 飞书机器人概述

```shell
https://www.feishu.cn/hc/zh-CN/articles/244506653275#tabs0|lineguid-AHuiGI
```

## 飞书开发文档

```shell
https://open.feishu.cn/document/ukTMukTMukTM/uADOwUjLwgDM14CM4ATN
```

## 安全设置

### 签名校验

```shell
https://www.feishu.cn/hc/zh-CN/articles/244506653275
```

---

## 多机器人配置

机器人列表支持三种来源，优先级：`APP_FS_BOTS_FILE` > `APP_FS_BOTS` > 旧版 `APP_FS_WEBHOOK`。

每个机器人字段：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| name | 是 | 机器人名（唯一），用于路由 |
| webhook | 是 | 飞书自定义机器人 webhook 地址 |
| secret | 否 | 签名校验密钥（机器人开启"签名校验"时必填） |
| alert_type | 否 | `post`(富文本) / `interactive`(消息卡片)，默认 `post` |
| default | 否 | 无标签告警的默认投递机器人；都不填则第一个为默认 |
| enabled | 否 | `false` 跳过该机器人 |

### 方式一：配置文件（推荐）

复制 `bots.json.example` 为 `bots.json` 后填写，docker-compose 已默认挂载：

```json
[
  {"name": "ops", "webhook": "https://open.feishu.cn/open-apis/bot/v2/hook/xxx", "secret": "密钥A", "alert_type": "interactive", "default": true},
  {"name": "dev", "webhook": "https://open.feishu.cn/open-apis/bot/v2/hook/yyy", "secret": "密钥B", "alert_type": "post"}
]
```

也支持对象形式（key 即机器人名）：

```json
{
  "ops": {"webhook": "https://.../xxx", "secret": "密钥A", "default": true},
  "dev": {"webhook": "https://.../yyy", "alert_type": "post"}
}
```

### 方式二：环境变量

```shell
APP_FS_BOTS='[{"name":"ops","webhook":"https://.../xxx","secret":"密钥A","alert_type":"interactive","default":true}]'
```

## 告警路由

1. **标签路由（默认）**：Alertmanager 告警带 `feishu_bot` 标签时按其值投递：
   - `feishu_bot="ops"` → ops 机器人
   - `feishu_bot="ops,dev"` → ops 与 dev
   - `feishu_bot="all"` 或 `"*"` → 全部机器人
   - 无此标签 / 名字不存在 → `default` 机器人
2. **URL 路由**：Alertmanager receiver 的 url 直接指定机器人：
   - `http://<host>:3880/send` → 默认（或按标签）路由
   - `http://<host>:3880/send/ops` → 固定发给 ops
3. **广播**：`APP_FS_ROUTE_ALL=true` → 每条告警都发给所有机器人。

Prometheus 规则里打标签示例：

```yaml
- alert: NodeDown
  expr: up == 0
  labels:
    severity: P1
    feishu_bot: ops      # 决定推到哪个飞书群
```

## 接口

- `POST /send` / `POST /send/<bot_name>`：Alertmanager webhook 入口
- `GET /bots`：查看已加载机器人（密钥不返回，webhook 打码）
- `GET /healthz`：健康检查

## 构建镜像

```shell
DATE=$(date +'%Y%m%d')
TIMESTAMP=$(date +%s)
docker build -t promalert-feishu-webhook:${DATE}-v${TIMESTAMP} .
cat docker-compose.yml | egrep [0-9]{8}\-v[0-9]{10} -o | xargs -i sed -i s#{}#${DATE}-v${TIMESTAMP}#g docker-compose.yml
```

## 启动服务

1. 更改 docker-compose.yml 中的镜像
2. 按需设置机器人配置（见"多机器人配置"），旧版单机器人仍可用
3. 启动服务

```shell
docker-compose up -d
```
