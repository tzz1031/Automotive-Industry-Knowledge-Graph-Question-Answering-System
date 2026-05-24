from __future__ import annotations

import json
import http.server
import os
import re
import sys
import webbrowser
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent
CAR_DATA_PATH = Path(os.getenv("CAR_DATA_JSON", BASE_DIR / "data" / "car_data" / "car_data.json"))
ENTITY_DATA_PATH = Path(os.getenv("ENTITY_DATA_JSON", BASE_DIR / "data" / "car_data" / "car_entity.json"))


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def as_list(value: Any) -> list[Any]:
    if value in (None, "", [], {}, "None", "[]"):
        return []
    if isinstance(value, list):
        return [item for item in value if item not in (None, "", "None", "[]")]
    return [value]


def clean(value: Any, default: str = "-") -> str:
    if value in (None, "", [], {}, "None", "[]"):
        return default
    return str(value)


def contains(haystack: Any, needle: str) -> bool:
    if not needle:
        return False
    return needle.casefold() in json.dumps(haystack, ensure_ascii=False).casefold()


def join_values(values: list[Any], limit: int = 8) -> str:
    names: list[str] = []
    seen: set[str] = set()
    for value in values:
        for item in as_list(value):
            text = clean(item, "").strip()
            if text and text not in seen:
                names.append(text)
                seen.add(text)
    if not names:
        return "暂无"
    suffix = f" 等{len(names)}项" if len(names) > limit else ""
    return "、".join(names[:limit]) + suffix


def category_zh(category: str | None) -> str:
    mapping = {
        "ADASSystem": "智能驾驶/ADAS系统",
        "BatteryTechnology": "电池技术",
        "Chip": "车规芯片",
        "Sensor": "传感器",
        "ChargingTechnology": "充电技术",
        "DriveSystem": "电驱系统",
        "VehiclePlatform": "整车平台",
        "EEArchitecture": "电子电气架构",
        "ChassisTechnology": "底盘技术",
        "MaterialProcess": "材料与工艺",
        "AlgorithmTechnology": "算法技术",
        "Technology": "技术实体",
        "IndustryEntity": "产业实体",
    }
    return mapping.get(category or "", category or "技术实体")


