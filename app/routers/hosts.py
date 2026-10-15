"""Hosts router: 数字人账号 (Host) CRUD + 品牌字段编辑.

Host 是账号/人物绑定位: script.host_id 贯通 audio (default_voice)、HF 品牌注入、
C 线出镜形象。品牌字段 (brand_name/stamp_name/brand_tag) 在此维护, 洗稿选 host
后随 script 走完整条流水线。删除受保护: laochen 内置账号或被引用的 host 拒绝。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Host, Script, Voice
from ..schemas import HostCreate, HostOut, HostUpdate

router = APIRouter(prefix="/api/hosts", tags=["hosts"])


@router.get("", response_model=list[HostOut])
def list_hosts(db: Session = Depends(get_db)):
    return db.query(Host).order_by(Host.name).all()


@router.post("", response_model=HostOut, status_code=201)
def create_host(body: HostCreate, db: Session = Depends(get_db)):
    """创建数字人账号 (persona_key 唯一)."""
    dup = db.query(Host).filter(Host.persona_key == body.persona_key).first()
    if dup:
        raise HTTPException(status_code=409, detail=f"persona_key '{body.persona_key}' 已被账号 '{dup.name}' 占用")
    if body.default_voice_id and not db.get(Voice, body.default_voice_id):
        raise HTTPException(status_code=404, detail=f"Voice {body.default_voice_id} not found")

    host = Host(
        name=body.name,
        persona_key=body.persona_key,
        fixed_opening=body.fixed_opening,
        fixed_ending=body.fixed_ending,
        brand_name=body.brand_name,
        stamp_name=body.stamp_name,
        brand_tag=body.brand_tag,
        default_voice_id=body.default_voice_id,
    )
    db.add(host)
    db.commit()
    db.refresh(host)
    return host


@router.put("/{host_id}", response_model=HostOut)
def update_host(host_id: str, body: HostUpdate, db: Session = Depends(get_db)):
    """编辑账号 (品牌名/印章/标语/开结尾等)."""
    host = db.get(Host, host_id)
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    if body.persona_key is not None and body.persona_key != host.persona_key:
        dup = (
            db.query(Host)
            .filter(Host.persona_key == body.persona_key, Host.id != host_id)
            .first()
        )
        if dup:
            raise HTTPException(status_code=409, detail=f"persona_key '{body.persona_key}' 已被账号 '{dup.name}' 占用")
        host.persona_key = body.persona_key

    # 注意: brand 字段留空应允许清空, 所以用「None 表示不修改」会吞掉清空请求。
    # 前端清空时传 "" 即可覆盖为 None (见下方 str 处理)。
    for field, val in (
        ("name", body.name),
        ("fixed_opening", body.fixed_opening),
        ("fixed_ending", body.fixed_ending),
        ("brand_name", body.brand_name),
        ("stamp_name", body.stamp_name),
        ("brand_tag", body.brand_tag),
    ):
        if val is not None:
            setattr(host, field, val or None)  # 空串 → None

    if body.default_voice_id is not None:
        if body.default_voice_id and not db.get(Voice, body.default_voice_id):
            raise HTTPException(status_code=404, detail=f"Voice {body.default_voice_id} not found")
        host.default_voice_id = body.default_voice_id or None

    db.commit()
    db.refresh(host)
    return host


@router.delete("/{host_id}", status_code=204)
def delete_host(host_id: str, db: Session = Depends(get_db)):
    """删除账号. 内置 laochen 或被 script/voice 引用的 host 拒绝删除."""
    host = db.get(Host, host_id)
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")
    if host.persona_key == "laochen":
        raise HTTPException(status_code=403, detail="内置账号 'laochen' 不允许删除")

    ref_scripts = db.query(Script).filter(Script.host_id == host_id).first()
    if ref_scripts:
        raise HTTPException(status_code=409, detail=f"账号 '{host.name}' 仍被脚本引用，请先处理引用")
    ref_voices = db.query(Voice).filter(Voice.host_id == host_id).first()
    if ref_voices:
        raise HTTPException(status_code=409, detail=f"账号 '{host.name}' 仍关联音色，请先解除关联")

    db.delete(host)
    db.commit()
