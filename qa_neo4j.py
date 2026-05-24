#!/usr/bin/env python3
# coding: utf-8
"""Neo4j-based QA for the current automotive knowledge graph.

This version matches graphBuild/import_all.py after the data migration:
  - car_data.json -> Car, Brand, Series, Motor, Battery, Supplier, SupplierProduct
  - car_entity.json -> Technology and technology relations
"""

from __future__ import annotations

import base64
import json
import os
import re
import urllib.error
import urllib.request
from typing import Any


URI = os.getenv("NEO4J_URI", "http://localhost:7474").rstrip("/")
USER = os.getenv("NEO4J_USER", "neo4j")
PASSWORD = os.getenv("NEO4J_PASSWORD", "12qwaszx")
DATABASE = os.getenv("NEO4J_DATABASE", "neo4j").strip("/") or "neo4j"


class Neo4jClient:
    def __init__(self, uri: str = URI, user: str = USER, password: str = PASSWORD, database: str = DATABASE):
        self.uri = uri.rstrip("/")
        self.database = database
        self.endpoint = f"{self.uri}/db/{self.database}/tx/commit"
        self.legacy_endpoint = f"{self.uri}/db/data/transaction/commit"
        self._legacy_mode = False
        token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
        self.headers = {"Content-Type": "application/json", "Authorization": f"Basic {token}"}

    def run(self, query: str, params: dict[str, Any] | None = None) -> list[list[Any]]:
        body = json.dumps(
            {"statements": [{"statement": query, "parameters": params or {}}]},
            ensure_ascii=False,
        ).encode("utf-8")
        data = self._post(body)
        if data.get("errors"):
            raise RuntimeError(json.dumps(data["errors"], ensure_ascii=False, indent=2))
        rows = data.get("results", [{}])[0].get("data", [])
        return [row["row"] for row in rows]

    def one(self, query: str, params: dict[str, Any] | None = None) -> list[Any] | None:
        rows = self.run(query, params)
        return rows[0] if rows else None

    def _post(self, body: bytes) -> dict[str, Any]:
        endpoint = self.legacy_endpoint if self._legacy_mode else self.endpoint
        req = urllib.request.Request(endpoint, body, headers=self.headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 404 and not self._legacy_mode:
                self._legacy_mode = True
                req = urllib.request.Request(self.legacy_endpoint, body, headers=self.headers)
                with urllib.request.urlopen(req, timeout=30) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            detail = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"Neo4j HTTP {exc.code} {exc.reason}: {detail}") from exc


DB = Neo4jClient()


def cypher(query: str, params: dict[str, Any] | None = None) -> list[list[Any]]:
    return DB.run(query, params)


def cypher_one(query: str, params: dict[str, Any] | None = None) -> list[Any] | None:
    return DB.one(query, params)


def first_match(pattern: str, text: str) -> str:
    m = re.search(pattern, text, re.I)
    return m.group(1).strip() if m else ""


def yes(value: Any) -> bool:
    return value is True or str(value).lower() == "true"


def clean_value(value: Any, default: str = "未知") -> str:
    if value in (None, "", [], {}, "None", "[]"):
        return default
    return str(value)


def join_names(values: list[Any], limit: int = 8) -> str:
    names = [str(v) for v in values if v not in (None, "", "None", "[]")]
    if not names:
        return "暂无"
    text = "、".join(names[:limit])
    if len(names) > limit:
        text += f"等{len(names)}项"
    return text


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


