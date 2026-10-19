"""级联重跑清账测试 (2026-10-19 bug 记账①):
rerun_cascade 须连产线链接一起作废 — 只清稿不清 script_id 时, produce 命中
_existing already 分支把旧 Script 冒充新稿产物; module/bs1/导演链接同源旧稿。
旧 Script 级联删; roadmap 旧稿问题单快照 (_issues/_checks) 随稿作废。
"""
from __future__ import annotations

from types import SimpleNamespace as NS

from app.services.book_service.episode_gen import rerun_cascade


class _FakeDB:
    def __init__(self, scripts: dict):
        self._scripts = scripts
        self.deleted: list = []

    def get(self, model, pk):
        return self._scripts.get(pk) if model.__name__ == "Script" else None

    def delete(self, obj):
        self.deleted.append(obj)

    def commit(self):
        pass


def _ep(n, sid=None):
    return NS(ep_index=n, status="confirmed", script_text="旧稿", script_generated_at="t",
              coverage_json=[1], script_id=sid, module_json=[{"name": "钩子"}],
              director_job_id="dj", bs1_pages_json=[{"p": 1}],
              roadmap_json={"主题": "x", "_issues": ["旧账"], "_checks": {"theme_in_script": False}})


def test_rerun_clears_links_deletes_old_scripts_resets_from_n():
    db = _FakeDB({"s3": NS(id="s3"), "s5": NS(id="s5")})
    book = NS(episodes=[_ep(2), _ep(3, sid="s3"), _ep(4), _ep(5, sid="s5"),
                        _ep(6, sid="ghost")],  # ghost: script 已不在库, 只清链接不炸
              status="done")
    out = rerun_cascade(db, book, 3)

    assert out is book and book.status == "writing"
    # ep2 (N 之前) 原样不动
    e2 = book.episodes[0]
    assert e2.status == "confirmed" and e2.script_text == "旧稿" and e2.script_id is None
    assert e2.roadmap_json["_issues"] == ["旧账"]
    # ep3..6 全复位, 产线链接与旧稿快照一并作废
    for e in book.episodes[1:]:
        assert e.status == "pending"
        assert e.script_text is None and e.script_generated_at is None and e.coverage_json == []
        assert e.script_id is None and e.module_json is None
        assert e.director_job_id is None and e.bs1_pages_json is None
        assert "_issues" not in e.roadmap_json and "_checks" not in e.roadmap_json
        assert e.roadmap_json["主题"] == "x"  # 总纲本体保留 (重生成输入)
    # 旧 Script 删了在库的两条; ghost 静默跳过
    assert sorted(s.id for s in db.deleted) == ["s3", "s5"]