class LocalJsonQA:
    ENERGY = {
        "纯电": "BEV",
        "电动": "BEV",
        "插混": "PHEV",
        "插电": "PHEV",
        "增程": "EREV",
        "燃油": "ICE",
        "汽油": "ICE",
        "轻混": "MHEV",
    }
    PRICE_SEGMENTS = {
        "入门": (0, 10),
        "经济": (10, 15),
        "中端": (15, 25),
        "高端": (25, 40),
        "豪华": (40, 999),
    }
    CATEGORY_WORDS = {
        "ADAS": "ADASSystem",
        "智驾": "ADASSystem",
        "智能驾驶": "ADASSystem",
        "电池": "BatteryTechnology",
        "芯片": "Chip",
        "传感器": "Sensor",
        "雷达": "Sensor",
        "激光雷达": "Sensor",
        "充电": "ChargingTechnology",
        "超充": "ChargingTechnology",
        "电驱": "DriveSystem",
        "平台": "VehiclePlatform",
        "架构": "EEArchitecture",
        "底盘": "ChassisTechnology",
        "材料": "MaterialProcess",
        "工艺": "MaterialProcess",
        "算法": "AlgorithmTechnology",
    }

    def __init__(self, car_path: Path = CAR_DATA_PATH, entity_path: Path = ENTITY_DATA_PATH):
        self.car_path = car_path
        self.entity_path = entity_path
        self.cars: list[dict[str, Any]] = load_json(car_path)
        graph = load_json(entity_path)
        self.entities: dict[str, dict[str, Any]] = graph.get("entities", {})
        self._fill_full_names()

    def answer(self, question: str) -> str:
        q = question.strip()
        if not q:
            return "请提出一个汽车技术、车型、企业或供应链相关问题。"

        chat = self._chat(q)
        if chat:
            return chat

        if any(word in q for word in ["统计", "概览", "分布", "数量", "多少个节点"]):
            return self._stats(q)
        if any(word in q for word in ["对比", "比较", "VS", "vs"]) and any(word in q for word in ["和", "与", "VS", "vs"]):
            left, right = self._extract_compare(q)
            if left and right:
                return self._compare(left, right)
        if any(word in q for word in ["研发", "发布", "推出"]) and any(word in q for word in ["技术", "系统", "平台", "方案"]):
            company = re.split(r"研发|发布|推出", q, maxsplit=1)[0].strip()
            if company:
                return self._company_technologies(company)
        if any(word in q for word in ["车型", "参数", "配置", "详情", "续航", "价格", "电池", "电机", "多少钱"]):
            car = self._find_car(q)
            if car:
                return self._car_detail(car)
        if any(word in q for word in ["算力", "测距", "角分辨率", "参数", "规格", "属于什么", "是什么", "介绍"]):
            tech_answer = self._technology_detail(q)
            if tech_answer:
                return tech_answer
        if any(word in q for word in ["用了", "使用", "搭载", "采用", "配备", "装配"]):
            target = self._extract_usage_target(q)
            if target:
                return self._cars_using(target)
        if any(word in q for word in ["供应", "供货", "供应商"]):
            target = re.split(r"供应|供货|供应商", q, maxsplit=1)[0].strip() or q.replace("供应商", "").strip()
            if target:
                return self._supplier(target)
        if self._looks_like_filter(q):
            return self._filter(q)
        if self._looks_like_technology_list(q):
            return self._technology_list(q)
        return self._fallback(q)

    def _fill_full_names(self) -> None:
        for car in self.cars:
            if not car.get("full_name"):
                parts = [car.get("brand_name"), car.get("series_name"), car.get("car_name")]
                car["full_name"] = " ".join(clean(part, "") for part in parts if clean(part, ""))

    def _chat(self, q: str) -> str | None:
        if any(word in q.lower() for word in ["你好", "您好", "hello", "hi", "在吗"]):
            return "你好，我是本地 JSON 版汽车技术知识问答助手。可以问我技术实体、车型配置、企业技术关系和供应链信息。"
        if any(word in q for word in ["你能做什么", "有什么功能", "怎么用", "可以问什么"]):
            return "你可以问：有哪些ADAS系统、C-Pilot 5.0的算力是多少、华为研发了哪些技术、哪些车用了Orin、有激光雷达的纯电SUV有哪些、宁德时代供应哪些车、小米SU7参数。"
        if any(word in q for word in ["谢谢", "感谢"]):
            return "不客气。继续问技术、车型、企业或供应链问题都可以。"
        return None

    def _paragraph(self, parts: list[Any]) -> str:
        sentences: list[str] = []
        for part in parts:
            text = re.sub(r"\s+", " ", str(part or "").strip())
            text = re.sub(r"^-+\s*", "", text).strip()
            if text:
                sentences.append(text.rstrip("；; "))
        paragraph = "；".join(sentences)
        return paragraph.replace("：；", "：").replace("。；", "。").replace("；。", "。")

    def _looks_like_technology_list(self, q: str) -> bool:
        return any(word in q for word in ["哪些", "有哪些", "有什么"]) and any(word in q for word in self.CATEGORY_WORDS)

    def _technology_list(self, q: str) -> str:
        category = ""
        for word, value in self.CATEGORY_WORDS.items():
            if word in q:
                category = value
                break
        rows = [
            (name, entity.get("category"))
            for name, entity in self.entities.items()
            if not category or entity.get("category") == category
        ]
        rows = sorted(rows, key=lambda item: (item[1] or "", item[0]))
        if not rows:
            return "本地 JSON 中没有找到相关技术实体。可以换成“ADAS系统”“电池技术”“芯片技术”等类别再试。"
        title = f"{category_zh(category)}" if category else "技术实体"
        lines = [f"本地 JSON 中共找到 {len(rows)} 项{title}，代表性条目如下："]
        for name, cat in rows[:40]:
            lines.append(f"  - {name}（{category_zh(cat)}）")
        return self._paragraph(lines)

    def _technology_detail(self, q: str) -> str | None:
        name = re.sub(r"的?(算力|测距能力|角分辨率|参数|规格|是什么|属于什么|介绍|多少)$", "", q).strip()
        match = self._find_entity(name or q)
        if not match:
            return None
        tech_name, entity = match
        lines = [f"{tech_name} 是一个{category_zh(entity.get('category'))}。"]
        attrs = entity.get("attributes") or {}
        if attrs:
            lines.append("关键参数包括：" + "；".join(f"{k}为{v}" for k, v in list(attrs.items())[:8]) + "。")
        rels = entity.get("relations") or []
        if rels:
            lines.append("相关关系如下：")
            for rel in rels[:10]:
                lines.append(f"  - {rel.get('relation', '相关')}：{join_values(as_list(rel.get('object')), 6)}")
        if not attrs and not rels:
            lines.append("当前 JSON 只记录了实体类别，尚未抽取到更细的参数或关系。")
        return self._paragraph(lines)

    def _company_technologies(self, company: str) -> str:
        rows: list[tuple[str, str | None, str]] = []
        for name, entity in self.entities.items():
            for rel in entity.get("relations") or []:
                relation = clean(rel.get("relation"), "")
                obj = rel.get("object")
                if contains(obj, company) and any(key in relation for key in ["研发", "发布", "推出", "合作"]):
                    rows.append((name, entity.get("category"), relation))
                    break
            if company in name:
                rows.append((name, entity.get("category"), "名称匹配"))
        rows = sorted(set(rows), key=lambda item: (item[1] or "", item[0]))
        if not rows:
            return f"本地 JSON 中没有找到“{company}”相关技术。可以尝试企业简称或完整名称。"
        lines = [f"本地 JSON 中找到 {len(rows)} 项与“{company}”相关的技术，主要包括："]
        for name, category, relation in rows[:30]:
            lines.append(f"  - {name}（{category_zh(category)}，{relation}）")
        return self._paragraph(lines)

    def _cars_using(self, target: str) -> str:
        rows = [car for car in self.cars if self._car_contains(car, target)]
        rows = sorted(rows, key=lambda car: self._num(car.get("price_wan"), 9999))
        if not rows:
            return f"本地 JSON 中没有找到使用或关联“{target}”的车型。可以尝试供应商名、芯片型号或技术简称。"
        return self._format_cars(rows, f"使用或关联“{target}”的车型共 {len(rows)} 款")

    def _supplier(self, name: str) -> str:
        rows = [car for car in self.cars if any(contains(item, name) for item in car.get("supplier_entities") or [])]
        if not rows:
            return self._cars_using(name)
        brands = sorted({clean(car.get("brand_name"), "") for car in rows if car.get("brand_name")})
        lines = [f"本地 JSON 中，“{name}”关联 {len(rows)} 款车型，覆盖 {len(brands)} 个品牌。"]
        if brands:
            lines.append("覆盖品牌包括：" + "、".join(brands[:12]) + "。")
        lines.append("代表车型如下：")
        for car in sorted(rows, key=lambda item: self._num(item.get("price_wan"), 9999))[:20]:
            lines.append(self._car_row(car))
        return self._paragraph(lines)

    def _filter(self, q: str) -> str:
        rows = list(self.cars)
        desc: list[str] = []
        m = re.search(r"(\d+(?:\.\d+)?)\s*万?(?:以内|以下|不超过|低于)", q)
        if m:
            limit = float(m.group(1))
            rows = [car for car in rows if self._num(car.get("price_wan"), 9999) <= limit]
            desc.append(f"{limit:g}万以内")
        m = re.search(r"(\d+(?:\.\d+)?)\s*万?(?:以上|起|高于)", q)
        if m:
            limit = float(m.group(1))
            rows = [car for car in rows if self._num(car.get("price_wan"), -1) >= limit]
            desc.append(f"{limit:g}万以上")
        m = re.search(r"(?:续航|里程)\s*(?:超过|大于|不少于|以上|>=?)\s*(\d+)", q)
        if m:
            limit = int(m.group(1))
            rows = [car for car in rows if self._num(car.get("range_km"), 0) >= limit]
            desc.append(f"续航{limit}km以上")
        for word, code in self.ENERGY.items():
            if word in q:
                rows = [car for car in rows if car.get("energy_category") == code]
                desc.append(word)
                break
        for word, (lo, hi) in self.PRICE_SEGMENTS.items():
            if word in q:
                rows = [car for car in rows if lo <= self._num(car.get("price_wan"), 9999) < hi]
                desc.append(word)
                break
        if "SUV" in q.upper():
            rows = [car for car in rows if "SUV" in clean(car.get("body_category") or car.get("series_type")).upper()]
            desc.append("SUV")
        if "MPV" in q.upper():
            rows = [car for car in rows if "MPV" in clean(car.get("body_category") or car.get("series_type")).upper()]
            desc.append("MPV")
        if "轿车" in q:
            rows = [car for car in rows if "轿车" in clean(car.get("car_class")) or clean(car.get("body_category")) == "Sedan"]
            desc.append("轿车")
        if "四驱" in q:
            rows = [car for car in rows if "四驱" in clean(car.get("drive_type"))]
            desc.append("四驱")
        if "后驱" in q:
            rows = [car for car in rows if "后驱" in clean(car.get("drive_type"))]
            desc.append("后驱")
        if "激光雷达" in q:
            rows = [car for car in rows if car.get("has_lidar") is True or self._num(car.get("lidar_count"), 0) > 0]
            desc.append("激光雷达")
        if not desc:
            return self._fallback(q)
        if not rows:
            return f"本地 JSON 中没有找到同时满足“{'、'.join(desc)}”的车型。"
        return self._format_cars(sorted(rows, key=lambda car: self._num(car.get("price_wan"), 9999)), f"满足“{'、'.join(desc)}”的车型共 {len(rows)} 款")

    def _car_detail(self, car: dict[str, Any]) -> str:
        lines = [f"{car.get('full_name')} 的核心配置如下："]
        lines.append(f"品牌/车系：{clean(car.get('brand_name'))} / {clean(car.get('series_name'))}；级别：{clean(car.get('car_class'))}。")
        lines.append(f"价格：{clean(car.get('price_wan'))}万元；能源：{clean(car.get('energy_category'))}（{clean(car.get('energy_type'))}）；续航：{clean(car.get('range_km'))}km。")
        lines.append(f"动力：0-100km/h加速 {clean(car.get('acceleration_0_100_num'))}s，最高车速 {clean(car.get('top_speed_num'))}km/h，驱动形式 {clean(car.get('drive_type'))}。")
        lines.append(f"尺寸：{clean(car.get('length_mm_num'))}x{clean(car.get('width_mm_num'))}x{clean(car.get('height_mm_num'))}mm，轴距 {clean(car.get('wheelbase_mm_num'))}mm。")
        lines.append(f"电机：{clean(car.get('motor_power_kw_num'))}kW，{clean(car.get('motor_torque_nm_num'))}Nm，{clean(car.get('motor_count'))}，类型 {clean(car.get('motor_type'))}。")
        lines.append(f"电池：{clean(car.get('battery_capacity_kwh_num'))}kWh，{clean(car.get('battery_type'))}，电芯品牌 {clean(car.get('battery_brand'))}。")
        lines.append(f"智能化：激光雷达 {clean(car.get('lidar_count'), '0')} 个，ADAS芯片 {join_values([car.get('adas_chip_supplier')], 6)}，座舱SoC {join_values([car.get('soc_chip_supplier')], 6)}。")
        return self._paragraph(lines)

    def _compare(self, left: str, right: str) -> str:
        a = self._find_car(left)
        b = self._find_car(right)
        if not a or not b:
            missing = [name for name, car in [(left, a), (right, b)] if not car]
            return "本地 JSON 中没有找到这些车型：" + "、".join(missing)
        fields = [
            ("价格", "price_wan", "万元"),
            ("续航", "range_km", "km"),
            ("加速", "acceleration_0_100_num", "s"),
            ("最高车速", "top_speed_num", "km/h"),
            ("轴距", "wheelbase_mm_num", "mm"),
            ("快充功率", "fast_charge_power_kw_num", "kW"),
            ("激光雷达数量", "lidar_count", "个"),
        ]
        lines = [f"下面对比 {a.get('full_name')} 和 {b.get('full_name')} 的核心参数："]
        for label, key, unit in fields:
            lines.append(f"  - {label}：{clean(a.get(key))}{unit} 对比 {clean(b.get(key))}{unit}")
        return self._paragraph(lines)

    def _stats(self, q: str) -> str:
        if "价格" in q:
            counter = Counter(clean(car.get("price_segment"), "未知") for car in self.cars)
            return self._paragraph(["当前车型数据的价格段分布如下：", *[f"{key}: {value}款" for key, value in counter.most_common()]])
        if "能源" in q or "动力" in q:
            counter = Counter(clean(car.get("energy_category"), "未知") for car in self.cars)
            return self._paragraph(["当前车型数据的能源类型分布如下：", *[f"{key}: {value}款" for key, value in counter.most_common()]])
        if "供应" in q:
            counter: Counter[str] = Counter()
            for car in self.cars:
                for item in car.get("supplier_entities") or []:
                    supplier = item.get("supplier")
                    if supplier:
                        counter[supplier] += 1
            return self._paragraph(["供应商关联车型数量 Top15 如下：", *[f"{key}: {value}款" for key, value in counter.most_common(15)]])
        if "技术" in q:
            counter = Counter(clean(entity.get("category"), "Technology") for entity in self.entities.values())
            return self._paragraph(["技术图谱中的实体分类如下：", *[f"{category_zh(key)}（{key}）: {value}项" for key, value in counter.most_common()]])
        return self._paragraph([f"当前本地 JSON 数据概览：", f"Car: {len(self.cars)}", f"Technology/Entity: {len(self.entities)}"])

    def _fallback(self, q: str) -> str:
        car = self._find_car(q)
        if car:
            return self._car_detail(car)
        entity = self._find_entity(q)
        if entity:
            return self._technology_detail(entity[0]) or ""
        return (
            "这个问题暂时没有匹配到合适的本地 JSON 查询。可以换成更明确的问法，例如："
            "有哪些ADAS系统？；"
            "C-Pilot 5.0的算力是多少？；"
            "华为研发了哪些技术？；"
            "哪些车用了Orin？；"
            "有激光雷达的纯电SUV有哪些？；"
            "统计技术分类。"
        )

    def _find_car(self, text: str) -> dict[str, Any] | None:
        key = self._clean_question_name(text)
        if not key:
            return None
        norm_key = self._norm_name(key)
        candidates = []
        for car in self.cars:
            blob = " ".join(
                clean(car.get(field), "")
                for field in ["full_name", "brand_name", "series_name", "car_name", "trim_name", "search_aliases"]
            )
            norm_blob = self._norm_name(blob)
            if (
                key.casefold() in blob.casefold()
                or blob.casefold() in key.casefold()
                or norm_key in norm_blob
                or norm_blob in norm_key
            ):
                candidates.append(car)
        return sorted(candidates, key=lambda car: len(clean(car.get("full_name"), "")))[0] if candidates else None

    def _find_entity(self, text: str) -> tuple[str, dict[str, Any]] | None:
        key = self._clean_question_name(text)
        if not key:
            return None
        candidates = [(name, entity) for name, entity in self.entities.items() if key.casefold() in name.casefold() or name.casefold() in key.casefold()]
        return sorted(candidates, key=lambda item: len(item[0]))[0] if candidates else None

    def _car_contains(self, car: dict[str, Any], target: str) -> bool:
        fields = [
            "full_name",
            "brand_name",
            "series_name",
            "car_name",
            "battery_type",
            "battery_brand",
            "drive_type",
            "adas_chip_supplier",
            "soc_chip_supplier",
            "lidar_supplier",
            "supplier_entities",
            "tech_config",
        ]
        return any(contains(car.get(field), target) for field in fields)

    def _looks_like_filter(self, q: str) -> bool:
        return bool(re.search(r"\d+\s*万|\d+\s*km|续航|以内|以下|以上|纯电|插混|增程|SUV|MPV|轿车|四驱|后驱|激光雷达", q, re.I))

    def _extract_usage_target(self, q: str) -> str:
        patterns = [
            r"(?:用了|使用|搭载|采用|配备|装配)(.+?)(?:的?(?:车|车型|汽车)|$)",
            r"(?:哪些车|什么车|车型).*(?:用了|使用|搭载|采用|配备|装配)(.+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, q)
            if match:
                target = re.sub(r"的?(电池|芯片|雷达|技术|系统|平台|方案|产品)$", "", match.group(1)).strip()
                target = target.strip("？?，,。.;；：:")
                if target:
                    return target
        return ""

    def _extract_compare(self, q: str) -> tuple[str, str]:
        match = re.search(r"(.{1,40}?)(?:和|与|vs|VS)(.{1,40}?)(?:对比|比较|区别|哪个好|$)", q)
        if not match:
            return "", ""
        return self._strip_name(match.group(1)), self._strip_name(match.group(2))

    def _clean_question_name(self, text: str) -> str:
        cleaned = re.sub(r"(的)?(参数|配置|详情|续航|价格|电池|电机|多少钱|算力|测距能力|角分辨率|是什么|介绍|对比|比较|有哪些|哪些车|用了|使用|搭载|采用|配备|装配|供应|供货|供应商|？|\?)", "", text)
        return self._strip_name(cleaned)

    def _strip_name(self, text: str) -> str:
        return str(text or "").strip("？?，,。.;；：:、 ")

    def _norm_name(self, text: str) -> str:
        return re.sub(r"[\s？?，,。.;；：:、_-]+", "", str(text or "")).casefold()

    def _format_cars(self, rows: list[dict[str, Any]], title: str) -> str:
        lines = [f"{title}，代表车型如下："]
        for car in rows[:20]:
            lines.append(self._car_row(car))
        return self._paragraph(lines)

    def _car_row(self, car: dict[str, Any]) -> str:
        return (
            f"  - {clean(car.get('full_name'))}：{clean(car.get('brand_name'))}，"
            f"{clean(car.get('price_wan'))}万元，{clean(car.get('energy_category'))}，"
            f"续航{clean(car.get('range_km'))}km"
        )

    def _num(self, value: Any, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default


print("加载本地 JSON 问答引擎...")
try:
    qa = LocalJsonQA()
    print(f"问答引擎已就绪：车型 {len(qa.cars)} 条，技术实体 {len(qa.entities)} 条")
except Exception as exc:
    print(f"本地 JSON 问答引擎加载失败：{exc}")
    raise


HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>汽车技术本地知识问答</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Microsoft YaHei',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#101820;color:#e8eef4;height:100vh;display:flex}
.sidebar{width:292px;background:#17222c;padding:20px;border-right:1px solid #263645;overflow-y:auto}
.sidebar h2{color:#7cc4ff;font-size:17px;margin-bottom:8px}
.sidebar .sub{font-size:12px;color:#8aa0b3;line-height:1.5;margin-bottom:18px}
.tag{display:inline-block;background:#21394a;color:#b8d8ee;padding:5px 10px;border-radius:8px;margin:4px 3px;font-size:12px;cursor:pointer;transition:.15s}
.tag:hover{background:#2e5f7e;color:#fff}
.main{flex:1;display:flex;flex-direction:column;min-width:0}
.header{background:#17222c;padding:13px 24px;border-bottom:1px solid #263645;display:flex;align-items:center;gap:12px}
.dot{width:8px;height:8px;border-radius:50%;background:#35d07f}
.header span{font-size:14px;color:#bfd2df}
.chat{flex:1;overflow-y:auto;padding:22px 26px}
.msg{margin-bottom:17px;display:flex;gap:12px;animation:fadeIn .2s}
.msg.user{flex-direction:row-reverse}
.avatar{width:34px;height:34px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:14px;flex-shrink:0}
.bot .avatar{background:#26394a;color:#9fd3ff}
.user .avatar{background:#2f6690;color:#fff}
.bubble{max-width:min(780px,78vw);padding:13px 16px;border-radius:8px;font-size:14px;line-height:1.7;white-space:pre-wrap;word-break:break-word}
.bot .bubble{background:#17222c;border:1px solid #263645}
.user .bubble{background:#24577d}
.time{font-size:10px;color:#72899a;margin-top:4px}
.input-area{background:#17222c;padding:16px 24px;border-top:1px solid #263645;display:flex;gap:12px}
input{flex:1;background:#0d141b;border:1px solid #2b3d4d;border-radius:8px;padding:11px 14px;color:#e8eef4;font-size:14px;outline:none}
input:focus{border-color:#7cc4ff}
button{background:#2f6f9f;color:#fff;border:none;border-radius:8px;padding:0 22px;font-size:14px;cursor:pointer}
button:hover{background:#3984ba}
.loading{display:flex;align-items:center;gap:8px;color:#8aa0b3;font-size:13px;padding:12px 0}
.spinner{width:16px;height:16px;border:2px solid #2b3d4d;border-top-color:#7cc4ff;border-radius:50%;animation:spin .8s linear infinite}
@keyframes fadeIn{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:translateY(0)}}
@keyframes spin{to{transform:rotate(360deg)}}
</style>
</head>
<body>
<div class="sidebar">
  <h2>汽车技术问答</h2>
  <div class="sub">基于本地 JSON 文件，面向智能驾驶、电池、芯片、传感器、充电、车型配置与供应链问题。</div>
  <div>
    <span class="tag" onclick="ask('有哪些ADAS系统？')">ADAS系统</span>
    <span class="tag" onclick="ask('有哪些电池技术？')">电池技术</span>
    <span class="tag" onclick="ask('有哪些芯片技术？')">芯片技术</span>
    <span class="tag" onclick="ask('有哪些传感器技术？')">传感器</span>
    <span class="tag" onclick="ask('哪些车用了Orin？')">Orin车型</span>
    <span class="tag" onclick="ask('华为研发了哪些技术？')">华为技术</span>
    <span class="tag" onclick="ask('C-Pilot 5.0的算力是多少？')">C-Pilot算力</span>
    <span class="tag" onclick="ask('有激光雷达的纯电SUV有哪些？')">激光雷达SUV</span>
    <span class="tag" onclick="ask('统计技术分类')">技术分类</span>
  </div>
</div>

<div class="main">
  <div class="header">
    <div class="dot"></div>
    <span>本地 JSON 汽车知识问答 · 在线</span>
  </div>
  <div class="chat" id="chat">
    <div class="msg bot">
      <div class="avatar">KG</div>
      <div>
        <div class="bubble">你好，我是汽车技术本地知识问答助手。你可以询问 ADAS、电池、芯片、激光雷达、充电技术、整车平台，以及技术与企业、车型之间的关系。</div>
      </div>
    </div>
  </div>
  <div class="input-area">
    <input id="input" placeholder="输入技术、车型或供应链问题，按 Enter 发送" onkeydown="if(event.key==='Enter')send()">
    <button onclick="send()">发送</button>
  </div>
</div>

<script>
let history = [];

function escapeHtml(text) {
  return text.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function addMsg(role, text) {
  let chat = document.getElementById('chat');
  let div = document.createElement('div');
  div.className = 'msg ' + role;
  let avatar = role === 'user' ? '我' : 'KG';
  let safe = escapeHtml(text || '');
  div.innerHTML = `<div class="avatar">${avatar}</div>
    <div><div class="bubble">${safe}</div>
    <div class="time">${new Date().toLocaleTimeString()}</div></div>`;
  chat.appendChild(div);
  chat.scrollTop = chat.scrollHeight;
}

function addLoading() {
  let chat = document.getElementById('chat');
  let div = document.createElement('div');
  div.className = 'loading';
  div.id = 'loading';
  div.innerHTML = '<div class="spinner"></div>正在查询本地 JSON...';
  chat.appendChild(div);
  chat.scrollTop = chat.scrollHeight;
}

function removeLoading() {
  let el = document.getElementById('loading');
  if (el) el.remove();
}

async function send() {
  let input = document.getElementById('input');
  let q = input.value.trim();
  if (!q) return;
  input.value = '';
  addMsg('user', q);
  addLoading();
  try {
    let resp = await fetch('/ask', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({q: q, history: history.slice(-6)})
    });
    let data = await resp.json();
    removeLoading();
    addMsg('bot', data.answer || '没有找到相关信息。');
    history.push({role:'user', content:q});
    history.push({role:'assistant', content:data.answer || ''});
  } catch(e) {
    removeLoading();
    addMsg('bot', '请求失败：' + e.message);
  }
}

function ask(q) {
  document.getElementById('input').value = q;
  send();
}
</script>
</body>
</html>"""


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML.encode("utf-8"))
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path != "/ask":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            question = body.get("q", "").strip()
            answer = qa.answer(question) if question else "请输入问题。"
            self.send_json({"answer": answer})
        except Exception as exc:
            self.send_json({"answer": f"系统错误：{exc}"})

    def send_json(self, data):
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def log_message(self, format, *args):
        pass


def main():
    os.chdir(BASE_DIR)
    port = int(os.getenv("WEB_UI_PORT", "8888"))
    print(f"\n{'=' * 50}")
    print("  本地 JSON 汽车技术知识问答 Web")
    print(f"  数据源: {CAR_DATA_PATH}")
    print(f"  数据源: {ENTITY_DATA_PATH}")
    print(f"  http://localhost:{port}")
    print(f"{'=' * 50}\n")
    webbrowser.open(f"http://localhost:{port}")
    http.server.HTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