CHAT_PATTERNS = [
    (
        ["你好", "您好", "hello", "hi", "嗨", "在吗"],
        "你好，我是汽车技术知识图谱问答助手。你可以问我智能驾驶、电池、芯片、激光雷达、充电技术、整车平台，以及这些技术和企业、车型之间的关系。",
    ),
    (
        ["你是谁", "你叫什么", "你是做什么的", "介绍一下你"],
        "我是一个基于 Neo4j 汽车技术知识图谱的问答系统，主要帮助查询汽车技术实体、技术参数、企业研发关系、车型技术配置和供应链信息。",
    ),
    (
        ["你能做什么", "有什么功能", "怎么用", "可以问什么"],
        "你可以这样问：有哪些ADAS系统、C-Pilot 5.0的算力是多少、华为研发了哪些技术、哪些车用了Orin、有激光雷达的纯电SUV有哪些、统计技术分类。",
    ),
    (
        ["谢谢", "感谢", "多谢", "辛苦了"],
        "不客气。继续问技术、车型或企业关系都可以，我会优先从图谱里找结构化答案。",
    ),
    (
        ["再见", "拜拜", "bye", "下次见"],
        "再见。需要查询汽车技术图谱时，随时回来继续问。",
    ),
    (
        ["早上好", "上午好"],
        "早上好。今天可以从一个技术问题开始，比如“有哪些ADAS系统？”或者“哪些车用了Orin？”。",
    ),
    (
        ["下午好", "晚上好"],
        "你好。可以问我汽车技术、企业研发关系、车型技术配置或供应链问题。",
    ),
    (
        ["讲个笑话", "来个笑话", "无聊"],
        "可以，不过我更擅长汽车技术。比如一个很图谱式的冷笑话：车企说自己全栈自研，知识图谱默默补了一条关系：仍然采用供应商若干。",
    ),
]


def chat_reply(question: str) -> str | None:
    q = question.lower()
    for keywords, reply in CHAT_PATTERNS:
        if any(keyword.lower() in q for keyword in keywords):
            return reply
    return None


