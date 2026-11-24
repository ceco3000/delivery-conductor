#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from host_trust import HostTrustError, enforce_host_trust
from conductor.assessment import AssessmentError, validate_assessment
from conductor.model import (
    build_state,
    component_line,
    components_for,
    derive_requirements,
    load_state,
    now_iso,
    save_state,
    set_phase,
)
from conductor.runtime import configure_stdio

FLAG_KEYS = (
    "trivial",
    "small",
    "bugfix",
    "large",
    "multi_file",
    "behavior_change",
    "new_capability",
    "ui",
    "design_change",
    "reuse_design",
)
ENTRY_PHRASE = "编码总控"
ENTRY_SEPARATORS = " \t\r\n:：,，.。;；-—"
UI_WORD_RE = re.compile(r"(?<![a-z0-9])(?:ui|ux)(?![a-z0-9])")
UI_KEYWORDS = (
    "页面", "界面", "组件", "样式", "布局", "颜色", "字体", "图表",
    "响应式", "美化", "前端", "dashboard", "landing", "启动器", "起动器",
    "游戏库", "列表", "卡片", "按钮", "显示", "隐藏", "筛选", "分类",
)
DESIGN_CHANGE_KEYWORDS = (
    "重新设计", "重做", "新页面", "页面布局", "组件样式", "视觉",
    "配色", "字体", "图表", "响应式", "美化", "界面重构", "设计系统",
    "动画", "动效",
)
NEW_CAPABILITY_KEYWORDS = (
    "新增功能", "新功能", "增加功能", "实现一个", "做一个", "支持",
    "集成", "新增完整", "全栈", "新模块",
)
LOCALIZED_KEYWORDS = (
    "隐藏", "过滤", "筛选", "排序", "显示", "改名", "文案", "单个",
    "局部", "列表", "按钮状态", "小改",
)


def strip_entry_phrase(text: str) -> str:
    """Remove the natural-language entry phrase and its punctuation."""
    cleaned = text.strip()
    if not cleaned.startswith(ENTRY_PHRASE):
        return cleaned
    return cleaned[len(ENTRY_PHRASE) :].lstrip(ENTRY_SEPARATORS).strip()


def infer_from_text(text: str) -> dict[str, bool]:
    normalized = text.lower()

    def mentions(*words: str) -> bool:
        return any(word in normalized for word in words)

    flags = {
        "trivial": mentions("文档", "注释", "格式", "readme", "typo", "formatting", "仅文档"),
        "small": mentions("小改", "局部", "小修复"),
        "bugfix": mentions("修复", "bug", "报错", "异常", "崩溃", "错误"),
        "large": mentions("重构", "跨模块", "架构", "多个模块", "全栈", "大规模", "整体改造"),
        "multi_file": mentions("多个文件", "多文件", "模块", "全栈", "前后端"),
        "behavior_change": mentions(
            "新增", "新功能", "功能", "实现", "做一个", "加上", "支持",
            "api", "接口", "数据格式", "行为", "验收", "需求", "修改",
            "隐藏", "过滤", "筛选", "排序", "显示", "改名", "限制",
        ),
        "new_capability": mentions(*NEW_CAPABILITY_KEYWORDS),
        "ui": any(word in normalized for word in UI_KEYWORDS) or bool(UI_WORD_RE.search(normalized)),
        "design_change": mentions(*DESIGN_CHANGE_KEYWORDS),
        "reuse_design": False,
    }
    flags["small"] = flags["small"] or mentions(*LOCALIZED_KEYWORDS)
    if flags["new_capability"]:
        flags["behavior_change"] = True
    if flags["new_capability"] and flags["ui"]:
        flags["design_change"] = True
    if flags["design_change"]:
        flags["ui"] = True
    flags["reuse_design"] = flags["ui"] and not flags["design_change"]
    if any(flags[key] for key in ("small", "bugfix", "large", "multi_file", "behavior_change", "ui")):
        flags["trivial"] = False
    return flags


def infer_tier(args: argparse.Namespace) -> tuple[str, list[str]]:
    notes: list[str] = []
    tier_arg = getattr(args, "tier", None)
    if tier_arg:
        tier = tier_arg
    elif getattr(args, "trivial", False):
        tier = "T0"
    elif getattr(args, "large", False):
        tier = "T3"
    elif getattr(args, "multi_file", False) or getattr(args, "new_capability", False):
        tier = "T2"
    else:
        tier = "T1"
    if tier == "T1" and getattr(args, "behavior_change", False):
        notes.append("T1 keeps the compact workflow for a localized behavior change")
    if tier == "T1" and getattr(args, "reuse_design", False):
        notes.append("Existing UI pattern reused; separate design gate not required")
    return tier, notes


