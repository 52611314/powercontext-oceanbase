"""生成「记仇小镇」Dify Chatflow DSL。

运行：python build_dsl.py
输出：grudge-town.yml（可用 Dify「导入 DSL」直接导入）

节点链路：
  start → 检索记忆(seekdb) → 读取关系 → 读取人设 → LLM(DeepSeek)
        → 解析回复(Code) → 写入记忆(seekdb) → answer
"""

from __future__ import annotations

import os

import yaml

DEEPSEEK_PLUGIN = (
    "langgenius/deepseek:0.0.26@"
    "bdf47cb41e9062bba00d26625d2a795c1874011d8ce64bff738c44b5a31787ea"
)
OPENAI_COMPAT_PLUGIN = (
    "langgenius/openai_api_compatible:0.0.73@"
    "4cf123ffaa4556f079bed6c47f3a462cc825504b859b183239e74eb19136a4b5"
)

SYSTEM_PROMPT = """你是小镇杂货店老板「老陈」，五十来岁，抠门、嘴硬、记性极好，说话带点方言味，爱用反问句。

【你的人设】
{{#http_profile.body#}}

【你和眼前这位玩家的关系（JSON）】
{{#http_relation.body#}}

【你记得他做过的相关事情（JSON，summary 字段是摘要）】
{{#http_search.body#}}

【回复要求】
1. 用老陈的口吻说话，1~3 句，口语化。可以翻旧账、阴阳怪气，也可以嘴硬心软。
2. 如果记忆里有他干过的坏事，要主动提起，比如「上次偷我苹果那事，我可没忘」。
3. 只输出一个 JSON 对象，不要输出其他任何文字，格式如下：
{"reply": "你对玩家说的话", "event_type": "steal_apple|firecracker|rumor|help|apologize|gift|chat", "memory": "用第三人称描述玩家这次做了什么，不超过40字"}
4. event_type 按玩家这次的行为选择：偷东西=steal_apple，放鞭炮/恶作剧=firecracker，造谣=rumor，帮忙=help，道歉=apologize，送礼=gift，其他=chat。
5. memory 是写给未来检索的记忆，要具体，例如「偷了老陈一个红苹果」。"""

CODE = '''import json
import re


def main(llm_text: str) -> dict:
    text = (llm_text or "").strip()
    # 去掉 DeepSeek 等模型的推理块
    text = re.sub(r"<think[^>]*>.*?</think\\s*>", "", text, flags=re.S).strip()
    text = re.sub(r"^<think[^>]*>.*$", "", text, flags=re.S).strip()
    if text.startswith("```"):
        text = text.strip("`").strip()
        if text.lower().startswith("json"):
            text = text[4:].strip()
    reply = ""
    event_type = "chat"
    memory = ""
    try:
        data = json.loads(text)
        reply = str(data.get("reply") or "").strip()
        event_type = str(data.get("event_type") or "chat").strip()
        memory = str(data.get("memory") or "").strip()
    except Exception:
        reply = text
    if not reply:
        reply = "……你刚才说啥？"
    if not memory:
        memory = "和玩家闲聊了几句"
    return {"reply": reply, "event_type": event_type, "memory": memory}
'''


def http_node(node_id: str, title: str, desc: str, method: str, url: str,
              json_template: str | None = None, params: str = "",
              x: int = 0, y: int = 282) -> dict:
    data: dict = {
        "desc": desc,
        "title": title,
        "type": "http-request",
        "method": method,
        "url": url,
        "authorization": {"type": "no-auth"},
        "headers": "Content-Type: application/json",
        "params": params,
        "body": {
            "type": "json" if json_template else "none",
            "data": (
                [{"key": "", "type": "text", "value": json_template}]
                if json_template
                else []
            ),
        },
        "timeout": {"connect": 10, "read": 30, "write": 10},
        "retry_config": {
            "retry_enabled": False,
            "max_retries": 1,
            "retry_interval": 1000,
        },
    }
    return {
        "data": data,
        "height": 90,
        "id": node_id,
        "position": {"x": x, "y": y},
        "positionAbsolute": {"x": x, "y": y},
        "selected": False,
        "sourcePosition": "right",
        "targetPosition": "left",
        "type": "custom",
        "width": 244,
    }