class Neo4jQA:
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

    def answer(self, question: str) -> str:
        q = question.strip()
        if not q:
            return "请提出一个汽车知识图谱相关问题。"

        chat = chat_reply(q)
        if chat:
            return chat

        # Put technology-list questions first. In some Windows consoles Chinese
        # input may be poorly decoded, but ASCII terms such as ADAS still remain.
        if any(w in q for w in ["ADAS", "C-Pilot", "ADS", "Orin", "M3"]) and any(w in q for w in ["哪些", "有哪些", "有什么", "系统", "技术", "?"]):
            return self._technology_list(q)

        if any(w in q for w in ["哪些", "有哪些", "有什么"]) and any(w in q for w in ["技术", "系统", "平台", "架构", "电池", "芯片", "传感器", "雷达", "ADAS", "智驾"]):
            return self._technology_list(q)

        if any(w in q for w in ["统计", "概览", "分布", "数量", "多少个节点"]):
            return self._stats(q)

        m = re.search(r"(.{1,40}?)\s*(?:和|与|vs|VS|对比|比较)\s*(.{1,40}?)(?:哪个好|哪个|对比|比较|区别|$)", q)
        if m:
            return self._compare(m.group(1).strip(), m.group(2).strip())

        if any(w in q for w in ["研发", "发布", "推出"]) and any(w in q for w in ["技术", "系统", "平台", "方案"]):
            company = first_match(r"(.+?)(?:研发|发布|推出)", q) or first_match(r"(.+?)(?:有哪些|有什么).*技术", q)
            if company:
                return self._company_technologies(company)

        if any(w in q for w in ["算力", "测距", "角分辨率", "参数", "规格", "属于什么", "是什么", "介绍"]):
            return self._technology_detail(q)

        if any(w in q for w in ["用了", "使用", "搭载", "采用", "配备", "装配"]):
            target = self._extract_usage_target(q)
            if target:
                return self._cars_using(target)

        if any(w in q for w in ["供应", "供货", "供应商"]):
            target = first_match(r"(.+?)(?:供应|供货)", q) or first_match(r"(.+?)(?:供应商)", q)
            if target:
                return self._supplier(target)

        if any(w in q for w in ["哪些车", "什么车", "车型"]) and any(w in q for w in ["品牌", "旗下", "有"]):
            brand = first_match(r"(.+?)(?:有哪些|有什么|旗下|品牌)", q)
            if brand:
                return self._brand_cars(brand)

        if self._looks_like_filter(q):
            return self._filter(q)

        if any(w in q for w in ["参数", "配置", "详情", "续航", "价格", "电池", "电机", "多少钱"]):
            name = re.sub(r"(的)?(参数|配置|详情|续航|价格|电池|电机|多少钱|多少)$", "", q).strip()
            return self._car_detail(name)

        return self._fallback(q)

    def _brand_cars(self, brand: str) -> str:
        rows = cypher(
            """
            MATCH (c:Car)-[:BELONGS_TO_BRAND]->(b:Brand)
            WHERE b.name CONTAINS $brand OR $brand CONTAINS b.name
            RETURN c.full_name, c.name, c.price_wan, c.energy_category, c.range_km
            ORDER BY c.price_wan
            """,
            {"brand": brand},
        )
        if not rows:
            return f"我没有在图谱中找到“{brand}”对应的品牌车型。可以换成品牌全称再试一次。"
        lines = [f"图谱中找到 {len(rows)} 款与“{brand}”相关的车型，按价格从低到高列出："]
        for full_name, name, price, energy, rng in rows[:20]:
            lines.append(f"  - {full_name or name}：{clean_value(price, '-')}万元，{clean_value(energy, '-')}，续航{clean_value(rng, '-')}km")
        return "\n".join(lines)

    def _car_detail(self, name: str) -> str:
        car = self._find_car(name)
        if not car:
            return f"我没有在图谱中匹配到车型“{name}”。可以尝试输入车系名或完整车型名。"

        cid = car.get("node_id")
        title = car.get("full_name") or car.get("name") or name
        lines = [f"{title} 的核心技术与配置如下。"]
        lines.append(
            f"它属于{clean_value(car.get('brand'))}品牌、{clean_value(car.get('series'))}车系，车型级别为{clean_value(car.get('vehicle_class'))}。"
        )
        lines.append(
            f"价格约为{clean_value(car.get('price_wan'), '-')}万元，能源类型是{clean_value(car.get('energy_category'), '-')}（{clean_value(car.get('powertrain_type'), '-')}），续航为{clean_value(car.get('range_km'), '-')}km。"
        )
        lines.append(
            f"动力方面，0-100km/h加速为{clean_value(car.get('acceleration'), '-')}s，最高车速{clean_value(car.get('top_speed'), '-')}km/h，驱动形式为{clean_value(car.get('drive_type'), '-')}。"
        )
        lines.append(
            f"车身尺寸为{clean_value(car.get('length_mm'), '-')}x{clean_value(car.get('width_mm'), '-')}x{clean_value(car.get('height_mm'), '-')}mm，轴距{clean_value(car.get('wheelbase_mm'), '-')}mm。"
        )
        lidar_desc = f"配备{car.get('lidar_count', 0)}个激光雷达" if yes(car.get("has_lidar")) else "未标注激光雷达配置"
        lines.append(f"智能化配置方面，该车型{lidar_desc}。")

        motor = cypher_one("MATCH (:Car {node_id:$id})-[:HAS_MOTOR]->(m:Motor) RETURN m", {"id": cid})
        if motor:
            m = motor[0]
            lines.append(
                f"电机参数为{clean_value(m.get('power_kw'), '-')}kW、{clean_value(m.get('torque_nm'), '-')}Nm，类型为{clean_value(m.get('motor_type'), '-')}，电机数量为{clean_value(m.get('motor_count'), '-')}。"
            )

        battery = cypher_one("MATCH (:Car {node_id:$id})-[:HAS_BATTERY]->(b:Battery) RETURN b", {"id": cid})
        if battery:
            b = battery[0]
            lines.append(
                f"电池容量为{clean_value(b.get('capacity_kwh'), '-')}kWh，电池类型为{clean_value(b.get('battery_type'), '-')}，电芯品牌为{clean_value(b.get('battery_brand'), '-')}。"
            )

        suppliers = cypher(
            """
            MATCH (:Car {node_id:$id})-[r]->(s:Supplier)
            WHERE type(r) STARTS WITH 'USES_'
            RETURN type(r), collect(DISTINCT s.name)[0..4]
            ORDER BY type(r)
            """,
            {"id": cid},
        )
        if suppliers:
            parts = [f"{rel.replace('USES_', '')}由{join_names(names, 4)}提供" for rel, names in suppliers[:6]]
            lines.append("供应链方面，" + "；".join(parts) + "。")

        products = cypher(
            """
            MATCH (:Car {node_id:$id})-[:USES_PRODUCT]->(p:SupplierProduct)
            RETURN p.supplier, p.name
            ORDER BY p.supplier, p.name
            """,
            {"id": cid},
        )
        if products:
            lines.append("关键芯片/部件包括：" + "、".join(f"{supplier}{product}" for supplier, product in products[:8]) + "。")

        return "\n".join(lines)

    def _filter(self, q: str) -> str:
        where = []
        params: dict[str, Any] = {}
        desc = []

        m = re.search(r"(\d+(?:\.\d+)?)\s*万?(?:以内|以下|不超过|低于)", q)
        if m:
            where.append("c.price_wan <= $price_max")
            params["price_max"] = float(m.group(1))
            desc.append(f"{m.group(1)}万以内")

        m = re.search(r"(\d+(?:\.\d+)?)\s*万?(?:以上|起|高于)", q)
        if m:
            where.append("c.price_wan >= $price_min")
            params["price_min"] = float(m.group(1))
            desc.append(f"{m.group(1)}万以上")

        m = re.search(r"(?:续航|里程)\s*(?:超过|大于|不少于|以上|>=?)\s*(\d+)", q)
        if m:
            where.append("c.range_km >= $range_min")
            params["range_min"] = int(m.group(1))
            desc.append(f"续航{m.group(1)}km以上")

        for word, code in self.ENERGY.items():
            if word in q:
                where.append("c.energy_category = $energy")
                params["energy"] = code
                desc.append(word)
                break

        for word, (lo, hi) in self.PRICE_SEGMENTS.items():
            if word in q:
                where.append("c.price_wan >= $seg_lo AND c.price_wan < $seg_hi")
                params["seg_lo"] = lo
                params["seg_hi"] = hi
                desc.append(word)
                break

        if "SUV" in q.upper():
            where.append("c.body_category = 'SUV'")
            desc.append("SUV")
        if "MPV" in q.upper():
            where.append("c.body_category = 'MPV'")
            desc.append("MPV")
        if "轿车" in q:
            where.append("(c.body_category = 'Sedan' OR c.vehicle_class CONTAINS '轿车')")
            desc.append("轿车")
        if "四驱" in q:
            where.append("c.drive_type CONTAINS '四驱'")
            desc.append("四驱")
        if "后驱" in q:
            where.append("c.drive_type CONTAINS '后驱'")
            desc.append("后驱")
        if "激光雷达" in q:
            where.append("c.has_lidar = true")
            desc.append("有激光雷达")

        if not where:
            return self._fallback(q)

        rows = cypher(
            f"""
            MATCH (c:Car)
            WHERE {' AND '.join(where)}
            RETURN c.full_name, c.price_wan, c.energy_category, c.range_km, c.lidar_count
            ORDER BY c.price_wan
            LIMIT 50
            """,
            params,
        )
        if not rows:
            return f"我没有找到同时满足“{'、'.join(desc)}”的车型。可以放宽价格、续航或车身类型条件。"
        lines = [f"满足“{'、'.join(desc)}”的车型共有 {len(rows)} 款，下面是较匹配的结果："]
        for full_name, price, energy, rng, lidar_count in rows[:20]:
            lines.append(f"  - {full_name}：{clean_value(price, '-')}万元，{clean_value(energy, '-')}，续航{clean_value(rng, '-')}km，激光雷达{lidar_count or 0}个")
        return "\n".join(lines)

    def _compare(self, left: str, right: str) -> str:
        a = self._find_car(left)
        b = self._find_car(right)
        if not a or not b:
            missing = [name for name, car in [(left, a), (right, b)] if not car]
            return "我没有找到这些车型：" + "、".join(missing)

        lines = [f"下面对比 {a.get('full_name') or a.get('name')} 和 {b.get('full_name') or b.get('name')} 的核心参数。"]
        fields = [
            ("价格", "price_wan", "万"),
            ("续航", "range_km", "km"),
            ("加速", "acceleration", "s"),
            ("极速", "top_speed", "km/h"),
            ("轴距", "wheelbase_mm", "mm"),
            ("快充功率", "fast_charge_power_kw", "kW"),
            ("激光雷达数量", "lidar_count", "个"),
        ]
        for label, key, unit in fields:
            lines.append(f"  - {label}：{clean_value(a.get(key), '-')}{unit} 对比 {clean_value(b.get(key), '-')}{unit}")
        return "\n".join(lines)

    def _supplier(self, name: str) -> str:
        rows = cypher(
            """
            MATCH (c:Car)-[r]->(s:Supplier)
            WHERE s.name CONTAINS $name OR $name CONTAINS s.name
            OPTIONAL MATCH (c)-[:BELONGS_TO_BRAND]->(b:Brand)
            RETURN DISTINCT c.full_name, b.name, c.price_wan, type(r)
            ORDER BY b.name, c.price_wan
            LIMIT 80
            """,
            {"name": name},
        )
        if not rows:
            return self._cars_using(name)

        brands = sorted({row[1] for row in rows if row[1]})
        lines = [f"图谱中，{name} 关联 {len(rows)} 款车型，覆盖 {len(brands)} 个品牌。"]
        if brands:
            lines.append("覆盖品牌包括：" + "、".join(brands[:12]) + "。")
        lines.append("代表车型如下：")
        for full_name, brand, price, rel in rows[:20]:
            lines.append(f"  - {full_name}：{brand or '-'}，{clean_value(price, '-')}万元，关系类型为 {rel}")
        return "\n".join(lines)

    def _cars_using(self, target: str) -> str:
        rows = cypher(
            """
            MATCH (c:Car)
            OPTIONAL MATCH (c)-[sr]->(s:Supplier)
            OPTIONAL MATCH (c)-[:USES_PRODUCT]->(p:SupplierProduct)
            WITH c,
                 collect(DISTINCT s.name) AS suppliers,
                 collect(DISTINCT p.name) AS products,
                 collect(DISTINCT p.supplier) AS product_suppliers
            WHERE c.full_name CONTAINS $target
               OR c.name CONTAINS $target
               OR c.brand CONTAINS $target
               OR c.series CONTAINS $target
               OR any(x IN suppliers WHERE x CONTAINS $target OR $target CONTAINS x)
               OR any(x IN products WHERE x CONTAINS $target OR $target CONTAINS x)
               OR any(x IN product_suppliers WHERE x CONTAINS $target OR $target CONTAINS x)
               OR c.battery_type CONTAINS $target
               OR c.drive_type CONTAINS $target
            RETURN DISTINCT c.full_name, c.brand, c.price_wan, c.energy_category, c.range_km
            ORDER BY c.price_wan
            LIMIT 50
            """,
            {"target": target},
        )
        if not rows:
            return f"我没有找到使用或关联“{target}”的车型。可以尝试输入供应商名、芯片型号或技术简称。"
        return self._format_car_rows(rows, f"使用或关联“{target}”")

    def _technology_detail(self, q: str) -> str:
        name = self._extract_technology_name(q)
        row = cypher_one(
            """
            MATCH (t:Technology)
            WHERE t.name CONTAINS $name OR $name CONTAINS t.name
            RETURN t
            ORDER BY size(t.name)
            LIMIT 1
            """,
            {"name": name or q},
        )
        if not row:
            return f"我没有在技术图谱中找到“{name or q}”。可以尝试输入完整技术名称，例如 C-Pilot 5.0、ADS 3.0、M3。"

        t = row[0]
        tid = t.get("node_id") or t.get("name")
        tech_name = t.get("name", tid)
        category = t.get("category", "Technology")
        lines = [f"{tech_name} 是一个{category_zh(category)}。"]

        attrs = []
        try:
            attrs_json = t.get("attributes_json")
            if attrs_json:
                attrs = list(json.loads(attrs_json).items())
        except Exception:
            attrs = []
        if attrs:
            lines.append("它在图谱中记录的关键参数包括：" + "；".join(f"{k}为{v}" for k, v in attrs[:8]) + "。")

        rels = cypher(
            """
            MATCH (:Technology {node_id:$id})-[r]->(o:Technology)
            RETURN type(r), r.name, o.name
            LIMIT 20
            """,
            {"id": tid},
        )
        if rels:
            lines.append("相关关系如下：")
            for rtype, rname, obj in rels[:10]:
                lines.append(f"  - {tech_name} 的“{rname or rtype}”指向 {obj}")
        if not attrs and not rels:
            lines.append("当前图谱只记录了它的实体类别，暂未抽取到更细的参数或关系。")
        return "\n".join(lines)

    def _company_technologies(self, company: str) -> str:
        rows = cypher(
            """
            MATCH (tech:Technology)-[:DEVELOPED_BY|PUBLISHED|LAUNCHED]->(company:Technology)
            WHERE company.name CONTAINS $company OR $company CONTAINS company.name
            RETURN DISTINCT tech.name, tech.category
            ORDER BY tech.category, tech.name
            LIMIT 80
            """,
            {"company": company},
        )
        if not rows:
            rows = cypher(
                """
                MATCH (tech:Technology)
                WHERE tech.name CONTAINS $company
                RETURN tech.name, tech.category
                LIMIT 30
                """,
                {"company": company},
            )
        if not rows:
            return f"我没有找到“{company}”相关技术。可以尝试输入企业简称或完整名称。"
        lines = [f"图谱中找到 {len(rows)} 项与“{company}”相关的技术，主要包括："]
        for name, category in rows[:30]:
            lines.append(f"  - {name}：{category_zh(category)}")
        return "\n".join(lines)

    def _technology_list(self, q: str) -> str:
        category_map = {
            "ADAS": "ADASSystem",
            "智驾": "ADASSystem",
            "智能驾驶": "ADASSystem",
            "电池": "BatteryTechnology",
            "芯片": "Chip",
            "传感器": "Sensor",
            "雷达": "Sensor",
            "充电": "ChargingTechnology",
            "电驱": "DriveSystem",
            "平台": "VehiclePlatform",
            "架构": "EEArchitecture",
            "底盘": "ChassisTechnology",
            "材料": "MaterialProcess",
            "工艺": "MaterialProcess",
        }
        category = ""
        for word, value in category_map.items():
            if word in q:
                category = value
                break

        if category:
            rows = cypher(
                """
                MATCH (t:Technology)
                WHERE t.category = $category
                RETURN t.name, t.category
                ORDER BY t.name
                LIMIT 80
                """,
                {"category": category},
            )
            title = f"{category} 类技术"
        else:
            rows = cypher(
                """
                MATCH (t:Technology)
                WHERE coalesce(t.category, 'Technology') <> 'IndustryEntity'
                RETURN t.name, t.category
                ORDER BY t.category, t.name
                LIMIT 80
                """
            )
            title = "技术实体"

        if not rows:
            return "我没有找到相关技术实体。可以换成“ADAS系统”“电池技术”“芯片技术”等类别再试。"
        lines = [f"图谱中共有 {len(rows)} 项{title}，代表性条目如下："]
        for name, cat in rows[:40]:
            lines.append(f"  - {name}：{category_zh(cat)}")
        return "\n".join(lines)

    def _stats(self, q: str) -> str:
        if "价格" in q:
            rows = cypher(
                """
                MATCH (c:Car)
                WHERE c.price_segment IS NOT NULL
                RETURN c.price_segment, count(*) AS cnt
                ORDER BY cnt DESC
                """
            )
            return "当前车型数据的价格段分布如下：\n" + "\n".join(f"  - {seg}: {cnt}款" for seg, cnt in rows)

        if "能源" in q or "动力" in q:
            rows = cypher(
                """
                MATCH (c:Car)
                RETURN c.energy_category, c.powertrain_type, count(*) AS cnt
                ORDER BY cnt DESC
                """
            )
            return "当前车型数据的能源类型分布如下：\n" + "\n".join(f"  - {code}（{label}）: {cnt}款" for code, label, cnt in rows)

        if "技术" in q:
            rows = cypher(
                """
                MATCH (t:Technology)
                RETURN coalesce(t.category, 'Technology') AS category, count(*) AS cnt
                ORDER BY cnt DESC
                """
            )
            return "技术图谱中的实体分类如下：\n" + "\n".join(f"  - {category_zh(cat)}（{cat}）: {cnt}项" for cat, cnt in rows)

        if "供应" in q:
            rows = cypher(
                """
                MATCH (s:Supplier)<-[r]-(c:Car)
                RETURN s.name, count(DISTINCT c) AS cnt
                ORDER BY cnt DESC
                LIMIT 15
                """
            )
            return "供应商关联车型数量 Top15 如下：\n" + "\n".join(f"  - {name}: {cnt}款" for name, cnt in rows)

        labels = ["Car", "Brand", "Series", "Supplier", "SupplierProduct", "Motor", "Battery", "Technology"]
        lines = ["当前 Neo4j 知识图谱概览如下："]
        for label in labels:
            row = cypher_one(f"MATCH (n:{label}) RETURN count(n)")
            lines.append(f"  - {label}: {row[0] if row else 0}")
        row = cypher_one("MATCH ()-[r]->() RETURN count(r)")
        lines.append(f"  - Relation: {row[0] if row else 0}")
        return "\n".join(lines)

    def _fallback(self, q: str) -> str:
        car = self._find_car(q)
        if car:
            return self._car_detail(q)

        brand = cypher_one("MATCH (b:Brand) WHERE b.name CONTAINS $q OR $q CONTAINS b.name RETURN b.name LIMIT 1", {"q": q})
        if brand:
            return self._brand_cars(brand[0])

        supplier = cypher_one("MATCH (s:Supplier) WHERE s.name CONTAINS $q OR $q CONTAINS s.name RETURN s.name LIMIT 1", {"q": q})
        if supplier:
            return self._supplier(supplier[0])

        tech = cypher_one("MATCH (t:Technology) WHERE t.name CONTAINS $q OR $q CONTAINS t.name RETURN t.name LIMIT 1", {"q": q})
        if tech:
            return self._technology_detail(tech[0])

        product = cypher_one("MATCH (p:SupplierProduct) WHERE p.name CONTAINS $q OR $q CONTAINS p.name RETURN p.name LIMIT 1", {"q": q})
        if product:
            return self._cars_using(product[0])

        return (
            "这个问题我暂时没有匹配到合适的图谱查询。你可以换成更明确的技术、车型或企业问法，例如：\n"
            "  - 有哪些ADAS系统？\n"
            "  - C-Pilot 5.0的算力是多少？\n"
            "  - 华为研发了哪些技术？\n"
            "  - 哪些车用了Orin？\n"
            "  - 有激光雷达的纯电SUV有哪些？\n"
            "  - 统计技术分类"
        )

    def _find_car(self, name: str) -> dict[str, Any] | None:
        key = name.strip()
        row = cypher_one(
            """
            MATCH (c:Car)
            WHERE c.full_name CONTAINS $key
               OR c.name CONTAINS $key
               OR c.series CONTAINS $key
               OR (coalesce(c.brand, '') + coalesce(c.series, '') + coalesce(c.name, '')) CONTAINS $key
               OR toString(coalesce(c.search_aliases, '')) CONTAINS $key
            RETURN c
            ORDER BY size(coalesce(c.full_name, c.name))
            LIMIT 1
            """,
            {"key": key},
        )
        return row[0] if row else None

    def _looks_like_filter(self, q: str) -> bool:
        return bool(re.search(r"\d+\s*万|\d+\s*km|续航|以内|以下|以上|纯电|插混|增程|SUV|MPV|轿车|四驱|后驱|激光雷达", q, re.I))

    def _extract_usage_target(self, q: str) -> str:
        patterns = [
            r"(?:用了|使用|搭载|采用|配备|装配)(.+?)(?:的)?(?:车|车型|汽车|$)",
            r"(?:哪些车|什么车|车型).*(?:用了|使用|搭载|采用|配备|装配)(.+)",
        ]
        for pattern in patterns:
            m = re.search(pattern, q)
            if m:
                target = re.sub(r"(的)?(电池|芯片|雷达|技术|系统|平台|方案|产品)$", "", m.group(1)).strip()
                if target:
                    return target
        return ""

    def _extract_technology_name(self, q: str) -> str:
        cleaned = re.sub(r"(的)?(算力|测距能力|角分辨率|参数|规格|是什么|属于什么|介绍|多少)$", "", q).strip()
        return cleaned or q

    def _format_car_rows(self, rows: list[list[Any]], title: str) -> str:
        lines = [f"{title}的车型共有 {len(rows)} 款，代表车型如下："]
        for full_name, brand, price, energy, rng in rows[:20]:
            lines.append(f"  - {full_name}：{brand or '-'}，{clean_value(price, '-')}万元，{energy or '-'}，续航{clean_value(rng, '-')}km")
        return "\n".join(lines)


if __name__ == "__main__":
    print("连接 Neo4j...")
    qa = Neo4jQA()
    print("请输入汽车技术或车型问题，输入 quit / exit / q 退出。")
    while True:
        try:
            query = input(">>> ").strip()
            if query.lower() in {"quit", "exit", "q"}:
                break
            if not query:
                continue
            print(qa.answer(query))
        except (KeyboardInterrupt, EOFError):
            break
        except Exception as exc:
            print(f"查询出错：{exc}")