def _state_path(base_dir: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else base_dir / path


def _heuristic_snapshot(args: argparse.Namespace, tier: str, notes: list[str]) -> dict:
    return {
        "tier": tier,
        "flags": {key: bool(getattr(args, key)) for key in FLAG_KEYS},
        "notes": list(notes),
    }


def _apply_assessment(state: dict, payload: str, base_dir: Path) -> dict:
    if state.get("route_status") != "provisional" or state.get("phase") != "ROUTING":
        raise AssessmentError("semantic assessment is only allowed while route_status=provisional")
    try:
        raw = json.loads(payload)
    except json.JSONDecodeError as error:
        raise AssessmentError(f"assessment is not valid JSON: {error}") from error
    heuristic = state.get("heuristic") if isinstance(state.get("heuristic"), dict) else {}
    assessment = validate_assessment(
        raw,
        task=str(state.get("task", "")),
        heuristic_tier=heuristic.get("tier"),
    )
    tier = assessment["tier"]
    ui = assessment["ui"]
    behavior_change = assessment["behavior_change"]
    design_change = assessment["design_change"]
    components = components_for(
        tier,
        ui=ui,
        design_change=design_change,
        behavior_change=behavior_change,
    )
    if tier != "T0":
        try:
            enforce_host_trust()
        except HostTrustError:
            raise

    state.update({
        "tier": tier,
        "components": components,
        "ui": ui,
        "behavior_change": behavior_change,
        "design_change": design_change,
        "new_capability": assessment["new_capability"],
        "requires": derive_requirements(
            tier,
            ui=ui,
            behavior_change=behavior_change,
            design_change=design_change,
        ),
        "route_status": "assessed",
        "route_source": "model_assessment",
        "assessment": assessment,
        "routed_at": now_iso(),
    })
    heuristic_tier = heuristic.get("tier")
    if heuristic_tier and heuristic_tier != tier:
        state.setdefault("routing_notes", []).append(
            f"Semantic assessment changed heuristic tier {heuristic_tier} to {tier}"
        )
    if assessment["unknowns"]:
        state.setdefault("routing_notes", []).append(
            "Assessment recorded with non-blocking unknowns: " + "; ".join(assessment["unknowns"])
        )
    set_phase(state, "ROUTED", "semantic route assessment recorded")
    return state


def main() -> int:
    configure_stdio()
    parser = argparse.ArgumentParser(description="Route a task through the four-layer delivery protocol.")
    parser.add_argument("--task", help="Short task description")
    parser.add_argument("--tier", choices=["T0", "T1", "T2", "T3"])
    parser.add_argument("--trivial", action="store_true", help="Documentation, formatting, or single non-behavioral edit")
    parser.add_argument("--small", action="store_true", help="Small bug fix or localized change")
    parser.add_argument("--bugfix", action="store_true", help="Bug fix that does not change the behavior contract")
    parser.add_argument("--large", action="store_true", help="Large or cross-module task")
    parser.add_argument("--multi-file", action="store_true")
    parser.add_argument("--behavior-change", action="store_true")
    parser.add_argument("--new-capability", action="store_true")
    parser.add_argument("--ui", action="store_true")
    parser.add_argument("--design-change", action="store_true", default=None)
    parser.add_argument("--reuse-design", action="store_true")
    parser.add_argument("--auto", dest="auto", action="store_true", default=True, help="Infer flags from the task text (default)")
    parser.add_argument("--no-auto", dest="auto", action="store_false", help="Disable inference from the task text")
    parser.add_argument("--provisional", action="store_true", help="Create a provisional route for semantic review")
    parser.add_argument("--assess-json", help="Complete a provisional route with a JSON semantic assessment")
    parser.add_argument("--state", default=".codex/delivery-state.json")
    parser.add_argument("--base-dir", default=".")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    base_dir = Path(args.base_dir).resolve()
    state_path = _state_path(base_dir, args.state)

    if args.assess_json:
        try:
            state = load_state(state_path)
            state = _apply_assessment(state, args.assess_json, base_dir)
        except (OSError, ValueError, HostTrustError) as error:
            print(str(error), file=sys.stderr)
            return 2
        finally:
            if "state" in locals() and state.get("route_status") == "assessed":
                save_state(state_path, state)
        route_line = component_line(state)
        if args.json:
            print(json.dumps({
                "route": route_line,
                "notes": state.get("routing_notes", []),
                "state": state,
            }, ensure_ascii=False, indent=2))
        else:
            print(route_line)
            for note in state.get("routing_notes", []):
                print(f"Note: {note}")
            print(f"State: {state_path}")
        return 0

    if not args.task:
        parser.error("--task is required unless --assess-json is used")
    args.task = strip_entry_phrase(args.task)
    if not args.task:
        print(f"{ENTRY_PHRASE} 后面需要描述要实现或修改的内容。", file=sys.stderr)
        return 2

    explicit = {key: bool(getattr(args, key)) for key in FLAG_KEYS}
    inferred: dict[str, bool] = {}
    if args.auto:
        inferred = infer_from_text(args.task)
        for key, value in inferred.items():
            if value and not explicit[key]:
                setattr(args, key, True)
    if args.design_change and args.reuse_design:
        parser.error("--design-change and --reuse-design cannot be used together")
    if args.design_change is None:
        args.design_change = args.ui and not args.reuse_design
    if args.design_change:
        args.ui = True

    tier, notes = infer_tier(args)
    if tier != "T0":
        try:
            enforce_host_trust()
        except HostTrustError as error:
            print(str(error), file=sys.stderr)
            return 4
    auto_keys = [key for key, value in inferred.items() if value and not explicit[key]]
    if auto_keys:
        notes.append("Auto-inferred flags: " + ", ".join(auto_keys))
    components = components_for(
        tier,
        ui=args.ui,
        design_change=args.design_change,
        behavior_change=args.behavior_change,
    )
    provisional = bool(args.provisional)
    state = build_state(
        args.task,
        tier,
        components,
        ui=args.ui,
        behavior_change=args.behavior_change,
        design_change=args.design_change,
        new_capability=args.new_capability,
        route_status="provisional" if provisional else "assessed",
        route_source="heuristic" if provisional else "auto",
        heuristic=_heuristic_snapshot(args, tier, notes),
        routing_notes=notes,
    )

    save_state(state_path, state)

    route_line = f"Route: pending assessment | heuristic {tier}" if provisional else component_line(state)
    if args.json:
        print(json.dumps({"route": route_line, "notes": notes, "state": state}, ensure_ascii=False, indent=2))
    else:
        print(route_line)
        for note in notes:
            print(f"Note: {note}")
        print(f"State: {state_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
