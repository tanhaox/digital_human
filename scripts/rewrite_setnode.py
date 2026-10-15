"""把 easy setNode / easy getNode / SetNode / GetNode 改写成直连。

机制:
- setNode(name, X) = 把 X 写入全局字典 'name'; 本身无图下游 (副作用)
- getNode(name) = 从字典读出 'name'; 本身无图上游

拓扑都是 1→N fan-out: 1 个 setNode, N 个 getNode (同名)。

改写规则:
- 对每个 name:
  - 找出 setNode 的输入 (from_node, from_slot)
  - 找出所有 getNode 的下游 (to_node, to_slot, type)
  - 删除所有 setNode + getNode 节点和它们的 link
  - 为每个下游 link 创建新 link: from = setNode 输入源, to = 原 getNode 的下游目标

输入:
- src = SSOT workflow JSON (会原地修改并写出)
- 输出: stdout 打印 diff summary
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "workflows" / "character_three_view.json"

# 这些节点改写后类型变成 None (即从 nodes 列表中删除)
SET_GET_TYPES = {"easy setNode", "SetNode", "easy getNode", "GetNode"}

# 这些节点类型是 markdown 注释, 删除
NOTE_TYPES = {"MarkdownNote", "Note"}


def main():
    wf = json.loads(SRC.read_text(encoding="utf-8"))
    nodes = wf["nodes"]
    links = wf["links"]  # [[link_id, from_node, from_slot, to_node, to_slot, type]]

    set_nodes = defaultdict(list)  # name -> [(from_node, from_slot, type, setNode_id)]
    get_nodes = defaultdict(list)  # name -> [(to_node, to_slot, type, getNode_id)]

    # 收集所有 setNode + getNode
    for n in nodes:
        t = n.get("type", "")
        if t in ("easy setNode", "SetNode"):
            wv = n.get("widgets_values", []) or []
            if not wv:
                continue
            name = str(wv[0])
            # 找输入 link
            for inp in n.get("inputs", []) or []:
                link_id = inp.get("link")
                if link_id is None:
                    continue
                src_link = next((l for l in links if l[0] == link_id), None)
                if src_link is None:
                    continue
                set_nodes[name].append({
                    "from_node": src_link[1],
                    "from_slot": src_link[2],
                    "type": src_link[5],
                    "set_node_id": n["id"],
                })
        elif t in ("easy getNode", "GetNode"):
            wv = n.get("widgets_values", []) or []
            if not wv:
                continue
            name = str(wv[0])
            # 找所有下游 link
            for l in links:
                if len(l) >= 5 and l[1] == n["id"]:
                    get_nodes[name].append({
                        "to_node": l[3],
                        "to_slot": l[4],
                        "type": l[5],
                        "get_node_id": n["id"],
                    })

    # 准备新 link id (从已有最大值 + 1 开始)
    max_link_id = max((l[0] for l in links if len(l) >= 1), default=0)

    # 删除 setNode / getNode 节点 + 它们的 link
    nodes_to_remove_ids = set()
    for name, sl in set_nodes.items():
        for s in sl:
            nodes_to_remove_ids.add(s["set_node_id"])
    for name, gl in get_nodes.items():
        for g in gl:
            nodes_to_remove_ids.add(g["get_node_id"])

    # 也删除 MarkdownNote / Note (改写后无影响, 但也避免 missing_node_type 错误)
    for n in nodes:
        if n.get("type", "") in NOTE_TYPES:
            nodes_to_remove_ids.add(n["id"])

    # 也删除孤立的: 只在 setNode/getNode/Note 之间的 link 全部清理
    set_get_link_ids = set()
    for l in links:
        if len(l) >= 5 and (l[1] in nodes_to_remove_ids or l[3] in nodes_to_remove_ids):
            set_get_link_ids.add(l[0])

    # 新 link (替换 setNode+getNode)
    new_links = []
    rewrites = []  # for summary

    for name in sorted(set(set_nodes.keys()) | set(get_nodes.keys())):
        sl = set_nodes.get(name, [])
        gl = get_nodes.get(name, [])
        if not sl or not gl:
            # 没有匹配的 setNode 或 getNode; 跳过 (无效果)
            rewrites.append(f"  WARN: name={name!r} setNode={len(sl)} getNode={len(gl)} - skipped")
            continue
        # 取第一个 setNode 的输入源 (通常只有 1 个 setNode per name)
        src = sl[0]
        from_node = src["from_node"]
        from_slot = src["from_slot"]
        src_type = src["type"]

        for g in gl:
            max_link_id += 1
            # 新 link: from setNode's input source -> getNode's downstream target
            # type 必须兼容: 通常 setNode 写入的 type == getNode 读出的 type
            new_links.append([max_link_id, from_node, from_slot, g["to_node"], g["to_slot"], g["type"]])
            rewrites.append(
                f"  name={name!r}: ({from_node}[{from_slot}] type={src_type}) -> ({g['to_node']}[{g['to_slot']}] type={g['type']})"
            )

    # 重构 links 列表: 删除旧的 (setNode/getNode/Note), 加入新的
    filtered_links = [l for l in links if l[0] not in set_get_link_ids]
    final_links = filtered_links + new_links

    # 重构 nodes 列表: 删除 setNode/getNode/Note
    final_nodes = [n for n in nodes if n["id"] not in nodes_to_remove_ids]

    # 重构 extra.anomalous_hashes: 删除对应节点 (cosmetic, but tidy)
    # 格式: {"<node_id>_<file_name>": {...}}
    if "extra" in wf and isinstance(wf["extra"].get("anomalous_hashes"), dict):
        wf["extra"]["anomalous_hashes"] = {
            k: v for k, v in wf["extra"]["anomalous_hashes"].items()
            if not any(k.startswith(f"{nid}_") for nid in nodes_to_remove_ids)
        }

    wf["nodes"] = final_nodes
    wf["links"] = final_links

    # 备份 + 写回
    bak = SRC.with_suffix(".json.bak_setget")
    bak.write_bytes(SRC.read_bytes())
    SRC.write_text(json.dumps(wf, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=== setNode/getNode rewrite summary ===")
    print(f"Input  nodes: {len(nodes)}  links: {len(links)}")
    print(f"Removed nodes: {len(nodes_to_remove_ids)}  ({len(set_get_link_ids)} links)")
    print(f"Output nodes: {len(final_nodes)}  links: {len(final_links)}")
    print(f"New links added: {len(new_links)}")
    print()
    print("Rewrites:")
    for r in rewrites:
        print(r)
    print()
    print(f"Backup: {bak}")
    print(f"Written: {SRC}")


if __name__ == "__main__":
    main()