def build_graph() -> dict:
    y = 282
    nodes = [
        {
            "data": {
                "desc": "",
                "selected": False,
                "title": "开始",
                "type": "start",
                "variables": [],
            },
            "height": 54,
            "id": "start",
            "position": {"x": 80, "y": y},
            "positionAbsolute": {"x": 80, "y": y},
            "selected": False,
            "sourcePosition": "right",
            "targetPosition": "left",
            "type": "custom",
            "width": 244,
        },
        http_node(
            "http_search",
            "检索记忆",
            "在 seekdb 中按语义检索老陈对这位玩家的记忆",
            "post",
            "http://town-backend:8000/memory/search",
            json_template=(
                '{"player_id": "player-1", "npc_id": "laochen", '
                '"query": "{{#sys.query#}}", "top_k": 5}'
            ),
            x=384,
        ),
        http_node(
            "http_relation",
            "读取关系",
            "读取老陈对这位玩家的愤怒值、信任值",
            "post",
            "http://town-backend:8000/relation/get",
            json_template='{"player_id": "player-1", "npc_id": "laochen"}',
            x=688,
        ),
        http_node(
            "http_profile",
            "读取人设",
            "读取老陈的人设与口头禅",
            "get",
            "http://town-backend:8000/npc/profile",
            params="npc_id=laochen",
            x=992,
        ),
        {
            "data": {
                "context": {"enabled": False, "variable_selector": []},
                "desc": "老陈根据人设、记忆、关系生成回复与结构化记忆",
                "memory": {
                    "query_prompt_template": "{{#sys.query#}}\n\n{{#sys.files#}}",
                    "window": {"enabled": False, "size": 10},
                },
                "model": {
                    "completion_params": {"temperature": 0.8},
                    "mode": "chat",
                    "name": "deepseek-flash",
                    "provider": "langgenius/deepseek/deepseek",
                },
                "prompt_template": [
                    {"role": "system", "text": SYSTEM_PROMPT},
                    {"role": "user", "text": "{{#sys.query#}}"},
                ],
                "selected": True,
                "title": "老陈回应",
                "type": "llm",
                "variables": [],
                "vision": {"enabled": False},
            },
            "height": 90,
            "id": "llm",
            "position": {"x": 1296, "y": y},
            "positionAbsolute": {"x": 1296, "y": y},
            "selected": False,
            "sourcePosition": "right",
            "targetPosition": "left",
            "type": "custom",
            "width": 244,
        },
        {
            "data": {
                "code": CODE,
                "code_language": "python3",
                "desc": "从模型输出中解析回复、事件类型与记忆描述",
                "outputs": {
                    "reply": {"children": None, "type": "string"},
                    "event_type": {"children": None, "type": "string"},
                    "memory": {"children": None, "type": "string"},
                },
                "selected": False,
                "title": "解析回复",
                "type": "code",
                "variables": [
                    {
                        "value_selector": ["llm", "text"],
                        "value_type": "string",
                        "variable": "llm_text",
                    }
                ],
            },
            "height": 54,
            "id": "code",
            "position": {"x": 1600, "y": y},
            "positionAbsolute": {"x": 1600, "y": y},
            "selected": False,
            "sourcePosition": "right",
            "targetPosition": "left",
            "type": "custom",
            "width": 244,
        },
        http_node(
            "http_write",
            "写入记忆",
            "把本次事件写入 seekdb，并更新关系值",
            "post",
            "http://town-backend:8000/memory/write",
            json_template=(
                '{"player_id": "player-1", "npc_id": "laochen", '
                '"event_type": "{{#code.event_type#}}", '
                '"content": "{{#code.memory#}}"}'
            ),
            x=1904,
        ),
        {
            "data": {
                "answer": "{{#code.reply#}}",
                "desc": "",
                "selected": True,
                "title": "回答",
                "type": "answer",
                "variables": [],
            },
            "height": 105,
            "id": "answer",
            "position": {"x": 2208, "y": y},
            "positionAbsolute": {"x": 2208, "y": y},
            "selected": False,
            "sourcePosition": "right",
            "targetPosition": "left",
            "type": "custom",
            "width": 244,
        },
    ]

    chain = [
        ("start", "http_search", "start", "http-request"),
        ("http_search", "http_relation", "http-request", "http-request"),
        ("http_relation", "http_profile", "http-request", "http-request"),
        ("http_profile", "llm", "http-request", "llm"),
        ("llm", "code", "llm", "code"),
        ("code", "http_write", "code", "http-request"),
        ("http_write", "answer", "http-request", "answer"),
    ]
    edges = []
    for src, tgt, stype, ttype in chain:
        edges.append(
            {
                "data": {
                    "isInIteration": False,
                    "isInLoop": False,
                    "sourceType": stype,
                    "targetType": ttype,
                },
                "id": f"{src}-{tgt}",
                "source": src,
                "sourceHandle": "source",
                "target": tgt,
                "targetHandle": "target",
                "type": "custom",
                "zIndex": 0,
            }
        )

    return {
        "edges": edges,
        "nodes": nodes,
        "viewport": {"x": 0, "y": 0, "zoom": 0.55},
    }


def build_dsl() -> dict:
    return {
        "app": {
            "description": "一个会记住你恶作剧的 NPC 互动叙事应用",
            "icon": "🏮",
            "icon_background": "#FFEAD5",
            "mode": "advanced-chat",
            "name": "记仇小镇",
            "use_icon_as_answer_icon": False,
        },
        "dependencies": [
            {
                "current_identifier": None,
                "type": "marketplace",
                "value": {
                    "marketplace_plugin_unique_identifier": DEEPSEEK_PLUGIN
                },
            },
            {
                "current_identifier": None,
                "type": "marketplace",
                "value": {
                    "marketplace_plugin_unique_identifier": OPENAI_COMPAT_PLUGIN
                },
            },
        ],
        "kind": "app",
        "version": "0.3.1",
        "workflow": {
            "conversation_variables": [],
            "environment_variables": [],
            "features": {
                "file_upload": {"enabled": False},
                "opening_statement": (
                    "老陈正靠在柜台后面打盹。你走进杂货店，他眼皮都没抬："
                    "「买啥？不买别乱摸。」"
                ),
                "retriever_resource": {"enabled": True},
                "sensitive_word_avoidance": {"enabled": False},
                "speech_to_text": {"enabled": False},
                "suggested_questions": [
                    "老板，这苹果甜不甜？",
                    "我帮你把货搬进来吧",
                    "上次那事……对不起",
                ],
                "suggested_questions_after_answer": {"enabled": False},
                "text_to_speech": {"enabled": False, "language": "", "voice": ""},
            },
            "graph": build_graph(),
        },
    }


if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    out = os.path.join(here, "grudge-town.yml")
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        yaml.dump(
            build_dsl(),
            f,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
            width=1000,
        )
    print("written:", out)
